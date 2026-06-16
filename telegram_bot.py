"""Telegram bot integration: setup, credential storage, sending, and command polling."""

from __future__ import annotations

import html
import os
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

from config import SCORE_THRESHOLD, EMA_SHORT, EMA_LONG, RSI_LENGTH, SCAN_INTERVAL

ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")
API_BASE = "https://api.telegram.org/bot{token}/{method}"


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _tg_get(token: str, method: str, **params) -> dict:
    url = API_BASE.format(token=token, method=method)
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def _autodetect_chat_id(token: str) -> str | None:
    try:
        data = _tg_get(token, "getUpdates", limit=10, timeout=0)
        results = data.get("result", [])
        if results:
            msg = results[-1].get("message") or results[-1].get("channel_post")
            if msg:
                return str(msg["chat"]["id"])
    except Exception:
        pass
    return None


# ── Credential management ─────────────────────────────────────────────────────

def _setup_telegram() -> tuple[str, str]:
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║          Telegram Bot First-Run Setup                ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()
    print("STEP 1 — Create a bot with @BotFather")
    print("  1. Open Telegram and search for @BotFather")
    print("  2. Send:  /newbot")
    print("  3. Choose a name and username (must end in 'bot')")
    print("  4. Copy the token BotFather gives you")
    print()

    while True:
        token = input("Paste your bot token here: ").strip()
        if ":" in token and len(token) > 20:
            break
        print("  That doesn't look like a valid token. Try again.")

    bot_name = "the bot"
    try:
        me = _tg_get(token, "getMe")
        bot_name = me["result"].get("username", "unknown")
        print(f"\n  Token valid — Bot: @{bot_name}")
    except Exception as e:
        print(f"\n  Could not validate token: {e}")

    print()
    print("STEP 2 — Get your Chat ID")
    print(f"  1. Find your bot @{bot_name} on Telegram and send it any message")
    input("\n  [Press Enter after you've sent a message] ")

    chat_id: str | None = None
    for attempt in range(3):
        chat_id = _autodetect_chat_id(token)
        if chat_id:
            print(f"\n  Chat ID detected: {chat_id}")
            break
        print(f"  No messages found yet ({attempt + 1}/3), retrying...")
        time.sleep(3)

    if not chat_id:
        chat_id = input("\n  Enter your Chat ID manually: ").strip()

    with open(ENV_FILE, "a") as f:
        f.write(f"\nTELEGRAM_BOT_TOKEN={token}\n")
        f.write(f"TELEGRAM_CHAT_ID={chat_id}\n")
    print(f"\n  Credentials saved to .env\n")
    return token, chat_id


def get_credentials() -> tuple[str, str]:
    load_dotenv(ENV_FILE)
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID",   "").strip()
    if not token or not chat_id:
        token, chat_id = _setup_telegram()
    return token, chat_id


# ── Message sending ───────────────────────────────────────────────────────────

def send_message(token: str, chat_id: str, text: str, retries: int = 3) -> bool:
    url = API_BASE.format(token=token, method="sendMessage")
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, timeout=15)
            if r.status_code == 429:
                wait = int(r.json().get("parameters", {}).get("retry_after", 5))
                print(f"  [Telegram] Rate limited — waiting {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return True
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"  [Telegram] Send failed: {e}")
    return False


# ── Command polling ───────────────────────────────────────────────────────────

def poll_commands(token: str, chat_id: str, offset: int) -> tuple[list[str], int]:
    try:
        data = _tg_get(token, "getUpdates", offset=offset, limit=20, timeout=0)
        results = data.get("result", [])
        commands: list[str] = []
        new_offset = offset
        for update in results:
            new_offset = update["update_id"] + 1
            msg = update.get("message")
            if not msg:
                continue
            if str(msg["chat"]["id"]) != str(chat_id):
                continue
            text = msg.get("text", "").strip()
            if text:
                commands.append(text)
        return commands, new_offset
    except Exception:
        return [], offset


# ── Message formatters ────────────────────────────────────────────────────────

def format_startup(scan_interval: int = SCAN_INTERVAL, threshold: int = SCORE_THRESHOLD) -> str:
    now = datetime.now(timezone.utc).strftime("%d %b %Y · %H:%M UTC")
    return (
        "🤖 <b>Signal Scanner is LIVE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Started:    {now}\n"
        f"Scans:      every {scan_interval // 60} minutes\n"
        f"Alert at:   score 55 or above\n\n"
        "Markets watched:\n"
        "  Crypto  — BTC, ETH, SOL, ADA, XRP\n"
        "  Stocks  — SPY, QQQ, AAPL, TSLA, NVDA\n"
        "  Forex   — EUR/USD, GBP/USD, USD/JPY, AUD/USD\n\n"
        "Both bullish and bearish setups are tracked.\n\n"
        "Commands:\n"
        "  /stop         pause scanning\n"
        "  /start        resume scanning\n"
        "  /status       show bot status\n"
        "  /score BTC    scan any symbol on demand"
    )


