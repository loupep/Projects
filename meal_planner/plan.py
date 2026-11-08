"""Plan a week of dinners from the collected recipes.

    python plan.py                          # uses prefs.json
    python plan.py --people 2 --dinners 4 --max-time 35 --diet veggie
    python plan.py --avoid mushroom,olive --sources gousto,hellofresh
    python plan.py --keep gousto:4151       # keep a meal you want, fill the rest
    python plan.py --save                   # remember this week so it isn't repeated
    python plan.py --like gousto:4151       # favourite (picked more often)
    python plan.py --never hellofresh:abc   # never suggest again

How it picks: good ratings (made fair across sites), meals that share fresh
ingredients so packs get used up, a spread of proteins and cuisines, nothing
cooked in the last few weeks. Meals are ordered so the ones with the most
perishable ingredients get cooked first.

Writes data/plans/plan-<date>.md and .html (the HTML is phone friendly).
"""

import argparse
import datetime as dt
import html
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingredients import AISLES, fmt_amount  # noqa: E402
from library import DATA, HERE, load, quality_scores, scaled_ingredients  # noqa: E402
from swaps import swap_for  # noqa: E402

PREFS_FILE = os.path.join(HERE, "prefs.json")
HISTORY_FILE = os.path.join(DATA, "history.json")
TASTE_FILE = os.path.join(DATA, "taste.json")
SOURCE_NAMES = {
    "hellofresh": "HelloFresh", "gousto": "Gousto", "greenchef": "Green Chef",
    "grubby": "Grubby", "simplycook": "SimplyCook",
}
# How quickly things go off: cook these early in the week.
SPOILS_FAST = ("fish", "salmon", "cod", "haddock", "prawn", "basa", "hake", "mince", "salad leaf",
               "rocket", "spinach", "coriander", "basil", "mint", "parsley", "dill", "chive",
               "avocado", "beansprout", "mushroom", "raspberr", "strawberr")


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def prefs_from_args():
    prefs = {
        "people": 2, "dinners": 4, "max_minutes": 45, "diet": "any",
        "avoid": [], "sources": [], "recent_weeks": 6, "min_ratings": 0,
    }
    prefs.update(read_json(PREFS_FILE, {}))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--people", type=int)
    ap.add_argument("--dinners", type=int)
    ap.add_argument("--max-time", type=int, dest="max_minutes")
    ap.add_argument("--diet", choices=["any", "veggie", "vegan"])
    ap.add_argument("--avoid", help="comma list of ingredients to avoid")
    ap.add_argument("--sources", help="comma list of sites to use")
    ap.add_argument("--keep", help="comma list of recipe ids to include")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--save", action="store_true", help="remember this plan so it isn't repeated soon")
    ap.add_argument("--like", help="recipe id to favourite")
    ap.add_argument("--never", help="recipe id to never suggest")
    ap.add_argument("--search", help="find recipe ids by words in the title")
    args = ap.parse_args()
    for k in ("people", "dinners", "max_minutes", "diet", "seed"):
        if getattr(args, k, None) is not None:
            prefs[k] = getattr(args, k)
    if args.avoid is not None:
        prefs["avoid"] = [a.strip().lower() for a in args.avoid.split(",") if a.strip()]
    if args.sources is not None:
        prefs["sources"] = [s.strip() for s in args.sources.split(",") if s.strip()]
    return prefs, args


# ------------------------------------------------------------------ picking

def eligible(recipes, prefs, history, taste, keep_ids):
    cutoff = (dt.date.today() - dt.timedelta(weeks=prefs["recent_weeks"])).isoformat()
    recent = {rid for h in history if h["date"] >= cutoff for rid in h["ids"]}
    recent_titles = {t for h in history if h["date"] >= cutoff for t in h.get("titles", [])}
    never = set(taste.get("never", []))
    avoid = [a.lower() for a in prefs["avoid"]]
    out = []
    for r in recipes:
        if r["id"] in keep_ids:
            out.append(r)
            continue
        if not r["main_ok"] or r["id"] in never or r["id"] in recent or r["title"] in recent_titles:
            continue
        if prefs["max_minutes"] and (r.get("total_min") or 999) > prefs["max_minutes"]:
            continue
        if prefs["diet"] == "veggie" and not r["veggie"]:
            continue
        if prefs["diet"] == "vegan" and not r["vegan"]:
            continue
        if avoid and any(a in ing["key"] or a in ing["name"].lower() for ing in r["ingredients"] for a in avoid):
            continue
        if (r.get("rating_count") or 0) < prefs.get("min_ratings", 0):
            continue
        if not any(ing.get("amounts") for ing in r["ingredients"]):
            continue  # no quantities: can't build a shopping list
        out.append(r)
    return out


