"""Indicator computation and bullish/bearish scoring logic."""

from __future__ import annotations

import pandas as pd
import ta

from config import (
    RSI_LENGTH, RSI_OVERBOUGHT, RSI_LOOKBACK,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL, MACD_CROSS_BACK,
    EMA_SHORT, EMA_LONG,
    VOLUME_MA_PERIOD, VOLUME_MULTIPLIER,
    LOWER_HIGH_LOOKBACK,
    SCORE_RSI_REJECTION, SCORE_MACD_CROSSOVER,
    SCORE_PRICE_BELOW_EMA, SCORE_EMA_ALIGN, SCORE_EMA_COUNTER, SCORE_MOMENTUM,
    SCORE_BEAR_ENGULF, SCORE_HIGH_VOL_RED, SCORE_LOWER_HIGH,
    SCORE_RSI_DIVERGENCE, SCORE_HAMMER, SCORE_MACD_HISTOGRAM,
    SCORE_FEAR_GREED, MIN_SIGNAL_COUNT, ATR_MIN_PCT,
)

RSI_OVERSOLD = 30


def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rsi"]       = ta.momentum.RSIIndicator(close=df["close"], window=RSI_LENGTH).rsi()
    macd_obj        = ta.trend.MACD(close=df["close"], window_slow=MACD_SLOW, window_fast=MACD_FAST, window_sign=MACD_SIGNAL)
    df["macd"]      = macd_obj.macd()
    df["macd_sig"]  = macd_obj.macd_signal()
    df["ema_short"] = ta.trend.EMAIndicator(close=df["close"], window=EMA_SHORT).ema_indicator()
    df["ema_long"]  = ta.trend.EMAIndicator(close=df["close"], window=EMA_LONG).ema_indicator()
    df["vol_ma"]    = df["volume"].rolling(window=VOLUME_MA_PERIOD).mean()
    df["atr"]       = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
    return df


def _fmt(v: float) -> str:
    if v != v: return "N/A"
    if abs(v) >= 1000: return f"{v:,.2f}"
    return f"{v:.4g}"


def _check_bearish(df: pd.DataFrame, has_volume: bool) -> tuple[int, list[str]]:
    last, prev = df.iloc[-1], df.iloc[-2]
    score = 0
    signals: list[str] = []

    # RSI overbought rejection
    rsi_window = df["rsi"].iloc[-(RSI_LOOKBACK + 1):-1]
    if pd.notna(last["rsi"]) and pd.notna(prev["rsi"]) and rsi_window.max() > RSI_OVERBOUGHT and last["rsi"] < prev["rsi"]:
        score += SCORE_RSI_REJECTION
        signals.append(f"Momentum cooling after hitting overbought levels (RSI {last['rsi']:.1f} ↓)")

    # MACD bearish crossover
    macd_diff = df["macd"] - df["macd_sig"]
    recent = macd_diff.iloc[-MACD_CROSS_BACK:]
    if recent.notna().all() and recent.iloc[0] > 0 and recent.iloc[-1] < 0:
        score += SCORE_MACD_CROSSOVER
        signals.append("Momentum indicator just turned negative (MACD crossover)")

    # Price below 20 EMA
    if pd.notna(last["ema_short"]) and last["close"] < last["ema_short"]:
        score += SCORE_PRICE_BELOW_EMA
        signals.append(f"Price slipped below its short-term trend line (EMA {EMA_SHORT}: {_fmt(last['ema_short'])})")

    # EMA trend alignment: bonus if aligned, silent penalty if counter-trend
    if pd.notna(last["ema_short"]) and pd.notna(last["ema_long"]):
        if last["ema_short"] < last["ema_long"]:
            score += SCORE_EMA_ALIGN
            signals.append("Bearish trend structure confirmed: short-term below long-term trend")
        else:
            score -= SCORE_EMA_COUNTER

    # Bearish engulfing
    prev_green   = prev["close"] > prev["open"]
    curr_red     = last["close"] < last["open"]
    body_engulfs = last["open"] >= prev["close"] and last["close"] <= prev["open"]
    if prev_green and curr_red and body_engulfs:
        score += SCORE_BEAR_ENGULF
        signals.append("Bears completely overtook the previous candle (engulfing)")

    # High-volume red candle
    vol_ma = last["vol_ma"]
    if has_volume and curr_red and pd.notna(vol_ma) and vol_ma > 0 and last["volume"] > VOLUME_MULTIPLIER * vol_ma:
        score += SCORE_HIGH_VOL_RED
        signals.append(f"Heavy selling pressure — {last['volume'] / vol_ma:.1f}× average volume")

    # Lower high pattern
    highs = df["high"].iloc[-LOWER_HIGH_LOOKBACK:]
    mid = len(highs) // 2
    fmax, smax = highs.iloc[:mid].max(), highs.iloc[mid:].max()
    if fmax > 0 and (fmax - smax) / fmax > 0.001:
        score += SCORE_LOWER_HIGH
        signals.append("Price making lower highs — uptrend is weakening")

    # Short-term momentum: 2+ of last 3 candles bearish
    recent_3 = df.iloc[-4:-1]
    bear_candles = sum(
        1 for _, r in recent_3.iterrows()
        if float(r["close"]) < float(r["open"])
    )
    if bear_candles >= 2:
        score += SCORE_MOMENTUM
        signals.append(f"Short-term momentum bearish: {bear_candles}/3 recent candles closed red")

    # Shooting star: long upper wick, small body, little lower wick
    body    = abs(float(last["close"]) - float(last["open"]))
    hi_wick = float(last["high"]) - float(max(last["close"], last["open"]))
    lo_wick = float(min(last["close"], last["open"])) - float(last["low"])
    c_range = float(last["high"]) - float(last["low"])
    if (c_range > 0 and body > 0 and
            hi_wick >= 2 * body and lo_wick <= 0.5 * body and body / c_range <= 0.35):
        score += SCORE_HAMMER
        signals.append("Shooting star: sellers rejected the highs — strong reversal candle")

    # Bearish RSI divergence: price higher high, RSI lower high
    if len(df) >= 20:
        win = df.iloc[-20:]
        mid = 10
        pe  = win["close"].iloc[:mid].max()
        pl  = win["close"].iloc[mid:].max()
        re  = win["rsi"].iloc[:mid].max()
        rl  = win["rsi"].iloc[mid:].max()
        if (pd.notna(re) and pd.notna(rl) and
                pl > pe * 1.001 and rl < re - 3 and rl > 50):
            score += SCORE_RSI_DIVERGENCE
            signals.append("Bearish RSI divergence: price making higher highs but momentum weakening")

    # MACD histogram expanding bearishly (momentum accelerating)
    hist = df["macd"] - df["macd_sig"]
    h    = hist.iloc[-4:]
    if h.notna().all() and h.iloc[-1] < 0 and h.iloc[-1] < h.iloc[-2] < h.iloc[-3]:
        score += SCORE_MACD_HISTOGRAM
        signals.append("MACD histogram expanding bearishly — selling momentum accelerating")

    return max(0, min(score, 100)), signals


