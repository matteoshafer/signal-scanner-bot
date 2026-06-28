#!/usr/bin/env python3
"""MCP server exposing Signal Scanner Bot tools to Claude Desktop."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

from config import (
    CRYPTO_SYMBOLS, STOCK_SYMBOLS, FOREX_SYMBOLS,
    CRYPTO_TIMEFRAME, STOCK_TIMEFRAME, FOREX_TIMEFRAME,
    SCORE_THRESHOLD,
)
from fetchers import fetch_crypto, fetch_stock, fetch_forex, fetch_fear_greed
from signals import analyze
import positions as pos_tracker
import signal_log

mcp = FastMCP("Signal Scanner")

_ALL_SYMBOLS = (
    [(s, "Crypto", CRYPTO_TIMEFRAME, fetch_crypto, True)  for s in CRYPTO_SYMBOLS] +
    [(s, "Stock",  STOCK_TIMEFRAME,  fetch_stock,  True)  for s in STOCK_SYMBOLS]  +
    [(s, "Forex",  FOREX_TIMEFRAME,  fetch_forex,  False) for s in FOREX_SYMBOLS]
)

# Separate alert-state file for the MCP server so it doesn't conflict with the Telegram bot.
_MCP_ALERT_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_alert_state.json")


def _load_active() -> set[str]:
    try:
        with open(_MCP_ALERT_STATE) as f:
            return set(json.load(f))
    except (FileNotFoundError, ValueError):
        return set()


def _save_active(active: set[str]) -> None:
    with open(_MCP_ALERT_STATE, "w") as f:
        json.dump(sorted(active), f)


def _find_symbol(query: str):
    q = query.upper().strip().replace("/", "").replace("=X", "")
    for sym_info, market, timeframe, fetch_fn, has_volume in _ALL_SYMBOLS:
        display_norm = sym_info["display"].upper().replace("/", "")
        symbol_norm  = sym_info["symbol"].upper().replace("/", "").split(":")[0].replace("=X", "")
        base         = display_norm.split("USDT")[0].split("USD")[0].rstrip("/")
        if q in (display_norm, symbol_norm, base):
            return sym_info, market, timeframe, fetch_fn, has_volume
    return None


def _fmt(v: float, prec: int = 4) -> str:
    if v != v:
        return "N/A"
    if abs(v) >= 1000:
        return f"{v:,.2f}"
    return f"{v:.{prec}g}"


def _strength_label(score: int, direction: str) -> str:
    word = "BULLISH" if direction == "bull" else "BEARISH"
    if score >= 90: return f"Very Strong {word}"
    if score >= 75: return f"Strong {word}"
    if score >= 55: return f"Moderate {word}"
    if score >= 35: return "Building Pressure"
    return "Weak / Noise"


@mcp.tool()
def scan_all() -> str:
    """Run a full scan of all crypto, stock, and forex symbols. Returns scores, highlights signals above the alert threshold, and automatically logs new signals for win-rate tracking."""
    lines:  list[str] = []
    active: set[str]  = _load_active()
    logged: list[str] = []

    try:
        fg_value, fg_label = fetch_fear_greed()
        lines.append(f"Fear & Greed: {fg_value}/100 — {fg_label}\n")
    except Exception as exc:
        fg_value = None
        lines.append(f"Fear & Greed: unavailable ({exc})\n")

    groups = [
        ("Crypto", CRYPTO_SYMBOLS, CRYPTO_TIMEFRAME, fetch_crypto, True,  fg_value),
        ("Stocks", STOCK_SYMBOLS,  STOCK_TIMEFRAME,  fetch_stock,  True,  None),
        ("Forex",  FOREX_SYMBOLS,  FOREX_TIMEFRAME,  fetch_forex,  False, None),
    ]

    for group_name, symbols, timeframe, fetch_fn, has_volume, fg in groups:
        lines.append(f"── {group_name} ({timeframe}) ──")
        for sym in symbols:
            display = sym["display"]
            try:
                df    = fetch_fn(sym["symbol"])
                bear, bear_sigs, bull, bull_sigs, ind = analyze(df, has_volume=has_volume, fear_greed=fg)
                price = ind.get("close", float("nan"))
                rsi   = ind.get("rsi",   float("nan"))
                atr   = ind.get("atr",   float("nan"))
                rsi_s = f"{rsi:.1f}" if rsi == rsi else "N/A"

                flag = ""
                for direction, score, sigs in [("bear", bear, bear_sigs), ("bull", bull, bull_sigs)]:
                    if score >= SCORE_THRESHOLD:
                        flag = f"  ⚠️  {direction.upper()} SIGNAL ({score}/100): " + "; ".join(sigs[:2])
                        key  = f"{display}_{direction}"
                        if key not in active and price == price and atr == atr and atr > 0:
                            lvls = pos_tracker.compute_levels(price, atr, direction)
                            signal_log.log_signal(
                                symbol=display, direction=direction, score=score,
                                entry_price=price, stop=lvls["stop"], tp1=lvls["tp1"],
                                risk_pct=lvls["risk_pct"], signals=sigs,
                            )
                            active.add(key)
                            logged.append(f"{display} ({direction})")
                    else:
                        active.discard(f"{display}_{direction}")

                lines.append(
                    f"  {display:<12}  Bear {bear:>3}/100  Bull {bull:>3}/100  "
                    f"Price {_fmt(price, 5):>12}  RSI {rsi_s}{flag}"
                )
            except Exception as exc:
                lines.append(f"  {display:<12}  Error: {exc}")
        lines.append("")

    _save_active(active)

    if logged:
        lines.append(f"📋 Logged {len(logged)} new signal(s) for tracking: {', '.join(logged)}")

    return "\n".join(lines)


@mcp.tool()
def score_symbol(symbol: str) -> str:
    """
    Get a detailed signal score and trade plan for a specific symbol.

    Args:
        symbol: Symbol to scan (e.g. BTC, ETH, TSLA, EURUSD, SPY)
    """
    result = _find_symbol(symbol)
    if not result:
        known = ", ".join(s["display"].split("/")[0] for s, *_ in _ALL_SYMBOLS)
        return f"Unknown symbol: {symbol}\n\nAvailable: {known}"

    sym_info, market, timeframe, fetch_fn, has_volume = result
    display = sym_info["display"]

    try:
        fg: int | None = None
        if market == "Crypto":
            try:
                fg, _ = fetch_fear_greed()
            except Exception:
                pass

        df = fetch_fn(sym_info["symbol"])
        bear, bear_sigs, bull, bull_sigs, ind = analyze(df, has_volume=has_volume, fear_greed=fg)

        price = ind.get("close", float("nan"))
        rsi   = ind.get("rsi",   float("nan"))
        ema_s = ind.get("ema_short", float("nan"))
        ema_l = ind.get("ema_long",  float("nan"))
        atr   = ind.get("atr",   float("nan"))

        lines = [
            f"{display}  ·  {market}  ·  {timeframe}",
            f"Price: {_fmt(price)}",
            "",
        ]

        for direction, score, sigs in [("bear", bear, bear_sigs), ("bull", bull, bull_sigs)]:
            label = _strength_label(score, direction)
            arrow = "📉" if direction == "bear" else "📈"
            lines.append(f"{arrow} {direction.upper()}: {score}/100 — {label}")
            for s in sigs:
                lines.append(f"   • {s}")
            if not sigs:
                lines.append("   • No signals")
            lines.append("")

        lines += [
            "Indicators:",
            f"  RSI (14):    {_fmt(rsi)}",
            f"  EMA 20:      {_fmt(ema_s)}",
            f"  EMA 50:      {_fmt(ema_l)}",
            f"  ATR:         {_fmt(atr)}",
        ]
        if fg is not None:
            lines.append(f"  Fear & Greed: {fg}/100")

        if atr == atr and atr > 0 and price == price:
            lines.append("")
            for direction in ("bull", "bear"):
                lvls = pos_tracker.compute_levels(price, atr, direction)
                lines.append(f"  {direction.upper()} trade plan (ATR-based):")
                lines.append(f"    Entry: {_fmt(price)}   Stop: {_fmt(lvls['stop'])} (–{lvls['risk_pct']:.1f}%)")
                lines.append(
                    f"    TP1: {_fmt(lvls['tp1'])} (+{lvls['tp1_pct']:.1f}%)  "
                    f"TP2: {_fmt(lvls['tp2'])} (+{lvls['tp2_pct']:.1f}%)  "
                    f"TP3: {_fmt(lvls['tp3'])} (+{lvls['tp3_pct']:.1f}%)"
                )

        return "\n".join(lines)

    except Exception as exc:
        return f"Error scanning {display}: {exc}"


@mcp.tool()
def get_positions() -> str:
    """Show all currently open trade positions with entry, stop, and take-profit levels."""
    open_pos = pos_tracker.get_open()
    if not open_pos:
        return "No open positions.\n\nUse log_trade() to record a new position."

    lines = ["Open Positions", "──────────────"]
    for sym, pos in open_pos.items():
        arrow    = "📈" if pos["direction"] == "bull" else "📉"
        leverage = pos.get("leverage", 1.0)
        size_pct = pos.get("size_pct")

        sizing = ""
        if leverage != 1.0 or size_pct is not None:
            parts = []
            if leverage != 1.0: parts.append(f"{leverage:g}× leverage")
            if size_pct:        parts.append(f"{size_pct:g}% portfolio")
            sizing = "  ·  " + "  ·  ".join(parts)

        lines.append(f"\n{arrow} {sym} ({pos['direction'].upper()}){sizing}")
        lines.append(f"  Entry: {_fmt(pos['entry'])}   Stop: {_fmt(pos['stop'])}")
        take_map = {"tp1": "33%", "tp2": "33%", "tp3": "34%"}
        for tp in ("tp1", "tp2", "tp3"):
            hit = "✅" if pos.get(f"{tp}_hit") else "⏳"
            lines.append(f"  {hit} {tp.upper()}: {_fmt(pos[tp])}  → take {take_map[tp]}")

    return "\n".join(lines)


@mcp.tool()
def log_trade(
    symbol: str,
    direction: str,
    entry_price: float,
    leverage: float = 1.0,
    size_pct: float | None = None,
) -> str:
    """
    Log a new trade position. Stop loss and take-profit levels are computed from ATR.

    Args:
        symbol: Symbol to trade (e.g. BTC, TSLA, EURUSD)
        direction: "bull" or "bear"
        entry_price: Entry price
        leverage: Leverage multiplier, default 1.0 (no leverage)
        size_pct: Position size as percentage of portfolio (optional)
    """
    if direction not in ("bull", "bear"):
        return "Direction must be 'bull' or 'bear'."

    result = _find_symbol(symbol)
    if not result:
        known = ", ".join(s["display"].split("/")[0] for s, *_ in _ALL_SYMBOLS)
        return f"Unknown symbol: {symbol}\n\nAvailable: {known}"

    sym_info, _, _, fetch_fn, has_vol = result
    try:
        df  = fetch_fn(sym_info["symbol"])
        _, _, _, _, ind = analyze(df, has_volume=has_vol)
        atr = ind.get("atr", float("nan"))
        if atr != atr or atr <= 0:
            return "Could not compute ATR for that symbol."

        levels = pos_tracker.compute_levels(entry_price, atr, direction)
        pos_tracker.add(sym_info["display"], direction, entry_price, levels,
                        leverage=leverage, size_pct=size_pct)

        arrow = "📈" if direction == "bull" else "📉"
        lines = [
            f"{arrow} Position logged: {sym_info['display']} ({direction.upper()})",
            f"  Entry: {_fmt(entry_price)}",
            f"  Stop:  {_fmt(levels['stop'])} (–{levels['risk_pct']:.1f}%)",
            f"  TP1:   {_fmt(levels['tp1'])} (+{levels['tp1_pct']:.1f}%)  → take 33%",
            f"  TP2:   {_fmt(levels['tp2'])} (+{levels['tp2_pct']:.1f}%)  → take 33%",
            f"  TP3:   {_fmt(levels['tp3'])} (+{levels['tp3_pct']:.1f}%)  → take 34%",
        ]
        if leverage != 1.0:
            lines.append(f"  Leverage: {leverage:g}×")
        if size_pct is not None:
            lines.append(f"  Size: {size_pct:g}% of portfolio")

        return "\n".join(lines)

    except Exception as exc:
        return f"Error logging position: {exc}"


@mcp.tool()
def close_position(symbol: str, close_price: float | None = None) -> str:
    """
    Close an open trade position and calculate P&L.

    Args:
        symbol: Symbol to close (e.g. BTC, TSLA)
        close_price: Exit price for P&L calculation (optional — omit if already closed at TP/SL)
    """
    result   = _find_symbol(symbol)
    lookup   = result[0]["display"] if result else symbol.upper()
    summary  = pos_tracker.close(lookup, close_price=close_price)

    if not summary:
        return f"No open position found for {symbol.upper()}."

    direction = summary["direction"].upper()
    arrow     = "📈" if summary["direction"] == "bull" else "📉"
    leverage  = summary.get("leverage", 1.0)
    size_pct  = summary.get("size_pct")
    pnl       = summary.get("pnl_pct")
    pnl_lev   = summary.get("pnl_leveraged_pct")
    cp        = summary.get("close_price")

    lines = [
        f"✅ Position closed: {lookup} ({direction})",
        f"  Entry: {_fmt(summary['entry'])}",
    ]
    if cp is not None:
        lines.append(f"  Exit:  {_fmt(cp)}")
    if pnl is not None:
        sign  = "+" if pnl >= 0 else ""
        emoji = "🟢" if pnl >= 0 else "🔴"
        lines.append(f"  P&L:   {emoji} {sign}{pnl:.2f}% (raw)")
        if leverage != 1.0 and pnl_lev is not None:
            lines.append(f"         {emoji} {sign}{pnl_lev:.2f}% at {leverage:g}× leverage")
        if size_pct and pnl is not None:
            impact = pnl * (size_pct / 100) * (leverage if leverage != 1.0 else 1.0)
            sign2  = "+" if impact >= 0 else ""
            lines.append(f"  Portfolio impact: {sign2}{impact:.2f}% ({size_pct:g}% position)")

    return "\n".join(lines)


@mcp.tool()
def get_fear_greed() -> str:
    """Fetch the current Crypto Fear & Greed Index (0-100) with interpretation."""
    try:
        value, label = fetch_fear_greed()
        if value <= 24:   note = "Historically a buying opportunity (contrarian signal)"
        elif value <= 44: note = "Mild fear — slight contrarian bullish lean"
        elif value <= 55: note = "Neutral zone"
        elif value <= 75: note = "Elevated greed — slight contrarian bearish lean"
        else:             note = "Historically a sell signal (contrarian signal)"
        return f"Fear & Greed Index: {value}/100 — {label}\n{note}"
    except Exception as exc:
        return f"Error fetching Fear & Greed: {exc}"


@mcp.tool()
def trade_history() -> str:
    """Show all closed trades with P&L and a win/loss summary."""
    history = pos_tracker.get_history()
    stats   = pos_tracker.get_summary()

    if not history:
        return "No closed trades yet. Use close_position() to close a trade."

    def fmt(v: float) -> str:
        if abs(v) >= 1000: return f"{v:,.2f}"
        return f"{v:.4g}"

    lines = ["Trade History", "═════════════"]
    for pos in history:
        sym       = pos["symbol"]
        direction = pos["direction"].upper()
        arrow     = "📈" if pos["direction"] == "bull" else "📉"
        entry     = pos["entry"]
        pnl       = pos.get("pnl_pct")
        leverage  = pos.get("leverage", 1.0)
        opened    = pos.get("opened", "")[:10]
        closed_at = pos.get("closed", "")[:10] or "—"

        if pnl is not None:
            sign  = "+" if pnl >= 0 else ""
            emoji = "🟢" if pnl >= 0 else "🔴"
            pnl_s = f"{emoji} {sign}{pnl:.2f}%"
            if leverage != 1.0:
                lev_pnl = pnl * leverage
                sign2   = "+" if lev_pnl >= 0 else ""
                pnl_s  += f"  ({sign2}{lev_pnl:.2f}% at {leverage:g}×)"
        elif pos["status"] == "stopped":
            pnl_s = "🛑 stopped (no exit price)"
        else:
            pnl_s = "— (no exit price)"

        lines.append(f"\n{arrow} {sym} ({direction})  {opened} → {closed_at}")
        lines.append(f"  Entry {fmt(entry)}   P&L {pnl_s}")

    lines.append("\n─────────────")
    if stats.get("win_rate") is not None:
        tw   = stats.get("total_pnl", 0.0)
        sign = "+" if tw >= 0 else ""
        lines.append(f"{stats['wins']}W / {stats['losses']}L  ·  Win rate {stats['win_rate']:.0f}%  ·  Total P&L {sign}{tw:.2f}%")
        if stats.get("avg_win") is not None:
            lines.append(f"Avg win +{stats['avg_win']:.2f}%  ·  Avg loss {stats['avg_loss']:.2f}%")
    else:
        lines.append(f"{stats['total']} closed trade(s) — no P&L data (close with a price to track)")

    return "\n".join(lines)


@mcp.tool()
def check_signal_outcomes() -> str:
    """
    Fetch current prices for all open logged signals and mark them as wins or losses.
    A win = price reached TP1 (1:1 R:R). A loss = price hit the stop first.
    Run this periodically to keep win-rate stats up to date.
    """
    all_sigs = signal_log.get_all()
    open_sigs = {s["symbol"] for s in all_sigs.values() if s["outcome"] == "open"}

    if not open_sigs:
        return "No open signals to check."

    current_prices: dict[str, float] = {}
    for sym_info, market, timeframe, fetch_fn, has_volume in _ALL_SYMBOLS:
        display = sym_info["display"]
        if display not in open_sigs:
            continue
        try:
            df = fetch_fn(sym_info["symbol"])
            current_prices[display] = float(df["close"].iloc[-1])
        except Exception:
            pass

    resolved = signal_log.resolve_outcomes(current_prices)

    if not resolved:
        lines = [f"Checked {len(open_sigs)} open signal(s) — none have hit TP1 or stop yet."]
        for sym in open_sigs:
            price = current_prices.get(sym)
            price_s = _fmt(price) if price is not None else "unavailable"
            sigs = [s for s in all_sigs.values() if s["symbol"] == sym and s["outcome"] == "open"]
            for s in sigs:
                direction = s["direction"]
                bull = direction == "bull"
                dist_tp   = abs(s["tp1"]  - price) / price * 100 if price else None
                dist_stop = abs(s["stop"] - price) / price * 100 if price else None
                tp_dir    = "above" if bull else "below"
                st_dir    = "below" if bull else "above"
                lines.append(
                    f"  {sym} ({direction})  current {price_s}  "
                    + (f"TP1 {dist_tp:.1f}% {tp_dir}  Stop {dist_stop:.1f}% {st_dir}" if dist_tp is not None else "")
                )
        return "\n".join(lines)

    lines = [f"Resolved {len(resolved)} signal(s):"]
    for s in resolved:
        icon = "✅ WIN" if s["outcome"] == "win" else "❌ LOSS"
        lines.append(
            f"  {icon}  {s['symbol']} ({s['direction']})  "
            f"entry {_fmt(s['entry_price'])}  exit {_fmt(s['exit_price'])}  "
            f"score was {s['score']}/100"
        )
    return "\n".join(lines)


@mcp.tool()
def signal_stats() -> str:
    """Show win rate and performance breakdown for all logged signals."""
    stats = signal_log.get_stats()

    if stats["total"] == 0:
        return "No signals logged yet. Run scan_all() to start tracking."

    win_rate = f"{stats['win_rate']:.1f}%" if stats["win_rate"] is not None else "n/a (no closed signals)"
    avg_w    = f"{stats['avg_score_wins']:.1f}" if stats["avg_score_wins"] is not None else "—"
    avg_l    = f"{stats['avg_score_losses']:.1f}" if stats["avg_score_losses"] is not None else "—"

    lines = [
        "Signal Performance",
        "══════════════════",
        f"Total logged:  {stats['total']}  ({stats['open']} open · {stats['wins']} wins · {stats['losses']} losses)",
        f"Win rate:      {win_rate}",
        f"Avg score — winners: {avg_w}   losers: {avg_l}",
        "",
        "By symbol:",
    ]

    for sym, counts in sorted(stats["by_symbol"].items()):
        closed = counts["wins"] + counts["losses"]
        wr = f"{counts['wins'] / closed * 100:.0f}%" if closed else "—"
        lines.append(
            f"  {sym:<14}  {counts['wins']}W / {counts['losses']}L  ({counts['open']} open)  win rate {wr}"
        )

    lines += ["", "By direction:"]
    for direction, counts in sorted(stats["by_direction"].items()):
        closed = counts["wins"] + counts["losses"]
        wr = f"{counts['wins'] / closed * 100:.0f}%" if closed else "—"
        lines.append(
            f"  {direction:<6}  {counts['wins']}W / {counts['losses']}L  ({counts['open']} open)  win rate {wr}"
        )

    return "\n".join(lines)


@mcp.tool()
def help() -> str:
    """Show all available Signal Scanner tools and example usage."""
    return """\
