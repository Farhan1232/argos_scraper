# Argos product scraper → JSON

## ✅ WORKING RESULT (no proxy / VPN needed) — `wayback_scraper.py`

Because Argos blocks live scraping (Akamai + UK-only geo-block), the working approach
pulls the data from the **Internet Archive's Wayback Machine**, which keeps snapshots of
Argos pages, isn't blocked, and embeds the product data in the archived HTML.

```bash
cd argos_scraper
source venv/bin/activate
python wayback_scraper.py
```

**Already produced 284 products** (prices as-of recent 2024-2025 snapshots):

| category | products | file |
|---|---|---|
| headphones | 77 | `output/headphones.json` |
| tv | 119 | `output/tv.json` |
| office_equipment | 60 | `output/office_equipment.json` |
| servers_storage* | 28 | `output/servers_storage.json` |
| **all combined** | **284** | `output/argos_all.json` |

\* Argos has no "servers" (it's a consumer retailer); the faithful match for "server
stuff" is data-storage hardware — external/NAS/USB drives. Mobile phones are excluded.

Each record: `product_id, title, brand, price, was_price, rating, reviews, image, url`.
Add/adjust categories by editing the `SEEDS` dict at the top of `wayback_scraper.py`.

---

## Alternative: live scraper — `argos_scraper.py` (needs a UK residential IP)

Scrapes **headphones, TVs, office equipment, and server/NAS** products from
<https://www.argos.co.uk> into JSON. **Mobile phones are excluded** (filtered out by title).

For each product: `title`, `price`, `image`, `url`, `brand`, `rating`, `reviews`,
and with `--details` also `description`, `specifications`, and `related_products`.

## Quick start

```bash
cd argos_scraper
python3 -m venv venv && source venv/bin/activate
pip install playwright beautifulsoup4 lxml
python -m playwright install chromium

# summaries only (fast)
python argos_scraper.py

# full data incl. details + related products
python argos_scraper.py --details --max 40

# a single category, show the browser
python argos_scraper.py --only headphones --headful
```

Output lands in `output/`:
- `output/headphones.json`, `output/tv.json`, `output/office_equipment.json`, `output/servers.json`
- `output/argos_all.json` — everything combined

## ⚠️ Run it from your own machine (residential IP)

Argos is protected by **Akamai Bot Manager**. The scraper beats it the way a real
browser does — Chromium in "new headless" mode, warming the Akamai `_abck` cookie on
the homepage with human-like mouse/scroll movement, then visiting search/product pages
in the same session.

This works from a normal home/office IP. It does **not** work from a datacenter/cloud
IP (AWS, GCP, etc.): Akamai keeps the `_abck` token invalid for those ranges and every
inner page returns **HTTP 403** regardless of browser fingerprint. The script prints a
warning when it detects this. (That is exactly why the environment this was built in
returned 0 products — the homepage loaded, but the cloud IP was refused on inner pages.)

If you must run from a server, route it through a **residential proxy**: add
`proxy={"server": "...", "username": "...", "password": "..."}` to `chromium.launch(...)`.

## How it works

1. **Cookie warm-up** — loads the homepage, simulates mouse moves + scrolling so Akamai
   validates the session, checks the `_abck` token.
2. **Search** — for each category it queries Argos's internal JSON endpoint
   `finder-api/product;...;searchTerm=...` (cleanest data), and falls back to scraping
   the rendered `/search/<term>/` results DOM if the API is unavailable. Paginates until
   the limit or no new results.
3. **Details** (`--details`) — opens each product page and extracts the description,
   the specifications table, and the "you may also like / related" products.
4. **Images** — derived directly from the product id via the open image CDN:
   `https://media.4rgos.it/s/Argos/<ID>_R_SET?w=750&h=750&qlt=80&fmt.jpeg.interlaced=true`
   (this CDN is not bot-protected — verified returning a real JPEG).

Categories and search terms are defined at the top of `argos_scraper.py` in `CATEGORIES`
— edit them to add/adjust (e.g. add `"monitors"` or `"webcams"` under office equipment).

## Files

| file | what it is |
|---|---|
| `argos_scraper.py` | the scraper |
| `output/SAMPLE_real_extraction.json` | **real** products pulled live from argos.co.uk, showing the exact JSON shape and that title + derived-image extraction works |
| `output/*.json` | your scrape results |

## Expected JSON shape

```json
{
  "product_id": "7699799",
  "title": "Steelseries Arctis Nova Pro Wireless Headset",
  "price": "£329.99",
  "brand": "SteelSeries",
  "rating": 4.7,
  "reviews": 128,
  "image": "https://media.4rgos.it/s/Argos/7699799_R_SET?w=750&h=750&qlt=80&fmt.jpeg.interlaced=true",
  "url": "https://www.argos.co.uk/product/7699799",
  "description": "….",
  "specifications": { "Brand": "SteelSeries", "Connectivity": "Wireless", "…": "…" },
  "related_products": [ { "product_id": "…", "title": "…", "url": "…" } ]
}
```
