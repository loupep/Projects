"""Ingredient parsing, matching and shop-aisle grouping.

Every source writes ingredients differently ("450 grams Potatoes",
"Garlic clove x2", "120g basmati rice"). This module turns them into
(amount, unit, name) and a short `key` used to spot the same ingredient
across recipes, so the planner can favour weeks that share ingredients.
"""

import re
import unicodedata
from fractions import Fraction

UNIT_ALIASES = {
    "g": "g", "gram": "g", "grams": "g", "gr": "g",
    "kg": "kg", "kilogram": "kg", "kilograms": "kg",
    "ml": "ml", "milliliter(s)": "ml", "millilitre(s)": "ml", "millilitre": "ml", "millilitres": "ml", "milliliter": "ml",
    "l": "l", "litre": "l", "litres": "l", "liter": "l",
    "tsp": "tsp", "teaspoon": "tsp", "teaspoons": "tsp",
    "tbsp": "tbsp", "tablespoon": "tbsp", "tablespoons": "tbsp",
    "unit(s)": "unit", "unit": "unit", "units": "unit",
    "sachet(s)": "sachet", "sachet": "sachet", "sachets": "sachet",
    "bunch(es)": "bunch", "bunch": "bunch", "bunches": "bunch",
    "clove(s)": "clove", "clove": "clove", "cloves": "clove",
    "pot(s)": "pot", "pot": "pot", "pots": "pot",
    "pack(s)": "pack", "pack": "pack", "packs": "pack", "packet": "pack",
    "tin(s)": "tin", "tin": "tin", "tins": "tin", "can": "tin", "cans": "tin",
    "punnet": "punnet", "punnets": "punnet",
    "handful": "handful", "handfuls": "handful",
    "pinch": "pinch", "pinches": "pinch",
    "slice(s)": "slice", "slice": "slice", "slices": "slice",
    "piece(s)": "unit", "piece": "unit", "pieces": "unit",
    "pod": "pod", "pods": "pod",
    "sprig": "sprig", "sprigs": "sprig",
    "cup": "cup", "cups": "cup",
    "cm": "cm",
    "pouch": "pouch", "pouches": "pouch",
    "jar": "jar", "jars": "jar",
    "bag": "bag", "bags": "bag",
    "block": "block", "blocks": "block",
}

# Words that describe an ingredient but don't change what you buy.
DESCRIPTORS = {
    "british", "free-range", "free", "range", "fresh", "organic", "large", "small",
    "medium", "chopped", "sliced", "diced", "finely", "roughly", "grated", "ripe",
    "unwaxed", "boneless", "skinless", "skin-on", "trimmed", "peeled", "whole",
    "frozen", "of", "a", "an", "the", "x", "approx", "approximately",
    "juice", "zest", "plus", "extra", "to", "serve", "for", "garnish", "optional",
    "thinly", "thickly", "halved", "quartered", "minced", "crushed", "and", "or",
    "outdoor", "bred", "reared", "responsibly", "sourced", "sustainably", "caught",
    "style", "inspired", "solid",
}

PANTRY_KEYS = {
    "salt", "pepper", "black pepper", "olive oil", "vegetable oil", "oil",
    "sunflower oil", "water", "sugar", "caster sugar", "plain flour", "flour",
    "salt pepper", "cooking oil", "rapeseed oil",
}

