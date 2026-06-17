"""Data fetching for crypto (Kraken REST), stocks, and forex (yfinance)."""

from __future__ import annotations

import requests
import pandas as pd
import yfinance as yf

from config import (
    CRYPTO_TIMEFRAME, STOCK_TIMEFRAME, FOREX_TIMEFRAME, CANDLE_LIMIT,
)

KRAKEN_BASE = "https://api.kraken.com/0/public"

# Kraken uses non-standard pair names (XBT instead of BTC, etc.)
_KRAKEN_PAIR: dict[str, str] = {
    "BTC/USD":  "XBTUSD",
    "ETH/USD":  "ETHUSD",
    "SOL/USD":  "SOLUSD",
    "ADA/USD":  "ADAUSD",
    "XRP/USD":  "XRPUSD",
    "NEAR/USD": "NEARUSD",
}

# Kraken interval is in minutes
_TF_MINUTES: dict[str, int] = {
    "1m": 1,  "5m": 5,  "15m": 15,  "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440, "1w": 10080,
}


def fetch_crypto(symbol: str, timeframe: str | None = None, limit: int | None = None) -> pd.DataFrame:
    """Fetch OHLCV from Kraken spot (USD pairs). Returns DataFrame indexed by UTC datetime."""
    tf       = timeframe or CRYPTO_TIMEFRAME
    lim      = limit    or CANDLE_LIMIT
    interval = _TF_MINUTES.get(tf, 60)
    pair     = _KRAKEN_PAIR.get(symbol, symbol)

    r = requests.get(
        f"{KRAKEN_BASE}/OHLC",
        params={"pair": pair, "interval": interval},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()

    if data.get("error"):
        raise ValueError(f"Kraken error: {data['error']}")

    # The result key varies per pair (e.g. XXBTZUSD, XETHZUSD, SOLUSD…)
    result = data.get("result", {})
    rows = next((v for k, v in result.items() if k != "last"), None)

    if not rows:
        raise ValueError(f"No kline data returned for {symbol}")

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "vwap", "volume", "count"])
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit="s", utc=True)
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)

    # Kraken returns up to 720 rows; trim to requested limit (most recent)
    return df.iloc[-lim:]


# ── yfinance shared helper ────────────────────────────────────────────────────

def _fetch_yf(symbol: str, interval: str, period: str = "60d") -> pd.DataFrame:
    ticker = yf.Ticker(symbol)
    df = ticker.history(interval=interval, period=period)

    if df.empty:
        raise ValueError(f"No data returned for {symbol}")

    df.columns = [c.lower() for c in df.columns]
    df = df[["open", "high", "low", "close", "volume"]].copy()
    df.dropna(subset=["open", "high", "low", "close"], inplace=True)
    return df


def fetch_stock(symbol: str, interval: str | None = None, period: str = "60d") -> pd.DataFrame:
    """Fetch OHLCV for a stock ticker via yfinance."""
    return _fetch_yf(symbol, interval or STOCK_TIMEFRAME, period)


def fetch_forex(symbol: str, interval: str | None = None, period: str = "60d") -> pd.DataFrame:
    """Fetch OHLCV for a forex pair via yfinance.
    Volume is typically 0 for FX; callers should pass has_volume=False to analyze()."""
    return _fetch_yf(symbol, interval or FOREX_TIMEFRAME, period)
