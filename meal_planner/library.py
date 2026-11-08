"""Load the collected recipes, remove duplicates, and work out per-recipe facts
the planner needs (diet, main protein, quality score, scaled ingredients)."""

import glob
import json
import math
import os
import re
from collections import defaultdict

from ingredients import (PERISHABLE_AISLES, aisle_for, is_pantry, key_for,
                         to_base)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

MEAT_WORDS = {
    "chicken", "beef", "pork", "lamb", "turkey", "duck", "mince", "sausage", "bacon",
    "ham", "chorizo", "salmon", "cod", "haddock", "prawn", "fish", "tuna", "mackerel",
    "basa", "hake", "steak", "venison", "pancetta", "meatball", "trout", "bass",
    "mussel", "scallop", "squid", "anchovy", "anchovie", "sausagemeat", "gelatine",
    "nduja", "salami", "prosciutto", "pollock", "crab", "lobster", "kipper", "goat meat",
}
DAIRY_EGG_WORDS = {
    "milk", "cream", "cheese", "cheddar", "mozzarella", "parmesan", "feta", "halloumi",
    "butter", "egg", "yoghurt", "creme fraiche", "mascarpone", "ricotta", "paneer",
    "honey", "mayo", "mayonnaise", "brioche", "ghee", "quark", "burrata", "aioli",
}
PROTEINS = [
    "chicken", "beef", "pork", "lamb", "turkey", "duck", "salmon", "cod", "haddock",
    "prawn", "basa", "hake", "fish", "sausage", "chorizo", "bacon", "tofu", "halloumi",
    "paneer", "egg", "chickpea", "lentil", "bean", "mushroom", "feta", "mozzarella",
    "bass", "seabass", "trout", "mackerel", "pollock", "tuna", "steak", "mussel", "squid",
]
PROTEIN_FAMILY = {
    "salmon": "fish", "cod": "fish", "haddock": "fish", "basa": "fish", "hake": "fish", "fish": "fish",
    "bass": "fish", "seabass": "fish", "trout": "fish", "mackerel": "fish", "pollock": "fish", "tuna": "fish",
    "prawn": "seafood", "mussel": "seafood", "squid": "seafood", "scallop": "seafood",
    "beef": "beef", "steak": "beef", "lamb": "lamb", "chicken": "chicken", "turkey": "chicken", "duck": "duck",
    "pork": "pork", "sausage": "pork", "chorizo": "pork", "bacon": "pork", "pancetta": "pork", "ham": "pork",
    "tofu": "tofu", "halloumi": "cheese", "paneer": "cheese", "feta": "cheese", "mozzarella": "cheese",
    "egg": "egg", "chickpea": "pulses", "lentil": "pulses", "bean": "pulses", "mushroom": "mushroom",
}
DISH_TYPES = {
    "curry": ["curry", "dhal", "dal", "korma", "tikka", "masala", "rogan", "jalfrezi", "massaman", "rendang", "laksa", "balti", "madras", "biryani"],
    "pasta": ["pasta", "spaghetti", "linguine", "penne", "rigatoni", "orzo", "ravioli", "tortellini", "gnocchi",
              "mafaldine", "lasagne", "tagliatelle", "fusilli", "conchiglie", "macaroni", "mac", "carbonara", "ragu", "ragù", "pappardelle", "casarecce"],
    "noodles": ["noodle", "noodles", "ramen", "udon", "chow", "pad", "lo"],
    "burger": ["burger", "burgers", "slider"],
    "wraps": ["taco", "tacos", "burrito", "burritos", "fajita", "fajitas", "wrap", "wraps", "quesadilla", "quesadillas", "enchilada", "enchiladas", "gyros", "kebab", "pitta", "pittas", "flatbread"],
    "pizza": ["pizza", "pizzas"],
    "soup": ["soup", "chowder", "broth"],
    "stew": ["stew", "chilli", "casserole", "tagine", "goulash", "cassoulet", "hotpot"],
    "risotto": ["risotto", "paella", "pilaf", "jambalaya"],
    "pie": ["pie", "pies", "wellington", "pasty"],
    "salad": ["salad"],
    "stir-fry": ["stir", "stir-fry", "fried rice"],
}
REGIONS = {
    "east asian": ["thai", "chinese", "japanese", "korean", "vietnamese", "malaysian", "indonesian", "asian",
                   "teriyaki", "katsu", "gochujang", "bao", "hoisin", "sesame", "ramen", "bibimbap", "satay",
                   "char siu", "szechuan", "sichuan", "pad", "laksa", "yaki", "miso", "pak choi", "stir-fry", "stir fry"],
    "south asian": ["indian", "sri lankan", "goan", "pakistani", "bengali", "curry", "tikka", "masala", "korma",
                    "biryani", "dhal", "dal", "paneer", "keema", "balti", "jalfrezi", "tandoori", "madras", "bhaji"],
    "latin": ["mexican", "central american", "tex-mex", "brazilian", "peruvian", "cuban", "caribbean", "jerk",
              "taco", "burrito", "fajita", "enchilada", "quesadilla", "chipotle", "chimichurri", "tostada", "nacho"],
    "italian": ["italian", "tuscan", "pasta", "risotto", "pizza", "lasagne", "gnocchi", "pesto", "parmigiana",
                "bolognese", "carbonara", "arrabbiata", "puttanesca", "ragu", "ragù", "orzo"],
    "middle eastern": ["middle eastern", "lebanese", "moroccan", "turkish", "persian", "israeli", "north african",
                       "harissa", "za'atar", "shawarma", "tagine", "falafel", "kofta", "sumac", "dukkah", "chermoula"],
    "mediterranean": ["greek", "spanish", "mediterranean", "moussaka", "souvlaki", "gyros", "halloumi", "paella", "chorizo"],
    "british": ["british", "irish", "scottish", "pie", "mash", "roast", "sausage", "fish and chips", "shepherd", "cottage"],
    "american": ["american", "burger", "bbq", "cajun", "creole", "southern", "hot dog", "mac", "buffalo"],
    "french": ["french", "bourguignon", "provencal", "dauphinoise", "cassoulet", "gratin", "orange"],
}
# Fresh things that keep a week or more, so leftovers aren't a problem.
KEEPS_WELL = {
    "onion", "red onion", "garlic", "potato", "sweet potato", "carrot", "lemon", "lime",
    "orange", "apple", "butternut squash", "cabbage", "red cabbage", "white cabbage",
    "egg", "butter", "parsnip", "swede", "beetroot", "ginger", "shallot",
}
MAIN_TAG_BLOCKLIST = re.compile(r"dessert|breakfast|brunch|side|snack|cake|cookie|smoothie|drink|bundle|add-?on", re.I)