AISLES = [
    ("Fruit & veg", [
        "onion", "garlic", "potato", "tomato", "pepper", "carrot", "courgette",
        "aubergine", "spinach", "lettuce", "cucumber", "lemon", "lime", "orange",
        "apple", "pear", "mushroom", "broccoli", "cauliflower", "cabbage", "kale",
        "leek", "celery", "chilli", "ginger", "spring onion", "shallot", "avocado",
        "mango", "pineapple", "banana", "berry", "sweetcorn", "corn", "pea", "bean",
        "asparagus", "squash", "pumpkin", "beetroot", "radish", "fennel", "rocket",
        "salad", "leaf", "leaves", "herb", "coriander", "parsley", "basil", "mint",
        "chive", "thyme", "rosemary", "dill", "sage", "tarragon", "lemongrass",
        "pak choi", "bok choy", "chard", "parsnip", "swede", "turnip", "sweet potato",
        "edamame", "mangetout", "sugar snap", "okra", "plantain", "grape", "cherry",
    ]),
    ("Meat & fish", [
        "chicken", "beef", "pork", "lamb", "turkey", "duck", "mince", "sausage",
        "bacon", "ham", "chorizo", "salmon", "cod", "haddock", "prawn", "fish",
        "tuna", "mackerel", "basa", "hake", "steak", "venison", "pancetta",
        "meatball", "trout", "sea bass", "seabass", "mussel", "scallop", "squid",
    ]),
    ("Dairy, eggs & chilled", [
        "milk", "cream", "creme fraiche", "crème fraîche", "yoghurt", "yogurt",
        "cheese", "cheddar", "mozzarella", "parmesan", "feta", "halloumi",
        "mascarpone", "ricotta", "butter", "egg", "paneer", "tofu", "hummus",
        "houmous", "pesto", "fresh pasta", "gnocchi", "ravioli", "tortellini",
        "quark", "goat", "burrata", "soured cream", "sour cream",
    ]),
    ("Bread & bakery", [
        "bread", "wrap", "tortilla", "naan", "pitta", "pita", "bun", "brioche",
        "ciabatta", "focaccia", "flatbread", "baguette", "roll", "crumpet",
    ]),
    ("Rice, pasta & grains", [
        "rice", "pasta", "spaghetti", "penne", "linguine", "fusilli", "rigatoni",
        "orzo", "noodle", "couscous", "bulgur", "bulgar", "quinoa", "lentil",
        "barley", "farro", "freekeh", "tagliatelle", "macaroni", "conchiglie",
        "mafaldine", "oats", "panko", "breadcrumb", "polenta",
    ]),
    ("Tins, jars & sauces", [
        "chopped tomatoes", "passata", "tomato puree", "tomato paste", "coconut milk",
        "stock", "sauce", "paste", "mayo", "mayonnaise", "ketchup", "mustard",
        "vinegar", "soy", "honey", "jam", "chutney", "chickpea", "kidney bean",
        "black bean", "cannellini", "butter bean", "tinned", "stock cube",
        "stock pot", "gravy", "miso", "tahini", "harissa", "sriracha", "hoisin",
        "teriyaki", "sweet chilli", "worcestershire", "oyster", "fish sauce",
        "peanut butter", "olives", "capers", "gherkin", "jalapeño", "jalapeno",
        "sun-dried", "sundried", "chicken stock", "beef stock", "vegetable stock",
        "veg stock", "stock paste", "stock mix", "chopped tomatoes", "tinned tomatoes",
        "coconut cream", "coconut milk", "curry paste", "tomato ketchup", "sweet chilli sauce",
        "ponzu", "mirin", "rice vinegar", "wine vinegar", "balsamic", "maple syrup",
        "golden syrup", "black beans", "baked beans", "pesto", "aioli", "salsa", "relish",
        "rice wine", "cooking wine", "stock jelly", "garlic paste", "ginger paste",
        "lemongrass paste", "chilli paste", "creamed coconut", "garlic puree", "ginger puree",
    ]),
    ("Herbs, spices & seasoning", [
        "spice", "seasoning", "paprika", "cumin", "turmeric", "garam masala",
        "curry powder", "chilli flakes", "oregano", "cinnamon", "coriander seed",
        "fennel seed", "mustard seed", "nutmeg", "bay leaf", "bay leaves",
        "five spice", "cajun", "za'atar", "zaatar", "sumac", "dukkah", "ras el hanout",
        "chipotle", "jerk", "herbes", "italian herbs", "mixed herbs", "black pepper",
        "chilli powder", "chilli flake", "dried", "ground", "garlic powder", "onion powder",
        "cardamom", "clove", "star anise", "allspice", "fajita", "shawarma", "tikka",
        "spice mix", "spice blend", "rub", "chilli flakes", "peppercorn",
    ]),
    ("Nuts & seeds", [
        "almond", "cashew", "peanut", "walnut", "pecan", "pistachio", "pine nut",
        "sesame", "pumpkin seed", "sunflower seed", "seed", "nut",
    ]),
    ("Frozen", ["frozen"]),
]