TITLE_STOP = {
    "with", "and", "the", "a", "in", "on", "of", "style", "inspired", "creamy", "spiced", "sauce",
    "salad", "rice", "chicken", "beef", "pork", "lamb", "fish", "veg", "vegetable", "vegetables",
    "roasted", "roast", "crispy", "sticky", "easy", "quick", "one", "pot", "pan", "tray", "bake",
    "fresh", "green", "red", "garlic", "herb", "cheese", "cheesy", "potato", "potatoes", "wedges",
    "mash", "&", "for", "your", "our", "breast", "thigh", "thighs", "fillet", "fillets", "steak",
}


def _title_words(title):
    words = set(re.findall(r"[a-zà-ÿ']+", title.lower().split(" with ")[0]))
    return {w for w in words if len(w) > 3 and w not in TITLE_STOP}


def objective(chosen):
    score = sum(r["_score"] for r in chosen)
    fresh = Counter(k for r in chosen for k in r["fresh_keys"])
    score += min(2.0, sum(0.35 * (n - 1) for n in fresh.values() if n > 1))  # shared fresh ingredients
    score -= 0.08 * sum(1 for n in fresh.values() if n == 1)      # one-off fresh items
    proteins = Counter(r["protein"] for r in chosen)
    score -= sum(1.2 * (n - 1) for p, n in proteins.items() if n > 1 and p != "veg")
    score -= sum(0.4 * (n - 2) for p, n in proteins.items() if n > 2 and p == "veg")
    meats = Counter(m for r in chosen for m in r.get("meats", []))
    score -= sum(0.7 * (n - 1) for n in meats.values() if n > 1)
    regions = Counter(r["region"] for r in chosen if r.get("region"))
    score -= sum(1.0 * (n - 1) for n in regions.values() if n > 1)
    sea = sum(1 for r in chosen if r["protein"] in ("fish", "seafood"))
    score -= 0.8 * max(0, sea - 1)
    dishes = Counter(r["dish"] for r in chosen if r.get("dish"))
    score -= sum(1.5 * (n - 1) for n in dishes.values() if n > 1)
    # Sharing fresh veg is good; sharing the same pastes and spices means
    # the meals will taste alike, so keep that down.
    for i, a in enumerate(chosen):
        fa = set(a["flavour_keys"])
        for b in chosen[i + 1:]:
            fb = set(b["flavour_keys"])
            if fa and fb:
                score -= 2.0 * len(fa & fb) / len(fa | fb)
    title_words = Counter(w for r in chosen for w in _title_words(r["title"]))
    score -= sum(1.5 * (n - 1) for n in title_words.values() if n > 1)
    score += 0.25 * len({r["source"] for r in chosen})
    return score


def choose(cands, n, keep, rng):
    pool = sorted(cands, key=lambda r: r["_score"], reverse=True)[:500]
    keep = [r for r in cands if r["id"] in keep]
    best, best_score = None, -1e9
    for _ in range(250):
        chosen = list(keep)
        start = [r for r in rng.sample(pool[:80], min(3, len(pool[:80]))) if r not in chosen]
        if len(chosen) < n and start:
            chosen.append(start[0])
        sample = rng.sample(pool, min(len(pool), 220))
        while len(chosen) < n:
            base = objective(chosen)
            pick, gain = None, -1e9
            for r in sample:
                if r in chosen:
                    continue
                g = objective(chosen + [r]) - base
                if g > gain:
                    pick, gain = r, g
            if pick is None:
                break
            chosen.append(pick)
        s = objective(chosen)
        if s > best_score:
            best, best_score = chosen, s
    return best or []


