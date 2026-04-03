from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import requests

from trader_signal_bot.services.registry import get_instrument_profile


_NEWS_CACHE: dict[tuple[str, int], tuple[datetime, list[dict[str, str]]]] = {}


def ticker_to_query(ticker: str) -> str:
    return get_instrument_profile(ticker).news_query


class NewsService:
    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def search_headlines(self, query: str, page_size: int = 3) -> list[dict[str, str]]:
        if not self.api_key:
            return []

        cache_key = (query, page_size)
        cached = _NEWS_CACHE.get(cache_key)
        now = datetime.now(timezone.utc)
        if cached and (now - cached[0]) < timedelta(minutes=10):
            return cached[1]

        url = (
            "https://newsapi.org/v2/everything"
            f"?q={quote_plus(query)}&language=en&sortBy=publishedAt&pageSize={page_size}"
        )
        try:
            response = requests.get(
                url,
                headers={"X-Api-Key": self.api_key},
                timeout=20.0,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException:
            return []
        headlines = [
            {
                "title": article.get("title", "").strip(),
                "source": (article.get("source") or {}).get("name", "").strip(),
                "url": article.get("url", "").strip(),
                "published_at": article.get("publishedAt", "").strip(),
            }
            for article in payload.get("articles", [])
            if article.get("title")
        ]

        _NEWS_CACHE[cache_key] = (now, headlines)
        return headlines

    def get_headlines(self, ticker: str, page_size: int = 3) -> list[dict[str, str]]:
        query = ticker_to_query(ticker)
        return self.search_headlines(query, page_size=page_size)