def _num(s):
    s = s.strip().replace("½", " 1/2").replace("¼", " 1/4").replace("¾", " 3/4").strip()
    total = 0.0
    for part in s.split():
        try:
            total += float(Fraction(part))
        except (ValueError, ZeroDivisionError):
            return None
    return total


_LEAD = re.compile(r"^\s*((?:\d+\s+)?\d+(?:[./]\d+)?|[½¼¾]|\d+\s*[½¼¾])\s*([a-zA-Z()]+)?\.?\s+(.*)$")
_GLUED = re.compile(r"^\s*(\d+(?:\.\d+)?)(g|kg|ml|l)\b\s*(.*)$", re.I)
_PACK = re.compile(r"\((\d+(?:\.\d+)?)\s*(g|kg|ml|l|tsp|tbsp)\)", re.I)
_TIMES = re.compile(r"\bx\s*(\d+)\s*$", re.I)


def parse_line(text):
    """'450 grams Potatoes' -> (450.0, 'g', 'Potatoes'). Amount may be None."""
    t = re.sub(r"\s+", " ", (text or "").replace(" ", " ")).strip()
    if not t:
        return None, None, ""
    m = _GLUED.match(t)
    if m:
        return float(m.group(1)), UNIT_ALIASES[m.group(2).lower()], m.group(3).strip()
    m = _LEAD.match(t)
    if m:
        amt = _num(m.group(1))
        unit_raw = (m.group(2) or "").lower()
        rest = m.group(3).strip()
        if unit_raw in UNIT_ALIASES:
            return amt, UNIT_ALIASES[unit_raw], rest
        return amt, "unit", ((m.group(2) or "") + " " + rest).strip()
    return None, None, t


def parse_gousto_label(label):
    """'Ravioli (250g)' -> ('Ravioli', 250, 'g'); 'Garlic clove x2' -> ('Garlic clove', 2, 'x')."""
    label = (label or "").strip()
    m = _PACK.search(label)
    if m:
        name = _TIMES.sub("", _PACK.sub("", label)).strip()
        return name, float(m.group(1)), UNIT_ALIASES[m.group(2).lower()]
    m = _TIMES.search(label)
    if m:
        return _TIMES.sub("", label).strip(), float(m.group(1)), "x"
    return label, None, None


IRREGULAR = {"leaves": "leaf", "loaves": "loaf", "halves": "half", "knives": "knife"}