def cook_order(chosen):
    """Fish and seafood first (a day or two in the fridge), then meals with
    soft herbs and salad leaves, then everything else."""
    def urgency(r):
        keys = " ".join(i["key"] for i in r["ingredients"])
        fish = 3 if r["protein"] in ("fish", "seafood") else 0
        mince = 1.5 if "mince" in keys else 0
        return -(fish + mince + sum(0.5 for w in SPOILS_FAST if w in keys))
    return sorted(chosen, key=urgency)


# ------------------------------------------------------------ shopping list

def shopping_list(meals, people):
    items = {}
    pantry = {}
    for idx, r in enumerate(meals, 1):
        for ing in scaled_ingredients(r, people):
            if ing["pantry"]:
                pantry.setdefault(ing["key"], ing["name"])
                continue
            it = items.setdefault(ing["key"], {
                "key": ing["key"], "names": Counter(), "aisle": ing["aisle"],
                "amounts": defaultdict(float), "unknown": False, "meals": [],
            })
            it["names"][ing["name"]] += 1
            if idx not in it["meals"]:
                it["meals"].append(idx)
            if ing["amount"] is None:
                it["unknown"] = True
            else:
                it["amounts"][ing["unit"] or "unit"] += ing["amount"]
    aisle_order = [a for a, _ in AISLES] + ["Other"]
    grouped = defaultdict(list)
    for it in items.values():
        name = min(it["names"], key=lambda s: (len(s), s))
        qty = ", ".join(fmt_amount(a, u) for u, a in it["amounts"].items())
        if it["unknown"] and not qty:
            qty = "some"
        swap = swap_for(name)
        grouped[it["aisle"]].append({
            "name": name, "qty": qty, "meals": it["meals"], "swap": swap,
            "shared": len(it["meals"]) > 1,
        })
    out = []
    for a in aisle_order:
        if grouped.get(a):
            out.append((a, sorted(grouped[a], key=lambda x: x["name"].lower())))
    pantry_names = sorted({v.strip().capitalize() for v in pantry.values() if v.strip()})
    return out, pantry_names


# ------------------------------------------------------------------ output

def shares_with(meals):
    notes = {}
    for i, r in enumerate(meals):
        other = []
        for j, o in enumerate(meals):
            if i != j:
                common = sorted(set(r["fresh_keys"]) & set(o["fresh_keys"]))
                if common:
                    other.append((j + 1, common))
        notes[r["id"]] = other
    return notes


def render_markdown(plan):
    L = [f"# Dinners for the week of {plan['week']}", "",
         f"{plan['people']} people · {len(plan['meals'])} dinners · cook in this order", ""]
    for i, r in enumerate(plan["meals"], 1):
        bits = [f"{r.get('total_min') or '?'} min"]
        if r.get("nutrition", {}).get("kcal"):
            bits.append(f"{r['nutrition']['kcal']} kcal")
        if r.get("rating"):
            bits.append(f"★ {r['rating']:.1f} ({r.get('rating_count', 0):,})")
        bits.append(SOURCE_NAMES.get(r["source"], r["source"]))
        L.append(f"{i}. **{r['title']}** {r.get('subtitle') or ''}  \n   {' · '.join(bits)} · `{r['id']}`")
    L += ["", "## Shopping list", ""]
    for aisle, items in plan["shopping"]:
        L.append(f"### {aisle}")
        for it in items:
            extra = f" — meals {', '.join(map(str, it['meals']))}"
            if it["swap"]:
                extra += f" — *{it['swap']}*"
            L.append(f"- [ ] {it['name']}: {it['qty']}{extra}")
        L.append("")
    L += ["### Check the cupboard", ", ".join(plan["pantry"]), ""]
    for i, r in enumerate(plan["meals"], 1):
        L += [f"## {i}. {r['title']}", f"*{r.get('subtitle') or ''}*", "", f"[Original recipe]({r['url']})", "", "**Ingredients**"]
        for ing in scaled_ingredients(r, plan["people"]):
            L.append(f"- {fmt_amount(ing['amount'], ing['unit'])} {ing['name']}".replace("-  ", "- "))
        L += ["", "**Method**"]
        for n, s in enumerate(r["steps"], 1):
            L.append(f"{n}. " + s.replace("\n", " "))
        L.append("")
    return "\n".join(L)


