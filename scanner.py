"""Main entry point — scan loop with Telegram command handling."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import (
    CRYPTO_SYMBOLS, STOCK_SYMBOLS, FOREX_SYMBOLS,
    CRYPTO_TIMEFRAME, STOCK_TIMEFRAME, FOREX_TIMEFRAME,
    SCAN_INTERVAL, SCORE_THRESHOLD,
)
from fetchers import fetch_crypto, fetch_stock, fetch_forex
from signals import analyze
from telegram_bot import (
    get_credentials, send_message,
    format_signal_card, format_startup, format_status,
    poll_commands,
)

COMMAND_POLL_INTERVAL = 10


# ── Bot state ─────────────────────────────────────────────────────────────────

@dataclass
class BotState:
    scanning:       bool            = True
    next_scan_at:   float           = field(default_factory=time.time)
    last_scan_time: datetime | None = None
    session_start:  datetime        = field(default_factory=lambda: datetime.now(timezone.utc))
    session_alerts: int             = 0
    update_offset:  int             = 0


# ── Console helpers ───────────────────────────────────────────────────────────

def _row_emoji(bear: int, bull: int, threshold: int) -> str:
    if bear >= threshold: return "📉"
    if bull >= threshold: return "📈"
    if max(bear, bull) >= 35: return "🟡"
    return "🟢"


def _fmt_price(v: float) -> str:
    if v != v: return "N/A"
    if abs(v) >= 100: return f"{v:,.2f}"
    return f"{v:.5g}"


def _print_row(display: str, bear: int, bull: int, price: float, rsi: float) -> None:
    rsi_str = f"{rsi:.1f}" if rsi == rsi else "N/A"
    print(
        f"  {_row_emoji(bear, bull, SCORE_THRESHOLD)} {display:<12} │ "
        f"Bear {bear:>3}  Bull {bull:>3} │ "
        f"Price {_fmt_price(price):>12} │ "
        f"RSI {rsi_str}"
    )


# ── Scan one ticker ───────────────────────────────────────────────────────────

def _scan_one(
    sym_info:    dict,
    market:      str,
    timeframe:   str,
    fetch_fn,
    has_volume:  bool,
    token:       str,
    chat_id:     str,
    alerts_sent: list,
) -> None:
    display = sym_info["display"]
    try:
        df = fetch_fn(sym_info["symbol"])
        bear_score, bear_signals, bull_score, bull_signals, indicators = analyze(df, has_volume=has_volume)

        _print_row(
            display, bear_score, bull_score,
            indicators.get("close", float("nan")),
            indicators.get("rsi",   float("nan")),
        )

        for direction, score, signals in [("bear", bear_score, bear_signals), ("bull", bull_score, bull_signals)]:
            if score >= SCORE_THRESHOLD and signals:
                card = format_signal_card(display, market, timeframe, direction, score, signals, indicators)
                if send_message(token, chat_id, card):
                    print(f"    {'📉' if direction == 'bear' else '📈'}  {direction.upper()} alert sent — {score}/100")
                    alerts_sent.append(f"{display} ({direction})")

    except Exception as exc:
        print(f"  ⚠️  {display:<12} │ Error: {exc}")


# ── Full scan pass ────────────────────────────────────────────────────────────

def run_scan(token: str, chat_id: str, state: BotState) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    sep = "─" * 72
    print(f"\n{sep}")
    print(f"  🔍  Scan  ·  {now}")
    print(f"  {'SYMBOL':<14} {'BEAR':>4}  {'BULL':>4}   PRICE              RSI")
    print(sep)

    alerts_sent: list[str] = []

    print("\n  ── Crypto (Kraken) ────────────────────────────────────────────")
    for sym in CRYPTO_SYMBOLS:
        _scan_one(sym, "Crypto", CRYPTO_TIMEFRAME, fetch_crypto, True, token, chat_id, alerts_sent)

    print("\n  ── Stocks ─────────────────────────────────────────────────────")
    for sym in STOCK_SYMBOLS:
        _scan_one(sym, "Stock", STOCK_TIMEFRAME, fetch_stock, True, token, chat_id, alerts_sent)

    print("\n  ── Forex ──────────────────────────────────────────────────────")
    for sym in FOREX_SYMBOLS:
        _scan_one(sym, "Forex", FOREX_TIMEFRAME, fetch_forex, False, token, chat_id, alerts_sent)

    print(f"\n{sep}")
    if alerts_sent:
        print(f"  Alerts sent: {', '.join(alerts_sent)}")
    else:
        print(f"  No signals above {SCORE_THRESHOLD}/100 this pass.")
    print(sep)

    state.last_scan_time  = datetime.now(timezone.utc)
    state.next_scan_at    = time.time() + SCAN_INTERVAL
    state.session_alerts += len(alerts_sent)


# ── /score on-demand ──────────────────────────────────────────────────────────

_ALL_SYMBOLS = (
    [(s, "Crypto", CRYPTO_TIMEFRAME, fetch_crypto, True)  for s in CRYPTO_SYMBOLS] +
    [(s, "Stock",  STOCK_TIMEFRAME,  fetch_stock,  True)  for s in STOCK_SYMBOLS]  +
    [(s, "Forex",  FOREX_TIMEFRAME,  fetch_forex,  False) for s in FOREX_SYMBOLS]
)


def _find_symbol(query: str):
    q = query.upper().strip().replace("/", "").replace("=X", "")
    for sym_info, market, timeframe, fetch_fn, has_volume in _ALL_SYMBOLS:
        display_norm = sym_info["display"].upper().replace("/", "")
        symbol_norm  = sym_info["symbol"].upper().replace("/", "").split(":")[0].replace("=X", "")
        base         = display_norm.split("USDT")[0].split("USD")[0].rstrip("/")
        if q in (display_norm, symbol_norm, base):
            return sym_info, market, timeframe, fetch_fn, has_volume
    return None


def score_one(query: str, token: str, chat_id: str) -> None:
    result = _find_symbol(query)
    if not result:
        known = ", ".join(s["display"].split("/")[0] for s, *_ in _ALL_SYMBOLS)
        send_message(token, chat_id,
            f"Unknown symbol: <code>{html_escape(query)}</code>\n\nAvailable: <code>{known}</code>")
        return

    sym_info, market, timeframe, fetch_fn, has_volume = result
    display = sym_info["display"]
    send_message(token, chat_id, f"Scanning <b>{display}</b>...")

    try:
        df = fetch_fn(sym_info["symbol"])
        bear_score, bear_signals, bull_score, bull_signals, indicators = analyze(df, has_volume=has_volume)

        # Always send both cards for on-demand scans
        for direction, score, signals in [("bear", bear_score, bear_signals), ("bull", bull_score, bull_signals)]:
            card = format_signal_card(display, market, timeframe, direction, score, signals, indicators, on_demand=True)
            if send_message(token, chat_id, card):
                print(f"  /score {query.upper()} → {direction} {score}/100 sent")
            else:
                print(f"  /score {query.upper()} → {direction} send failed")

    except Exception as exc:
        import html as _html
        send_message(token, chat_id, f"Error fetching <b>{display}</b>: {_html.escape(str(exc))}")


def html_escape(s: str) -> str:
    import html as _html
    return _html.escape(s)


# ── Command handler ───────────────────────────────────────────────────────────

def handle_command(text: str, state: BotState, token: str, chat_id: str) -> None:
    parts = text.strip().split()
    cmd   = parts[0].lower().split("@")[0]
    args  = parts[1:]
    print(f"  Command: {text!r}")

    if cmd == "/stop":
        if state.scanning:
            state.scanning = False
            send_message(token, chat_id, "Scanning paused.\n\nSend /start to resume.")
        else:
            send_message(token, chat_id, "Already paused. Send /start to resume.")

    elif cmd == "/start":
        if not state.scanning:
            state.scanning     = True
            state.next_scan_at = time.time()
            send_message(token, chat_id, "Scanning resumed — running a scan now...")
        else:
            remaining = max(0, state.next_scan_at - time.time())
            send_message(token, chat_id, f"Already running. Next scan in {int(remaining)}s.")

    elif cmd == "/status":
        remaining = max(0, state.next_scan_at - time.time()) if state.scanning else 0
        send_message(token, chat_id, format_status(
            scanning=state.scanning, last_scan_time=state.last_scan_time,
            next_scan_in=remaining, session_alerts=state.session_alerts,
            session_start=state.session_start,
        ))

    elif cmd == "/score":
        if args:
            score_one(args[0], token, chat_id)
        else:
            send_message(token, chat_id,
                "Usage: /score SYMBOL\n\nExamples:\n  /score BTC\n  /score TSLA\n  /score EURUSD")

    elif cmd in ("/help", "/commands"):
        send_message(token, chat_id,
            "<b>Commands</b>\n\n"
            "/start — resume scanning\n"
            "/stop — pause scanning\n"
            "/status — show bot status\n"
            "/score SYMBOL — scan any symbol now\n\n"
            "Symbols: BTC ETH SOL ADA XRP\n"
            "         SPY QQQ AAPL TSLA NVDA\n"
            "         EURUSD GBPUSD USDJPY AUDUSD")
    else:
        send_message(token, chat_id, f"Unknown command. Send /help for the list.")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    print("╔══════════════════════════════════════════════════════╗")
    print("║         Signal Scanner Bot                          ║")
    print("╚══════════════════════════════════════════════════════╝")

    token, chat_id = get_credentials()
    state          = BotState()

    if send_message(token, chat_id, format_startup()):
        print("Startup message sent to Telegram")

    print(f"\n  Interval: {SCAN_INTERVAL}s  |  Threshold: {SCORE_THRESHOLD}/100  |  Ctrl+C to stop\n")

    _, state.update_offset = poll_commands(token, chat_id, state.update_offset)

    consecutive_errors = 0

    while True:
        try:
            commands, state.update_offset = poll_commands(token, chat_id, state.update_offset)
            for cmd_text in commands:
                handle_command(cmd_text, state, token, chat_id)

            if state.scanning and time.time() >= state.next_scan_at:
                run_scan(token, chat_id, state)
                consecutive_errors = 0
                print(f"\n  Next scan in {int(state.next_scan_at - time.time())}s\n")

            time.sleep(COMMAND_POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\n\nBot stopped.")
            sys.exit(0)

        except Exception as exc:
            consecutive_errors += 1
            wait = min(60 * consecutive_errors, 300)
            print(f"\nError #{consecutive_errors}: {exc}  — retrying in {wait}s")
            time.sleep(wait)


if __name__ == "__main__":
    main()
