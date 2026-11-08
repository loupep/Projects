# DIY meal kit

A personal replacement for HelloFresh, Gousto and the rest. It keeps a library of their recipes,
plans a week of dinners from it, and writes one shopping list you can take to any supermarket.

## Each week

```
python plan.py                 # pick this week's dinners (settings in prefs.json)
python plan.py --save          # happy with it? remember it so it isn't repeated soon
```

That writes `data/plans/plan-<date>.html` (phone page: meals, tick-off shopping list, recipes)
and a `.md` copy. "Copy list" on the page gives plain text to paste into Sainsbury's SmartLists
or the Tesco app.

Useful options:

| Option | What it does |
| --- | --- |
| `--people 4 --dinners 5` | household size and dinners per week |
| `--max-time 30` | only recipes that take 30 minutes or less |
| `--diet veggie` / `vegan` | meat-free weeks |
| `--avoid mushroom,olive` | never pick recipes with these |
| `--sources gousto,hellofresh` | only use some sites |
| `--search halloumi` | find a recipe and its id |
| `--keep gousto:4151` | make sure a recipe is in this week's plan |
| `--like ID` / `--never ID` | pick a recipe more often / never again |

## How it chooses

- Ratings, ranked within each site, because sites rate very differently.
- Meals that share fresh ingredients, so a bunch of coriander or a bag of spinach gets used up.
- A spread of proteins, cuisines and dish types (no two curries or two pasta bakes).
- Nothing you've cooked in the last six weeks.
- Fish and soft herbs early in the week; sturdier meals later.
- Kit-only sachets ("Central American Style Spice Mix", stock pastes) come with a supermarket swap.

## The recipe library

```
python collect.py              # collect or top up every site (safe to stop and re-run)
python collect.py --status     # counts per site
```

Sites: HelloFresh UK, Gousto, Green Chef UK, Grubby, SimplyCook. Requests are spaced out,
only public recipe pages listed in each site's sitemap are read, and robots.txt is respected.
Mindful Chef and Riverford block automated access, so they're left out.

Recipes are saved in `data/recipes/*.jsonl` (one recipe per line). `data/` is kept out of git
because this repo is public and the recipe text belongs to those companies.

## Files

- `collect.py`: fetches recipes; `sources.py`: one reader per site
- `library.py`: loads recipes, removes duplicates, scores them
- `plan.py`: picks the week and writes the plan; `prefs.json`: your defaults
- `ingredients.py`: reads amounts, matches ingredients across sites, sorts them into aisles
- `swaps.py`: supermarket swaps for meal-kit sachets
