from __future__ import annotations

from trader_signal_bot.domain import PortfolioPosition, TrackedTrade, UserProfile


class UserStateStore:
    def __init__(self, default_risk_per_trade: float) -> None:
        self.default_risk_per_trade = default_risk_per_trade
        self._profiles: dict[int, UserProfile] = {}
        self._last_alerts: dict[tuple[int, str], tuple[str, int]] = {}
        self._tracked_trades: dict[tuple[int, str], TrackedTrade] = {}

    def get_profile(self, chat_id: int) -> UserProfile:
        profile = self._profiles.get(chat_id)
        if profile is None:
            profile = UserProfile(chat_id=chat_id, risk_per_trade=self.default_risk_per_trade)
            self._profiles[chat_id] = profile
        return profile

    def backend_name(self) -> str:
        return "memory"

    def persistence_enabled(self) -> bool:
        return False

    def list_chat_ids(self) -> list[int]:
        return sorted(self._profiles.keys())

    def should_send_alert(self, chat_id: int, ticker: str, side: str, confidence: int) -> bool:
        key = (chat_id, ticker.upper())
        last = self._last_alerts.get(key)
        if last is None:
            self._last_alerts[key] = (side, confidence)
            return True

        last_side, last_confidence = last
        if last_side != side or abs(last_confidence - confidence) >= 5:
            self._last_alerts[key] = (side, confidence)
            return True
        return False

    def get_tracked_trade(self, chat_id: int, ticker: str) -> TrackedTrade | None:
        return self._tracked_trades.get((chat_id, ticker.upper()))

    def set_tracked_trade(self, trade: TrackedTrade) -> TrackedTrade:
        self._tracked_trades[(trade.chat_id, trade.ticker.upper())] = trade
        return trade

    def clear_tracked_trade(self, chat_id: int, ticker: str) -> None:
        self._tracked_trades.pop((chat_id, ticker.upper()), None)

    def add_watchlist(self, chat_id: int, ticker: str) -> UserProfile:
        profile = self.get_profile(chat_id)
        normalized = ticker.upper()
        if normalized not in profile.watchlist:
            profile.watchlist.append(normalized)
            profile.watchlist.sort()
        return profile

    def set_watchlist(self, chat_id: int, tickers: list[str]) -> UserProfile:
        profile = self.get_profile(chat_id)
        normalized = sorted({ticker.upper() for ticker in tickers if ticker.strip()})
        profile.watchlist = normalized
        return profile

    def remove_watchlist(self, chat_id: int, ticker: str) -> UserProfile:
        profile = self.get_profile(chat_id)
        normalized = ticker.upper()
        profile.watchlist = [item for item in profile.watchlist if item != normalized]
        return profile

    def clear_watchlist(self, chat_id: int) -> UserProfile:
        profile = self.get_profile(chat_id)
        profile.watchlist = []
        return profile

    def set_alert_mode(self, chat_id: int, mode: str) -> UserProfile:
        profile = self.get_profile(chat_id)
        profile.alert_mode = mode
        return profile

    def add_portfolio_position(self, chat_id: int, ticker: str, entry_price: float, size: float) -> UserProfile:
        profile = self.get_profile(chat_id)
        normalized = ticker.upper()
        profile.portfolio = [item for item in profile.portfolio if item.ticker != normalized]
        profile.portfolio.append(
            PortfolioPosition(ticker=normalized, entry_price=float(entry_price), size=float(size))
        )
        return profile

    def remove_portfolio_position(self, chat_id: int, ticker: str) -> UserProfile:
        profile = self.get_profile(chat_id)
        normalized = ticker.upper()
        profile.portfolio = [item for item in profile.portfolio if item.ticker != normalized]
        return profile
