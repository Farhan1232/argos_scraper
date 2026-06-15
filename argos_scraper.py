#!/usr/bin/env python3
"""
Argos category scraper  ->  JSON
=================================
Scrapes product data from https://www.argos.co.uk for the categories you asked for:

    headphones, tv, office equipment, server stuff      (mobile phones are EXCLUDED)

For every product it collects:  title, price, image, product url, rating, brand,
plus (optional) per-product DETAILS (description + specifications) and RELATED PRODUCTS.

Argos sits behind Akamai Bot Manager.  This script defeats it the way a real browser
does: it launches Chromium in the (hard-to-detect) "new headless" mode, warms up the
Akamai `_abck` cookie on the homepage with human-like mouse/scroll activity, and only
then visits the search + product pages *inside the same browser session*.

IMPORTANT — run this from a normal/residential IP (your laptop, home, etc.).
From a datacenter / cloud IP Argos's Akamai keeps the `_abck` token invalid and every
inner page returns HTTP 403, no matter how good the browser fingerprint is.

Usage
-----
    python argos_scraper.py                       # all 4 categories, summaries only
    python argos_scraper.py --details             # also scrape details + related products
    python argos_scraper.py --details --max 40    # cap at 40 products per category
    python argos_scraper.py --only headphones tv  # just these categories
    python argos_scraper.py --headful             # show the browser (debugging)

Output  ->  ./output/<category>.json  and  ./output/argos_all.json
"""

import argparse, json, re, sys, time, random, pathlib
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# --------------------------------------------------------------------------- config
CATEGORIES = {
    "headphones":       ["headphones"],
    "tv":               ["televisions"],
    "office_equipment": ["office equipment", "printers", "office chairs", "shredders"],
    "servers":          ["server", "nas drive"],
}

# Anything matching this is dropped (you said: no mobile phones)
EXCLUDE = re.compile(
    r"\b(mobile phone|smartphone|sim[- ]?free|iphone|android phone|pay as you go phone)\b", re.I)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

STEALTH = """
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
window.chrome={runtime:{},app:{},loadTimes:function(){},csi:function(){}};
Object.defineProperty(navigator,'languages',{get:()=>['en-GB','en']});
Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>8});
Object.defineProperty(navigator,'deviceMemory',{get:()=>8});
const _q=window.navigator.permissions.query;
window.navigator.permissions.query=(p)=>p && p.name==='notifications'
   ? Promise.resolve({state:Notification.permission}) : _q(p);
"""

OUT = pathlib.Path(__file__).parent / "output"

# --------------------------------------------------------------------------- helpers
def img_url(pid, w=750, h=750):
    """Argos image CDN — derivable from the product id, never bot-protected."""
    return (f"https://media.4rgos.it/s/Argos/{pid}_R_SET"
            f"?w={w}&h={h}&qlt=80&fmt.jpeg.interlaced=true")

def jitter(a=0.6, b=1.6):
    time.sleep(random.uniform(a, b))

def abck_valid(ctx):
    for c in ctx.cookies():
        if c["name"] == "_abck":
            return "~0~" in c["value"] or "~-1~" not in c["value"]
    return False

def human(page):
    """Mouse + scroll wiggle so Akamai validates the session as human."""
    for i in range(6):
        page.mouse.move(150 + i * 130, 120 + i * 70)
        page.wait_for_timeout(random.randint(250, 550))
        page.mouse.wheel(0, random.randint(400, 900))

# --------------------------------------------------------------------------- search
def finder_api(term, page_no):
    """Argos internal JSON search endpoint — cleanest data source."""
    qp = json.dumps({"page": str(page_no), "templateType": None}, separators=(",", ":"))
    return ("https://www.argos.co.uk/finder-api/product;"
            f"isSearch=true;queryParams={qp};searchTerm={term};sort=relevance"
            "?returnMeta=true")

def parse_finder(payload):
    """Defensive walk of the finder-api JSON -> list of product summary dicts."""
    prods, seen = [], set()

    def walk(node):
        if isinstance(node, dict):
            # a product object usually has an id + attributes(name/price)
            attrs = node.get("attributes") if isinstance(node.get("attributes"), dict) else None
            pid = str(node.get("id") or node.get("partNumber") or "")
            if attrs and pid and pid not in seen and (attrs.get("name") or attrs.get("title")):
                seen.add(pid)
                price = attrs.get("price") or attrs.get("nowPrice") or attrs.get("sellPrice")
                prods.append({
                    "product_id": pid,
                    "title": attrs.get("name") or attrs.get("title"),
                    "price": (f"£{price}" if isinstance(price, (int, float)) else price),
                    "brand": attrs.get("brand"),
                    "rating": attrs.get("avgRating") or attrs.get("rating"),
                    "reviews": attrs.get("reviewCount"),
                    "image": img_url(pid),
                    "url": f"https://www.argos.co.uk/product/{pid}",
                })
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    return prods

def scrape_search_dom(page):
    """Fallback: read product cards straight from the rendered search HTML."""
    page.wait_for_timeout(2500)
    cards = page.evaluate("""() => {
        const out = [];
        document.querySelectorAll('[id^="product-title-"]').forEach(t => {
            const id = t.id.replace('product-title-','');
            const card = t.closest('[data-test*="product-card"], li, article') || t.parentElement;
            let price = null;
            if (card) {
                const m = card.innerText.match(/£[\\d,]+\\.\\d{2}/);
                if (m) price = m[0];
            }
            out.push({product_id:id, title:t.innerText.trim(), price});
        });
        return out;
    }""")
    for c in cards:
        c["image"] = img_url(c["product_id"])
        c["url"] = f"https://www.argos.co.uk/product/{c['product_id']}"
    return cards

