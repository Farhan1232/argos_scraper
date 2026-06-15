#!/usr/bin/env python3
"""
Live availability checker for the scraped Argos data
====================================================
The Wayback data is real but historical, so some products have since been
delisted (their page shows "Oops, that didn't go to plan"). This script visits
every product link LIVE and removes the ones that are no longer available, then
rewrites the JSON files.

WHY THIS RUNS ON YOUR MACHINE (not in the build environment)
------------------------------------------------------------
Argos is behind Akamai Bot Manager + a UK geo-block. A datacenter / cloud / VPN
IP gets HTTP 403 on every product page, so availability cannot be checked there.
Run this from a NORMAL UK residential IP (your home connection), exactly like the
live scraper. It reuses the same stealth + Akamai cookie warm-up technique.

Usage
-----
    source venv/bin/activate
    pip install playwright && python -m playwright install chromium   # if not already
    python check_availability.py                 # check every category
    python check_availability.py --only tv       # just one file
    python check_availability.py --headful       # watch the browser

What it does
------------
  * keeps a product if its page loads with a real product title
  * removes it if the page shows "Oops, that didn't go to plan", a 404/410,
    or redirects away from /product/<id>
  * rewrites output/<category>.json and output/argos_all.json in place
  * prints a summary: kept vs removed per category
"""
import argparse, json, pathlib, sys, time, random
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

OUT = pathlib.Path(__file__).parent / "output"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

STEALTH = """
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
window.chrome={runtime:{},app:{},loadTimes:function(){},csi:function(){}};
Object.defineProperty(navigator,'languages',{get:()=>['en-GB','en']});
Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>8});
Object.defineProperty(navigator,'deviceMemory',{get:()=>8});
"""

# phrases Argos shows when a product no longer exists
DEAD_MARKERS = ("oops, that didn't go to plan",
                "that didn't go to plan",
                "page not found",
                "sorry, we can't find that page")


def jitter(a=0.5, b=1.3):
    time.sleep(random.uniform(a, b))


def human(page):
    for i in range(5):
        page.mouse.move(140 + i * 120, 110 + i * 60)
        page.wait_for_timeout(random.randint(200, 450))
        page.mouse.wheel(0, random.randint(300, 800))


def is_available(page, pid):
    """True if the product page is live, False if delisted/missing."""
    try:
        r = page.goto(f"https://www.argos.co.uk/product/{pid}",
                      wait_until="domcontentloaded", timeout=40000)
    except PWTimeout:
        return None  # unknown — don't delete on a timeout
    if not r:
        return None
    if r.status in (404, 410):
        return False
    if r.status != 200:
        return None  # 403 etc. -> can't tell; keep it
    page.wait_for_timeout(1200)
    low = (page.content() or "").lower()
    if any(m in low for m in DEAD_MARKERS):
        return False
    # a live product page keeps /product/<id> in the URL and shows a title
    if f"/product/{pid}" not in page.url:
        return False
    has_title = page.evaluate("""() => !!(document.querySelector('h1') &&
        document.querySelector('h1').innerText.trim().length > 3)""")
    return bool(has_title)


def run(only, headful):
    files = sorted(OUT.glob("*.json"))
    files = [f for f in files if f.name != "argos_all.json"]
    if only:
        files = [f for f in files if f.stem in only]
    if not files:
        print("No category JSON files found in output/.", file=sys.stderr)
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not headful,
            args=([] if headful else ["--headless=new"]) +
                 ["--no-sandbox", "--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage", "--window-size=1366,900"])
        ctx = browser.new_context(user_agent=UA, locale="en-GB",
                                  viewport={"width": 1366, "height": 900})
        ctx.add_init_script(STEALTH)
        page = ctx.new_page()

        print("Warming up Akamai session on the homepage ...")
        page.goto("https://www.argos.co.uk/", wait_until="domcontentloaded", timeout=45000)
        human(page)
        page.wait_for_timeout(2500)

        combined, totals = {}, [0, 0]
        for f in files:
            products = json.loads(f.read_text())
            kept, removed = [], 0
            print(f"\n== {f.stem} ({len(products)} products) ==")
            for i, prod in enumerate(products, 1):
                pid = prod.get("product_id")
                avail = is_available(page, pid)
                if avail is False:
                    removed += 1
                    print(f"  [{i}/{len(products)}] REMOVED {pid}  {prod.get('title','')[:50]}")
                else:
                    kept.append(prod)         # keep on True or unknown(None)
                jitter()
            f.write_text(json.dumps(kept, indent=2, ensure_ascii=False))
            combined[f.stem] = kept
            totals[0] += len(kept); totals[1] += removed
            print(f"  -> kept {len(kept)}, removed {removed}  (saved {f.name})")

        (OUT / "argos_all.json").write_text(
            json.dumps(combined, indent=2, ensure_ascii=False))
        browser.close()

    print(f"\nDONE. kept {totals[0]} available products, removed {totals[1]} delisted "
          f"-> output/*.json refreshed")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Remove delisted products by checking Argos live.")
    ap.add_argument("--only", nargs="*", help="limit to these category files (e.g. tv headphones)")
    ap.add_argument("--headful", action="store_true", help="show the browser window")
    a = ap.parse_args()
    run(a.only, a.headful)
