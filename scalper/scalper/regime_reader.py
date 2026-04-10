from __future__ import annotations

import json
import os
from typing import Any


def read_regime(ticker: str, regime_file: str) -> dict[str, Any] | None:
    """
    Read the market regime for a ticker from the signal bot's regime file.
    Returns None if file missing or ticker not found.
    """
    if not os.path.exists(regime_file):
        return None
    try:
        with open(regime_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        symbol = ticker.upper().replace("-USD", "USDT").replace("-USDT", "USDT")
        # Try exact match first, then base symbol
        return data.get(symbol) or data.get(ticker.upper())
    except Exception:
        return None


def get_regime_label(ticker: str, regime_file: str) -> str:
    """Returns regime label string or 'unknown'."""
    r = read_regime(ticker, regime_file)
    if r is None:
        return "unknown"
    return r.get("regime", "unknown")


def is_tradeable_regime(ticker: str, regime_file: str, side: str) -> bool:
    """
    Gate entries by regime.
    - trending_bull  → LONG ok, SHORT blocked
    - trending_bear  → SHORT ok, LONG blocked
    - choppy         → both blocked
    - neutral/unknown → both allowed (regime data unavailable or transitional)
    """
    label = get_regime_label(ticker, regime_file)
    if label == "choppy":
        return False
    if label == "trending_bull" and side == "SHORT":
        return False
    if label == "trending_bear" and side == "LONG":
        return False
    return True