CSS = """
:root {
  /* Layout: one phone-width column; a sticky bar jumps between Meals, Shopping and Recipes. */
  --paper: #f5f7f3; --card: #ffffff; --ink: #1d2a22; --muted: #5d6b62;
  --line: #dde3dc; --basil: #2f6b46; --basil-soft: #e3efe6; --pepper: #b8432f; --mark: #f0e4b8;
  --display: "Bricolage Grotesque", "Avenir Next", "Segoe UI", sans-serif;
  --body: "Atkinson Hyperlegible", "Segoe UI", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --paper: #121814; --card: #1a221d; --ink: #e6ede7; --muted: #9aa99f;
  --line: #2b3630; --basil: #7cc495; --basil-soft: #1f3327; --pepper: #f0876f; --mark: #4a4325; color-scheme: dark } }
:root[data-theme="dark"] {
  --paper: #121814; --card: #1a221d; --ink: #e6ede7; --muted: #9aa99f;
  --line: #2b3630; --basil: #7cc495; --basil-soft: #1f3327; --pepper: #f0876f; --mark: #4a4325; color-scheme: dark }
* { box-sizing: border-box; }
body { background: var(--paper); color: var(--ink); font: 16px/1.55 var(--body); }
body::before { content: ""; position: fixed; inset: 0 0 auto; height: env(safe-area-inset-top, 0px); z-index: 10; background: var(--paper); }
.wrap { max-width: 720px; margin: 0 auto; padding-block: 20px 64px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
h1, h2, h3 { font-family: var(--display); text-wrap: balance; margin: 0; line-height: 1.15; }
h1 { font-size: 1.9rem; font-weight: 700; letter-spacing: -0.01em; }
h2 { font-size: 1.35rem; font-weight: 650; }
h3 { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.09em; color: var(--muted); font-family: var(--body); font-weight: 700; }
.lede { color: var(--muted); margin: 6px 0 0; }
nav { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 5; background: var(--paper);
  display: flex; gap: 6px; padding-block: 10px; margin-top: 16px; border-bottom: 1px solid var(--line); }
nav a { flex: 1; text-align: center; text-decoration: none; color: var(--ink); font-weight: 700; font-size: 0.92rem;
  padding: 8px 6px; border-radius: 999px; border: 1px solid var(--line); background: var(--card); }
nav a:focus-visible, button:focus-visible, input:focus-visible + span { outline: 3px solid var(--basil); outline-offset: 2px; }
section { display: grid; gap: 14px; padding-top: 26px; scroll-margin-top: calc(env(safe-area-inset-top, 0px) + 64px); }
.meals { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
.meal { display: grid; grid-template-columns: 2.2rem 1fr; gap: 12px; background: var(--card); border: 1px solid var(--line);
  border-radius: 14px; padding: 14px; }
.num { font-family: var(--display); font-size: 1.6rem; font-weight: 700; color: var(--basil); line-height: 1; }
.meal-body { min-width: 0; display: grid; gap: 4px; }
.meal a { color: var(--ink); text-decoration: none; font-weight: 700; font-size: 1.05rem; }
.sub { color: var(--muted); font-size: 0.92rem; }
.facts { display: flex; flex-wrap: wrap; gap: 6px 12px; font-family: var(--mono); font-size: 0.78rem; color: var(--muted);
  font-variant-numeric: tabular-nums; }
.share { font-size: 0.85rem; color: var(--basil); }
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
button { font: 700 0.9rem var(--body); color: var(--paper); background: var(--basil); border: 0; border-radius: 999px; padding: 9px 16px; cursor: pointer; }
button.ghost { background: transparent; color: var(--ink); border: 1px solid var(--line); }
.status { font-size: 0.85rem; color: var(--muted); }
.aisle { display: grid; gap: 4px; }
.items { list-style: none; margin: 0; padding: 0; background: var(--card); border: 1px solid var(--line); border-radius: 14px; }
.items li + li { border-top: 1px solid var(--line); }
.items label { display: grid; grid-template-columns: 1.4rem 1fr auto; gap: 10px; align-items: start; padding: 10px 12px; cursor: pointer; }
.items input { width: 1.15rem; height: 1.15rem; margin: 3px 0 0; accent-color: var(--basil); }
.iname { min-width: 0; }
.iname small { display: block; color: var(--muted); font-size: 0.8rem; }
.iname em { display: block; font-style: normal; font-size: 0.8rem; background: var(--mark); color: var(--ink);
  border-radius: 6px; padding: 2px 6px; margin-top: 4px; width: fit-content; max-width: 100%; }
.qty { font-family: var(--mono); font-size: 0.85rem; font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
.items input:checked ~ .iname, .items input:checked ~ .qty { text-decoration: line-through; color: var(--muted); }
.shared-tag { color: var(--basil); font-weight: 700; }
.pantry { background: var(--basil-soft); border-radius: 14px; padding: 12px 14px; font-size: 0.92rem; }
.recipe { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px; display: grid; gap: 12px; }
.recipe h2 { display: flex; gap: 10px; align-items: baseline; }
.recipe ul { margin: 0; padding-left: 0; list-style: none; display: grid; gap: 2px; }
.recipe ul li { display: grid; grid-template-columns: 5.5rem 1fr; gap: 10px; }
.recipe ul .amt { font-family: var(--mono); font-size: 0.85rem; text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }
.recipe ol { margin: 0; padding-left: 1.3rem; display: grid; gap: 10px; }
.recipe ol li { white-space: pre-line; }
.recipe .src { font-size: 0.85rem; }
.recipe .src a { color: var(--basil); }
@media (prefers-reduced-motion: no-preference) { html { scroll-behavior: smooth; } }
"""

