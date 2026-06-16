# Bearish Signal Scanner Bot

Scans crypto perpetuals (Bybit), stocks, and forex every 5 minutes for bearish setups and sends Telegram alerts when a signal score ≥ 55/100 is reached.

## Quick Start

### 1. Install dependencies

```bash
cd signal-scanner-bot
python -m venv venv && source venv/bin/activate   # optional but recommended
pip install -r requirements.txt
```

### 2. Create a Telegram bot (one-time)

You only need to do this once — credentials are saved to `.env` automatically.

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts to get a **bot token**
3. Run the scanner — it will guide you through the rest interactively

### 3. Run the bot

```bash
python scanner.py
```

On first run you'll be asked to paste your bot token and send a test message to the bot; the Chat ID is detected automatically. After that, the bot runs fully headless.

---

## Signal Scoring

Each ticker is scored 0–100 (capped). An alert fires when `score ≥ SCORE_THRESHOLD`.

| Signal | Points |
|--------|--------|
| RSI overbought rejection (RSI > 70 then drops) | 25 |
| MACD bearish crossover | 25 |
| Price broke below 20 EMA | 15 |
| 20 EMA crossed below 50 EMA | 15 |
| Bearish engulfing candle | 20 |
| High-volume red candle (≥ 2× avg volume) | 15 |
| Lower high pattern | 15 |

Max possible raw score = 130 → capped at 100.

---

## Configuration (`config.py`)

| Setting | Default | Description |
|---------|---------|-------------|
| `SCAN_INTERVAL` | `300` | Seconds between scans |
| `SCORE_THRESHOLD` | `55` | Minimum score to send an alert |
| `CRYPTO_TIMEFRAME` | `"1h"` | Candle interval for crypto (ccxt format) |
| `STOCK_TIMEFRAME` | `"1h"` | Candle interval for stocks (yfinance format) |
| `FOREX_TIMEFRAME` | `"1h"` | Candle interval for forex |
| `CANDLE_LIMIT` | `200` | Historical candles to fetch |
| `RSI_OVERBOUGHT` | `70` | RSI threshold for overbought |
| `VOLUME_MULTIPLIER` | `2.0` | Volume must exceed this × 20-period MA |

Add or remove symbols by editing the `CRYPTO_SYMBOLS`, `STOCK_SYMBOLS`, or `FOREX_SYMBOLS` lists in `config.py`.

---

## Project Structure

```
signal-scanner-bot/
├── scanner.py        Main entry point and scan loop
├── signals.py        Indicator computation and scoring logic
├── fetchers.py       Data fetching (Bybit / yfinance)
├── telegram_bot.py   Telegram setup, credential storage, and formatting
├── config.py         All configurable settings
├── .env              Telegram credentials (git-ignored, auto-created)
├── requirements.txt
└── README.md
```

---

## Troubleshooting

**No Telegram message received**
- Check `.env` has `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
- Make sure you sent a message *to* the bot before the Chat ID auto-detection step
- Visit `https://api.telegram.org/bot<TOKEN>/getMe` to verify your token

**Bybit errors / no crypto data**
- Bybit's public API is rate-limited; the bot uses `enableRateLimit=True` automatically
- The bot backs off exponentially on repeated errors

**yfinance returns empty data**
- For 1h intervals, yfinance provides up to ~730 days; 60 days is fetched by default
- Forex volume is always 0 — that's expected; the volume signal is skipped for FX

---

> ⚠️ **Disclaimer:** This bot is for educational and informational purposes only. It is not financial advice. Always do your own research before making any trading decisions.