def format_status(
    scanning: bool,
    last_scan_time: datetime | None,
    next_scan_in: float,
    session_alerts: int,
    session_start: datetime,
) -> str:
    status_str = "ACTIVE" if scanning else "PAUSED"
    last_str   = last_scan_time.strftime("%H:%M UTC") if last_scan_time else "not yet"
    next_str   = f"in {int(next_scan_in)}s" if scanning else "paused"
    uptime     = int((datetime.now(timezone.utc) - session_start).total_seconds())
    h, rem     = divmod(uptime, 3600)
    m          = rem // 60
    return (
        f"<b>Bot Status</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Scanner:    {'▶ ' + status_str if scanning else '⏸ ' + status_str}\n"
        f"Last scan:  {last_str}\n"
        f"Next scan:  {next_str}\n"
        f"Alerts sent:{session_alerts} this session\n"
        f"Uptime:     {h}h {m}m\n\n"
        f"/start  /stop  /status  /score SYMBOL"
    )


def _score_bar(score: int, width: int = 20) -> str:
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _strength_label(score: int, direction: str) -> str:
    word = "BULLISH" if direction == "bull" else "BEARISH"
    if score >= 90: return f"VERY STRONG {word}"
    if score >= 75: return f"STRONG {word}"
    if score >= 55: return f"MODERATE {word}"
    if score >= 35: return f"BUILDING PRESSURE"
    return "WEAK / NOISE"


def _rsi_label(rsi: float) -> str:
    if rsi != rsi: return ""
    if rsi >= 70: return "high — overbought zone"
    if rsi <= 30: return "low — oversold zone"
    return "normal range"


def _ema_label(price: float, ema_s: float, ema_l: float) -> str:
    if price != price or ema_s != ema_s: return ""
    above_s = price > ema_s
    above_l = price > ema_l if ema_l == ema_l else None
    lines = []
    lines.append(f"Price vs short trend: {'above' if above_s else 'below'} {'(bullish)' if above_s else '(bearish)'}")
    if above_l is not None:
        lines.append(f"Price vs long trend:  {'above' if above_l else 'below'} {'(bullish)' if above_l else '(bearish)'}")
    return "\n".join(lines)


def format_signal_card(
    display:    str,
    market:     str,
    timeframe:  str,
    direction:  str,          # "bull" or "bear"
    score:      int,
    signals:    list[str],
    indicators: dict,
    on_demand:  bool = False,
) -> str:
    price  = indicators.get("close",        float("nan"))
    rsi    = indicators.get("rsi",          float("nan"))
    ema_s  = indicators.get("ema_short",    float("nan"))
    ema_l  = indicators.get("ema_long",     float("nan"))

    def fmt(v: float) -> str:
        if v != v: return "N/A"
        if abs(v) >= 1000: return f"{v:,.2f}"
        return f"{v:.4g}"

    arrow      = "📈" if direction == "bull" else "📉"
    dir_word   = "BULLISH" if direction == "bull" else "BEARISH"
    title      = f"{arrow} {'ON-DEMAND SCAN' if on_demand else dir_word + ' SETUP DETECTED'}"
    strength   = _strength_label(score, direction)
    bar        = _score_bar(score)
    bullet_sym = "🟢" if direction == "bull" else "🔴"
    bullets    = "\n".join(f"  {bullet_sym} {html.escape(s)}" for s in signals) if signals else "  — none"
    now        = datetime.now(timezone.utc).strftime("%d %b %Y · %H:%M UTC")

    # Plain-English indicator context
    rsi_ctx = _rsi_label(rsi)
    ema_ctx = _ema_label(price, ema_s, ema_l)

    return (
        f"<b>{title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{display}</b>  ·  {market}  ·  {timeframe}\n"
        f"Price  <b>{fmt(price)}</b>  ·  {now}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>SIGNAL STRENGTH:  {score} / 100</b>\n"
        f"<code>{bar}</code>\n"
        f"<b>{strength}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>WHY THIS FIRED:</b>\n"
        f"{bullets}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>KEY NUMBERS:</b>\n"
        f"  Momentum (RSI):  {fmt(rsi)}  —  {rsi_ctx}\n"
        f"  {ema_ctx}\n\n"
        f"<i>Not financial advice. Always do your own research.</i>"
    )