JS = """
(function () {
  var KEY = 'mealplan-ticks-' + document.body.dataset.plan;
  var boxes = Array.prototype.slice.call(document.querySelectorAll('.items input'));
  var status = document.getElementById('status');
  var ticked = {};
  try { ticked = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) {}
  function update() {
    var left = boxes.filter(function (b) { return !b.checked; }).length;
    status.textContent = left === 0 ? 'Everything ticked off.' : left + ' of ' + boxes.length + ' items left';
  }
  boxes.forEach(function (b) {
    b.checked = !!ticked[b.id];
    b.addEventListener('change', function () {
      ticked[b.id] = b.checked;
      try { localStorage.setItem(KEY, JSON.stringify(ticked)); } catch (e) {}
      update();
    });
  });
  update();
  document.getElementById('clear').addEventListener('click', function () {
    boxes.forEach(function (b) { b.checked = false; }); ticked = {};
    try { localStorage.removeItem(KEY); } catch (e) {}
    update();
  });
  document.getElementById('copy').addEventListener('click', function () {
    var lines = [];
    document.querySelectorAll('.aisle').forEach(function (a) {
      var rows = Array.prototype.slice.call(a.querySelectorAll('li')).filter(function (li) { return !li.querySelector('input').checked; });
      if (!rows.length) return;
      lines.push(a.querySelector('h3').textContent.toUpperCase());
      rows.forEach(function (li) { lines.push(li.dataset.copy); });
      lines.push('');
    });
    var text = lines.join('\\n').trim();
    var done = function () { status.textContent = 'Copied. Paste it into your supermarket app.'; };
    var fallback = function () {
      var ta = document.getElementById('copytext'); ta.hidden = false; ta.value = text; ta.select();
      status.textContent = 'Copy the list from the box below.';
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, fallback);
    } else { fallback(); }
  });
})();
"""


def _dinners(nums):
    nums = [str(n) for n in nums]
    if len(nums) == 1:
        return f"dinner {nums[0]}"
    return "dinners " + ", ".join(nums[:-1]) + " & " + nums[-1]


