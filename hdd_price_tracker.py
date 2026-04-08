#!/usr/bin/env python3
"""
Seagate IronWolf — Canadian HDD Price Tracker with Telegram Alerts
===================================================================

Checks prices every 3 minutes across 6 Canadian stores using a real
headless browser (Playwright) so JavaScript-rendered prices are captured.

Sends a Telegram message when a drive is in stock AND at or below
your target price.

SETUP (one-time)
────────────────
  1.  pip install playwright
      playwright install chromium

  2.  Create a Telegram bot:
        • Open Telegram → search @BotFather → /newbot → follow prompts
        • Copy the API token it gives you

  3.  Get your chat ID:
        • Message your new bot (just say "hi")
        • Visit  https://api.telegram.org/bot<TOKEN>/getUpdates
        • Find "chat":{"id": 123456789 ...}  — that number is your chat ID

  4.  Paste the token and chat ID below (or set env vars)

RUN
───
  python nas_price_checker.py            # loop every 5 min
  python nas_price_checker.py --once     # single check then exit
"""

import json
import logging
import os
import random
import re
import sys
import time
import csv
from datetime import datetime
from pathlib import Path
from typing import Optional

# ───────────────────────────────────────────────────────────────────
# CONFIGURATION — edit these or set environment variables
# ───────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8774802130:AAGDu2xeTSxpQjxCZdnQJYLn78aR6lFrx1Y")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "123199076")

CHECK_INTERVAL_SECONDS = 3 * 60   # 3 minutes

CSV_LOG = "price_history.csv"      # append-only log of every price seen

# Drives to track — IronWolf & IronWolf Pro, with target price ranges (CAD)
DRIVES = [
    # ── IronWolf Pro ──
    {
        "name":       "IronWolf Pro 8TB",
        "sku":        "ST8000NT001",
        "capacity":   8,
        "target_min": 300.00,
        "target_max": 350.00,
        "stores": {
            "Amazon.ca": "https://www.amazon.ca/dp/B0B94M13NH",
            "Canada Computers": "https://www.canadacomputers.com/en/desktop-internal-hard-drives/239798/seagate-ironwolf-pro-8-tb-hard-drive-st8000nt001.html",
            "Memory Express": "https://www.memoryexpress.com/Search/Products?Search=ST8000NT001",
            "Newegg.ca": "https://www.newegg.ca/p/pl?d=ST8000NT001",
            "Best Buy": "17109667",
            "CDW Canada": "https://www.cdw.ca/product/seagate-ironwolf-pro-st8000nt001-hard-drive-8-tb-sata-6gb-s/7480662",
        },
    },
    {
        "name":       "IronWolf Pro 12TB",
        "sku":        "ST12000NT001",
        "capacity":   12,
        "target_min": 390.00,
        "target_max": 460.00,
        "stores": {
            "Amazon.ca": "https://www.amazon.ca/dp/B0B94KSFTH",
            "Canada Computers": "https://www.canadacomputers.com/en/desktop-internal-hard-drives/238856/seagate-ironwolf-pro-12tb-hard-drive-3-5-internal-sata-sata-600-st12000nt001.html",
            "Memory Express": "https://www.memoryexpress.com/Products/MX00126780",
            "Newegg.ca": "https://www.newegg.ca/p/pl?d=ST12000NT001",
            "Best Buy": "19186375,17077279",
            "CDW Canada": "https://www.cdw.ca/product/seagate-ironwolf-pro-st12000nt001-hard-drive-12-tb-sata-6gb-s/7509268",
        },
    },
    # ── IronWolf (Regular) ──
    {
        "name":       "IronWolf 8TB",
        "sku":        "ST8000VN004",
        "capacity":   8,
        "target_min": 250.00,
        "target_max": 320.00,
        "stores": {
            "Amazon.ca": "https://www.amazon.ca/dp/B084ZV4DXB",
            "Canada Computers": "https://www.canadacomputers.com/en/desktop-internal-hard-drives/160801/seagate-ironwolf-8tb-nas-7200rpm-256-mb-st8000vn004.html",
            "Memory Express": "https://www.memoryexpress.com/Products/MX80662",
            "Newegg.ca": "https://www.newegg.ca/p/pl?d=ST8000VN004",
            "Best Buy": "14590652",
            "CDW Canada": "https://www.cdw.ca/product/seagate-ironwolf-st8000vn004-hard-drive-8-tb-sata-6gb-s/5903591",
        },
    },
    {
        "name":       "IronWolf 12TB",
        "sku":        "ST12000VN0008",
        "capacity":   12,
        "target_min": 350.00,
        "target_max": 400.00,
        "stores": {
            "Amazon.ca": "https://www.amazon.ca/dp/B084ZTSMWF",
            "Canada Computers": "https://www.canadacomputers.com/en/desktop-internal-hard-drives/137535/seagate-ironwolf-12tb-sata6gb-s-256mb-desktop-hard-drives-st12000vn0008.html",
            "Memory Express": "https://www.memoryexpress.com/Products/MX77890",
            "Newegg.ca": "https://www.newegg.ca/p/pl?d=ST12000VN0008",
            "Best Buy": "13800313",
            "CDW Canada": "https://www.cdw.ca/product/seagate-ironwolf-st12000vn0008-hard-drive-12-tb-sata-6gb-s/5398228",
        },
    },
]

