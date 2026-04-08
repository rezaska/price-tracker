# Seagate IronWolf Pro — Canadian HDD Price Tracker

Tracks Seagate IronWolf Pro HDD prices across 5 Canadian retailers and sends Telegram alerts when prices fall within your target range.

## Drives Tracked

| Drive | SKU | Target Range (CAD) |
|-------|-----|--------------------|
| IronWolf Pro 12TB | ST12000NT001 | $350 – $480 |
| IronWolf Pro 16TB | ST16000NT001 | $550 – $600 |
| IronWolf Pro 20TB | ST20000NT001 | $680 – $740 |

## Stores

- Amazon.ca
- Canada Computers
- Memory Express
- Newegg.ca
- Best Buy Canada

## Setup

### 1. Install dependencies

```bash
pip install playwright
playwright install chromium
```

### 2. Create a Telegram bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the API token

### 3. Get your chat ID

1. Message your new bot (just say "hi")
2. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find `"chat":{"id": 123456789}` — that number is your chat ID

### 4. Configure

Set your token and chat ID as environment variables:

```bash
export TELEGRAM_BOT_TOKEN="your-token-here"
export TELEGRAM_CHAT_ID="your-chat-id"
```

Or edit them directly in `hdd_price_tracker.py`.

## Usage

```bash
# Check every 3 minutes (default)
python hdd_price_tracker.py

# Single check then exit
python hdd_price_tracker.py --once
```

## How It Works

- Uses Playwright (headless Chromium) to load store pages, capturing JavaScript-rendered prices
- Extracts prices using store-specific selectors with fallback strategies
- Logs all prices to `price_history.csv` for tracking over time
- Sends a Telegram alert when a drive is **in stock** and priced **within your target range**
