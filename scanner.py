"""Main entry point — scan loop with Telegram command handling."""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import (
    CRYPTO_SYMBOLS, STOCK_SYMBOLS, FOREX_SYMBOLS,
    CRYPTO_TIMEFRAME, STOCK_TIMEFRAME, FOREX_TIMEFRAME,
    SCAN_INTERVAL, SCORE_THRESHOLD,
)
from fetchers import fetch_crypto, fetch_stock, fetch_forex, fetch_fear_greed
from signals import analyze
from telegram_bot import (
    get_credentials, send_message,
    format_signal_card, format_startup, format_status,
    format_tp_alert, format_sl_alert, format_positions,
    format_manual_close, format_scan_summary,
    poll_commands,
)
import positions as pos_tracker

COMMAND_POLL_INTERVAL = 10

_ALERT_STATE_FILE = os.path.join(os.path.dirname(__file__), "alert_state.json")


def _load_active_signals() -> set[str]:
    try:
        with open(_ALERT_STATE_FILE) as f:
            return set(json.load(f))
    except (FileNotFoundError, ValueError):
        return set()


def _save_active_signals(active: set[str]) -> None:
    with open(_ALERT_STATE_FILE, "w") as f:
        json.dump(sorted(active), f)


# Tracks which (display, direction) signals are currently active above threshold.
# Persisted to disk so bot restarts don't re-fire the same alert.
_active_signals: set[str] = _load_active_signals()


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
    fear_greed:  int | None = None,
) -> None:
    display = sym_info["display"]
    try:
        df = fetch_fn(sym_info["symbol"])
        bear_score, bear_signals, bull_score, bull_signals, indicators = analyze(df, has_volume=has_volume, fear_greed=fear_greed)

        _print_row(
            display, bear_score, bull_score,
            indicators.get("close", float("nan")),
            indicators.get("rsi",   float("nan")),
        )

        for direction, score, signals in [("bear", bear_score, bear_signals), ("bull", bull_score, bull_signals)]:
            key = f"{display}_{direction}"
            if score >= SCORE_THRESHOLD and signals:
                if key in _active_signals:
                    print(f"    ⏭  {display} ({direction}) already active — skipping")
                    continue
                card = format_signal_card(display, market, timeframe, direction, score, signals, indicators)
                if send_message(token, chat_id, card):
                    _active_signals.add(key)
                    _save_active_signals(_active_signals)
                    print(f"    {'📉' if direction == 'bear' else '📈'}  {direction.upper()} alert sent — {score}/100")
                    alerts_sent.append(f"{display} ({direction})")
            else:
                if key in _active_signals:
                    _active_signals.discard(key)
                    _save_active_signals(_active_signals)

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

    fg_value: int | None = None
    try:
        fg_value, fg_label = fetch_fear_greed()
        print(f"\n  Fear & Greed: {fg_value}/100 — {fg_label}")
    except Exception as exc:
        print(f"  ⚠️  Fear & Greed fetch failed: {exc}")

    print("\n  ── Crypto (Kraken) ────────────────────────────────────────────")
    for sym in CRYPTO_SYMBOLS:
        _scan_one(sym, "Crypto", CRYPTO_TIMEFRAME, fetch_crypto, True, token, chat_id, alerts_sent, fear_greed=fg_value)

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

    # ── Check open positions for TP/SL hits ───────────────────────────────────
    _check_positions(token, chat_id)


# ── Position TP/SL monitor ────────────────────────────────────────────────────

