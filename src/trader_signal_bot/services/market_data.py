from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import quote

import requests

from trader_signal_bot.domain import AssetClass, PriceSnapshot


def classify_ticker(ticker: str) -> AssetClass:
    normalized = ticker.upper()
    if normalized.endswith("-USD") or normalized.endswith("-USDT"):
        return AssetClass.CRYPTO
    if normalized.endswith("=X"):
        return AssetClass.FOREX
    if normalized.endswith("=F"):
        return AssetClass.FUTURES
    if normalized.startswith("OPT:") or normalized.count(" ") > 1:
        return AssetClass.OPTIONS
    if normalized.startswith("STAKE:"):
        return AssetClass.STAKING
    if normalized in {"SPY", "QQQ", "IWM", "DIA", "ARKK"}:
        return AssetClass.ETF
    return AssetClass.STOCK


class MarketDataProvider(ABC):
    @abstractmethod
    def get_snapshot(self, ticker: str) -> PriceSnapshot:
        raise NotImplementedError


def _to_binance_symbol(ticker: str) -> str | None:
    normalized = ticker.upper()
    if normalized.endswith("-USD") or normalized.endswith("-USDT"):
        base = normalized.split("-")[0]
        return f"{base}USDT"
    return None


class BinanceMarketDataProvider(MarketDataProvider):
    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    def get_snapshot(self, ticker: str) -> PriceSnapshot:
        symbol = _to_binance_symbol(ticker)
        if not symbol:
            raise ValueError(f"{ticker} is not a supported Binance spot symbol format.")

        headers = {"X-MBX-APIKEY": self.api_key} if self.api_key else {}
        ticker_response = requests.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            params={"symbol": symbol},
            headers=headers,
            timeout=20.0,
        )
        ticker_response.raise_for_status()
        ticker_payload = ticker_response.json()

        klines_response = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": "1d", "limit": 60},
            headers=headers,
            timeout=20.0,
        )
        klines_response.raise_for_status()
        klines_payload = klines_response.json()
        closes = [float(item[4]) for item in klines_payload]
        if len(closes) < 2:
            raise ValueError(f"Insufficient Binance history returned for {ticker}")

        current_price = float(ticker_payload["lastPrice"])
        previous_close = float(ticker_payload["prevClosePrice"])
        high = float(ticker_payload["highPrice"])
        low = float(ticker_payload["lowPrice"])
        volume = float(ticker_payload["volume"])

        return PriceSnapshot(
            ticker=ticker.upper(),
            asset_class=AssetClass.CRYPTO,
            currency="USDT",
            current_price=current_price,
            previous_close=previous_close,
            high=high,
            low=low,
            volume=volume,
            history=closes[-60:],
            meta={
                "exchange": "Binance",
                "market_cap": None,
                "day_change_pct": (
                    ((current_price - previous_close) / previous_close) * 100
                    if previous_close
                    else 0.0
                ),
                "binance_symbol": symbol,
                "requested_ticker": ticker.upper(),
                "pricing_symbol": symbol,
                "price_source": "Binance",
            },
        )


class TwelveDataMarketDataProvider(MarketDataProvider):
    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def get_snapshot(self, ticker: str) -> PriceSnapshot:
        if not self.api_key:
            raise ValueError("Twelve Data API key is not configured.")

        response = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol": ticker.upper(),
                "interval": "1day",
                "outputsize": 60,
                "apikey": self.api_key,
            },
            timeout=20.0,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") == "error":
            raise ValueError(payload.get("message", f"No Twelve Data response for {ticker}"))

        values = payload.get("values") or []
        if len(values) < 2:
            raise ValueError(f"Insufficient Twelve Data history returned for {ticker}")

        ordered = list(reversed(values))
        closes = [float(item["close"]) for item in ordered]
        latest = ordered[-1]
        previous_close = closes[-2]
        current_price = float(latest["close"])

        return PriceSnapshot(
            ticker=ticker.upper(),
            asset_class=classify_ticker(ticker),
            currency=str(payload.get("meta", {}).get("currency", "USD")),
            current_price=current_price,
            previous_close=previous_close,
            high=float(latest["high"]),
            low=float(latest["low"]),
            volume=float(latest.get("volume") or 0.0),
            history=closes[-60:],
            meta={
                "exchange": payload.get("meta", {}).get("exchange", "Twelve Data"),
                "market_cap": None,
                "day_change_pct": (
                    ((current_price - previous_close) / previous_close) * 100
                    if previous_close
                    else 0.0
                ),
                "requested_ticker": ticker.upper(),
                "pricing_symbol": ticker.upper(),
                "price_source": "Twelve Data",
            },
        )


