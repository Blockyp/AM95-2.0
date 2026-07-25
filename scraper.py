"""
AM95 Tracker - Scraper
Checks watchlist pairs for price/stock changes, and scans retailer
category pages for newly listed Air Max 95 products.

Run manually with: python scraper.py
Designed to be run on a schedule via GitHub Actions (see .github/workflows/scrape.yml)
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
}

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

WATCHLIST_FILE = ROOT / "watchlist.json"
PRICES_FILE = DATA_DIR / "prices.json"
RELEASES_FILE = DATA_DIR / "new_releases.json"
HISTORY_FILE = DATA_DIR / "price_history.json"

OUT_OF_STOCK_PATTERNS = re.compile(
    r"sold out|out of stock|unavailable|notify me|coming soon|currently unavailable",
    re.IGNORECASE,
)
PRICE_PATTERN = re.compile(r"[£$€]\s?\d{1,4}(?:[.,]\d{2})?")


def fetch(url, retries=3, timeout=15):
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            if resp.status_code == 200:
                return resp.text
            print(f"  [warn] status {resp.status_code} for {url}")
        except requests.RequestException as e:
            print(f"  [warn] attempt {attempt+1} failed for {url}: {e}")
        time.sleep(2)
    return None


def parse_product_page(html):
    """Best-effort generic parse: pull a price and infer stock status.
    Retailer page layouts change over time, so this uses broad heuristics
    rather than site-specific selectors. Treat results as a starting point
    and refine selectors per-site if you see wrong/missing data."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    price_match = PRICE_PATTERN.search(text)
    price = price_match.group(0) if price_match else None

    in_stock = not bool(OUT_OF_STOCK_PATTERNS.search(text))

    return {"price": price, "in_stock": in_stock}


def parse_category_page(html, base_url):
    """Extract product links + names from a category/listing page.
    Generic heuristic: any <a> tag whose href looks like a product page."""
    soup = BeautifulSoup(html, "html.parser")
    products = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        name = a.get_text(strip=True)
        if not name or len(name) < 4:
            continue
        if any(k in href.lower() for k in ["/product", "/products/", ".html", "/t/"]):
            if href.startswith("/"):
                from urllib.parse import urlparse
                parsed = urlparse(base_url)
                href = f"{parsed.scheme}://{parsed.netloc}{href}"
            products[href] = name
    return products


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return default
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2))


def check_watchlist(watchlist):
    prices = load_json(PRICES_FILE, {})
    history = load_json(HISTORY_FILE, {})
    now = datetime.now(timezone.utc).isoformat()

    for pair in watchlist["pairs"]:
        pair_key = pair["sku"]
        prices.setdefault(pair_key, {"name": pair["name"], "sku": pair_key, "retailers": {}})
        history.setdefault(pair_key, [])

        for retailer, url in pair["urls"].items():
            print(f"Checking {pair['name']} @ {retailer}...")
            html = fetch(url)
            if not html:
                prices[pair_key]["retailers"][retailer] = {
                    "url": url, "price": None, "in_stock": None,
                    "error": "fetch_failed", "checked_at": now,
                }
                continue

            result = parse_product_page(html)
            prev = prices[pair_key]["retailers"].get(retailer, {})

            entry = {
                "url": url,
                "price": result["price"],
                "in_stock": result["in_stock"],
                "checked_at": now,
            }
            prices[pair_key]["retailers"][retailer] = entry

            # log price changes to history
            if result["price"] and result["price"] != prev.get("price"):
                history[pair_key].append({
                    "retailer": retailer,
                    "price": result["price"],
                    "in_stock": result["in_stock"],
                    "at": now,
                })

    save_json(PRICES_FILE, prices)
    save_json(HISTORY_FILE, history)
    print(f"Saved watchlist results -> {PRICES_FILE}")


def check_new_releases(watchlist):
    seen = load_json(RELEASES_FILE, {"known_urls": {}, "feed": []})
    now = datetime.now(timezone.utc).isoformat()

    for retailer, page_url in watchlist["new_release_scan_pages"].items():
        print(f"Scanning {retailer} category page for new listings...")
        html = fetch(page_url)
        if not html:
            continue

        products = parse_category_page(html, page_url)
        known = seen["known_urls"].setdefault(retailer, {})

        for url, name in products.items():
            if "95" not in name and "95" not in url:
                continue  # keep it to Air Max 95 listings
            if url not in known:
                known[url] = {"name": name, "first_seen": now}
                seen["feed"].append({
                    "retailer": retailer,
                    "name": name,
                    "url": url,
                    "first_seen": now,
                })
                print(f"  [new] {name}")

    # newest first
    seen["feed"].sort(key=lambda x: x["first_seen"], reverse=True)
    save_json(RELEASES_FILE, seen)
    print(f"Saved release feed -> {RELEASES_FILE}")


def main():
    watchlist = load_json(WATCHLIST_FILE, {"pairs": [], "new_release_scan_pages": {}})
    check_watchlist(watchlist)
    check_new_releases(watchlist)


if __name__ == "__main__":
    main()