def render_html(plan):
    e = html.escape
    people = plan["people"]
    notes = shares_with(plan["meals"])
    P = [
        f"<title>Dinners · w/c {e(plan['week_short'])}</title>",
        '<meta name="claude:viewport-fit-sides" content="cover">',
        '<link rel="preconnect" href="https://fonts.googleapis.com">',
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700'
        '&family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700&family=IBM+Plex+Mono:wght@400;500&display=swap">',
        f"<style>{CSS}</style>",
        f'<div class="wrap" id="top"><header>',
        f"<h1>Dinners for the week of {e(plan['week'])}</h1>",
        f'<p class="lede">{len(plan["meals"])} dinners for {people}. Cook them in this order: '
        f"the ones with fish, herbs and salad leaves come first so nothing goes off.</p></header>",
        '<nav aria-label="Sections"><a href="#meals">Meals</a><a href="#shop">Shopping</a><a href="#cook">Recipes</a></nav>',
        '<section id="meals" aria-labelledby="meals-h"><h3 id="meals-h">This week</h3><ol class="meals">',
    ]
    for i, r in enumerate(plan["meals"], 1):
        facts = [f"{r.get('total_min') or '?'} min"]
        if r.get("nutrition", {}).get("kcal"):
            facts.append(f"{r['nutrition']['kcal']} kcal")
        if r.get("rating"):
            facts.append(f"★ {r['rating']:.1f} · {r.get('rating_count', 0):,} ratings")
        facts.append(SOURCE_NAMES.get(r["source"], r["source"]))
        share = ""
        by_key = defaultdict(list)
        for j, common in notes[r["id"]]:
            for k in common:
                by_key[k].append(j)
        if by_key:
            share = "Shares " + ", ".join(
                f"{k} ({_dinners(js)})" for k, js in sorted(by_key.items(), key=lambda x: -len(x[1]))[:3])
        P.append(
            f'<li class="meal"><span class="num" aria-hidden="true">{i}</span><div class="meal-body">'
            f'<a href="#r{i}">{e(r["title"])}</a>'
            + (f'<span class="sub">{e(r["subtitle"])}</span>' if r.get("subtitle") else "")
            + f'<span class="facts">{"".join(f"<span>{e(f)}</span>" for f in facts)}</span>'
            + (f'<span class="share">{e(share)}</span>' if share else "")
            + "</div></li>")
    P.append("</ol></section>")

    total = sum(len(items) for _, items in plan["shopping"])
    P += ['<section id="shop" aria-labelledby="shop-h"><h3 id="shop-h">Shopping list</h3>',
          '<div class="toolbar"><button id="copy" type="button">Copy list</button>'
          '<button id="clear" class="ghost" type="button">Untick all</button>'
          f'<span class="status" id="status" aria-live="polite">{total} items</span></div>',
          '<textarea id="copytext" hidden rows="8" style="width:100%;font:0.85rem var(--mono);background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:8px"></textarea>']
    n = 0
    for aisle, items in plan["shopping"]:
        P.append(f'<div class="aisle"><h3>{e(aisle)}</h3><ul class="items">')
        for it in items:
            n += 1
            meals_txt = _dinners(it["meals"])
            copy = f"{it['name']} – {it['qty']}"
            P.append(
                f'<li data-copy="{e(copy)}"><label for="i{n}"><input type="checkbox" id="i{n}">'
                f'<span class="iname">{e(it["name"])}<small>'
                + (f'<span class="shared-tag">In {len(it["meals"])} dinners · </span>' if it["shared"] else "")
                + f"{e(meals_txt)}</small>"
                + (f"<em>{e(it['swap'])}</em>" if it["swap"] else "")
                + f'</span><span class="qty">{e(it["qty"])}</span></label></li>')
        P.append("</ul></div>")
    P.append(f'<div class="pantry"><strong>Check the cupboard:</strong> {e(", ".join(plan["pantry"]))}</div></section>')

    P.append('<section id="cook" aria-labelledby="cook-h"><h3 id="cook-h">Recipes</h3>')
    for i, r in enumerate(plan["meals"], 1):
        P.append(f'<article class="recipe" id="r{i}"><h2><span class="num">{i}</span><span>{e(r["title"])}</span></h2>')
        if r.get("subtitle"):
            P.append(f'<span class="sub">{e(r["subtitle"])}</span>')
        P.append(f"<h3>Ingredients for {people}</h3><ul>")
        for ing in scaled_ingredients(r, people):
            P.append(f'<li><span class="amt">{e(fmt_amount(ing["amount"], ing["unit"]))}</span><span>{e(ing["name"])}</span></li>')
        P.append("</ul><h3>Method</h3><ol>")
        for s in r["steps"]:
            P.append(f"<li>{e(s)}</li>")
        P.append(f'</ol><p class="src">From {e(SOURCE_NAMES.get(r["source"], r["source"]))} · '
                 f'<a href="{e(r["url"])}" rel="noopener">original page</a> · <a href="#top">back to top</a></p></article>')
    P.append("</section></div>")
    P.append(f"<script>document.body.dataset.plan = {json.dumps(plan['week'])};{JS}</script>")
    return "\n".join(P)