# ───────────────────────────────────────────────────────────────────
# Logging
# ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("nas")


# ───────────────────────────────────────────────────────────────────
# Telegram
# ───────────────────────────────────────────────────────────────────

import urllib.request
import urllib.parse

def telegram_send(text: str):
    """Send a message via the Telegram Bot API."""
    if "YOUR_" in TELEGRAM_BOT_TOKEN or "YOUR_" in TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured — skipping notification")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    }).encode()

    try:
        req = urllib.request.Request(url, data=payload)
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                log.info("  ✅ Telegram message sent")
            else:
                log.warning(f"  Telegram returned status {resp.status}")
    except Exception as e:
        log.error(f"  Telegram send failed: {e}")


# ───────────────────────────────────────────────────────────────────
# CSV logging
# ───────────────────────────────────────────────────────────────────

def init_csv():
    if not Path(CSV_LOG).exists():
        with open(CSV_LOG, "w", newline="") as f:
            csv.writer(f).writerow([
                "timestamp", "drive", "sku", "store",
                "price_cad", "per_tb", "in_stock", "meets_target", "url",
            ])


def log_csv(ts, drive, store, price, in_stock, meets_target, url):
    with open(CSV_LOG, "a", newline="") as f:
        csv.writer(f).writerow([
            ts, drive["name"], drive["sku"], store,
            f"{price:.2f}" if price else "",
            f"{price/drive['capacity']:.2f}" if price else "",
            in_stock, meets_target, url,
        ])


# ───────────────────────────────────────────────────────────────────
# Price extraction helpers (per store)
# ───────────────────────────────────────────────────────────────────

PRICE_RE = re.compile(r"\$\s?([\d,]+\.?\d*)")

def find_price(text: str) -> Optional[float]:
    """Pull the first reasonable CAD price from a text blob."""
    for m in PRICE_RE.finditer(text):
        val = float(m.group(1).replace(",", ""))
        if 50 < val < 5000:   # sanity range for a hard drive
            return val
    return None


