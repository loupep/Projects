"""Meal kits use their own sachets and blends. This maps them to things you
can buy in any UK supermarket (or mix from jars you already have).

Checked against the most common kit-only items in the collected recipes.
First matching pattern wins, so put specific names above general ones.
"""

import re

SWAPS = [
    # Stocks
    (r"red wine (jus|stock)|red wine paste", "Beef stock pot or cube + a splash of red wine (or gravy granules)"),
    (r"white wine stock", "Veg stock cube + a splash of white wine"),
    (r"chicken (stock|broth) (paste|mix|powder)|intense chicken stock", "Chicken stock pot or cube (half for 2 people)"),
    (r"beef stock (paste|mix|powder)", "Beef stock pot or cube (half for 2 people)"),
    (r"(vegetable|veg) (stock|broth) (paste|mix|powder)", "Vegetable stock pot or cube (half for 2 people)"),
    (r"mushroom (stock|broth) paste|wild mushroom paste", "Mushroom stock pot, or a veg stock cube + a dash of soy sauce"),
    # Spice blends
    (r"central american style spice", "1 tsp ground cumin + 1 tsp smoked paprika + pinch of chilli powder + pinch of oregano"),
    (r"mexican (style )?spice|fajita", "Fajita seasoning sachet or jar"),
    (r"brazilian style spice", "1 tsp smoked paprika + 1 tsp cumin + ½ tsp garlic granules + pinch of oregano"),
    (r"ancho spice", "1 tsp smoked paprika + ½ tsp cumin + ½ tsp chipotle paste"),
    (r"mediterranean style seasoning|greek style seasoning", "1 tsp dried oregano + ½ tsp garlic granules + ½ tsp paprika"),
    (r"garlic (&|and) herb seasoning", "½ tsp garlic granules + 1 tsp dried mixed herbs"),
    (r"lemon (&|and) herb seasoning", "Lemon & herb seasoning jar, or lemon zest + 1 tsp dried oregano + garlic granules"),
    (r"chermoula", "1 tsp ground cumin + 1 tsp ground coriander + 1 tsp paprika + pinch of chilli (or ras el hanout)"),
    (r"middle eastern style spice|shawarma", "Shawarma seasoning, or 1 tsp cumin + 1 tsp paprika + ½ tsp cinnamon"),
    (r"moroccan|ras el hanout", "Ras el hanout jar"),
    (r"west african style", "1 tsp paprika + ½ tsp ground ginger + ½ tsp garlic granules + pinch of cayenne (or suya spice)"),
    (r"indonesian style spice", "1 tsp mild curry powder + ½ tsp ground ginger + ½ tsp ground coriander"),
    (r"thai style spice", "1 tsp lemongrass paste + ½ tsp ground coriander + pinch of chilli flakes"),
    (r"north indian style spice", "Garam masala"),
    (r"tikka spice|tikka seasoning", "Tikka masala spice mix or paste"),
    (r"curry powder mix", "Mild curry powder"),
    (r"cajun", "Cajun seasoning jar"),
    (r"peri peri|piri piri", "Peri peri seasoning jar"),
    (r"jerk", "Jerk seasoning or paste"),
    (r"italian (style )?herb|mixed herbs|herbes de provence", "Dried mixed herbs"),
    (r"bold spice blend", "1 tsp smoked paprika + ½ tsp garlic granules + ½ tsp onion granules + pinch of chilli"),
    (r"steak seasoning|bbq seasoning|barbecue seasoning", "BBQ or steak seasoning jar, or smoked paprika + garlic granules + brown sugar"),
    # Pastes and odds
    (r"ginger (&|and) garlic paste", "Jar of ginger & garlic paste, or 1 garlic clove + 1cm ginger, grated"),
    (r"roasted garlic paste", "Garlic purée (tube)"),
    (r"black garlic", "Black garlic (big supermarkets), or a little garlic purée + a drop of balsamic"),
    (r"caramelised onion paste", "Caramelised onion chutney"),
    (r"truffle zest", "A few drops of truffle oil (optional)"),
    (r"vegan fish sauce", "Soy sauce + a squeeze of lime"),
    (r"plant-based 'nduja|vegan 'nduja", "Vegan 'nduja, or chipotle paste + smoked paprika"),
    (r"creamed coconut", "Block of creamed coconut, or a tin of coconut milk"),
    (r"crispy onions", "Tub of crispy fried onions"),
    (r"^tamari$", "Soy sauce (tamari if you need gluten-free)"),
]

_COMPILED = [(re.compile(p, re.I), s) for p, s in SWAPS]


def swap_for(name):
    n = (name or "").strip()
    for rx, s in _COMPILED:
        if rx.search(n):
            return s
    return None
