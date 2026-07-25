"""
AM95 Tracker - Scraper
Checks watchlist pairs for price/stock changes, and scans retailer
category pages for newly listed Air Max 95 products.

Run manually with: python scraper.py
Designed to be run on a schedule via GitHub Actions (see .github/workflows/scrape.yml)
"""

import json
import os
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

DISCORD_WEBHOOK_PRICE_URL = os.environ.get("DISCORD_WEBHOOK_PRICE_URL", "").strip()
DISCORD_WEBHOOK_LISTINGS_URL = os.environ.get("DISCORD_WEBHOOK_LISTINGS_URL", "").strip()


def notify_discord(message, webhook_url):
    if not webhook_url:
        return
    try:
        requests.post(webhook_url, json={"content": message}, timeout=10)
    except requests.RequestException as e:
        print(f"  [warn] discord notify failed: {e}")


def fetch(url, retries=3, timeout=15):
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
            if resp.status_code == 200:
                return resp.text
            print(f"  [warn] status {resp.status_code} for {url}")
        except requests.RequestException as e:
            print(f"  [warn] attempt {attempt+1} failed for {url}: {e}")
        time.sleep(2)
    return None


CURRENCY_SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}


def _meta(soup, name):
    tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
    return tag.get("content", "").strip() if tag else None


def parse_product_page(html):
    """Read structured product data first (meta tags used for social/SEO
    previews — og:price, product:price, twitter:data1/data2, JSON-LD),
    since these are far more reliable than scanning visible page text.
    Falls back to text scanning only if no structured data is found."""
    soup = BeautifulSoup(html, "html.parser")

    price = None
    in_stock = None

    # --- 1. Open Graph / Shopify-style product meta tags ---
    amount = _meta(soup, "og:price:amount") or _meta(soup, "product:price:amount")
    currency = _meta(soup, "og:price:currency") or _meta(soup, "product:price:currency")
    if amount:
        try:
            amount_f = float(amount)
            symbol = CURRENCY_SYMBOLS.get((currency or "").upper(), "")
            price = f"{symbol}{amount_f:.2f}"
        except ValueError:
            pass

    availability = _meta(soup, "product:availability")
    if availability:
        in_stock = availability.strip().lower() in ("instock", "in stock", "in_stock")

    # --- 2. Twitter card product data (used by size?, JD-family sites) ---
    if price is None:
        data1 = _meta(soup, "twitter:data1")  # usually the price
        label1 = _meta(soup, "twitter:label1")
        if data1 and label1 and "price" in label1.lower():
            try:
                price = f"£{float(data1):.2f}"
            except ValueError:
                price = data1 if any(c.isdigit() for c in data1) else None

    if in_stock is None:
        data2 = _meta(soup, "twitter:data2")
        label2 = _meta(soup, "twitter:label2")
        if data2 and label2 and "availab" in label2.lower():
            in_stock = "in stock" in data2.strip().lower()

    # --- 3. JSON-LD structured data (schema.org Product/Offer) ---
    if price is None or in_stock is None:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            candidates = data if isinstance(data, list) else [data]
            for item in candidates:
                offers = item.get("offers") if isinstance(item, dict) else None
                if isinstance(offers, list):
                    offers = offers[0] if offers else None
                if isinstance(offers, dict):
                    if price is None and offers.get("price"):
                        cur = offers.get("priceCurrency", "")
                        symbol = CURRENCY_SYMBOLS.get(cur.upper(), "")
                        try:
                            price = f"{symbol}{float(offers['price']):.2f}"
                        except (ValueError, TypeError):
                            pass
                    if in_stock is None and offers.get("availability"):
                        in_stock = "instock" in offers["availability"].lower()

    # --- 4. Fallback: loose text scan (last resort, least reliable) ---
    if price is None or in_stock is None:
        text = soup.get_text(" ", strip=True)
        if price is None:
            m = re.search(r"[£$€]\s?\d{1,4}(?:[.,]\d{2})?", text)
            if m:
                price = m.group(0)
        if in_stock is None:
            out_of_stock = re.search(
                r"sold out|out of stock|unavailable|notify me|coming soon",
                text, re.IGNORECASE,
            )
            in_stock = not bool(out_of_stock)

    return {"price": price, "in_stock": in_stock}


def parse_category_page(html, base_url):
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

            if result["price"] and result["price"] != prev.get("price"):
                history[pair_key].append({
                    "retailer": retailer,
                    "price": result["price"],
                    "in_stock": result["in_stock"],
                    "at": now,
                })
                if prev.get("price"):
                    notify_discord(
                        f"💰 **Price change** — {pair['name']} @ {retailer}\n"
                        f"{prev.get('price')} → {result['price']}\n{url}",
                        DISCORD_WEBHOOK_PRICE_URL,
                    )

            prev_stock = prev.get("in_stock")
            if prev_stock is not None and result["in_stock"] != prev_stock:
                if result["in_stock"]:
                    notify_discord(f"✅ **Back in stock** — {pair['name']} @ {retailer}\n{url}", DISCORD_WEBHOOK_PRICE_URL)
                else:
                    notify_discord(f"❌ **Sold out** — {pair['name']} @ {retailer}\n{url}", DISCORD_WEBHOOK_PRICE_URL)

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
                continue
            if url not in known:
                known[url] = {"name": name, "first_seen": now}
                seen["feed"].append({
                    "retailer": retailer,
                    "name": name,
                    "url": url,
                    "first_seen": now,
                })
                print(f"  [new] {name}")
                notify_discord(f"🆕 **New AM95 listing** — {retailer}\n{name}\n{url}", DISCORD_WEBHOOK_LISTINGS_URL)

    seen["feed"].sort(key=lambda x: x["first_seen"], reverse=True)
    save_json(RELEASES_FILE, seen)
    print(f"Saved release feed -> {RELEASES_FILE}")


def main():
    watchlist = load_json(WATCHLIST_FILE, {"pairs": [], "new_release_scan_pages": {}})
    check_watchlist(watchlist)
    check_new_releases(watchlist)


if __name__ == "__main__":
    main()
