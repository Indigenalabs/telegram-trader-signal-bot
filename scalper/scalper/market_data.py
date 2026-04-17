from __future__ import annotations

import time
from urllib.parse import quote

import requests

# ── Yahoo Finance interval map ─────────────────────────────────────────────────
_YF_INTERVAL_MAP: dict[str, tuple[str, str]] = {
    "1m":  ("1d",  "1m"),
    "5m":  ("5d",  "5m"),
    "15m": ("5d",  "15m"),
    "30m": ("5d",  "30m"),
    "1h":  ("7d",  "60m"),
    "4h":  ("60d", "60m"),
    "1d":  ("3mo", "1d"),
}


def _to_binance_symbol(ticker: str) -> str:
    normalized = ticker.upper()
    if normalized.endswith("-USD") or normalized.endswith("-USDT"):
        base = normalized.split("-")[0]
        return f"{base}USDT"
    return normalized


def is_crypto_ticker(ticker: str) -> bool:
    """True if ticker is a crypto pair (ends in -USD or -USDT)."""
    t = ticker.upper()
    return t.endswith("-USD") or t.endswith("-USDT")


def fetch_ohlcv_yahoo(ticker: str, interval: str = "5m", limit: int = 100) -> dict | None:
    """
    Fetch OHLCV candles from Yahoo Finance for stocks, ETFs, and indices.
    Uses the same return format as fetch_ohlcv() so the rest of the scalper
    works identically for both crypto and stocks.
    """
    range_str, yf_interval = _YF_INTERVAL_MAP.get(interval, ("5d", "5m"))
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker)}"
        f"?range={range_str}&interval={yf_interval}&includePrePost=false"
    )
    for attempt in range(3):
        try:
            r = requests.get(
                url,
                headers={"User-Agent": "scalper-bot/1.0"},
                timeout=15.0,
            )
            if r.status_code == 429:
                time.sleep(3.0 * (2 ** attempt))
                continue
            r.raise_for_status()
            payload = r.json()
            result = (payload.get("chart", {}).get("result") or [None])[0]
            if not result:
                return None

            quote_data = result["indicators"]["quote"][0]
            opens  = [float(v) for v in quote_data.get("open",   []) if v is not None]
            closes = [float(v) for v in quote_data.get("close",  []) if v is not None]
            highs  = [float(v) for v in quote_data.get("high",   []) if v is not None]
            lows   = [float(v) for v in quote_data.get("low",    []) if v is not None]
            volumes = [float(v) for v in quote_data.get("volume", []) if v is not None]

            if len(closes) < 10:
                return None

            # Align all arrays to the shortest length
            n = min(len(opens), len(closes), len(highs), len(lows), len(volumes))
            meta = result.get("meta", {})
            current_price = float(meta.get("regularMarketPrice", closes[-1]))

            return {
                "symbol": ticker.upper(),
                "ticker": ticker.upper(),
                "opens":  opens[-n:][-limit:],
                "closes": closes[-n:][-limit:],
                "highs":  highs[-n:][-limit:],
                "lows":   lows[-n:][-limit:],
                "volumes": volumes[-n:][-limit:],
                "current_price": current_price,
            }
        except Exception:
            if attempt < 2:
                time.sleep(1.5)
    return None


def fetch_price(ticker: str, api_key: str = "") -> float | None:
    """Fetch only the current price for a ticker. Fast — single API call."""
    symbol = _to_binance_symbol(ticker)
    headers = {"X-MBX-APIKEY": api_key} if api_key else {}
    for attempt in range(3):
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": symbol},
                headers=headers,
                timeout=10.0,
            )
            if r.status_code == 429:
                time.sleep(2.0 * (2 ** attempt))
                continue
            if r.status_code == 200:
                return float(r.json()["price"])
            return None
        except Exception:
            if attempt < 2:
                time.sleep(1.5)
    return None


def fetch_ohlcv(ticker: str, interval: str = "5m", limit: int = 100, api_key: str = "") -> dict | None:
    """
    Fetch OHLCV candles from Binance.
    Returns dict with keys: closes, highs, lows, volumes, current_price, symbol
    Returns None on failure.
    """
    symbol = _to_binance_symbol(ticker)
    headers = {"X-MBX-APIKEY": api_key} if api_key else {}

    for attempt in range(3):
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
                headers=headers,
                timeout=15.0,
            )
            if r.status_code == 429:
                time.sleep(2.0 * (2 ** attempt))
                continue
            r.raise_for_status()
            klines = r.json()
            if len(klines) < 10:
                return None

            opens = [float(k[1]) for k in klines]
            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            volumes = [float(k[5]) for k in klines]

            # Get live price from ticker endpoint
            pr = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": symbol},
                headers=headers,
                timeout=10.0,
            )
            if pr.status_code == 200:
                current_price = float(pr.json()["price"])
            else:
                current_price = closes[-1]

            return {
                "symbol": symbol,
                "ticker": ticker.upper(),
                "opens": opens,
                "closes": closes,
                "highs": highs,
                "lows": lows,
                "volumes": volumes,
                "current_price": current_price,
            }
        except Exception:
            if attempt < 2:
                time.sleep(1.5)
    return None