def extract_amazon(page, sku: str = "") -> tuple[Optional[float], bool]:
    """Amazon.ca — handles both direct product pages and search results.

    For search results, filters by SKU in the product title to avoid
    picking up the wrong product's price.
    """
    url = page.url

    # ── Direct product page (e.g. /dp/B0B94NFYWX) ──
    if "/dp/" in url or "/gp/product/" in url:
        selectors = [
            "#corePrice_feature_div .a-offscreen",
            "#tp_price_block_total_price_ww .a-offscreen",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            "span.a-price > span.a-offscreen",
            ".a-price .a-offscreen",
            '[data-a-color="price"] .a-offscreen',
        ]
        for sel in selectors:
            els = page.query_selector_all(sel)
            for el in els:
                price = find_price(el.inner_text())
                if price:
                    body = page.inner_text("body")
                    oos = any(s in body.lower() for s in [
                        "currently unavailable", "out of stock",
                        "not available", "see all buying options",
                    ])
                    return price, not oos
        return None, False

    # ── Search results page ──
    # Each result card is a div with data-component-type="s-search-result"
    # and carries a data-asin attribute.
    result_cards = page.query_selector_all(
        'div[data-component-type="s-search-result"]'
    )

    for card in result_cards:
        # Get the product title from the card
        title_el = card.query_selector("h2 a span")
        if not title_el:
            title_el = card.query_selector("h2 span")
        title_text = title_el.inner_text().upper() if title_el else ""

        # Filter: the SKU (e.g. ST12000NT001) or key terms must appear
        # in the title so we don't grab an unrelated product's price.
        sku_upper = sku.upper()
        if sku_upper and sku_upper not in title_text:
            # Also try matching without the "ST" prefix numeric part
            # e.g. "12TB" for ST12000NT001 → "12"
            capacity_match = re.search(r"ST(\d+)000", sku_upper)
            capacity_tb = capacity_match.group(1) + "TB" if capacity_match else ""
            if not (capacity_tb and capacity_tb in title_text
                    and "IRONWOLF" in title_text and "PRO" in title_text):
                continue

        # Extract price from this specific card
        price_selectors = [
            "span.a-price > span.a-offscreen",
            ".a-price .a-offscreen",
            '[data-a-color="price"] .a-offscreen',
        ]
        for sel in price_selectors:
            els = card.query_selector_all(sel)
            for el in els:
                price = find_price(el.inner_text())
                if price:
                    # Check stock from card text
                    card_text = card.inner_text().lower()
                    oos = any(s in card_text for s in [
                        "currently unavailable", "out of stock",
                        "not available",
                    ])
                    return price, not oos

    # Fallback: if no card matched by SKU, don't return a random price
    return None, False


def extract_canadacomputers(page, sku: str = "") -> tuple[Optional[float], bool]:
    """Canada Computers product page."""
    selectors = [
        ".price-show strong",
        ".sell-price",
        "strong.text-red",
        '[class*="price"] strong',
        "span.price",
    ]
    for sel in selectors:
        el = page.query_selector(sel)
        if el:
            price = find_price(el.inner_text())
            if price:
                body = page.inner_text("body").lower()
                oos = "out of stock" in body or "not available" in body or "sold out" in body
                return price, not oos

    # Fallback: scan whole page
    body = page.inner_text("body")
    price = find_price(body)
    if price:
        oos = any(s in body.lower() for s in ["out of stock", "not available", "sold out"])
        return price, not oos
    return None, False


def extract_memoryexpress(page, sku: str = "") -> tuple[Optional[float], bool]:
    """Memory Express — handles both direct product pages and search results.

    Direct product pages use /Products/MX00xxxxxx and have a clear price
    element.  Search pages at /Search/Products?Search=... return a grid
    of product cards that must be filtered by SKU.
    """
    url = page.url

    # Random delay to avoid Cloudflare rate-limiting on consecutive requests
    page.wait_for_timeout(random.randint(2000, 5000))

    # ── Direct product page ──
    if "/Products/MX" in url:
        # Wait for network to settle and price elements to render
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        price_wait_selectors = [
            ".GrandTotal",
            ".c-capr-pricing__grand-total",
            '[class*="GrandTotal"]',
        ]
        for ws in price_wait_selectors:
            try:
                page.wait_for_selector(ws, timeout=8000)
                break
            except Exception:
                continue

        selectors = [
            ".GrandTotal",
            ".c-capr-pricing__grand-total",
            '[class*="grand-total"]',
            '[class*="GrandTotal"]',
            ".ProductPrice",
            "div.IPBH_price",
            '[class*="pricing"] [class*="total"]',
        ]
        for sel in selectors:
            el = page.query_selector(sel)
            if el:
                price = find_price(el.inner_text())
                if price:
                    body = page.inner_text("body").lower()
                    oos = any(s in body for s in [
                        "out of stock", "back order", "not available",
                        "sold out", "temporarily unavailable",
                    ])
                    return price, not oos

        # Fallback: try JSON-LD on product pages
        scripts = page.query_selector_all('script[type="application/ld+json"]')
        for s in scripts:
            try:
                data = json.loads(s.inner_text())
                if isinstance(data, dict):
                    offers = data.get("offers", data)
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    p = offers.get("price")
                    if p:
                        avail = offers.get("availability", "")
                        in_stock = "InStock" in avail
                        return float(p), in_stock
            except (json.JSONDecodeError, ValueError, KeyError, IndexError):
                continue

        # Last resort for direct pages: scan body
        body = page.inner_text("body")
        price = find_price(body)
        if price:
            oos = any(s in body.lower() for s in [
                "out of stock", "back order", "sold out",
            ])
            return price, not oos
        return None, False

    # ── Search results page ──
    # Memory Express search results show product cards.  We look for
    # common card container selectors used by their site.
    card_selectors = [
        ".c-shca-icon-item",           # shopping cart add item cards
        '[class*="product-list"] [class*="item"]',
        ".c-shca-add-product-button",  # known ME class near product links
        '[class*="search-result"]',
        ".productResult",
    ]

    # Try to find individual product cards
    cards = []
    for cs in card_selectors:
        cards = page.query_selector_all(cs)
        if cards:
            break

    sku_upper = sku.upper()

    if cards:
        for card in cards:
            # Walk up to the parent container to get the full product card
            # The button class is nested; get the ancestor row/card
            container = card.evaluate_handle(
                """el => {
                    // Walk up to find a reasonable container
                    let node = el;
                    for (let i = 0; i < 6; i++) {
                        if (node.parentElement) node = node.parentElement;
                        // Stop at a div that looks like a product card
                        let cls = node.className || '';
                        if (cls.includes('product') || cls.includes('item')
                            || cls.includes('result') || cls.includes('row')
                            || node.querySelector('[class*="price"]'))
                            break;
                    }
                    return node;
                }"""
            ).as_element()
            if not container:
                container = card

            card_text = container.inner_text()
            card_text_upper = card_text.upper()

            # Filter by SKU in card text
            if sku_upper and sku_upper not in card_text_upper:
                capacity_match = re.search(r"ST(\d+)000", sku_upper)
                capacity_tb = capacity_match.group(1) + "TB" if capacity_match else ""
                if not (capacity_tb and capacity_tb in card_text_upper
                        and "IRONWOLF" in card_text_upper):
                    continue

            # Extract price from this card
            price = find_price(card_text)
            if price:
                card_lower = card_text.lower()
                oos = any(s in card_lower for s in [
                    "out of stock", "back order", "sold out",
                ])
                return price, not oos

    # Fallback: scan the full page but only if SKU appears on the page
    body = page.inner_text("body")
    if sku_upper and sku_upper in body.upper():
        price = find_price(body)
        if price:
            oos = any(s in body.lower() for s in [
                "out of stock", "back order", "sold out",
            ])
            return price, not oos

    return None, False


