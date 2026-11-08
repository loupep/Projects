"""One adapter per recipe site.

Each adapter lists recipe URLs from the site's public sitemap and turns one
recipe into the shared format below. Sites are only read at a low rate and
only paths their robots.txt allows.

Shared recipe format:
{
  "id", "source", "url", "title", "subtitle", "description", "image",
  "total_min", "prep_min", "difficulty", "servings", "rating", "rating_count",
  "cuisines": [], "tags": [], "allergens": [],
  "nutrition": {"kcal", "protein_g", "carbs_g", "fat_g"},   # per serving
  "ingredients": [{"name", "key", "pantry", "amounts": {"2": [amount, unit], ...}}],
  "steps": [str],
  "is_main", "canonical_id", "published"
}
"""

import gzip
import html as htmllib
import json
import re
import time
import urllib.error
import urllib.request

from ingredients import is_pantry, key_for, norm_unit, parse_gousto_label, parse_line

USER_AGENT = "personal-recipe-archive/1.0 (single-user meal planner; low request rate)"


def fetch(url, timeout=40, tries=3):
    last = None
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip",
            "Accept": "text/html,application/json,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return body.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410, 403):
                raise
            last = e
        except Exception as e:  # network hiccup
            last = e
        time.sleep(2 * (attempt + 1))
    raise last


def sitemap_locs(xml):
    """[(url, lastmod)] from a sitemap."""
    out = []
    for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
        loc = re.search(r"<loc>\s*(.*?)\s*</loc>", block, re.S)
        mod = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", block, re.S)
        if loc:
            out.append((htmllib.unescape(loc.group(1)), mod.group(1) if mod else ""))
    return out