def _check_bullish(df: pd.DataFrame, has_volume: bool) -> tuple[int, list[str]]:
    last, prev = df.iloc[-1], df.iloc[-2]
    score = 0
    signals: list[str] = []

    # RSI oversold bounce
    rsi_window = df["rsi"].iloc[-(RSI_LOOKBACK + 1):-1]
    if pd.notna(last["rsi"]) and pd.notna(prev["rsi"]) and rsi_window.min() < RSI_OVERSOLD and last["rsi"] > prev["rsi"]:
        score += SCORE_RSI_REJECTION
        signals.append(f"Momentum rising back from oversold levels (RSI {last['rsi']:.1f} ↑)")

    # MACD bullish crossover
    macd_diff = df["macd"] - df["macd_sig"]
    recent = macd_diff.iloc[-MACD_CROSS_BACK:]
    if recent.notna().all() and recent.iloc[0] < 0 and recent.iloc[-1] > 0:
        score += SCORE_MACD_CROSSOVER
        signals.append("Momentum indicator just turned positive (MACD crossover)")

    # Price above 20 EMA
    if pd.notna(last["ema_short"]) and last["close"] > last["ema_short"]:
        score += SCORE_PRICE_BELOW_EMA
        signals.append(f"Price climbed back above its short-term trend line (EMA {EMA_SHORT}: {_fmt(last['ema_short'])})")

    # EMA trend alignment: bonus if aligned, silent penalty if counter-trend
    if pd.notna(last["ema_short"]) and pd.notna(last["ema_long"]):
        if last["ema_short"] > last["ema_long"]:
            score += SCORE_EMA_ALIGN
            signals.append("Bullish trend structure confirmed: short-term above long-term trend")
        else:
            score -= SCORE_EMA_COUNTER

    # Bullish engulfing
    prev_red     = prev["close"] < prev["open"]
    curr_green   = last["close"] > last["open"]
    body_engulfs = last["open"] <= prev["close"] and last["close"] >= prev["open"]
    if prev_red and curr_green and body_engulfs:
        score += SCORE_BEAR_ENGULF
        signals.append("Bulls completely overtook the previous candle (engulfing)")

    # High-volume green candle
    vol_ma = last["vol_ma"]
    if has_volume and curr_green and pd.notna(vol_ma) and vol_ma > 0 and last["volume"] > VOLUME_MULTIPLIER * vol_ma:
        score += SCORE_HIGH_VOL_RED
        signals.append(f"Heavy buying pressure — {last['volume'] / vol_ma:.1f}× average volume")

    # Higher low pattern
    lows = df["low"].iloc[-LOWER_HIGH_LOOKBACK:]
    mid = len(lows) // 2
    fmin, smin = lows.iloc[:mid].min(), lows.iloc[mid:].min()
    if fmin > 0 and (smin - fmin) / fmin > 0.001:
        score += SCORE_LOWER_HIGH
        signals.append("Price making higher lows — downtrend is weakening")

    # Short-term momentum: 2+ of last 3 candles bullish
    recent_3 = df.iloc[-4:-1]
    bull_candles = sum(
        1 for _, r in recent_3.iterrows()
        if float(r["close"]) > float(r["open"])
    )
    if bull_candles >= 2:
        score += SCORE_MOMENTUM
        signals.append(f"Short-term momentum bullish: {bull_candles}/3 recent candles closed green")

    # Hammer: long lower wick, small body, little upper wick
    body    = abs(float(last["close"]) - float(last["open"]))
    lo_wick = float(min(last["close"], last["open"])) - float(last["low"])
    hi_wick = float(last["high"]) - float(max(last["close"], last["open"]))
    c_range = float(last["high"]) - float(last["low"])
    if (c_range > 0 and body > 0 and
            lo_wick >= 2 * body and hi_wick <= 0.5 * body and body / c_range <= 0.35):
        score += SCORE_HAMMER
        signals.append("Hammer candle: buyers pushed price back up from the lows — strong reversal signal")

    # Bullish RSI divergence: price lower low, RSI higher low
    if len(df) >= 20:
        win = df.iloc[-20:]
        mid = 10
        pe  = win["close"].iloc[:mid].min()
        pl  = win["close"].iloc[mid:].min()
        re  = win["rsi"].iloc[:mid].min()
        rl  = win["rsi"].iloc[mid:].min()
        if (pd.notna(re) and pd.notna(rl) and
                pl < pe * 0.999 and rl > re + 3 and rl < 50):
            score += SCORE_RSI_DIVERGENCE
            signals.append("Bullish RSI divergence: price making lower lows but momentum recovering")

    # MACD histogram expanding bullishly (momentum accelerating)
    hist = df["macd"] - df["macd_sig"]
    h    = hist.iloc[-4:]
    if h.notna().all() and h.iloc[-1] > 0 and h.iloc[-1] > h.iloc[-2] > h.iloc[-3]:
        score += SCORE_MACD_HISTOGRAM
        signals.append("MACD histogram expanding bullishly — buying momentum accelerating")

    return max(0, min(score, 100)), signals