def _words(s):
    return set(re.findall(r"[a-z]+", s))


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def load(sources=None):
    recipes = []
    for path in sorted(glob.glob(os.path.join(DATA, "recipes", "*.jsonl"))):
        src = os.path.basename(path)[:-6]
        if sources and src not in sources:
            continue
        with open(path) as f:
            for line in f:
                try:
                    recipes.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # a half-written last line while collecting
    return [enrich(r) for r in dedupe(recipes)]


def dedupe(recipes):
    """HelloFresh re-runs popular recipes under new IDs. Keep the version with
    the most ratings for each (source, canonical recipe / title)."""
    best = {}
    for r in recipes:
        keys = [(r["source"], "t", _norm_title(r["title"] + " " + (r.get("subtitle") or "")))]
        if r.get("canonical_id"):
            keys.append((r["source"], "c", r["canonical_id"]))
        found = next((best[k] for k in keys if k in best), None)
        if found is None or (r.get("rating_count") or 0) > (found.get("rating_count") or 0):
            for k in keys:
                best[k] = r
        elif found is not None:
            for k in keys:
                best.setdefault(k, found)
    seen, out = set(), []
    for r in best.values():
        if id(r) not in seen:
            seen.add(id(r))
            out.append(r)
    return out


def enrich(r):
    """Recompute matching keys (so improvements to ingredients.py apply to old
    data) and add the facts the planner scores on."""
    keys = []
    for ing in r["ingredients"]:
        ing["key"] = key_for(ing["name"])
        ing["pantry"] = bool(ing.get("pantry")) or is_pantry(ing["key"])
        ing["aisle"] = aisle_for(ing["key"])
        if not ing["pantry"]:
            keys.append(ing["key"])
    all_words = set()
    for k in keys:
        all_words |= _words(k)
    title_words = _words((r["title"] + " " + (r.get("subtitle") or "")).lower())
    tags = " ".join(r.get("tags") or []).lower()

    meat = bool(all_words & MEAT_WORDS) or "fish sauce" in " ".join(keys)
    r["veggie"] = not meat
    r["vegan"] = (not meat and not (all_words & DAIRY_EGG_WORDS) and not any(
        w in " ".join(keys) for w in ("creme fraiche", "soured cream"))) or r["source"] == "grubby" or "vegan" in tags
    if r["vegan"]:
        r["veggie"] = True

    protein = None
    for p in PROTEINS:
        if p in title_words or any(p in _words(k) for k in keys if aisle_for(k) in ("Meat & fish", "Dairy, eggs & chilled")):
            protein = p
            break
    r["protein"] = PROTEIN_FAMILY.get(protein, protein) if protein else "veg"
    r["meats"] = sorted({PROTEIN_FAMILY.get(w) for k in keys if aisle_for(k) == "Meat & fish"
                         for w in _words(k) if PROTEIN_FAMILY.get(w)} | {"bacon" for k in keys if "bacon" in k or "pancetta" in k})
    blob = " " + " ".join([r["title"], r.get("subtitle") or ""] + list(r.get("cuisines") or [])).lower() + " "
    r["region"] = next((reg for reg, ws in REGIONS.items() if any(f" {w}" in blob for w in ws)), None)
    tw = _words(r["title"].lower())
    r["dish"] = next((d for d, ws in DISH_TYPES.items() if tw & set(ws)), None)
    r["flavour_keys"] = sorted({
        k for k in keys if aisle_for(k) in ("Tins, jars & sauces", "Herbs, spices & seasoning")
        and not any(w in k for w in ("stock", "vinegar", "soy sauce", "tomato paste", "tomato puree", "honey"))
    } | ({"coconut"} if any("coconut" in k for k in keys) else set()))
    r["fresh_keys"] = sorted({
        k for k in keys if aisle_for(k) in PERISHABLE_AISLES and k not in KEEPS_WELL
    })
    r["main_ok"] = bool(r.get("is_main", True)) and bool(r.get("steps")) and not MAIN_TAG_BLOCKLIST.search(
        tags + " " + (r.get("title") or "")) and len(keys) >= 4
    return r


