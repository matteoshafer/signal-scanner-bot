"""Claude Fable 5 AI filter — multi-timeframe, context-rich signal assessment."""

from __future__ import annotations

import json
import os
import re

import anthropic
import signal_log


# ── Context builders ──────────────────────────────────────────────────────────

def _candle_rows(df, n: int = 15) -> str:
    rows = df.tail(n)
    lines = []
    prev_close = None
    for ts, row in rows.iterrows():
        c = float(row["close"])
        h = float(row["high"])
        l = float(row["low"])
        pct = f"{(c - prev_close) / prev_close * 100:+.2f}%" if prev_close else "  —  "
        try:
            ts_str = ts.strftime("%m/%d %H:%M")
        except Exception:
            ts_str = str(ts)[:13]
        lines.append(f"  {ts_str}  C:{c:.5g}({pct})  H:{h:.5g}  L:{l:.5g}")
        prev_close = c
    return "\n".join(lines) or "  (no data)"


def _htf_summary(df, label: str) -> str:
    if df is None or len(df) < 5:
        return f"{label} data unavailable."
    recent = df.tail(10)
    closes = [float(v) for v in recent["close"]]
    highs  = [float(v) for v in recent["high"]]
    lows   = [float(v) for v in recent["low"]]
    mid    = len(closes) // 2
    avg_early = sum(closes[:mid]) / mid
    avg_late  = sum(closes[mid:]) / (len(closes) - mid)
    if avg_late > avg_early * 1.005:
        trend = "rising (uptrend)"
    elif avg_late < avg_early * 0.995:
        trend = "falling (downtrend)"
    else:
        trend = "ranging / sideways"
    green     = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
    red       = len(closes) - 1 - green
    rng_pct   = (max(highs) - min(lows)) / min(lows) * 100 if min(lows) > 0 else 0
    return (
        f"{label} trend: {trend}\n"
        f"  Last {len(closes)} candles: {green} green / {red} red\n"
        f"  Range over that period: {rng_pct:.2f}%\n"
        f"  Latest {label} close: {closes[-1]:.5g}"
    )


def _stats_block(symbol: str, direction: str) -> str:
    try:
        stats = signal_log.get_stats()
    except Exception:
        return "Signal history unavailable."
    if not stats or stats["total"] == 0:
        return "No signal history yet — bot is still building a track record."
    total  = stats["total"]
    closed = stats["wins"] + stats["losses"]
    wr_all = f"{stats['win_rate']:.1f}%" if stats["win_rate"] is not None else "n/a"
    d      = stats.get("by_direction", {}).get(direction, {})
    d_cl   = d.get("wins", 0) + d.get("losses", 0)
    wr_d   = f"{d['wins'] / d_cl * 100:.1f}%" if d_cl else "no closed signals"
    s      = stats.get("by_symbol", {}).get(symbol, {})
    s_cl   = s.get("wins", 0) + s.get("losses", 0)
    wr_s   = f"{s['wins'] / s_cl * 100:.1f}%" if s_cl else "no history"
    return (
        f"Total logged: {total}  ({closed} resolved)  overall win rate: {wr_all}\n"
        f"  {direction.upper()} signals:  {d.get('wins',0)}W / {d.get('losses',0)}L  "
        f"({d.get('open',0)} open)  win rate: {wr_d}\n"
        f"  {symbol}:  {s.get('wins',0)}W / {s.get('losses',0)}L  "
        f"({s.get('open',0)} open)  win rate: {wr_s}"
    )


# ── Main entry point ──────────────────────────────────────────────────────────