def _apply_fear_greed(value: int) -> tuple[int, int, str, str]:
    """Returns (bull_points, bear_points, bull_signal, bear_signal) from Fear & Greed value."""
    if value <= 24:
        return SCORE_FEAR_GREED, 0, f"Market in Extreme Fear ({value}/100) — historically a buying opportunity", ""
    if value <= 44:
        return SCORE_FEAR_GREED // 2, 0, f"Market fear present ({value}/100) — contrarian bullish lean", ""
    if value >= 76:
        return 0, SCORE_FEAR_GREED, "", f"Market in Extreme Greed ({value}/100) — historically a sell signal"
    if value >= 56:
        return 0, SCORE_FEAR_GREED // 2, "", f"Elevated market greed ({value}/100) — contrarian bearish lean"
    return 0, 0, "", ""  # Neutral (45-55)


def analyze(df: pd.DataFrame, has_volume: bool = True, fear_greed: int | None = None) -> tuple[int, list[str], int, list[str], dict]:
    """
    Returns (bear_score, bear_signals, bull_score, bull_signals, indicators).
    Scores are 0-100. Signal lists use plain-English descriptions.
    """
    df = _add_indicators(df)

    min_rows = MACD_SLOW + MACD_SIGNAL + 5
    if df.dropna(subset=["macd", "rsi"]).shape[0] < min_rows:
        return 0, [], 0, [], {}

    last = df.iloc[-1]
    bear_score, bear_signals = _check_bearish(df, has_volume)
    bull_score, bull_signals = _check_bullish(df, has_volume)

    # Penalise signals when market is too quiet (low ATR = choppy / no real trend)
    if pd.notna(last["atr"]) and float(last["close"]) > 0:
        atr_pct = float(last["atr"]) / float(last["close"])
        if atr_pct < ATR_MIN_PCT:
            bear_score = int(bear_score * 0.7)
            bull_score = int(bull_score * 0.7)

    if fear_greed is not None:
        fg_bull, fg_bear, fg_bull_sig, fg_bear_sig = _apply_fear_greed(fear_greed)
        if fg_bull and bull_score >= 30:
            bull_score = min(bull_score + fg_bull, 100)
            bull_signals.append(fg_bull_sig)
        if fg_bear and bear_score >= 30:
            bear_score = min(bear_score + fg_bear, 100)
            bear_signals.append(fg_bear_sig)

    # Require minimum signal count — suppress low-confluence setups entirely
    if len(bear_signals) < MIN_SIGNAL_COUNT:
        bear_score, bear_signals = 0, []
    if len(bull_signals) < MIN_SIGNAL_COUNT:
        bull_score, bull_signals = 0, []

    indicators = {
        "close":       last["close"],
        "rsi":         last["rsi"],
        "ema_short":   last["ema_short"],
        "ema_long":    last["ema_long"],
        "macd":        last["macd"],
        "macd_signal": last["macd_sig"],
        "atr":         last["atr"],
        "fear_greed":  fear_greed,
    }

    return bear_score, bear_signals, bull_score, bull_signals, indicators
