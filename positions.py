"""Position tracking — log entries, monitor TP/SL, alert on hits."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

POSITIONS_FILE = os.path.join(os.path.dirname(__file__), "positions.json")


def _load() -> dict:
    if os.path.exists(POSITIONS_FILE):
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    return {}


def _save(data: dict) -> None:
    with open(POSITIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def compute_levels(price: float, atr: float, direction: str) -> dict:
    """
    Compute stop loss and take-profit levels using ATR.
    Risk = 1.5× ATR. TPs at 1:1, 1:2, 1:3 risk/reward.
    """
    risk = 1.5 * atr

    if direction == "bull":
        stop = price - risk
        tp1  = price + risk          # 1:1
        tp2  = price + risk * 2      # 1:2
        tp3  = price + risk * 3      # 1:3
    else:
        stop = price + risk
        tp1  = price - risk
        tp2  = price - risk * 2
        tp3  = price - risk * 3

    def pct(target: float) -> float:
        return abs(target - price) / price * 100

    return {
        "entry":    price,
        "stop":     stop,
        "tp1":      tp1,
        "tp2":      tp2,
        "tp3":      tp3,
        "risk_pct": pct(stop),
        "tp1_pct":  pct(tp1),
        "tp2_pct":  pct(tp2),
        "tp3_pct":  pct(tp3),
    }


def add(symbol: str, direction: str, entry: float, levels: dict) -> None:
    data = _load()
    data[symbol.upper()] = {
        "direction": direction,
        "entry":     entry,
        "stop":      levels["stop"],
        "tp1":       levels["tp1"],  "tp1_hit": False,
        "tp2":       levels["tp2"],  "tp2_hit": False,
        "tp3":       levels["tp3"],  "tp3_hit": False,
        "status":    "open",
        "opened":    datetime.now(timezone.utc).isoformat(),
    }
    _save(data)


def check(symbol: str, current_price: float) -> list[str]:
    """
    Check if price has hit any TP or SL levels.
    Returns list of triggered events: 'tp1', 'tp2', 'tp3', 'stop'.
    """
    data = _load()
    key  = symbol.upper()
    if key not in data:
        return []

    pos = data[key]
    if pos["status"] != "open":
        return []

    events: list[str] = []
    bull = pos["direction"] == "bull"

    if (bull and current_price <= pos["stop"]) or (not bull and current_price >= pos["stop"]):
        events.append("stop")
        pos["status"] = "stopped"
    else:
        for tp in ("tp1", "tp2", "tp3"):
            if pos[f"{tp}_hit"]:
                continue
            hit = (current_price >= pos[tp]) if bull else (current_price <= pos[tp])
            if hit:
                events.append(tp)
                pos[f"{tp}_hit"] = True

    _save(data)
    return events


def close(symbol: str) -> bool:
    data = _load()
    key  = symbol.upper()
    if key not in data:
        return False
    data[key]["status"] = "closed"
    data[key]["closed"] = datetime.now(timezone.utc).isoformat()
    _save(data)
    return True


def get_open() -> dict:
    return {k: v for k, v in _load().items() if v["status"] == "open"}