def assess_signal(
    symbol: str,
    direction: str,
    score: int,
    signals: list[str],
    indicators: dict,
    df,
    df_htf=None,
    htf_label: str = "4h",
) -> dict:
    """
    Ask Claude Fable 5 to assess a signal using multi-timeframe context,
    recent price action, and bot performance history.

    Returns dict with keys:
      confidence  — HIGH | MEDIUM | LOW
      reasoning   — one-sentence overall assessment
      entry_note  — specific actionable advice
      watch_level — price level to watch (float or None)
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {
            "confidence": "MEDIUM",
            "reasoning": "ANTHROPIC_API_KEY not set — skipping AI filter",
            "entry_note": "",
            "watch_level": None,
        }

    client = anthropic.Anthropic(api_key=api_key)

    rsi        = indicators.get("rsi")
    price      = indicators.get("close")
    ema_s      = indicators.get("ema_short")
    ema_l      = indicators.get("ema_long")
    macd       = indicators.get("macd")
    macd_sig   = indicators.get("macd_signal")
    atr        = indicators.get("atr")
    fear_greed = indicators.get("fear_greed")

    def _f(v):
        if v is None or v != v: return "N/A"
        return f"{v:.5g}"

    rsi_str  = f"{rsi:.1f}" if rsi is not None and rsi == rsi else "N/A"
    rsi_zone = ""
    if rsi is not None and rsi == rsi:
        if rsi >= 70:   rsi_zone = "overbought — supports bearish, risky for bullish"
        elif rsi <= 30: rsi_zone = "oversold — supports bullish, risky for bearish"
        else:           rsi_zone = "neutral"

    price_vs_ema = (
        "above EMA20 (bullish)" if price and ema_s and price > ema_s
        else "below EMA20 (bearish)"
    )
    ema_align = (
        "EMA20 > EMA50 — bullish structure" if ema_s and ema_l and ema_s > ema_l
        else "EMA20 < EMA50 — bearish structure"
    )
    macd_line = ""
    if macd is not None and macd_sig is not None and macd == macd and macd_sig == macd_sig:
        cross     = "above signal (bullish)" if macd > macd_sig else "below signal (bearish)"
        macd_line = f"\n  MACD: {_f(macd)} vs signal {_f(macd_sig)}  —  MACD {cross}"

    fg_line = ""
    if fear_greed is not None:
        if fear_greed <= 24:   fg_desc = "Extreme Fear — contrarian bullish"
        elif fear_greed <= 44: fg_desc = "Fear — mild contrarian bullish lean"
        elif fear_greed >= 76: fg_desc = "Extreme Greed — contrarian bearish"
        elif fear_greed >= 56: fg_desc = "Greed — mild contrarian bearish lean"
        else:                  fg_desc = "Neutral sentiment"
        fg_line = f"\n  Fear & Greed: {fear_greed}/100  —  {fg_desc}"

    signals_block  = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(signals))
    candle_history = _candle_rows(df, n=15)
    htf_block      = _htf_summary(df_htf, htf_label)
    stats_block    = _stats_block(symbol, direction)

    prompt = (
        f"You are an expert technical analyst evaluating a live trading signal.\n\n"
        f"=== SIGNAL ===\n"
        f"Symbol: {symbol}  |  Direction: {direction.upper()}  |  TA Score: {score}/100\n\n"
        f"Conditions that fired:\n{signals_block}\n\n"
        f"=== CURRENT INDICATORS (1h) ===\n"
        f"  Price:  {_f(price)}\n"
        f"  RSI:    {rsi_str}  —  {rsi_zone}\n"
        f"  EMA20:  {_f(ema_s)}  —  price is {price_vs_ema}\n"
        f"  EMA50:  {_f(ema_l)}  —  {ema_align}\n"
        f"  ATR:    {_f(atr)}  (current volatility / candle range)"
        f"{macd_line}{fg_line}\n\n"
        f"=== RECENT PRICE ACTION (1h, last 15 candles, oldest → newest) ===\n"
        f"{candle_history}\n\n"
        f"=== HIGHER TIMEFRAME ({htf_label}) ===\n"
        f"{htf_block}\n\n"
        f"=== BOT PERFORMANCE HISTORY ===\n"
        f"{stats_block}\n\n"
        f"=== ASSESSMENT CRITERIA ===\n"
        f"Rate HIGH when:\n"
        f"  - 1h signal aligns with {htf_label} trend (e.g. bull signal in {htf_label} uptrend)\n"
        f"  - RSI supports the direction (not chasing overbought/oversold extremes in the wrong direction)\n"
        f"  - Price action shows conviction (e.g. sustained move, not a 1-candle spike)\n"
        f"  - EMA structure agrees with direction\n"
        f"  - Historical win rate is decent or insufficient data to counter\n\n"
        f"Rate MEDIUM when:\n"
        f"  - Setup is reasonable but one factor is mildly conflicting\n"
        f"  - {htf_label} is ranging / neutral (no clear tailwind but no headwind either)\n\n"
        f"Rate LOW when:\n"
        f"  - Signal opposes the {htf_label} trend\n"
        f"  - RSI in extreme zone that contradicts this direction\n"
        f"  - Price action looks like chop or a 1-candle noise spike\n"
        f"  - Historical win rate is poor for this direction/symbol\n\n"
        f"Respond with JSON ONLY — no markdown, no surrounding text:\n"
        f'{{"confidence":"HIGH|MEDIUM|LOW","reasoning":"one sentence","entry_note":"specific actionable note","watch_level":price_number_or_null}}'
    )

    try:
        response = client.beta.messages.create(
            model="claude-fable-5",
            max_tokens=1024,
            betas=["server-side-fallback-2026-06-01"],
            fallbacks=[{"model": "claude-opus-4-8"}],
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            return {
                "confidence": "MEDIUM",
                "reasoning": "AI declined to assess — passing through",
                "entry_note": "",
                "watch_level": None,
            }
        text = response.content[0].text.strip()
        # Strip markdown fences if model wrapped in them
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        # Robust JSON extraction — grab first complete {...} block
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r'\{.*?\}', text, re.DOTALL)
            if m:
                result = json.loads(m.group())
            else:
                raise
        if result.get("confidence") not in ("HIGH", "MEDIUM", "LOW"):
            result["confidence"] = "MEDIUM"
        result.setdefault("reasoning", "")
        result.setdefault("entry_note", "")
        result.setdefault("watch_level", None)
        return result

    except Exception as exc:
        return {
            "confidence": "MEDIUM",
            "reasoning": f"AI filter error: {exc}",
            "entry_note": "",
            "watch_level": None,
        }
