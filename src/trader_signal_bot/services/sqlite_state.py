from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock

from trader_signal_bot.domain import AssetClass, PortfolioPosition, Signal, SignalSide, TrackedTrade, TradeStage, UserProfile
from trader_signal_bot.services.state import UserStateStore


class SQLiteStateStore(UserStateStore):
    def __init__(self, default_risk_per_trade: float, database_path: str, namespace: str = "default") -> None:
        super().__init__(default_risk_per_trade)
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.namespace = self._normalize_namespace(namespace)
        self._profiles_table = "profiles" if self.namespace == "default" else f"{self.namespace}_profiles"
        self._tracked_trades_table = (
            "tracked_trades" if self.namespace == "default" else f"{self.namespace}_tracked_trades"
        )
        self._alerts_table = "alerts" if self.namespace == "default" else f"{self.namespace}_alerts"
        self._lock = Lock()
        self._conn = sqlite3.connect(self.database_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()
        self._load_profiles()
        self._load_tracked_trades()

    @staticmethod
    def _normalize_namespace(namespace: str) -> str:
        normalized = "".join(ch if ch.isalnum() else "_" for ch in namespace.strip().lower())
        return normalized or "default"

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def backend_name(self) -> str:
        return f"sqlite:{self.namespace}"

    def persistence_enabled(self) -> bool:
        return True

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS {self._profiles_table} (
                    chat_id INTEGER PRIMARY KEY,
                    risk_per_trade REAL NOT NULL,
                    watchlist_json TEXT NOT NULL,
                    portfolio_json TEXT NOT NULL,
                    alert_mode TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS {self._tracked_trades_table} (
                    chat_id INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    trade_id TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    side TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    entry_low REAL NOT NULL,
                    entry_high REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit_1 REAL NOT NULL,
                    take_profit_2 REAL NOT NULL,
                    confidence INTEGER NOT NULL,
                    market_session TEXT NOT NULL,
                    signal_quality TEXT NOT NULL,
                    scores_json TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (chat_id, ticker)
                );

                CREATE TABLE IF NOT EXISTS {self._alerts_table} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    chat_id INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    side TEXT NOT NULL,
                    confidence INTEGER NOT NULL,
                    market_session TEXT NOT NULL,
                    signal_quality TEXT NOT NULL
                );
                """
            )
            self._conn.commit()
        # Migrate existing databases that predate the expires_at column
        try:
            self._conn.execute(
                f"ALTER TABLE {self._tracked_trades_table} ADD COLUMN expires_at TEXT NOT NULL DEFAULT ''"
            )
            self._conn.commit()
        except Exception:
            pass  # Column already exists

    def _load_profiles(self) -> None:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT chat_id, risk_per_trade, watchlist_json, portfolio_json, alert_mode FROM {self._profiles_table}"
            ).fetchall()
        for row in rows:
            profile = UserProfile(
                chat_id=int(row["chat_id"]),
                risk_per_trade=float(row["risk_per_trade"]),
                watchlist=json.loads(row["watchlist_json"] or "[]"),
                portfolio=[
                    PortfolioPosition(**item)
                    for item in json.loads(row["portfolio_json"] or "[]")
                ],
                alert_mode=row["alert_mode"] or "high",
            )
            self._profiles[profile.chat_id] = profile

    def _load_tracked_trades(self) -> None:
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT chat_id, ticker, trade_id, asset_class, side, stage, entry_low, entry_high,
                       stop_loss, take_profit_1, take_profit_2, confidence, market_session,
                       signal_quality, scores_json, opened_at, expires_at
                FROM {self._tracked_trades_table}
                """
            ).fetchall()
        for row in rows:
            trade = TrackedTrade(
                trade_id=row["trade_id"],
                chat_id=int(row["chat_id"]),
                ticker=row["ticker"],
                asset_class=AssetClass(row["asset_class"]),
                side=SignalSide(row["side"]),
                stage=TradeStage(row["stage"]),
                entry_low=float(row["entry_low"]),
                entry_high=float(row["entry_high"]),
                stop_loss=float(row["stop_loss"]),
                take_profit_1=float(row["take_profit_1"]),
                take_profit_2=float(row["take_profit_2"]),
                confidence=int(row["confidence"]),
                market_session=row["market_session"],
                signal_quality=row["signal_quality"],
                opened_at=row["opened_at"],
                scores=json.loads(row["scores_json"] or "{}"),
                expires_at=row["expires_at"] or "",
            )
            self._tracked_trades[(trade.chat_id, trade.ticker.upper())] = trade

    def _sync_profile(self, profile: UserProfile) -> None:
        with self._lock:
            self._conn.execute(
                f"""
                INSERT INTO {self._profiles_table} (chat_id, risk_per_trade, watchlist_json, portfolio_json, alert_mode, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id) DO UPDATE SET
                    risk_per_trade=excluded.risk_per_trade,
                    watchlist_json=excluded.watchlist_json,
                    portfolio_json=excluded.portfolio_json,
                    alert_mode=excluded.alert_mode,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    profile.chat_id,
                    profile.risk_per_trade,
                    json.dumps(profile.watchlist),
                    json.dumps(
                        [
                            {
                                "ticker": position.ticker,
                                "entry_price": position.entry_price,
                                "size": position.size,
                            }
                            for position in profile.portfolio
                        ]
                    ),
                    profile.alert_mode,
                ),
            )
            self._conn.commit()

    def _sync_tracked_trade(self, trade: TrackedTrade) -> None:
        with self._lock:
            self._conn.execute(
                f"""
                INSERT INTO {self._tracked_trades_table} (
                    chat_id, ticker, trade_id, asset_class, side, stage, entry_low, entry_high,
                    stop_loss, take_profit_1, take_profit_2, confidence, market_session,
                    signal_quality, scores_json, opened_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, ticker) DO UPDATE SET
                    trade_id=excluded.trade_id,
                    asset_class=excluded.asset_class,
                    side=excluded.side,
                    stage=excluded.stage,
                    entry_low=excluded.entry_low,
                    entry_high=excluded.entry_high,
                    stop_loss=excluded.stop_loss,
                    take_profit_1=excluded.take_profit_1,
                    take_profit_2=excluded.take_profit_2,
                    confidence=excluded.confidence,
                    market_session=excluded.market_session,
                    signal_quality=excluded.signal_quality,
                    scores_json=excluded.scores_json,
                    opened_at=excluded.opened_at,
                    expires_at=excluded.expires_at
                """,
                (
                    trade.chat_id,
                    trade.ticker.upper(),
                    trade.trade_id,
                    trade.asset_class.value,
                    trade.side.value,
                    trade.stage.value,
                    trade.entry_low,
                    trade.entry_high,
                    trade.stop_loss,
                    trade.take_profit_1,
                    trade.take_profit_2,
                    trade.confidence,
                    trade.market_session,
                    trade.signal_quality,
                    json.dumps(trade.scores),
                    trade.opened_at,
                    trade.expires_at,
                ),
            )
            self._conn.commit()

    def get_profile(self, chat_id: int) -> UserProfile:
        profile = super().get_profile(chat_id)
        self._sync_profile(profile)
        return profile

    def set_tracked_trade(self, trade: TrackedTrade) -> TrackedTrade:
        stored = super().set_tracked_trade(trade)
        self._sync_tracked_trade(stored)
        return stored

    def clear_tracked_trade(self, chat_id: int, ticker: str) -> None:
        super().clear_tracked_trade(chat_id, ticker)
        with self._lock:
            self._conn.execute(
                f"DELETE FROM {self._tracked_trades_table} WHERE chat_id = ? AND ticker = ?",
                (chat_id, ticker.upper()),
            )
            self._conn.commit()

    def add_watchlist(self, chat_id: int, ticker: str) -> UserProfile:
        profile = super().add_watchlist(chat_id, ticker)
        self._sync_profile(profile)
        return profile

    def set_watchlist(self, chat_id: int, tickers: list[str]) -> UserProfile:
        profile = super().set_watchlist(chat_id, tickers)
        self._sync_profile(profile)
        return profile

    def remove_watchlist(self, chat_id: int, ticker: str) -> UserProfile:
        profile = super().remove_watchlist(chat_id, ticker)
        self._sync_profile(profile)
        return profile

    def clear_watchlist(self, chat_id: int) -> UserProfile:
        profile = super().clear_watchlist(chat_id)
        self._sync_profile(profile)
        return profile

    def set_alert_mode(self, chat_id: int, mode: str) -> UserProfile:
        profile = super().set_alert_mode(chat_id, mode)
        self._sync_profile(profile)
        return profile

    def add_portfolio_position(self, chat_id: int, ticker: str, entry_price: float, size: float) -> UserProfile:
        profile = super().add_portfolio_position(chat_id, ticker, entry_price, size)
        self._sync_profile(profile)
        return profile

    def remove_portfolio_position(self, chat_id: int, ticker: str) -> UserProfile:
        profile = super().remove_portfolio_position(chat_id, ticker)
        self._sync_profile(profile)
        return profile

    def log_alert(self, chat_id: int, signal: Signal) -> None:
        with self._lock:
            self._conn.execute(
                f"""
                INSERT INTO {self._alerts_table} (chat_id, ticker, side, confidence, market_session, signal_quality)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    signal.ticker,
                    signal.side.value,
                    signal.confidence,
                    signal.market_session,
                    signal.signal_quality,
                ),
            )
            self._conn.commit()
