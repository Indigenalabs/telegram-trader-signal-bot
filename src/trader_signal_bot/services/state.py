from __future__ import annotations

import json
import os
from pathlib import Path

from trader_signal_bot.domain import (
    AssetClass,
    PortfolioPosition,
    SignalSide,
    TrackedTrade,
    TradeStage,
    UserProfile,
)


class UserStateStore:
    def __init__(self, default_risk_per_trade: float, data_dir: str | None = None) -> None:
        self.default_risk_per_trade = default_risk_per_trade
        self._profiles: dict[int, UserProfile] = {}
        self._last_alerts: dict[tuple[int, str], tuple[str, int]] = {}
        self._tracked_trades: dict[tuple[int, str], TrackedTrade] = {}
        self._data_dir = Path(data_dir) if data_dir else None
        if self._data_dir:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._load_tracked_trades()

    def get_profile(self, chat_id: int) -> UserProfile:
        profile = self._profiles.get(chat_id)
        if profile is None:
            profile = UserProfile(chat_id=chat_id, risk_per_trade=self.default_risk_per_trade)
            self._profiles[chat_id] = profile
        return profile

    def backend_name(self) -> str:
        return "memory+disk" if self._data_dir else "memory"

    def persistence_enabled(self) -> bool:
        return self._data_dir is not None

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
        self._persist_tracked_trades()
        return trade

    def clear_tracked_trade(self, chat_id: int, ticker: str) -> None:
        self._tracked_trades.pop((chat_id, ticker.upper()), None)
        self._persist_tracked_trades()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _tracked_trades_path(self) -> Path | None:
        if self._data_dir is None:
            return None
        return self._data_dir / "tracked_trades.json"

    def _persist_tracked_trades(self) -> None:
        path = self._tracked_trades_path()
        if path is None:
            return
        records = []
        for trade in self._tracked_trades.values():
            records.append({
                "trade_id": trade.trade_id,
                "chat_id": trade.chat_id,
                "ticker": trade.ticker,
                "asset_class": trade.asset_class.value,
                "side": trade.side.value,
                "stage": trade.stage.value,
                "entry_low": trade.entry_low,
                "entry_high": trade.entry_high,
                "stop_loss": trade.stop_loss,
                "take_profit_1": trade.take_profit_1,
                "take_profit_2": trade.take_profit_2,
                "confidence": trade.confidence,
                "market_session": trade.market_session,
                "signal_quality": trade.signal_quality,
                "opened_at": trade.opened_at,
                "scores": trade.scores,
                "expires_at": trade.expires_at,
            })
        try:
            path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load_tracked_trades(self) -> None:
        path = self._tracked_trades_path()
        if path is None or not path.exists():
            return
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
            for r in records:
                trade = TrackedTrade(
                    trade_id=r["trade_id"],
                    chat_id=int(r["chat_id"]),
                    ticker=r["ticker"],
                    asset_class=AssetClass(r["asset_class"]),
                    side=SignalSide(r["side"]),
                    stage=TradeStage(r["stage"]),
                    entry_low=float(r["entry_low"]),
                    entry_high=float(r["entry_high"]),
                    stop_loss=float(r["stop_loss"]),
                    take_profit_1=float(r["take_profit_1"]),
                    take_profit_2=float(r["take_profit_2"]),
                    confidence=int(r["confidence"]),
                    market_session=r["market_session"],
                    signal_quality=r["signal_quality"],
                    opened_at=r["opened_at"],
                    scores=r.get("scores", {}),
                    expires_at=r.get("expires_at", ""),
                )
                self._tracked_trades[(trade.chat_id, trade.ticker.upper())] = trade
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Watchlist / portfolio / alert mode (unchanged)
    # ------------------------------------------------------------------

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