Signal Scanner — available tools
═════════════════════════════════

SCANNING
────────
scan_all()
  Run a full pass across all markets (crypto, stocks, forex).
  Highlights any symbol with a bear or bull score above the alert threshold.
  Example: "scan everything"

score_symbol(symbol)
  Deep-dive score + trade plan for one symbol.
  Shows bull/bear score, every signal that fired, RSI/EMA/ATR, and ATR-based TP/SL levels.
  Symbols: BTC  ETH  SOL  ADA  XRP  NEAR
           SPY  QQQ  AAPL  TSLA  NVDA  HOOD
           EURUSD  GBPUSD  USDJPY  AUDUSD
  Example: "score BTC" / "score TSLA" / "score EURUSD"

get_fear_greed()
  Current Crypto Fear & Greed Index (0-100) with contrarian interpretation.
  Example: "what's the fear and greed index"

TRADE TRACKING
──────────────
log_trade(symbol, direction, entry_price, leverage?, size_pct?)
  Record a new position. Stop loss and three take-profit levels are
  computed automatically from ATR (1.5× ATR risk, 1:1/1:2/1:3 R:R).
  direction : "bull" or "bear"
  leverage  : optional multiplier, e.g. 5.0 for 5×  (default: 1.0)
  size_pct  : optional % of portfolio, e.g. 10.0 for 10%
  Example: "log a bull trade on BTC at 68500"
           "log a bear trade on TSLA at 210 with 2x leverage and 5% size"

get_positions()
  List all open positions with entry, stop, and TP levels (✅ = hit, ⏳ = pending).
  Example: "show my positions"

trade_history()
  All closed trades with P&L per trade and summary stats (win rate, avg win/loss, total P&L).
  Example: "show my trade history" / "how have my trades done"

close_position(symbol, close_price?)
  Mark a position closed and calculate P&L (raw % and leveraged %).
  close_price is optional — omit if closing at a TP/SL that already fired.
  Example: "close BTC at 72000" / "close my TSLA position"

SIGNAL PERFORMANCE
──────────────────
check_signal_outcomes()
  Fetch current prices for every open logged signal and mark wins/losses.
  Win  = price reached TP1 (1:1 R:R target).
  Loss = price hit the stop loss first.
  Example: "check my signal outcomes" / "update win rate"

signal_stats()
  Win rate, total logged, breakdown by symbol and direction, and average
  signal score for winners vs losers.
  Example: "show my win rate" / "how are my signals performing"

HELP
────
help()
  Show this reference.
"""


if __name__ == "__main__":
    mcp.run()