# -------------------------------------------------------------------- main

def main():
    prefs, args = prefs_from_args()
    history = read_json(HISTORY_FILE, [])
    taste = read_json(TASTE_FILE, {"likes": [], "never": []})

    if args.like or args.never:
        if args.like:
            taste.setdefault("likes", []).append(args.like)
        if args.never:
            taste.setdefault("never", []).append(args.never)
        write_json(TASTE_FILE, taste)
        print("Saved.")
        return

    recipes = quality_scores(load(prefs["sources"] or None))
    if args.search:
        words = args.search.lower().split()
        for r in recipes:
            if all(w in r["title"].lower() for w in words):
                print(f"{r['id']:45s} {r['title']} ({r.get('total_min')} min, ★{r.get('rating')})")
        return

    likes = set(taste.get("likes", []))
    keep = {k.strip() for k in (args.keep or "").split(",") if k.strip()}
    rng = random.Random(prefs.get("seed"))
    for r in recipes:
        r["_score"] = r["quality"] + (1.5 if r["id"] in likes else 0) + rng.uniform(0, 1.0)
    cands = eligible(recipes, prefs, history, taste, keep)
    if len(cands) < prefs["dinners"]:
        sys.exit(f"Only {len(cands)} recipes match those settings. Try a longer --max-time or fewer --avoid words.")
    meals = cook_order(choose(cands, prefs["dinners"], keep, rng))

    today = dt.date.today()
    monday = today + dt.timedelta(days=(7 - today.weekday()) % 7)
    shopping, pantry = shopping_list(meals, prefs["people"])
    plan = {
        "week": monday.strftime("%-d %B %Y"), "week_short": monday.strftime("%-d %b"),
        "people": prefs["people"], "meals": meals, "shopping": shopping, "pantry": pantry,
    }
    out_dir = os.path.join(DATA, "plans")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, f"plan-{monday.isoformat()}")
    with open(base + ".md", "w") as f:
        f.write(render_markdown(plan))
    with open(base + ".html", "w") as f:
        f.write(render_html(plan))

    print(f"Picked from {len(cands):,} matching recipes ({len(recipes):,} in the library).\n")
    for i, r in enumerate(meals, 1):
        print(f"{i}. {r['title']} {r.get('subtitle') or ''}")
        print(f"   {r.get('total_min')} min · ★{r.get('rating')} ({r.get('rating_count')}) · {SOURCE_NAMES.get(r['source'])} · {r['id']}")
    shared = [it["name"] for _, items in shopping for it in items if it["shared"]]
    print(f"\nShopping list: {sum(len(i) for _, i in shopping)} items; used in more than one dinner: {', '.join(shared) or 'none'}")
    print(f"Saved {base}.md and .html")

    if args.save:
        history.append({"date": today.isoformat(), "ids": [r["id"] for r in meals], "titles": [r["title"] for r in meals]})
        write_json(HISTORY_FILE, history)
        print("Remembered this week so these won't come up again for a while.")


if __name__ == "__main__":
    main()