def _check_positions(token: str, chat_id: str) -> None:
    open_pos = pos_tracker.get_open()
    if not open_pos:
        return

    for symbol, pos in open_pos.items():
        # Fetch current price
        result = _find_symbol(symbol)
        if not result:
            continue
        sym_info, _, _, fetch_fn, _ = result
        try:
            df = fetch_fn(sym_info["symbol"])
            current = float(df["close"].iloc[-1])
        except Exception:
            continue

        events = pos_tracker.check(symbol, current)
        for event in events:
            direction = pos["direction"]
            entry     = pos["entry"]

            if event == "stop":
                msg = format_sl_alert(symbol, direction, entry, pos["stop"], current)
                send_message(token, chat_id, msg)
                print(f"  🛑 {symbol} stop hit at {current}")

            elif event.startswith("tp"):
                remaining = [
                    f"TP{n[-1]}: {pos[n]:.4g}"
                    for n in ("tp1", "tp2", "tp3")
                    if not pos.get(f"{n}_hit") and n != event
                ]
                msg = format_tp_alert(symbol, direction, event, entry, pos[event], current, remaining)
                send_message(token, chat_id, msg)
                print(f"  🎯 {symbol} {event} hit at {current}")


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
        fg: int | None = None
        if market == "Crypto":
            try:
                fg, _ = fetch_fear_greed()
            except Exception:
                pass

        df = fetch_fn(sym_info["symbol"])
        bear_score, bear_signals, bull_score, bull_signals, indicators = analyze(df, has_volume=has_volume, fear_greed=fg)

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

    elif cmd == "/scan":
        send_message(token, chat_id, "Scanning all markets…")
        fg_value: int | None = None
        fg_label: str = ""
        try:
            fg_value, fg_label = fetch_fear_greed()
        except Exception:
            pass

        results = []
        for sym_info, market, timeframe, fetch_fn, has_volume in _ALL_SYMBOLS:
            display = sym_info["display"]
            try:
                df = fetch_fn(sym_info["symbol"])
                fg = fg_value if market == "Crypto" else None
                bear, bear_sigs, bull, bull_sigs, ind = analyze(df, has_volume=has_volume, fear_greed=fg)
                results.append({
                    "display":   display,
                    "market":    market,
                    "bear":      bear,
                    "bull":      bull,
                    "bear_sigs": bear_sigs,
                    "bull_sigs": bull_sigs,
                    "price":     ind.get("close", float("nan")),
                    "rsi":       ind.get("rsi",   float("nan")),
                })
            except Exception as exc:
                results.append({
                    "display": display, "market": market,
                    "bear": 0, "bull": 0,
                    "bear_sigs": [], "bull_sigs": [],
                    "price": float("nan"), "rsi": float("nan"),
                })

        fg_pair = (fg_value, fg_label) if fg_value is not None else None
        msg = format_scan_summary(results, fg_pair, SCORE_THRESHOLD)
        send_message(token, chat_id, msg)

    elif cmd == "/trade":
        # /trade SYMBOL bull|bear PRICE [LEVERAGEx] [SIZE%]
        if len(args) < 3:
            send_message(token, chat_id,
                "<b>Usage:</b> <code>/trade SYMBOL bull|bear PRICE [LEVERAGEx] [SIZE%]</code>\n\n"
                "Examples:\n"
                "  <code>/trade BTC bull 68500</code>\n"
                "  <code>/trade BTC bull 68500 5x 10%</code>")
            return
        sym_query, direction_raw = args[0], args[1].lower()
        if direction_raw not in ("bull", "bear"):
            send_message(token, chat_id, "Direction must be <code>bull</code> or <code>bear</code>.")
            return
        try:
            entry_price = float(args[2])
        except ValueError:
            send_message(token, chat_id, f"Invalid price: <code>{html_escape(args[2])}</code>")
            return

        leverage = 1.0
        size_pct = None
        for extra in args[3:]:
            low = extra.lower()
            if low.endswith("x"):
                try: leverage = float(low[:-1])
                except ValueError: pass
            elif low.endswith("%"):
                try: size_pct = float(low[:-1])
                except ValueError: pass

        result = _find_symbol(sym_query)
        if not result:
            send_message(token, chat_id, f"Unknown symbol: <code>{html_escape(sym_query)}</code>")
            return
        sym_info, _, _, fetch_fn, has_vol = result
        try:
            df      = fetch_fn(sym_info["symbol"])
            _, _, _, _, indicators = analyze(df, has_volume=has_vol)
            atr     = indicators.get("atr", float("nan"))
            if atr != atr or atr <= 0:
                send_message(token, chat_id, "Could not compute ATR for that symbol.")
                return
            levels  = pos_tracker.compute_levels(entry_price, atr, direction_raw)
            pos_tracker.add(sym_info["display"], direction_raw, entry_price, levels,
                            leverage=leverage, size_pct=size_pct)

            def fmt(v: float) -> str:
                return f"{v:,.2f}" if abs(v) >= 1000 else f"{v:.4g}"

            arrow       = "📈" if direction_raw == "bull" else "📉"
            sizing_line = ""
            if leverage != 1.0 or size_pct is not None:
                parts = []
                if leverage != 1.0: parts.append(f"Leverage: <b>{leverage:g}×</b>")
                if size_pct is not None: parts.append(f"Size: <b>{size_pct:g}% of portfolio</b>")
                sizing_line = "━━━━━━━━━━━━━━━━━━━━\n" + "   ".join(parts) + "\n"

            send_message(token, chat_id,
                f"{arrow} <b>Position logged: {html_escape(sym_info['display'])} ({direction_raw.upper()})</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Entry:    <code>{fmt(entry_price)}</code>\n"
                f"Stop:     <code>{fmt(levels['stop'])}</code>  (–{levels['risk_pct']:.1f}%)\n"
                f"Target 1: <code>{fmt(levels['tp1'])}</code>  (+{levels['tp1_pct']:.1f}%)  → take 33%\n"
                f"Target 2: <code>{fmt(levels['tp2'])}</code>  (+{levels['tp2_pct']:.1f}%)  → take 33%\n"
                f"Target 3: <code>{fmt(levels['tp3'])}</code>  (+{levels['tp3_pct']:.1f}%)  → take 34%\n"
                f"{sizing_line}\n"
                f"I'll alert you when price hits a target or your stop."
            )
        except Exception as exc:
            send_message(token, chat_id, f"Error logging position: {html_escape(str(exc))}")

    elif cmd == "/positions":
        open_pos = pos_tracker.get_open()
        send_message(token, chat_id, format_positions(open_pos))

    elif cmd == "/close":
        if not args:
            send_message(token, chat_id,
                "Usage: <code>/close SYMBOL [PRICE]</code>\n\n"
                "Examples:\n  <code>/close BTC</code>\n  <code>/close BTC 70500</code>")
            return
        sym = args[0].upper()
        close_price = None
        if len(args) >= 2:
            try:
                close_price = float(args[1])
            except ValueError:
                send_message(token, chat_id, f"Invalid price: <code>{html_escape(args[1])}</code>")
                return

        result = _find_symbol(sym)
        lookup = result[0]["display"] if result else sym
        summary = pos_tracker.close(lookup, close_price=close_price)
        if summary:
            _active_signals.discard(f"{lookup}_bull")
            _active_signals.discard(f"{lookup}_bear")
            _save_active_signals(_active_signals)
            send_message(token, chat_id, format_manual_close(lookup, summary))
        else:
            send_message(token, chat_id, f"No open position found for <code>{html_escape(sym)}</code>.")

    elif cmd in ("/help", "/commands"):
        send_message(token, chat_id,
            "<b>Commands</b>\n\n"
            "/start — resume scanning\n"
            "/stop — pause scanning\n"
            "/status — show bot status\n"
            "/scan — full snapshot of all markets right now\n"
            "/score SYMBOL — deep score for one symbol\n\n"
            "<b>Trade tracking:</b>\n"
            "/trade SYMBOL bull|bear PRICE [LEVERAGEx] [SIZE%]\n"
            "  e.g. /trade BTC bull 68500 5x 10%\n"
            "/positions — show open positions\n"
            "/close SYMBOL [PRICE] — close a position\n"
            "  e.g. /close BTC 70500\n\n"
            "Symbols: BTC ETH SOL ADA XRP NEAR\n"
            "         SPY QQQ AAPL TSLA NVDA HOOD\n"
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
