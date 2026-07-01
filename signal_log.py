"""Signal performance tracking — log every alert, check outcomes, compute win rates."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

LOG_FILE = os.path.join(os.path.dirname(__file__), "signal_log.json")


def _load() -> dict:
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f:
            return json.load(f)
    return {}


def _save(data: dict) -> None:
    with open(LOG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def log_signal(
    symbol: str,
    direction: str,
    score: int,
    entry_price: float,
    stop: float,
    tp1: float,
    risk_pct: float,
    signals: list[str],
) -> str:
    """Record a new signal. Returns the short ID."""
    data = _load()
    sig_id = str(uuid.uuid4())[:8]
    data[sig_id] = {
        "id":          sig_id,
        "symbol":      symbol,
        "direction":   direction,
        "score":       score,
        "entry_price": entry_price,
        "stop":        stop,
        "tp1":         tp1,
        "risk_pct":    risk_pct,
        "signals":     signals,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "outcome":     "open",
        "exit_price":  None,
        "exit_time":   None,
    }
    _save(data)
    return sig_id


def resolve_outcomes(current_prices: dict[str, float]) -> list[dict]:
    """
    Compare open signals against current_prices and mark wins/losses.
    A win = price reaches TP1 before stop. A loss = stop hit first.
    Returns list of newly resolved signal dicts.
    """
    data     = _load()
    resolved = []

    for sig in data.values():
        if sig["outcome"] != "open":
            continue
        price = current_prices.get(sig["symbol"])
        if price is None:
            continue

        bull     = sig["direction"] == "bull"
        hit_tp   = (price >= sig["tp1"]) if bull else (price <= sig["tp1"])
        hit_stop = (price <= sig["stop"]) if bull else (price >= sig["stop"])

        if hit_tp or hit_stop:
            sig["outcome"]    = "win" if hit_tp else "loss"
            sig["exit_price"] = price
            sig["exit_time"]  = datetime.now(timezone.utc).isoformat()
            resolved.append(sig.copy())

    _save(data)
    return resolved


def get_all() -> dict:
    return _load()


def get_stats() -> dict:
    data   = _load()
    wins   = [s for s in data.values() if s["outcome"] == "win"]
    losses = [s for s in data.values() if s["outcome"] == "loss"]
    open_  = [s for s in data.values() if s["outcome"] == "open"]
    closed = len(wins) + len(losses)

    by_symbol: dict[str, dict] = {}
    by_dir:    dict[str, dict] = {}

    for sig in data.values():
        for key, bucket in [(sig["symbol"], by_symbol), (sig["direction"], by_dir)]:
            if key not in bucket:
                bucket[key] = {"wins": 0, "losses": 0, "open": 0}
            outcome_key = "wins" if sig["outcome"] == "win" else "losses" if sig["outcome"] == "loss" else "open"
            bucket[key][outcome_key] += 1

    def avg(sigs: list[dict]) -> float | None:
        scores = [s["score"] for s in sigs]
        return sum(scores) / len(scores) if scores else None

    return {
        "total":             len(data),
        "open":              len(open_),
        "wins":              len(wins),
        "losses":            len(losses),
        "win_rate":          len(wins) / closed * 100 if closed else None,
        "avg_score_wins":    avg(wins),
        "avg_score_losses":  avg(losses),
        "by_symbol":         by_symbol,
        "by_direction":      by_dir,
    }
