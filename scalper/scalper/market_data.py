from __future__ import annotations

import time
import requests


def _to_binance_symbol(ticker: str) -> str:
    normalized = ticker.upper()
    if normalized.endswith("-USD") or normalized.endswith("-USDT"):
        base = normalized.split("-")[0]
        return f"{base}USDT"
    return normalized


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
