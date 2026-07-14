"""All configurable settings for the signal scanner bot."""

# ── Symbols ──────────────────────────────────────────────────────────────────

CRYPTO_SYMBOLS = [
    {"symbol": "BTC/USD",  "display": "BTC/USD"},
    {"symbol": "ETH/USD",  "display": "ETH/USD"},
    {"symbol": "SOL/USD",  "display": "SOL/USD"},
    {"symbol": "ADA/USD",  "display": "ADA/USD"},
    {"symbol": "NEAR/USD", "display": "NEAR/USD"},
]

STOCK_SYMBOLS = [
    {"symbol": "SPY",  "display": "SPY"},
    {"symbol": "QQQ",  "display": "QQQ"},
    {"symbol": "AAPL", "display": "AAPL"},
    {"symbol": "TSLA", "display": "TSLA"},
    {"symbol": "NVDA", "display": "NVDA"},
    {"symbol": "HOOD", "display": "HOOD"},
]

FOREX_SYMBOLS = []

# ── Timing ────────────────────────────────────────────────────────────────────

SCAN_INTERVAL = 300          # seconds between full scans (5 min)
SCORE_THRESHOLD = 65         # minimum score to fire a Telegram alert
MIN_SIGNAL_COUNT = 3         # minimum number of triggered conditions to fire

# ── Timeframes ────────────────────────────────────────────────────────────────

CRYPTO_TIMEFRAME = "1h"      # ccxt format: 1m, 5m, 15m, 1h, 4h, 1d
STOCK_TIMEFRAME  = "1h"      # yfinance format: 1m, 5m, 15m, 1h, 1d
FOREX_TIMEFRAME  = "1h"

CANDLE_LIMIT = 200           # how many historical candles to fetch

# ── Indicator parameters ──────────────────────────────────────────────────────

RSI_LENGTH      = 14
RSI_OVERBOUGHT  = 70
RSI_LOOKBACK    = 5          # candles to look back for overbought condition

MACD_FAST       = 12
MACD_SLOW       = 26
MACD_SIGNAL     = 9
MACD_CROSS_BACK = 5          # candles to look back for fresh crossover

EMA_SHORT       = 20
EMA_LONG        = 50

VOLUME_MA_PERIOD    = 20
VOLUME_MULTIPLIER   = 2.0    # x times average volume = "high volume"

LOWER_HIGH_LOOKBACK = 10     # candles used for lower-high pattern check

# ── Signal weights (must sum ≤ 130; score is capped at 100) ──────────────────

SCORE_FEAR_GREED       = 20  # max points from Fear & Greed index (crypto only, contrarian)

SCORE_RSI_REJECTION    = 25
SCORE_MACD_CROSSOVER   = 25
SCORE_PRICE_BELOW_EMA  = 15
SCORE_EMA_ALIGN        = 20  # bonus when signal aligns with EMA trend structure
SCORE_EMA_COUNTER      = 15  # penalty subtracted when signal opposes EMA trend
SCORE_MOMENTUM         = 10  # bonus when recent candles confirm signal direction
SCORE_BEAR_ENGULF      = 20
SCORE_HIGH_VOL_RED     = 15
SCORE_LOWER_HIGH       = 10  # reduced — pattern is too noisy at 15
SCORE_RSI_DIVERGENCE   = 20  # RSI divergence (price vs momentum disagree — strong reversal signal)
SCORE_HAMMER           = 15  # hammer (bull) or shooting star (bear) candlestick pattern
SCORE_MACD_HISTOGRAM   = 10  # MACD histogram expanding in signal direction

ATR_MIN_PCT            = 0.002  # suppress signals when ATR < 0.2% of price (dead / ranging market)

# ── ADX trend-strength regime ─────────────────────────────────────────────────
ADX_PERIOD        = 14   # standard ADX window
ADX_TREND_MIN     = 25   # ADX above this = strong directional trend
ADX_RANGE_MAX     = 20   # ADX below this = ranging / choppy — reduce scores
SCORE_ADX_TREND   = 15   # bonus when ADX confirms a strong trend in signal direction
SCORE_ADX_RANGING = 15   # silent penalty when market is ranging (subtracted, not shown)
