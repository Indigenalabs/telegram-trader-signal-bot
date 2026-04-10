from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trader_signal_bot.domain import AssetClass, Signal
from trader_signal_bot.services.news import NewsService


class MacroRiskService:
    def __init__(self, news_service: NewsService) -> None:
        self.news_service = news_service

    def should_filter_alert(self, signal: Signal) -> tuple[bool, str]:
        if not self.news_service.is_configured():
            return False, ""

        query = self._query_for_signal(signal)
        if not query:
            return False, ""

        headlines = self.news_service.search_headlines(query, page_size=4)
        recent = [item for item in headlines if self._is_recent(item.get("published_at", ""))]
        if not recent:
            return False, ""

        if signal.confidence < 66:
            return True, f"Fresh macro-event risk is elevated for {signal.ticker}."
        return False, ""

    def _is_recent(self, raw: str) -> bool:
        if not raw:
            return False
        try:
            published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return False
        return (datetime.now(timezone.utc) - published) <= timedelta(hours=8)

    def _query_for_signal(self, signal: Signal) -> str:
        if signal.asset_class == AssetClass.FOREX:
            return "FOMC OR Federal Reserve OR ECB OR inflation OR CPI OR NFP OR payrolls OR BOE OR BOJ"
        if signal.ticker.upper() in {"GC=F", "SI=F", "GLD", "SLV"}:
            return "Federal Reserve OR inflation OR CPI OR PCE OR yields OR central bank"
        if signal.ticker.upper() in {"CL=F", "BZ=F", "NG=F", "XOM", "CVX"}:
            return "OPEC OR crude inventories OR oil supply OR energy demand"
        if signal.asset_class == AssetClass.CRYPTO:
            return "crypto regulation OR SEC crypto OR Bitcoin ETF OR stablecoin OR crypto ban OR CFTC crypto"
        if signal.asset_class in {AssetClass.STOCK, AssetClass.ETF}:
            # Use the ticker itself plus broad market risk events
            return f"{signal.ticker} OR tariff OR earnings OR Fed rate OR recession OR market crash"
        return ""