def extract_newegg(page, sku: str = "") -> tuple[Optional[float], bool]:
    """Newegg.ca product/search page."""
    selectors = [
        "li.price-current",
        ".price-current strong",
        ".price-current",
        ".item-action .price-current",
    ]
    for sel in selectors:
        el = page.query_selector(sel)
        if el:
            text = el.inner_text().strip()
            price = find_price("$" + text if "$" not in text else text)
            if price:
                body = page.inner_text("body").lower()
                oos = "out of stock" in body or "sold out" in body
                return price, not oos

    body = page.inner_text("body")
    price = find_price(body)
    if price:
        oos = "out of stock" in body.lower() or "sold out" in body.lower()
        return price, not oos
    return None, False


def extract_bestbuy(product_ids_str: str, sku: str = "") -> tuple[Optional[float], bool, str]:
    """Best Buy Canada — checks multiple listings via JSON API.

    The url field for Best Buy stores contains comma-separated product IDs.
    We check all listings and return the lowest price.
    """
    best_price = None
    best_in_stock = False
    best_url = ""

    for product_id in product_ids_str.split(","):
        product_id = product_id.strip()
        api_url = f"https://www.bestbuy.ca/api/v2/json/product/{product_id}"
        try:
            req = urllib.request.Request(api_url, headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())

            price = data.get("salePrice") or data.get("regularPrice")
            if not price or not (50 < price < 5000):
                continue

            avail = data.get("availability", {})
            in_stock = avail.get("onlineAvailability") not in (
                "SoldOut", "NotAvailable", None
            )

            # Prefer in-stock listings; among same stock status, pick lowest price
            if best_price is None or (in_stock and not best_in_stock) or \
               (in_stock == best_in_stock and price < best_price):
                best_price = price
                best_in_stock = in_stock
                best_url = f"https://www.bestbuy.ca/en-ca/product/{product_id}"
        except Exception:
            continue

    if best_price:
        return float(best_price), best_in_stock, best_url
    return None, False, ""


