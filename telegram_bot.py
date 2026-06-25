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
        "  Crypto  — BTC, ETH, SOL, ADA, XRP, NEAR\n"
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


def _fear_greed_label(value: int) -> str:
    if value <= 24: return "Extreme Fear 😱"
    if value <= 44: return "Fear 😨"
    if value <= 55: return "Neutral 😐"
    if value <= 75: return "Greed 🤑"
    return "Extreme Greed 🚀"


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


def _entry_quality(score: int, n_signals: int, rsi: float, direction: str) -> str:
    """Returns entry recommendation label."""
    rsi_ok = True
    if rsi == rsi:
        if direction == "bull" and rsi > 65:
            rsi_ok = False   # already extended, chasing
        if direction == "bear" and rsi < 35:
            rsi_ok = False
    if score >= 65 and n_signals >= 3 and rsi_ok:
        return "🟢 ENTER NOW"
    if score >= 55 and n_signals >= 2:
        return "🟡 WAIT FOR CONFIRMATION"
    return "⚪ WATCH ONLY"


def format_signal_card(
    display:    str,
    market:     str,
    timeframe:  str,
    direction:  str,
    score:      int,
    signals:    list[str],
    indicators: dict,
    on_demand:  bool = False,
) -> str:
    price      = indicators.get("close",      float("nan"))
    rsi        = indicators.get("rsi",        float("nan"))
    ema_s      = indicators.get("ema_short",  float("nan"))
    ema_l      = indicators.get("ema_long",   float("nan"))
    atr        = indicators.get("atr",        float("nan"))
    fear_greed = indicators.get("fear_greed", None)

    def fmt(v: float, prec: int = 4) -> str:
        if v != v: return "N/A"
        if abs(v) >= 1000: return f"{v:,.2f}"
        return f"{v:.{prec}g}"

    def pct(v: float) -> str:
        return f"{v:.1f}%"

    arrow      = "📈" if direction == "bull" else "📉"
    dir_word   = "BULLISH" if direction == "bull" else "BEARISH"
    title      = f"{arrow} {'ON-DEMAND SCAN' if on_demand else dir_word + ' SETUP'}"
    strength   = _strength_label(score, direction)
    bar        = _score_bar(score)
    bullet_sym = "🟢" if direction == "bull" else "🔴"
    bullets    = "\n".join(f"  {bullet_sym} {html.escape(s)}" for s in signals) if signals else "  — none"
    now        = datetime.now(timezone.utc).strftime("%d %b %Y · %H:%M UTC")
    entry_rec  = _entry_quality(score, len(signals), rsi, direction)
    rsi_ctx    = _rsi_label(rsi)
    ema_ctx    = _ema_label(price, ema_s, ema_l)

    # Trade plan section (only when ATR is available)
    if atr == atr and atr > 0 and price == price:
        from positions import compute_levels
        lvls = compute_levels(price, atr, direction)
        sym_upper = display.split("/")[0]
        trade_cmd = f"/trade {sym_upper} {direction} {fmt(price)}"
        trade_section = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>TRADE PLAN</b>\n"
            f"  Entry zone:  <code>{fmt(price)}</code>\n"
            f"  Stop loss:   <code>{fmt(lvls['stop'])}</code>  (–{pct(lvls['risk_pct'])})\n"
            f"  Target 1:    <code>{fmt(lvls['tp1'])}</code>  (+{pct(lvls['tp1_pct'])})  → take 33%\n"
            f"  Target 2:    <code>{fmt(lvls['tp2'])}</code>  (+{pct(lvls['tp2_pct'])})  → take 33%\n"
            f"  Target 3:    <code>{fmt(lvls['tp3'])}</code>  (+{pct(lvls['tp3_pct'])})  → take 34%\n\n"
            f"  To track: <code>{trade_cmd}</code>\n"
        )
    else:
        trade_section = ""

    if fear_greed is not None:
        fg_label = _fear_greed_label(fear_greed)
        fg_line  = f"\n  Sentiment (F&amp;G): {fear_greed}/100 — {fg_label}"
    else:
        fg_line = ""

    return (
        f"<b>{title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{display}</b>  ·  {market}  ·  {timeframe}\n"
        f"Price  <b>{fmt(price)}</b>  ·  {now}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>SIGNAL STRENGTH:  {score} / 100</b>\n"
        f"<code>{bar}</code>\n"
        f"<b>{strength}</b>\n\n"
        f"<b>Entry signal:  {entry_rec}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>WHY THIS FIRED:</b>\n"
        f"{bullets}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>KEY NUMBERS:</b>\n"
        f"  Momentum (RSI):  {fmt(rsi)}  —  {rsi_ctx}\n"
        f"  {ema_ctx}{fg_line}\n\n"
        f"{trade_section}"
        f"<i>Not financial advice. Always do your own research.</i>"
    )