def _singular(w):
    if w in IRREGULAR:
        return IRREGULAR[w]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("oes"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


KEEP_PHRASES = {
    "chopped tomatoes": "chopped tomato", "chopped tomato": "chopped tomato",
    "sun-dried tomato": "sundried tomato", "sundried tomato": "sundried tomato",
}


SYNONYMS = {
    "garlic clove": "garlic", "brown onion": "onion", "white onion": "onion",
    "yellow onion": "onion", "white potato": "potato", "baking potato": "potato",
    "baby spinach": "spinach", "baby spinach leaf": "spinach", "spinach leaf": "spinach",
    "baby leaf mix": "salad leaf", "baby leaf salad": "salad leaf", "mixed salad leaf": "salad leaf",
    "salad leaf mix": "salad leaf", "bell pepper": "pepper", "red bell pepper": "red pepper",
    "spring onion": "spring onion", "scallion": "spring onion", "coriander leaf": "coriander",
    "flat leaf parsley": "parsley", "flat-leaf parsley": "parsley", "leaf parsley": "parsley",
    "greek style yoghurt": "greek yoghurt", "greek-style yoghurt": "greek yoghurt",
    "natural yoghurt": "yoghurt", "plain yoghurt": "yoghurt", "yogurt": "yoghurt",
    "double cream": "double cream", "cooking cream": "cream", "single cream": "single cream",
    "chestnut mushroom": "mushroom", "closed cup mushroom": "mushroom", "button mushroom": "mushroom",
    "basmati": "basmati rice", "long grain rice": "rice", "cherry tomatoe": "cherry tomato",
    "medium tomato": "tomato", "vine tomato": "tomato", "plum tomato": "tomato",
    "chicken breast fillet": "chicken breast", "chicken thigh fillet": "chicken thigh",
    "diced chicken thigh": "chicken thigh", "chicken breast strip": "chicken breast",
    "beef mince": "beef mince", "minced beef": "beef mince", "pork mince": "pork mince",
    "minced pork": "pork mince", "lamb mince": "lamb mince", "minced lamb": "lamb mince",
    "mature cheddar": "cheddar", "cheddar cheese": "cheddar", "grated cheddar": "cheddar",
    "mozzarella ball": "mozzarella", "halloumi cheese": "halloumi", "feta cheese": "feta",
    "lime juice": "lime", "lemon juice": "lemon",
}


def _strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def key_for(name):
    """Short matching key: 'British Chicken Thighs' -> 'chicken thigh'."""
    k = _raw_key(name)
    k = re.sub(r"\bcurry paste$", "paste", k)
    return SYNONYMS.get(k, k)


def _raw_key(name):
    n = _strip_accents((name or "").lower())
    for phrase, key in KEEP_PHRASES.items():
        if phrase in n:
            return key
    n = re.sub(r"\(.*?\)", " ", n)
    n = n.replace("&", " ").replace(",", " ")
    n = re.sub(r"[^a-zàâçéèêëîïôûùüÿñæœ' -]", " ", n)
    words = [w for w in n.split() if w not in DESCRIPTORS]
    if not words:
        words = n.split()
    words = [_singular(w) for w in words]
    return " ".join(words[-3:]).strip()


def _norm_phrase(p):
    return " ".join(_singular(w) for w in p.lower().split())


_AISLE_INDEX = sorted(
    ((_norm_phrase(w), aisle) for aisle, words in AISLES for w in words),
    key=lambda x: -len(x[0]),
)


def aisle_for(key):
    """Pick the aisle whose keyword is the longest match ('coconut milk' beats 'milk')."""
    k = f" {key} "
    if key.split()[:1] in (["dried"], ["ground"]):
        return "Herbs, spices & seasoning"
    for phrase, aisle in _AISLE_INDEX:
        if f" {phrase} " in k or f" {phrase}" in k:
            return aisle
    return "Other"


def is_pantry(key):
    return key in PANTRY_KEYS or key.endswith(" oil") and key.split()[0] in {"olive", "vegetable", "sunflower", "rapeseed"}


PERISHABLE_AISLES = {"Fruit & veg", "Meat & fish", "Dairy, eggs & chilled", "Bread & bakery"}


def norm_unit(u):
    u = (u or "").lower().strip()
    return UNIT_ALIASES.get(u, u or "unit")


def to_base(amount, unit):
    """Convert to a base unit so amounts can be added up."""
    if amount is None:
        return None, unit
    if unit == "kg":
        return amount * 1000, "g"
    if unit == "l":
        return amount * 1000, "ml"
    return amount, unit


_FRACTIONS = {0.25: "¼", 0.5: "½", 0.75: "¾", 0.33: "⅓", 0.67: "⅔"}


def _nice_number(a):
    whole = int(a)
    frac = round(a - whole, 2)
    if frac == 0:
        return str(whole)
    if frac in _FRACTIONS:
        return (str(whole) if whole else "") + _FRACTIONS[frac]
    return f"{a:.1f}".rstrip("0").rstrip(".")


def fmt_amount(amount, unit):
    if amount is None:
        return ""
    if unit == "g" and amount >= 1000:
        amount, unit = amount / 1000, "kg"
    if unit == "ml" and amount >= 1000:
        amount, unit = amount / 1000, "l"
    if unit in ("g", "ml"):
        return f"{round(amount):d}{unit}"
    a = _nice_number(amount)
    if unit in (None, "", "unit", "x"):
        return a
    if unit in ("kg", "l"):
        return f"{a}{unit}"
    if amount > 1 and not unit.endswith("s"):
        unit += "es" if unit in ("bunch", "punnet", "pouch", "pinch") else "s"
    return f"{a} {unit}"