def get_json_from_page(page):
    """When we navigate to a finder-api URL the body is raw JSON inside <pre>."""
    txt = page.evaluate("() => document.body.innerText")
    try:
        return json.loads(txt)
    except Exception:
        return None

def search_term(page, term, max_items):
    """Collect product summaries for one search term, paginating until empty/limit."""
    found, page_no = [], 1
    while len(found) < max_items and page_no <= 10:
        # 1) try the clean JSON API first
        resp = page.goto(finder_api(term, page_no), wait_until="domcontentloaded", timeout=45000)
        data = get_json_from_page(page) if resp and resp.status == 200 else None
        batch = parse_finder(data) if data else []

        # 2) fall back to scraping the search results page DOM
        if not batch:
            r = page.goto(f"https://www.argos.co.uk/search/{term.replace(' ','%20')}/"
                          f"?clickOrigin=&pageNo={page_no}",
                          wait_until="domcontentloaded", timeout=45000)
            if r and r.status == 200 and "Access Denied" not in page.content():
                batch = scrape_search_dom(page)

        if not batch:
            break
        new = [p for p in batch if p["product_id"] not in {f["product_id"] for f in found}]
        if not new:
            break
        found.extend(new)
        print(f"    page {page_no}: +{len(new)} (total {len(found)})")
        page_no += 1
        jitter()
    return found[:max_items]

# --------------------------------------------------------------------------- details
def scrape_details(page, prod):
    """Visit a product page -> description, specifications, related products."""
    try:
        r = page.goto(prod["url"], wait_until="domcontentloaded", timeout=45000)
        if not r or r.status != 200 or "Access Denied" in page.content():
            return prod
        page.wait_for_timeout(1800)
        info = page.evaluate("""() => {
            const txt = (s)=>{const e=document.querySelector(s);return e?e.innerText.trim():null;};
            // description
            let desc = txt('[data-test="product-description-text"]')
                    || txt('[class*="ProductDescription"]')
                    || txt('section[aria-label*="escription"]');
            // specifications table
            const specs = {};
            document.querySelectorAll('table tr').forEach(tr=>{
                const c=tr.querySelectorAll('th,td');
                if(c.length===2){const k=c[0].innerText.trim();const v=c[1].innerText.trim();
                    if(k&&v) specs[k]=v;}
            });
            // related / "you may also like"
            const related=[];const seen=new Set();
            document.querySelectorAll('a[href*="/product/"]').forEach(a=>{
                const m=a.getAttribute('href').match(/\\/product\\/([A-Za-z0-9]+)/);
                if(!m) return; const id=m[1]; if(seen.has(id)) return; seen.add(id);
                const t=(a.getAttribute('aria-label')||a.innerText||'').trim();
                if(t) related.push({product_id:id,title:t,url:'https://www.argos.co.uk/product/'+id});
            });
            return {desc, specs, related};
        }""")
        prod["description"] = info.get("desc")
        prod["specifications"] = info.get("specs") or {}
        # drop self + cap related list
        rel = [x for x in (info.get("related") or []) if x["product_id"] != prod["product_id"]]
        prod["related_products"] = rel[:12]
    except PWTimeout:
        pass
    return prod

# --------------------------------------------------------------------------- driver
def run(only, do_details, max_items, headful):
    OUT.mkdir(exist_ok=True)
    cats = {k: v for k, v in CATEGORIES.items() if not only or k in only}
    combined = {}

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

        # ---- warm up Akamai cookies on the homepage ----
        print("Warming up session on homepage ...")
        page.goto("https://www.argos.co.uk/", wait_until="domcontentloaded", timeout=45000)
        human(page)
        page.wait_for_timeout(3000)
        if not abck_valid(ctx):
            print("  ! Akamai _abck token did not validate — you are probably on a "
                  "datacenter IP. Run this from a residential/home IP.", file=sys.stderr)

        # ---- scrape each category ----
        for cat, terms in cats.items():
            print(f"\n== {cat} ==")
            bucket, ids = [], set()
            for term in terms:
                print(f"  search: {term!r}")
                for prod in search_term(page, term, max_items):
                    if prod["product_id"] in ids:
                        continue
                    if EXCLUDE.search(prod.get("title") or ""):
                        continue                       # skip mobile phones
                    ids.add(prod["product_id"])
                    bucket.append(prod)
                    if len(bucket) >= max_items:
                        break
                if len(bucket) >= max_items:
                    break

            if do_details:
                print(f"  fetching details for {len(bucket)} products ...")
                for i, prod in enumerate(bucket, 1):
                    scrape_details(page, prod)
                    if i % 10 == 0:
                        print(f"    details {i}/{len(bucket)}")
                    jitter()

            combined[cat] = bucket
            (OUT / f"{cat}.json").write_text(
                json.dumps(bucket, indent=2, ensure_ascii=False))
            print(f"  -> saved {len(bucket)} products to output/{cat}.json")

        browser.close()

    (OUT / "argos_all.json").write_text(json.dumps(combined, indent=2, ensure_ascii=False))
    total = sum(len(v) for v in combined.values())
    print(f"\nDONE. {total} products across {len(combined)} categories "
          f"-> output/argos_all.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Scrape Argos product data to JSON.")
    ap.add_argument("--only", nargs="*", choices=list(CATEGORIES),
                    help="limit to these categories")
    ap.add_argument("--details", action="store_true",
                    help="also scrape description, specs and related products (slower)")
    ap.add_argument("--max", type=int, default=60, dest="max_items",
                    help="max products per category (default 60)")
    ap.add_argument("--headful", action="store_true", help="show the browser window")
    a = ap.parse_args()
    run(a.only, a.details, a.max_items, a.headful)