def extract_cdw(page, sku: str = "") -> tuple[Optional[float], bool]:
    """CDW Canada product page."""
    # Try JSON-LD first
    scripts = page.query_selector_all('script[type="application/ld+json"]')
    for s in scripts:
        try:
            data = json.loads(s.inner_text())
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                offers = item.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                p = offers.get("price")
                avail = offers.get("availability", "")
                if p:
                    in_stock = "InStock" in avail
                    return float(p), in_stock
        except (json.JSONDecodeError, ValueError, KeyError, IndexError):
            continue

    # CSS selectors
    selectors = [
        '[class*="price-current"]',
        '[class*="product-price"]',
        '[data-testid="price"]',
        ".price-type-price",
        ".price",
    ]
    for sel in selectors:
        el = page.query_selector(sel)
        if el:
            price = find_price(el.inner_text())
            if price:
                body = page.inner_text("body").lower()
                oos = any(s in body for s in [
                    "out of stock", "unavailable", "sold out",
                ])
                return price, not oos

    # Full page scan
    body = page.inner_text("body")
    price = find_price(body)
    if price:
        oos = any(s in body.lower() for s in ["out of stock", "unavailable", "sold out"])
        return price, not oos
    return None, False


def extract_walmart(page, sku: str = "") -> tuple[Optional[float], bool]:
    """Walmart Canada — extracts price from __NEXT_DATA__ JSON or page."""
    # Strategy 1: __NEXT_DATA__ JSON blob
    next_data = page.query_selector('script#__NEXT_DATA__')
    if next_data:
        try:
            data = json.loads(next_data.inner_text())
            # Navigate the nested structure for price info
            text = json.dumps(data)
            for key in ["currentPrice", "price", "minPrice"]:
                pattern = rf'"{key}"\s*:\s*([\d.]+)'
                m = re.search(pattern, text)
                if m:
                    price = float(m.group(1))
                    if 50 < price < 5000:
                        oos = '"OUT_OF_STOCK"' in text or '"NotAvailable"' in text
                        return price, not oos
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 2: JSON-LD
    scripts = page.query_selector_all('script[type="application/ld+json"]')
    for s in scripts:
        try:
            data = json.loads(s.inner_text())
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                offers = item.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                p = offers.get("price")
                avail = offers.get("availability", "")
                if p:
                    in_stock = "InStock" in avail
                    return float(p), in_stock
        except (json.JSONDecodeError, ValueError, KeyError, IndexError):
            continue

    # Strategy 3: CSS selectors
    selectors = [
        '[data-testid="price-wrap"] [itemprop="price"]',
        '[itemprop="price"]',
        '[data-automation="buybox-price"]',
        '[class*="price-characteristic"]',
    ]
    for sel in selectors:
        el = page.query_selector(sel)
        if el:
            price = find_price(el.inner_text())
            if not price:
                # Walmart sometimes puts price in content attribute
                content = el.get_attribute("content")
                if content:
                    try:
                        price = float(content)
                    except ValueError:
                        pass
            if price and 50 < price < 5000:
                body = page.inner_text("body").lower()
                oos = any(s in body for s in [
                    "out of stock", "not available", "sold out",
                ])
                return price, not oos

    # Strategy 4: Full page scan
    body = page.inner_text("body")
    price = find_price(body)
    if price:
        oos = any(s in body.lower() for s in ["out of stock", "not available", "sold out"])
        return price, not oos
    return None, False


EXTRACTORS = {
    "Amazon.ca":         extract_amazon,
    "Canada Computers":  extract_canadacomputers,
    "Memory Express":    extract_memoryexpress,
    "Newegg.ca":         extract_newegg,
    "Best Buy":          extract_bestbuy,
    "CDW Canada":        extract_cdw,
}


# ───────────────────────────────────────────────────────────────────
# Main check cycle
# ───────────────────────────────────────────────────────────────────

