"""
Dynamic ticker universe — fetches the top crypto pairs by 24h USDT volume
from Binance. Results are cached for 30 minutes so the scanner doesn't
hammer the API every 60-second cycle.

Filters applied:
  - USDT pairs only (clean, liquid pricing)
  - Excludes stablecoins and pegged tokens
  - Minimum 24h volume threshold (default $10M)
  - Maximum universe size (default 50)
"""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

# ── Exclusions ────────────────────────────────────────────────────────────────
# Stablecoins, wrapped tokens, and leveraged tokens are noise for directional scalps
_EXCLUDE: set[str] = {
    # Stablecoins / pegged tokens
    "USDC", "BUSD", "DAI", "TUSD", "USDP", "USDD", "FDUSD", "PYUSD",
    "EURC", "UST", "USTC", "FRAX", "LUSD", "SUSD", "GUSD", "HUSD",
    "USD1", "USDE", "USDS", "USDX", "USDG", "USDH", "USDL", "USDM",
    "CRVUSD", "MKRUSD", "GBPT", "EURS", "XSGD",
    # Wrapped tokens — track underlying
    "WBTC", "WETH", "WBNB", "WSTETH", "RETH", "CBETH",
    # Leveraged tokens
    "BTCUP", "BTCDOWN", "ETHUP", "ETHDOWN",
}

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: list[str] = []
_cache_ts: float = 0.0
_REFRESH_SECONDS: int = 30 * 60  # Refresh every 30 minutes


def get_top_tickers(
    api_key: str = "",
    min_volume_usdt: float = 10_000_000,   # $10M 24h volume minimum
    max_tickers: int = 50,
) -> list[str]:
    """
    Return top crypto tickers sorted by 24h USDT volume.
    Returns strings in the format 'BTC-USD', 'ETH-USD', etc.
    Falls back to stale cache on API failure.
    """
    global _cache, _cache_ts

    now = time.time()
    if _cache and (now - _cache_ts) < _REFRESH_SECONDS:
        return _cache

    headers = {"X-MBX-APIKEY": api_key} if api_key else {}
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            headers=headers,
            timeout=20.0,
        )
        r.raise_for_status()
        all_tickers = r.json()
    except Exception as exc:
        log.warning("ticker_scanner: Binance fetch failed — %s. Using cached list.", exc)
        return _cache  # return stale if available, empty list on first call

    candidates: list[tuple[str, float]] = []
    for item in all_tickers:
        symbol: str = item.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        base = symbol[:-4]  # strip "USDT"
        if base in _EXCLUDE:
            continue
        # Skip leveraged/bear/bull tokens (names like "BTCUP", "3L", "3S")
        if any(tag in base for tag in ("UP", "DOWN", "BEAR", "BULL", "3L", "3S")):
            continue
        try:
            vol = float(item.get("quoteVolume", 0))
            price_change_pct = abs(float(item.get("priceChangePercent", 99)))
        except (ValueError, TypeError):
            continue
        if vol < min_volume_usdt:
            continue
        # Filter out stable/pegged tokens — real tradeable assets move > 0.5% in 24h
        if price_change_pct < 0.5:
            continue
        candidates.append((base, vol))

    # Sort by 24h volume descending
    candidates.sort(key=lambda x: x[1], reverse=True)
    tickers = [f"{base}-USD" for base, _ in candidates[:max_tickers]]

    if tickers:
        _cache = tickers
        _cache_ts = now
        log.info(
            "ticker_scanner: universe refreshed — %d tickers | top 5: %s",
            len(tickers),
            ", ".join(tickers[:5]),
        )

    return _cache
