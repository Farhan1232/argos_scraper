#!/usr/bin/env python3
"""
Argos scraper via the WAYBACK MACHINE  ->  JSON      (no proxy / no VPN / no UK IP needed)
==========================================================================================
Argos blocks scraping (Akamai + UK-only geo-block). The Internet Archive's Wayback Machine
keeps snapshots of Argos pages, is NOT blocked, and the archived HTML embeds the product
data (title, price, rating, reviews) in its Next.js payload. We read that.

Categories: headphones, tv, office equipment, servers   (mobile phones excluded)
Output:     output/<category>.json  +  output/argos_all.json
Note:       prices are as-of the snapshot date (recent, but not live).
"""
import urllib.request, urllib.parse, re, json, time, pathlib, sys

OUT = pathlib.Path(__file__).parent / "output"

# Archived Argos category pages to harvest, per category.
SEEDS = {
    "tv": [
        "argos.co.uk/browse/technology/televisions-and-accessories/televisions/c:30106/",
        "argos.co.uk/browse/technology/televisions-and-accessories/c:29955/",
    ],
    "headphones": [
        "argos.co.uk/browse/technology/headphones-and-earphones/c:30128/",
    ],
    "office_equipment": [
        "argos.co.uk/browse/technology/printers/c:30088/",
        "argos.co.uk/browse/technology/home-office/c:29954/",
        "argos.co.uk/browse/technology/computer-accessories/printer-ink/c:30098/",
    ],
    # Argos is a consumer retailer with no "servers" — the faithful match for
    # "server stuff" is data-storage hardware (external/NAS drives, USB storage).
    "servers_storage": [
        "argos.co.uk/browse/technology/computer-accessories/external-hard-drives/c:30073/",
        "argos.co.uk/browse/technology/computer-accessories/usb-storage/c:30072/",
    ],
}

EXCLUDE = re.compile(r"\b(mobile phone|smartphone|sim[- ]?free|iphone|android phone)\b", re.I)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}

def fetch(url, timeout=40):
    for _ in range(2):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                          timeout=timeout).read().decode("utf-8", "ignore")
        except Exception:
            time.sleep(1.5)
    return ""

def img(pid, w=750, h=750):
    return f"https://media.4rgos.it/s/Argos/{pid}_R_SET?w={w}&h={h}&qlt=80&fmt.jpeg.interlaced=true"

def snapshots(seed, limit=150):
    """Biggest captures first → most products per page. Window is 2024→now so we
    pull the largest candidate list possible; freshness/availability is then
    enforced separately by check_availability.py against the live Argos site.
    (Argos's newest pages embed fewer products in raw HTML, so fresh-only
    snapshots yield far fewer products — a big candidate list + live checker
    gives more confirmed-available products in the end.)"""
    api = ("http://web.archive.org/cdx/search/cdx?url=" + urllib.parse.quote(seed) +
           "&output=text&fl=timestamp,length&filter=statuscode:200"
           "&from=2024&limit=" + str(limit))
    rows = []
    for line in fetch(api, 30).splitlines():
        p = line.split()
        if len(p) == 2 and p[0].isdigit():
            rows.append((int(p[1]) if p[1].isdigit() else 0, p[0]))
    rows.sort(reverse=True)                 # largest byte-length first
    return [ts for _, ts in rows]

def _num(pat, text):
    m = re.search(pat, text)
    return float(m.group(1)) if m else None

def extract(html):
    """Pull product cards out of the archived Next.js payload.
    Handles both layouts Argos uses:
      - hot-product card:  productId, name, price
      - full listing card: productId, name, brand, price, avgRating, reviewsCount, wasPrice
    """
    out = []
    for m in re.finditer(r'"productId":"(\w+)","name":"((?:[^"\\]|\\.)*)"', html):
        pid = m.group(1)
        try:
            name = m.group(2).encode().decode("unicode_escape")
        except Exception:
            name = m.group(2)
        if EXCLUDE.search(name):
            continue
        win = html[m.end():m.end() + 400]      # fields follow within the card object
        price = _num(r'"price":([\d.]+)', win)
        if price is None:
            continue                            # no price -> not a real product card
        was = _num(r'"wasPrice":([\d.]+)', win)
        # rating: either avgRating (0-5) or rating (0-100)
        avg = _num(r'"avgRating":([\d.]+)', win)
        r100 = _num(r'"rating":([\d.]+)', win)
        rating = round(avg, 1) if avg else (round(r100 / 20, 1) if r100 else None)
        rv = re.search(r'"reviewsCount":(\d+)', win)
        brand = re.search(r'"brand":"([^"]*)"', win)
        rec = {
            "product_id": pid,
            "title": name,
            "brand": brand.group(1) if brand and brand.group(1) else None,
            "price": f"£{price:.2f}",
            "was_price": f"£{was:.2f}" if was else None,
            "rating": rating,
            "reviews": int(rv.group(1)) if rv else None,
            "image": img(pid),
            "url": f"https://www.argos.co.uk/product/{pid}",
        }
        out.append(rec)
    return out

def main():
    OUT.mkdir(exist_ok=True)
    combined = {}
    for cat, seeds in SEEDS.items():
        print(f"\n== {cat} ==")
        bucket, seen = [], set()
        for seed in seeds:
            good = 0           # snapshots that actually contained products
            tried = 0
            for ts in snapshots(seed):
                if good >= 3 or tried >= 12:   # enough good captures / give up on this seed
                    break
                tried += 1
                prods = extract(fetch(f"https://web.archive.org/web/{ts}id_/https://{seed}"))
                if not prods:
                    continue                   # thin/partial capture -> skip
                good += 1
                new = [p for p in prods if p["product_id"] not in seen]
                for p in new:
                    seen.add(p["product_id"])
                bucket.extend(new)
                print(f"  {seed.split('/')[-2] or seed[:28]} @{ts[:8]}: +{len(new)} (total {len(bucket)})")
                time.sleep(0.5)
        combined[cat] = bucket
        (OUT / f"{cat}.json").write_text(json.dumps(bucket, indent=2, ensure_ascii=False))
        print(f"  -> {len(bucket)} products saved to output/{cat}.json")
    (OUT / "argos_all.json").write_text(json.dumps(combined, indent=2, ensure_ascii=False))
    total = sum(len(v) for v in combined.values())
    print(f"\nDONE. {total} products across {len(combined)} categories -> output/argos_all.json")

if __name__ == "__main__":
    main()