def format_tp_alert(symbol: str, direction: str, tp_num: str, entry: float, tp_price: float, current: float, remaining: list[str]) -> str:
    arrow  = "📈" if direction == "bull" else "📉"
    pnl    = (current - entry) / entry * 100 if direction == "bull" else (entry - current) / entry * 100
    remain = "  " + "\n  ".join(remaining) if remaining else "  All targets hit!"

    def fmt(v: float) -> str:
        if abs(v) >= 1000: return f"{v:,.2f}"
        return f"{v:.4g}"

    return (
        f"🎯 <b>TARGET {tp_num.upper()} HIT — {symbol} ({direction.upper()})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Entry:    <code>{fmt(entry)}</code>\n"
        f"Target:   <code>{fmt(tp_price)}</code> ✅\n"
        f"Current:  <code>{fmt(current)}</code>\n"
        f"P&amp;L:      <b>+{pnl:.1f}%</b>\n\n"
        f"Remaining targets:\n{remain}\n\n"
        f"<i>Consider moving stop to breakeven.</i>"
    )


def format_sl_alert(symbol: str, direction: str, entry: float, stop: float, current: float) -> str:
    pnl = (current - entry) / entry * 100 if direction == "bull" else (entry - current) / entry * 100

    def fmt(v: float) -> str:
        if abs(v) >= 1000: return f"{v:,.2f}"
        return f"{v:.4g}"

    return (
        f"🛑 <b>STOP LOSS HIT — {symbol} ({direction.upper()})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Entry:    <code>{fmt(entry)}</code>\n"
        f"Stop:     <code>{fmt(stop)}</code> ⚠️\n"
        f"Current:  <code>{fmt(current)}</code>\n"
        f"Loss:     <b>{pnl:.1f}%</b>\n\n"
        f"<i>Position closed. Take the loss and move on.</i>"
    )


def format_positions(open_positions: dict) -> str:
    if not open_positions:
        return "No open positions.\n\nUse <code>/trade SYMBOL bull 105.21</code> to log a trade."

    lines = ["<b>Open Positions</b>\n━━━━━━━━━━━━━━━━━━━━"]
    tp_take = {"tp1": "33%", "tp2": "33%", "tp3": "34%"}

    for sym, pos in open_positions.items():
        direction = pos["direction"].upper()
        arrow     = "📈" if pos["direction"] == "bull" else "📉"
        leverage  = pos.get("leverage", 1.0)
        size_pct  = pos.get("size_pct")

        def fmt(v: float) -> str:
            if abs(v) >= 1000: return f"{v:,.2f}"
            return f"{v:.4g}"

        sizing = ""
        if leverage != 1.0 or size_pct is not None:
            parts = []
            if leverage != 1.0: parts.append(f"{leverage:g}× leverage")
            if size_pct is not None: parts.append(f"{size_pct:g}% portfolio")
            sizing = "  " + "  ·  ".join(parts) + "\n"

        tp_status = ""
        for tp in ("tp1", "tp2", "tp3"):
            hit   = "✅" if pos.get(f"{tp}_hit") else "⏳"
            take  = tp_take[tp]
            tp_status += f"  {hit} {tp.upper()}: {fmt(pos[tp])}  → take {take}\n"

        lines.append(
            f"\n{arrow} <b>{sym}</b>  ({direction})\n"
            f"  Entry: {fmt(pos['entry'])}   Stop: {fmt(pos['stop'])}\n"
            f"{sizing}"
            f"{tp_status}"
            f"  /close {sym}"
        )

    return "\n".join(lines)


def format_manual_close(symbol: str, summary: dict) -> str:
    direction   = summary["direction"].upper()
    arrow       = "📈" if summary["direction"] == "bull" else "📉"
    entry       = summary["entry"]
    close_price = summary.get("close_price")
    pnl         = summary.get("pnl_pct")
    pnl_lev     = summary.get("pnl_leveraged_pct")
    leverage    = summary.get("leverage", 1.0)
    size_pct    = summary.get("size_pct")

    def fmt(v: float) -> str:
        if abs(v) >= 1000: return f"{v:,.2f}"
        return f"{v:.4g}"

    lines = [
        f"✅ <b>Position closed: {html.escape(symbol)} ({direction})</b>",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"Entry:  <code>{fmt(entry)}</code>",
    ]

    if close_price is not None:
        lines.append(f"Exit:   <code>{fmt(close_price)}</code>")

    if pnl is not None:
        sign  = "+" if pnl >= 0 else ""
        emoji = "🟢" if pnl >= 0 else "🔴"
        lines.append(f"P&amp;L:   {emoji} <b>{sign}{pnl:.2f}%</b> (raw)")
        if leverage != 1.0 and pnl_lev is not None:
            lines.append(f"        {emoji} <b>{sign}{pnl_lev:.2f}%</b> at {leverage:g}× leverage")
        if size_pct is not None and pnl is not None:
            portfolio_impact = pnl * (size_pct / 100)
            if leverage != 1.0:
                portfolio_impact *= leverage
            sign2 = "+" if portfolio_impact >= 0 else ""
            lines.append(f"Portfolio impact: <b>{sign2}{portfolio_impact:.2f}%</b> ({size_pct:g}% size)")

    return "\n".join(lines)
