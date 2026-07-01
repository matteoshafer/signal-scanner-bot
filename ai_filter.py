"""Claude Fable 5 AI filter for signal quality assessment."""

from __future__ import annotations

import json
import os

import anthropic


def assess_signal(
    symbol: str,
    direction: str,
    score: int,
    signals: list[str],
    indicators: dict,
) -> dict:
    """
    Ask Claude Fable 5 to rate signal quality as HIGH / MEDIUM / LOW.
    Reads fear_greed from indicators dict if present.
    Returns {'confidence': str, 'reasoning': str}. Fails safe to MEDIUM.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {"confidence": "MEDIUM", "reasoning": "ANTHROPIC_API_KEY not set — skipping AI filter"}

    client = anthropic.Anthropic(api_key=api_key)

    rsi        = indicators.get("rsi")
    price      = indicators.get("close")
    ema_s      = indicators.get("ema_short")
    ema_l      = indicators.get("ema_long")
    macd       = indicators.get("macd")
    macd_sig   = indicators.get("macd_signal")
    atr        = indicators.get("atr")
    fear_greed = indicators.get("fear_greed")

    def _fmt(v):
        if v is None or v != v:
            return "N/A"
        return f"{v:.4g}"

    rsi_str       = f"{rsi:.1f}" if rsi is not None and rsi == rsi else "N/A"
    price_vs_ema  = "above EMA20" if price and ema_s and price > ema_s else "below EMA20"
    ema_alignment = "bullish (EMA20 > EMA50)" if ema_s and ema_l and ema_s > ema_l else "bearish (EMA20 < EMA50)"
    macd_line     = f"\n  MACD: {_fmt(macd)} vs signal {_fmt(macd_sig)}" if macd is not None else ""
    fg_line       = f"\n  Fear & Greed: {fear_greed}/100" if fear_greed is not None else ""

    signals_block = "\n".join(f"  - {s}" for s in signals)

    prompt = (
        f"You are a concise trading signal analyst. Evaluate this technical signal.\n\n"
        f"Symbol: {symbol}  Direction: {direction.upper()}  TA Score: {score}/100\n\n"
        f"Triggered signals:\n{signals_block}\n\n"
        f"Key indicators:\n"
        f"  RSI: {rsi_str}  |  Price: {price_vs_ema}  |  EMA trend: {ema_alignment}\n"
        f"  ATR: {_fmt(atr)}{macd_line}{fg_line}\n\n"
        f"Rate the setup quality:\n"
        f"  HIGH   = strong confluence, signals agree, good momentum alignment\n"
        f"  MEDIUM = decent setup, minor conflicting signals\n"
        f"  LOW    = weak, contradictory, or insufficient confluence\n\n"
        f'Reply with JSON only — no other text:\n{{"confidence": "HIGH|MEDIUM|LOW", "reasoning": "one sentence"}}'
    )

    try:
        response = client.beta.messages.create(
            model="claude-fable-5",
            max_tokens=128,
            betas=["server-side-fallback-2026-06-01"],
            fallbacks=[{"model": "claude-opus-4-8"}],
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            return {"confidence": "MEDIUM", "reasoning": "AI declined to assess — treating as MEDIUM"}
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(text)
        if result.get("confidence") not in ("HIGH", "MEDIUM", "LOW"):
            result["confidence"] = "MEDIUM"
        return result
    except Exception as exc:
        return {"confidence": "MEDIUM", "reasoning": f"error: {exc}"}