def strip_html(s):
    s = re.sub(r"<\s*br\s*/?>|</p>|</li>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmllib.unescape(s)
    s = re.sub(r"\*\*|__", "", s)
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def iso_minutes(v):
    if not v:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = re.match(r"P(?:T)?(?:(\d+)H)?(?:(\d+)M)?", str(v))
    if not m or not (m.group(1) or m.group(2)):
        return None
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def _first_number(v):
    m = re.search(r"\d+(?:\.\d+)?", str(v or ""))
    return float(m.group()) if m else None


def _ingredient(name, amounts, pantry=None):
    key = key_for(name)
    return {
        "name": name.strip(),
        "key": key,
        "pantry": bool(pantry) if pantry is not None else is_pantry(key),
        "amounts": amounts,
    }


# --------------------------------------------------------------- HelloFresh

class HelloFreshPlatform:
    """HelloFresh UK and Green Chef UK run on the same platform."""

    def __init__(self, name, host, sitemap, prefix):
        self.name, self.host, self.sitemap, self.prefix = name, host, sitemap, prefix
        self.workers = 3
        self.min_interval = 0.5

    def list_urls(self):
        locs = sitemap_locs(fetch(self.sitemap))
        locs = [(u, m) for u, m in locs if re.search(self.prefix + r"[^/]+-[0-9a-f]{24}$", u)]
        locs.sort(key=lambda x: x[1], reverse=True)  # newest first
        return [u for u, _ in locs]

    @staticmethod
    def _find_recipe(o, depth=0):
        if depth > 12:
            return None
        if isinstance(o, dict):
            if "yields" in o and "ingredients" in o and "steps" in o:
                return o
            for v in o.values():
                r = HelloFreshPlatform._find_recipe(v, depth + 1)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = HelloFreshPlatform._find_recipe(v, depth + 1)
                if r:
                    return r
        return None

    def parse(self, page, url):
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
        if not m:
            return None
        r = self._find_recipe(json.loads(m.group(1)))
        if not r:
            return None

        names = {i["id"]: i for i in r.get("ingredients") or []}
        amounts = {}
        for y in r.get("yields") or []:
            n = str(y.get("yields"))
            for ing in y.get("ingredients") or []:
                amounts.setdefault(ing["id"], {})[n] = [ing.get("amount"), ing.get("unit")]
        ingredients = []
        for iid, info in names.items():
            ing = _ingredient(info.get("name", ""), {
                k: [a, norm_unit(u)] for k, (a, u) in amounts.get(iid, {}).items()
            }, pantry=not info.get("shipped", True))
            if info.get("type"):
                ing["type"] = info["type"]
            ingredients.append(ing)

        nut = {}
        for n in r.get("nutrition") or []:
            nm = (n.get("name") or "").lower()
            if "kcal" in nm:
                nut["kcal"] = n.get("amount")
            elif nm == "protein":
                nut["protein_g"] = n.get("amount")
            elif nm == "carbohydrate":
                nut["carbs_g"] = n.get("amount")
            elif nm == "fat":
                nut["fat_g"] = n.get("amount")

        steps = []
        for s in sorted(r.get("steps") or [], key=lambda s: s.get("index", 0)):
            txt = strip_html(s.get("instructionsMarkdown") or s.get("instructions") or s.get("instructionsHTML"))
            if txt:
                steps.append(txt)

        rating = r.get("aggregateRating") if isinstance(r.get("aggregateRating"), (int, float)) else r.get("averageRating")
        count = r.get("aggregateRatingsCount") if isinstance(r.get("aggregateRatingsCount"), int) else r.get("ratingsCount")
        img = r.get("imagePath")
        servings = sorted(int(k) for k in {k for a in amounts.values() for k in a} if str(k).isdigit())
        return {
            "id": f"{self.name}:{r.get('id')}",
            "source": self.name,
            "url": url,
            "title": (r.get("name") or "").strip(),
            "subtitle": (r.get("headline") or "").strip(),
            "description": strip_html(r.get("descriptionMarkdown") or r.get("description")),
            "image": f"https://img.hellofresh.com/f_auto,fl_lossy,q_auto,w_640/hellofresh_s3{img}" if img else None,
            "total_min": iso_minutes(r.get("totalTime")),
            "prep_min": iso_minutes(r.get("prepTime")),
            "difficulty": r.get("difficulty"),
            "servings": 2 if 2 in servings else (servings[0] if servings else 2),
            "rating": round(rating, 2) if isinstance(rating, (int, float)) else None,
            "rating_count": count or 0,
            "cuisines": [c.get("name") for c in r.get("cuisines") or [] if c.get("name")],
            "tags": [t.get("name") for t in r.get("tags") or [] if t.get("name") and t.get("name") not in ("SEO", "fruit-boxes")],
            "allergens": [a.get("name") for a in r.get("allergens") or [] if isinstance(a, dict) and a.get("name")],
            "nutrition": nut,
            "ingredients": ingredients,
            "steps": steps,
            "is_main": not r.get("isAddon", False) and bool(steps) and "fruit-boxes" not in [t.get("name") for t in r.get("tags") or []],
            "canonical_id": r.get("canonical") or r.get("clonedFrom") or None,
            "published": (r.get("createdAt") or "")[:10],
            "active": r.get("active"),
        }


# ------------------------------------------------------------------- Gousto

class Gousto:
    name = "gousto"
    host = "www.gousto.co.uk"
    workers = 3
    min_interval = 0.5
    api = "https://production-api.gousto.co.uk/cmsreadbroker/v1"

    def list_urls(self):
        locs = sitemap_locs(fetch(f"{self.api}/sitemap.xml"))
        slugs, out = set(), []
        for u, _ in locs:
            m = re.match(r"https://www\.gousto\.co\.uk/cookbook/[^/]+/([^/?#]+)$", u)
            if m and m.group(1) not in slugs:
                slugs.add(m.group(1))
                out.append(f"https://www.gousto.co.uk/cookbook/recipes/{m.group(1)}")
        return out

    def get(self, url):
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        return fetch(f"{self.api}/recipe/{slug}")

    def parse(self, body, url):
        data = json.loads(body)
        d = (data.get("data") or {}).get("entry")
        if not d or not d.get("ingredients"):
            return None

        by_code = {}
        for ing in d.get("ingredients") or []:
            m = re.search(r"-(I[A-Z]?-\d.*)$", ing.get("title") or "")
            by_code[m.group(1) if m else ing.get("title")] = ing
        per_portion = {}
        for ps in d.get("portion_sizes") or []:
            if not ps.get("is_offered", True):
                continue
            for sku in ps.get("ingredients_skus") or []:
                per_portion.setdefault(sku.get("code"), {})[str(ps.get("portions"))] = (sku.get("quantities") or {}).get("in_box", 1)

        # Odd portion sizes add extra half-packs as separate entries, so
        # merge entries for the same ingredient and add their amounts up.
        merged = {}
        for code, ing in by_code.items():
            name, pack, unit = parse_gousto_label(ing.get("label") or ing.get("name"))
            item = merged.setdefault(key_for(name), {"name": name, "amounts": {}})
            if len(name) < len(item["name"]):
                item["name"] = name
            for n, in_box in per_portion.get(code, {}).items():
                amt, u = (in_box * pack, unit) if unit in ("g", "ml", "tsp", "tbsp") else (in_box, "unit")
                prev = item["amounts"].get(n)
                item["amounts"][n] = [round(prev[0] + amt, 2), u] if prev and prev[1] == u else [amt, u]
        ingredients = [_ingredient(v["name"], v["amounts"], pantry=False) for v in merged.values()]
        for b in d.get("basics") or []:
            ingredients.append(_ingredient(b.get("title", ""), {}, pantry=True))

        steps = [strip_html(s.get("instruction")) for s in sorted(d.get("cooking_instructions") or [], key=lambda s: s.get("order", 0))]
        steps = [s for s in steps if s]
        pp = (d.get("nutritional_information") or {}).get("per_portion") or {}
        imgs = ((d.get("media") or {}).get("images")) or []
        img = next((i["image"] for i in imgs if i.get("width", 0) >= 700), imgs[-1]["image"] if imgs else None)
        prep = d.get("prep_times") or {}
        rating = d.get("rating") or {}
        portions = sorted(int(k) for v in per_portion.values() for k in v)
        cuisine = (d.get("cuisine") or {}).get("title")
        return {
            "id": f"gousto:{d.get('gousto_id') or d.get('uid')}",
            "source": "gousto",
            "url": url,
            "title": (d.get("title") or "").strip(),
            "subtitle": "",
            "description": strip_html(d.get("description")),
            "image": img,
            "total_min": prep.get("for_2") or prep.get("for_4"),
            "prep_min": None,
            "difficulty": None,
            "servings": 2 if 2 in portions else (portions[0] if portions else 2),
            "rating": rating.get("average"),
            "rating_count": rating.get("count") or 0,
            "cuisines": [cuisine] if cuisine else [],
            "tags": [c.get("title") for c in d.get("categories") or [] if c.get("title") and c.get("title") != "All Gousto Recipes"],
            "allergens": [a.get("title") for a in d.get("allergens") or [] if a.get("title")],
            "nutrition": {
                "kcal": pp.get("energy_kcal"),
                "protein_g": round(pp["protein_mg"] / 1000, 1) if pp.get("protein_mg") else None,
                "carbs_g": round(pp["carbs_mg"] / 1000, 1) if pp.get("carbs_mg") else None,
                "fat_g": round(pp["fat_mg"] / 1000, 1) if pp.get("fat_mg") else None,
            },
            "ingredients": ingredients,
            "steps": steps,
            "is_main": True,
            "canonical_id": None,
            "published": "",
        }


# ------------------------------------------------- schema.org JSON-LD sites

class JsonLdSite:
    """Sites that publish standard schema.org Recipe data (Grubby, SimplyCook)."""

    workers = 2
    min_interval = 0.6

    def __init__(self, name, host, sitemap, url_pattern, sitemap_index=False):
        self.name, self.host, self.sitemap, self.url_pattern = name, host, sitemap, url_pattern
        self.sitemap_index = sitemap_index

    def list_urls(self):
        xml = fetch(self.sitemap)
        urls = [u for u, _ in sitemap_locs(xml)]
        if not urls or self.sitemap_index:
            for sm in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml):
                if sm.endswith(".xml"):
                    urls += [u for u, _ in sitemap_locs(fetch(sm))]
        seen, out = set(), []
        for u in urls:
            u = u.replace("://grubby.co.uk", "://www.grubby.co.uk")
            if re.search(self.url_pattern, u) and u not in seen:
                seen.add(u)
                out.append(u)
        return out

    @staticmethod
    def _recipe_obj(page):
        for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
            try:
                d = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            stack = [d]
            while stack:
                o = stack.pop()
                if isinstance(o, list):
                    stack.extend(o)
                elif isinstance(o, dict):
                    t = o.get("@type")
                    if t == "Recipe" or (isinstance(t, list) and "Recipe" in t):
                        return o
                    if "@graph" in o:
                        stack.append(o["@graph"])
        return None

    def parse(self, page, url):
        r = self._recipe_obj(page)
        if not r:
            return None
        servings = int(_first_number(r.get("recipeYield")) or 2)
        ingredients = []
        for line in r.get("recipeIngredient") or []:
            amt, unit, name = parse_line(line)
            ingredients.append(_ingredient(name or line, {str(servings): [amt, unit]} if amt is not None else {}))
        steps = []
        for s in r.get("recipeInstructions") or []:
            if isinstance(s, dict) and s.get("@type") == "HowToSection":
                steps += [strip_html(x.get("text")) for x in s.get("itemListElement") or [] if isinstance(x, dict)]
            elif isinstance(s, dict):
                steps.append(strip_html(s.get("text")))
            else:
                steps.append(strip_html(str(s)))
        steps = [s for s in steps if s]
        img = r.get("image")
        if isinstance(img, list):
            img = img[0] if img else None
        if isinstance(img, dict):
            img = img.get("url")
        agg = r.get("aggregateRating") or {}
        nut = r.get("nutrition") or {}
        kcal = _first_number(nut.get("calories"))
        total = iso_minutes(r.get("totalTime")) or (
            (iso_minutes(r.get("prepTime")) or 0) + (iso_minutes(r.get("cookTime")) or 0)) or None
        kw = r.get("keywords") or ""
        tags = [k.strip() for k in (kw.split(",") if isinstance(kw, str) else kw) if k and k.strip()]
        cat = r.get("recipeCategory")
        tags += cat if isinstance(cat, list) else ([cat] if cat else [])
        cuis = r.get("recipeCuisine")
        diet = str(r.get("suitableForDiet") or "")
        if "Vegan" in diet:
            tags.append("Vegan")
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        return {
            "id": f"{self.name}:{slug}",
            "source": self.name,
            "url": url,
            "title": strip_html(r.get("name")),
            "subtitle": "",
            "description": strip_html(r.get("description")),
            "image": img,
            "total_min": total,
            "prep_min": iso_minutes(r.get("prepTime")),
            "difficulty": None,
            "servings": servings,
            "rating": _first_number(agg.get("ratingValue")),
            "rating_count": int(_first_number(agg.get("ratingCount") or agg.get("reviewCount")) or 0),
            "cuisines": cuis if isinstance(cuis, list) else ([cuis] if cuis else []),
            "tags": list(dict.fromkeys(tags)),
            "allergens": re.findall(r"Contains: (.*)", r.get("description") or "")[:1],
            "nutrition": {
                "kcal": kcal or None,
                "protein_g": _first_number(nut.get("proteinContent")) or None,
                "carbs_g": _first_number(nut.get("carbohydrateContent")) or None,
                "fat_g": _first_number(nut.get("fatContent")) or None,
            },
            "ingredients": ingredients,
            "steps": steps,
            "is_main": True,
            "canonical_id": None,
            "published": (r.get("datePublished") or "")[:10],
        }


SITES = {
    "hellofresh": HelloFreshPlatform(
        "hellofresh", "www.hellofresh.co.uk",
        "https://www.hellofresh.co.uk/sitemap_recipe_pages.xml", r"/recipes/"),
    "greenchef": HelloFreshPlatform(
        "greenchef", "www.greenchef.co.uk",
        "https://www.greenchef.co.uk/sitemap.xml", r"/recipes/"),
    "gousto": Gousto(),
    "grubby": JsonLdSite(
        "grubby", "www.grubby.co.uk",
        "https://www.grubby.co.uk/api/sitemap.xml", r"/vegan-recipes/recipe/[^/]+$"),
    "simplycook": JsonLdSite(
        "simplycook", "www.simplycook.com",
        "https://www.simplycook.com/sitemap.xml", r"/recipes/[^/]+$", sitemap_index=True),
}
