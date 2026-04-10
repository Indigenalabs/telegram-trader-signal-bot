"""
Market regime writer and reader.

The regime writer runs as a scheduled job (hourly) and records per-ticker
state to data/market_regime.json.  The regime reader is a lightweight utility
that any bot (including a future scalper bot) can import to get the latest
cached regime without re-running a full analysis.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

_DEFAULT_PATH = os.path.join("data", "market_regime.json")


def _regime_label(technical_score: float, is_choppy: bool, atr: float, price: float) -> str:
    """
    Classify market regime from technical score and chop flag.

    Returns one of: "trending_bull", "trending_bear", "choppy", "neutral"
    """
    if is_choppy:
        return "choppy"
    if technical_score >= 62:
        return "trending_bull"
    if technical_score <= 38:
        return "trending_bear"
    return "neutral"


def write_market_regime(
    ticker: str,
    technical_score: float,
    facts: dict[str, Any],
    data_dir: str = "data",
) -> dict[str, Any]:
    """
    Write a single ticker's regime state to market_regime.json.

    Each call upserts the ticker entry under the top-level key.
    Returns the entry that was written.
    """
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "market_regime.json")

    # Load existing data
    existing: dict[str, Any] = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing = {}

    is_choppy = bool(facts.get("is_choppy", False))
    atr = float(facts.get("atr", 0.0))
    price = float(facts.get("current_price", 0.0))
    ema_9 = float(facts.get("ema_9", 0.0))
    ema_21 = float(facts.get("ema_21", 0.0))
    vwap = float(facts.get("vwap", 0.0))

    entry: dict[str, Any] = {
        "ticker": ticker.upper(),
        "regime": _regime_label(technical_score, is_choppy, atr, price),
        "technical_score": round(technical_score, 2),
        "is_choppy": is_choppy,
        "atr": round(atr, 6),
        "ema_9": round(ema_9, 6),
        "ema_21": round(ema_21, 6),
        "vwap": round(vwap, 6),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    existing[ticker.upper()] = entry

    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)

    return entry


def read_market_regime(ticker: str, data_dir: str = "data") -> dict[str, Any] | None:
    """
    Read the latest cached regime for a ticker.

    Returns None if no data is available.
    Useful for the scalper bot to check regime before generating a signal.
    """
    path = os.path.join(data_dir, "market_regime.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get(ticker.upper())
    except (json.JSONDecodeError, OSError):
        return None


def read_all_regimes(data_dir: str = "data") -> dict[str, Any]:
    """Return the full regime snapshot for all tracked tickers."""
    path = os.path.join(data_dir, "market_regime.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
