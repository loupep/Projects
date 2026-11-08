"""Collect recipes from UK meal-kit sites into data/recipes/<site>.jsonl.

    python collect.py                       # every site, everything
    python collect.py --sites gousto --limit 20
    python collect.py --status              # how many collected so far

Safe to stop and re-run: URLs already done are skipped. Each site is read
by a couple of workers with a pause between requests, and only the public
recipe pages listed in the site's sitemap are fetched.
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sources import SITES, fetch  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
RECIPES = os.path.join(DATA, "recipes")
STATE = os.path.join(DATA, "state")


class RateLimiter:
    def __init__(self, interval):
        self.interval, self.lock, self.next = interval, threading.Lock(), 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            if now < self.next:
                time.sleep(self.next - now)
            self.next = max(now, self.next) + self.interval


def done_urls(name):
    path = os.path.join(STATE, f"{name}.done")
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return {line.split("\t", 1)[0] for line in f if line.strip()}


def collect_site(name, limit=None, log=print):
    site = SITES[name]
    os.makedirs(RECIPES, exist_ok=True)
    os.makedirs(STATE, exist_ok=True)
    urls_file = os.path.join(STATE, f"{name}.urls")
    if os.path.exists(urls_file):
        with open(urls_file) as f:
            urls = [u.strip() for u in f if u.strip()]
    else:
        urls = site.list_urls()
        with open(urls_file, "w") as f:
            f.write("\n".join(urls) + "\n")
    done = done_urls(name)
    todo = [u for u in urls if u not in done]
    if limit:
        todo = todo[:limit]
    log(f"[{name}] {len(urls)} recipes listed, {len(done)} already done, {len(todo)} to fetch")
    if not todo:
        return

    limiter = RateLimiter(site.min_interval)
    write_lock = threading.Lock()
    out = open(os.path.join(RECIPES, f"{name}.jsonl"), "a")
    state = open(os.path.join(STATE, f"{name}.done"), "a")
    counts = {"ok": 0, "skip": 0, "err": 0}

    def work(url):
        limiter.wait()
        status, recipe = "ok", None
        try:
            body = site.get(url) if hasattr(site, "get") else fetch(url)
            recipe = site.parse(body, url)
            if not recipe or not recipe.get("ingredients") or not recipe.get("steps"):
                status = "no-recipe"
        except urllib.error.HTTPError as e:
            status = f"http-{e.code}"
        except Exception as e:  # keep going; record the failure
            status = f"error-{type(e).__name__}"
        with write_lock:
            if recipe and status == "ok":
                out.write(json.dumps(recipe, ensure_ascii=False) + "\n")
                counts["ok"] += 1
            elif status.startswith("error") or status in ("http-429", "http-500", "http-502", "http-503"):
                counts["err"] += 1
                return  # not marked done, so a re-run retries it
            else:
                counts["skip"] += 1
            state.write(f"{url}\t{status}\n")
            n = sum(counts.values())
            if n % 250 == 0:
                out.flush()
                state.flush()
                log(f"[{name}] {n}/{len(todo)}  saved={counts['ok']} skipped={counts['skip']} errors={counts['err']}")

    with ThreadPoolExecutor(max_workers=site.workers) as pool:
        list(pool.map(work, todo))
    out.close()
    state.close()
    log(f"[{name}] finished: saved={counts['ok']} skipped={counts['skip']} errors={counts['err']}")


def status():
    for name in SITES:
        listed = 0
        p = os.path.join(STATE, f"{name}.urls")
        if os.path.exists(p):
            listed = sum(1 for _ in open(p))
        saved = 0
        p = os.path.join(RECIPES, f"{name}.jsonl")
        if os.path.exists(p):
            saved = sum(1 for _ in open(p))
        print(f"{name:12s} listed={listed:6d} done={len(done_urls(name)):6d} saved={saved:6d}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default=",".join(SITES))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        return status()
    names = [s.strip() for s in args.sites.split(",") if s.strip()]
    threads = [threading.Thread(target=collect_site, args=(n, args.limit), name=n) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    status()


if __name__ == "__main__":
    main()