class YahooMarketDataProvider(MarketDataProvider):
    def get_snapshot(self, ticker: str) -> PriceSnapshot:
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker)}"
            "?range=3mo&interval=1d&includePrePost=false&events=div%2Csplits"
        )
        response = requests.get(
            url,
            headers={"User-Agent": "ultimate-trader-signal-bot/0.1"},
            timeout=20.0,
        )
        response.raise_for_status()

        payload = response.json()
        result = (payload.get("chart", {}).get("result") or [None])[0]
        if not result:
            raise ValueError(f"No market data returned for {ticker}")

        quote_data = result["indicators"]["quote"][0]
        closes = [float(value) for value in quote_data.get("close", []) if value is not None]
        highs = [float(value) for value in quote_data.get("high", []) if value is not None]
        lows = [float(value) for value in quote_data.get("low", []) if value is not None]
        volumes = [float(value) for value in quote_data.get("volume", []) if value is not None]
        if len(closes) < 2:
            raise ValueError(f"Insufficient market history returned for {ticker}")

        meta = result.get("meta", {})
        current_price = float(meta.get("regularMarketPrice", closes[-1]))
        previous_close = float(meta.get("chartPreviousClose", closes[-2]))
        day_high = float(meta.get("regularMarketDayHigh", highs[-1] if highs else current_price))
        day_low = float(meta.get("regularMarketDayLow", lows[-1] if lows else current_price))
        volume = float(meta.get("regularMarketVolume", volumes[-1] if volumes else 0.0))

        return PriceSnapshot(
            ticker=ticker.upper(),
            asset_class=classify_ticker(ticker),
            currency=str(meta.get("currency", "USD")),
            current_price=current_price,
            previous_close=previous_close,
            high=day_high,
            low=day_low,
            volume=volume,
            history=closes[-60:],
            meta={
                "exchange": meta.get("exchangeName", "unknown"),
                "market_cap": meta.get("marketCap"),
                "day_change_pct": (
                    ((current_price - previous_close) / previous_close) * 100
                    if previous_close
                    else 0.0
                ),
                "requested_ticker": ticker.upper(),
                "pricing_symbol": ticker.upper(),
                "price_source": "Yahoo Finance",
            },
        )


class CompositeMarketDataProvider(MarketDataProvider):
    def __init__(
        self,
        binance_provider: BinanceMarketDataProvider,
        yahoo_provider: YahooMarketDataProvider,
        twelvedata_provider: TwelveDataMarketDataProvider | None = None,
    ) -> None:
        self.binance_provider = binance_provider
        self.yahoo_provider = yahoo_provider
        self.twelvedata_provider = twelvedata_provider

    def get_snapshot(self, ticker: str) -> PriceSnapshot:
        asset_class = classify_ticker(ticker)
        if asset_class == AssetClass.CRYPTO:
            try:
                return self.binance_provider.get_snapshot(ticker)
            except Exception:
                return self.yahoo_provider.get_snapshot(ticker)
        if asset_class in {AssetClass.STOCK, AssetClass.ETF, AssetClass.FOREX, AssetClass.FUTURES}:
            if self.twelvedata_provider and self.twelvedata_provider.is_configured():
                try:
                    return self.twelvedata_provider.get_snapshot(ticker)
                except Exception:
                    pass
        return self.yahoo_provider.get_snapshot(ticker)
