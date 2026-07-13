"""Adaptive score adjustment — learns from historical signal outcomes."""

from __future__ import annotations

import signal_log


def get_score_adjustment(symbol: str, direction: str) -> int:
    """
    Returns a score point adjustment based on recent win rate for this symbol+direction.

    Needs at least 5 closed signals before having any effect.
    Range:  -10 (losing streak)  to  +5 (winning streak).

    This lets the bot become more cautious on symbols/directions that have
    been underperforming and slightly more aggressive on ones that have been winning.
    """
    try:
        data = signal_log.get_all()
    except Exception:
        return 0

    relevant = [
        s for s in data.values()
        if s["symbol"] == symbol
        and s["direction"] == direction
        and s["outcome"] in ("win", "loss")
    ]

    if len(relevant) < 5:
        return 0  # not enough history to draw conclusions

    # Take the 10 most recent closed signals
    relevant.sort(key=lambda s: s.get("timestamp", ""), reverse=True)
    recent   = relevant[:10]
    wins     = sum(1 for s in recent if s["outcome"] == "win")
    win_rate = wins / len(recent)

    if win_rate < 0.30:
        return -10   # clear losing pattern — raise effective threshold by 10
    if win_rate < 0.45:
        return -5    # below average — slight caution
    if win_rate > 0.70:
        return 5     # well above average — slight confidence boost
    return 0         # normal range — no adjustment


def summary() -> dict:
    """Return per-(symbol, direction) win rates for diagnostic use."""
    try:
        data = signal_log.get_all()
    except Exception:
        return {}

    buckets: dict[tuple[str, str], list[str]] = {}
    for s in data.values():
        if s["outcome"] not in ("win", "loss"):
            continue
        key = (s["symbol"], s["direction"])
        buckets.setdefault(key, []).append(s["outcome"])

    result = {}
    for (sym, d), outcomes in sorted(buckets.items()):
        wins = outcomes.count("win")
        result[f"{sym} {d}"] = {
            "signals": len(outcomes),
            "win_rate": round(wins / len(outcomes) * 100, 1),
            "adjustment": get_score_adjustment(sym, d),
        }
    return result
