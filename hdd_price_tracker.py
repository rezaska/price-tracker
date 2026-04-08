#!/usr/bin/env python3
"""
Seagate IronWolf Pro — Canadian Price Tracker with Telegram Alerts
===================================================================

Checks prices every 5 minutes across 5 Canadian stores using a real
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

# Drives to track — only IronWolf Pro, with your target prices (CAD)
DRIVES = [
    {
        "name":       "IronWolf Pro 12TB",
        "sku":        "ST12000NT001",
        "capacity":   12,
        "target_min": 350.00,
        "target_max": 480.00,
        "stores": {
            "Amazon.ca": "https://www.amazon.ca/dp/B0B94KSFTH",
            "Canada Computers": "https://www.canadacomputers.com/en/desktop-internal-hard-drives/238856/seagate-ironwolf-pro-12tb-hard-drive-3-5-internal-sata-sata-600-st12000nt001.html",
            "Memory Express": "https://www.memoryexpress.com/Products/MX00126780",
            "Newegg.ca": "https://www.newegg.ca/p/pl?d=ST12000NT001",
            "Best Buy": "https://www.bestbuy.ca/en-ca/product/seagate-ironwolf-pro-12tb-3-5-7200rpm-sata-desktop-internal-hard-drive-st12000ntz01/19186375",
        },
    },
    {
        "name":       "IronWolf Pro 16TB",
        "sku":        "ST16000NT001",
        "capacity":   16,
        "target_min": 550.00,
        "target_max": 600.00,
        "stores": {
            "Amazon.ca": "https://www.amazon.ca/dp/B0B94NFYWX",
            "Canada Computers": "https://www.canadacomputers.com/en/desktop-internal-hard-drives/235732/seagate-ironwolf-pro16tb-nas-hard-drive-st16000nt001.html",
            "Memory Express": "https://www.memoryexpress.com/Products/MX00124956",
            "Newegg.ca": "https://www.newegg.ca/p/pl?d=ST16000NT001",
            "Best Buy": "https://www.bestbuy.ca/en-ca/product/seagate-ironwolf-pro-16tb-3-5-7200rpm-sata-desktop-internal-hard-drive-st16000ntz01/19186376",
        },
    },
    {
        "name":       "IronWolf Pro 20TB",
        "sku":        "ST20000NT001",
        "capacity":   20,
        "target_min": 680.00,
        "target_max": 740.00,
        "stores": {
            "Amazon.ca": "https://www.amazon.ca/dp/B0B94MF4LP",
            "Canada Computers": "https://www.canadacomputers.com/en/desktop-internal-hard-drives/236572/seagate-ironwolf-pro-20tb-hard-drive-3-5-internal-sata-st20000nt001.html",
            "Memory Express": "https://www.memoryexpress.com/Products/MX00124968",
            "Newegg.ca": "https://www.newegg.ca/p/pl?d=ST20000NT001",
            "Best Buy": "https://www.bestbuy.ca/en-ca/product/seagate-ironwolf-pro-20tb-3-5-7200rpm-sata-desktop-internal-hard-drive-st20000ntz01/19186377",
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

    # ── Direct product page ──
    if "/Products/MX" in url:
        # Wait for price elements to render (ME uses JS-loaded pricing)
        price_wait_selectors = [
            ".GrandTotal",
            ".c-capr-pricing__grand-total",
            '[class*="GrandTotal"]',
        ]
        for ws in price_wait_selectors:
            try:
                page.wait_for_selector(ws, timeout=5000)
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


def extract_bestbuy(page, sku: str = "") -> tuple[Optional[float], bool]:
    """Best Buy Canada — uses public JSON API instead of scraping.

    Best Buy blocks headless browsers, but their JSON API at
    /api/v2/json/product/{id} returns pricing and stock data directly.
    The product ID is the last segment of the product page URL.
    """
    # Extract product ID from the page URL (last path segment)
    product_id = page.url.rstrip("/").split("/")[-1]

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
            return None, False

        avail = data.get("availability", {})
        in_stock = avail.get("onlineAvailability") not in (
            "SoldOut", "NotAvailable", None
        )
        return float(price), in_stock
    except Exception as e:
        log.warning(f"    Best Buy API error: {e}")
        return None, False


EXTRACTORS = {
    "Amazon.ca":         extract_amazon,
    "Canada Computers":  extract_canadacomputers,
    "Memory Express":    extract_memoryexpress,
    "Newegg.ca":         extract_newegg,
    "Best Buy":          extract_bestbuy,
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
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)   # let JS render prices
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
    ╔═══════════════════════════════════════════════════╗
    ║  Seagate IronWolf Pro — Price Tracker  🇨🇦        ║
    ║  Amazon · CC · MemEx · Newegg · Best Buy         ║
    ║  Telegram alerts when price ≤ target & in stock   ║
    ╚═══════════════════════════════════════════════════╝
    """)

    if "YOUR_" in TELEGRAM_BOT_TOKEN:
        log.warning("⚠  TELEGRAM_BOT_TOKEN not set — alerts will be skipped")
        log.warning("   Set it in the script or via env var\n")

    init_csv()
    once = "--once" in sys.argv

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        log.info("Launching headless browser...")
        browser = pw.chromium.launch(headless=True)

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