def quality_scores(recipes):
    """Rank each recipe against others from the same site (sites rate very
    differently: Gousto averages 4.6 stars, HelloFresh nearer 3.8), so the
    best of every site competes evenly. Popularity counts a little too."""
    by_src = defaultdict(list)
    for r in recipes:
        if r.get("rating") and r.get("rating_count"):
            by_src[r["source"]].append(r)
    for src, rs in by_src.items():
        n = sum(r["rating_count"] for r in rs)
        mean = sum(r["rating"] * r["rating_count"] for r in rs) / n
        for r in rs:
            r["_bayes"] = (r["rating"] * r["rating_count"] + mean * 30) / (r["rating_count"] + 30)
        for field, weight in (("_bayes", 2.4), ("rating_count", 0.8)):
            ordered = sorted(rs, key=lambda r: r[field])
            for i, r in enumerate(ordered):
                pct = i / max(1, len(ordered) - 1)
                r["quality"] = r.get("quality", 0.0) + weight * (pct - 0.5)
    for r in recipes:
        r.setdefault("quality", 0.0)  # unrated: middle of the pack
    return recipes


def scaled_ingredients(r, people):
    """Ingredients for `people`, using the site's own quantities when it has
    them and scaling from the nearest size when it doesn't."""
    out = []
    for ing in r["ingredients"]:
        amounts = ing.get("amounts") or {}
        amt, unit = None, None
        if str(people) in amounts:
            amt, unit = amounts[str(people)]
        elif amounts:
            sizes = sorted((int(k) for k in amounts if str(k).isdigit()), key=lambda n: (abs(n - people), -n))
            if sizes:
                base = sizes[0]
                a, unit = amounts[str(base)]
                if a is not None:
                    amt = a * people / base
                    if unit not in ("g", "ml", "kg", "l", "tsp", "tbsp"):
                        amt = math.ceil(amt * 2) / 2  # whole or half items
        amt, unit = to_base(amt, unit)
        out.append({**ing, "amount": amt, "unit": unit})
    return out