def check_all(browser):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.info(f"{'═'*55}")
    log.info(f"  Price check at {ts}")
    log.info(f"{'═'*55}")

    alerts = []
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        locale="en-CA",
        timezone_id="America/Toronto",
    )
    page = context.new_page()

    for drive in DRIVES:
        log.info(f"\n  🔍 {drive['name']}  (target CA${drive['target_min']:.2f} – ${drive['target_max']:.2f})")
        log.info(f"  {'─'*45}")

        for store_name, url in drive["stores"].items():
            extractor = EXTRACTORS.get(store_name)
            if not extractor:
                continue

            try:
                # Best Buy: API-only, no browser needed
                if store_name == "Best Buy":
                    price, in_stock, url = extractor(url, sku=drive["sku"])
                # Memory Express: fresh browser context per request to
                # avoid Cloudflare tracking and blocking repeat visits
                elif store_name == "Memory Express":
                    me_ctx = browser.new_context(
                        user_agent=(
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            f"Chrome/{random.randint(120, 130)}.0.0.0 Safari/537.36"
                        ),
                        locale="en-CA",
                        timezone_id="America/Toronto",
                        viewport={"width": random.randint(1200, 1920), "height": random.randint(800, 1080)},
                    )
                    me_page = me_ctx.new_page()
                    me_page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    me_page.wait_for_timeout(3000)
                    price, in_stock = extractor(me_page, sku=drive["sku"])
                    me_ctx.close()
                else:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(3000)
                    price, in_stock = extractor(page, sku=drive["sku"])
            except Exception as e:
                log.warning(f"    {store_name:22s}  ❌  Error: {e}")
                log_csv(ts, drive, store_name, None, False, False, url)
                continue

            if price is None:
                log.info(f"    {store_name:22s}  —   price not found")
                log_csv(ts, drive, store_name, None, False, False, url)
                continue

            meets = drive["target_min"] <= price <= drive["target_max"]
            stock_icon = "✅" if in_stock else "⛔"
            price_icon = "🔥" if meets else "  "
            per_tb = price / drive["capacity"]

            log.info(
                f"    {store_name:22s}  "
                f"CA${price:>7.2f}  (${per_tb:.2f}/TB)  "
                f"{stock_icon} {'in stock' if in_stock else 'OOS'}  "
                f"{price_icon}"
            )

            log_csv(ts, drive, store_name, price, in_stock, meets, url)

            if meets and in_stock:
                alerts.append({
                    "drive": drive["name"],
                    "store": store_name,
                    "price": price,
                    "per_tb": per_tb,
                    "target_min": drive["target_min"],
                    "target_max": drive["target_max"],
                    "url": url,
                })

    context.close()

    # ── Send Telegram alerts ──
    if alerts:
        lines = ["🔥 <b>NAS Drive Price Alert!</b>\n"]
        for a in alerts:
            lines.append(
                f"<b>{a['drive']}</b>\n"
                f"  Store: {a['store']}\n"
                f"  Price: <b>CA${a['price']:.2f}</b>  "
                f"(${a['per_tb']:.2f}/TB)\n"
                f"  Target: CA${a['target_min']:.2f} – ${a['target_max']:.2f}\n"
                f"  <a href=\"{a['url']}\">🛒 Buy now</a>\n"
            )
        msg = "\n".join(lines)
        log.info(f"\n  📬 Sending {len(alerts)} alert(s) to Telegram...")
        telegram_send(msg)
    else:
        log.info("\n  No deals found this round.")


# ───────────────────────────────────────────────────────────────────
# Entry point
# ───────────────────────────────────────────────────────────────────

def main():
    print(r"""
    ╔════════════════════════════════════════════════════════════╗
    ║  Seagate IronWolf Pro — HDD Price Tracker  🇨🇦             ║
    ║  Amazon · CC · MemEx · Newegg · Best Buy · CDW            ║
    ║  Telegram alerts when price is in range & in stock         ║
    ╚════════════════════════════════════════════════════════════╝
    """)

    if "YOUR_" in TELEGRAM_BOT_TOKEN:
        log.warning("⚠  TELEGRAM_BOT_TOKEN not set — alerts will be skipped")
        log.warning("   Set it in the script or via env var\n")

    init_csv()
    once = "--once" in sys.argv

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        log.info("Launching headless browser...")
        browser = pw.chromium.launch(headless=True, channel="chrome")

        while True:
            try:
                check_all(browser)
            except Exception as e:
                log.error(f"  Unexpected error: {e}", exc_info=True)

            if once:
                log.info("  Single check complete (--once). Exiting.")
                break

            log.info(
                f"\n  ⏰ Next check in {CHECK_INTERVAL_SECONDS // 60} minutes  "
                f"(Ctrl+C to stop)\n"
            )
            try:
                time.sleep(CHECK_INTERVAL_SECONDS)
            except KeyboardInterrupt:
                log.info("\n  Stopped. Goodbye!")
                break

        try:
            browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
