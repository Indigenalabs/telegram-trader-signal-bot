from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from trader_signal_bot.domain import Signal, TrackedTrade, TradeStage


class SQLiteLearningStore:
    def __init__(self, database_path: str, namespace: str = "default") -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.namespace = self._normalize_namespace(namespace)
        self._signals_table = "signals" if self.namespace == "default" else f"{self.namespace}_signals"
        self._trades_table = "trades" if self.namespace == "default" else f"{self.namespace}_trades"
        self._performance_table = (
            "performance_metrics" if self.namespace == "default" else f"{self.namespace}_performance_metrics"
        )
        self._lock = Lock()
        self._conn = sqlite3.connect(self.database_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    @staticmethod
    def _normalize_namespace(namespace: str) -> str:
        normalized = "".join(ch if ch.isalnum() else "_" for ch in namespace.strip().lower())
        return normalized or "default"

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS {self._signals_table} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id TEXT NOT NULL UNIQUE,
                    timestamp TEXT NOT NULL,
                    source_bot TEXT NOT NULL,
                    wallet_address TEXT,
                    transaction_hash TEXT,
                    token_address TEXT,
                    ticker TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    amount_usd REAL,
                    signal_type TEXT NOT NULL,
                    side TEXT NOT NULL,
                    confidence INTEGER NOT NULL,
                    edge_score INTEGER NOT NULL,
                    confluence_count INTEGER NOT NULL,
                    signal_quality TEXT NOT NULL,
                    market_session TEXT NOT NULL,
                    scores_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS {self._trades_table} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id TEXT NOT NULL UNIQUE,
                    wallet_address TEXT,
                    token_address TEXT,
                    ticker TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entry_price REAL,
                    exit_price REAL,
                    position_size REAL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT,
                    status TEXT NOT NULL,
                    profit_loss REAL,
                    return_pct REAL,
                    r_multiple REAL,
                    confidence INTEGER NOT NULL,
                    edge_score INTEGER NOT NULL,
                    signal_quality TEXT NOT NULL,
                    market_session TEXT NOT NULL,
                    win INTEGER,
                    source_bot TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS {self._performance_table} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    period_type TEXT NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    total_trades INTEGER NOT NULL,
                    winning_trades INTEGER NOT NULL,
                    win_rate REAL NOT NULL,
                    total_pnl REAL NOT NULL,
                    roi REAL NOT NULL,
                    avg_win REAL NOT NULL,
                    avg_loss REAL NOT NULL,
                    profit_factor REAL NOT NULL,
                    expectancy REAL NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(period_type, period_start)
                );
                """
            )
            self._conn.commit()

    def record_signal_event(self, trade: TrackedTrade, stage: TradeStage) -> None:
        payload = {
            "trade_id": trade.trade_id,
            "chat_id": trade.chat_id,
            "entry_low": trade.entry_low,
            "entry_high": trade.entry_high,
            "stop_loss": trade.stop_loss,
            "take_profit_1": trade.take_profit_1,
            "take_profit_2": trade.take_profit_2,
            "opened_at": trade.opened_at,
        }
        with self._lock:
            self._conn.execute(
                f"""
                INSERT INTO {self._signals_table} (
                    signal_id, timestamp, source_bot, wallet_address, transaction_hash, token_address,
                    ticker, asset_class, amount_usd, signal_type, side, confidence, edge_score,
                    confluence_count, signal_quality, market_session, scores_json, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signal_id) DO UPDATE SET
                    timestamp=excluded.timestamp,
                    signal_type=excluded.signal_type,
                    side=excluded.side,
                    confidence=excluded.confidence,
                    edge_score=excluded.edge_score,
                    confluence_count=excluded.confluence_count,
                    signal_quality=excluded.signal_quality,
                    market_session=excluded.market_session,
                    scores_json=excluded.scores_json,
                    metadata_json=excluded.metadata_json
                """,
                (
                    trade.trade_id,
                    trade.opened_at,
                    self.namespace,
                    None,
                    None,
                    trade.ticker,
                    trade.ticker,
                    trade.asset_class.value,
                    None,
                    stage.value,
                    trade.side.value,
                    trade.confidence,
                    int(trade.scores.get("edge_score", 0)),
                    int(trade.scores.get("confluence_count", 0)),
                    trade.signal_quality,
                    trade.market_session,
                    json.dumps(trade.scores),
                    json.dumps(payload),
                ),
            )
            self._conn.execute(
                f"""
                INSERT INTO {self._trades_table} (
                    signal_id, wallet_address, token_address, ticker, asset_class, action, entry_price,
                    exit_price, position_size, entry_time, exit_time, status, profit_loss, return_pct,
                    r_multiple, confidence, edge_score, signal_quality, market_session, win, source_bot, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signal_id) DO UPDATE SET
                    action=excluded.action,
                    entry_price=excluded.entry_price,
                    confidence=excluded.confidence,
                    edge_score=excluded.edge_score,
                    signal_quality=excluded.signal_quality,
                    market_session=excluded.market_session,
                    metadata_json=excluded.metadata_json
                """,
                (
                    trade.trade_id,
                    None,
                    trade.ticker,
                    trade.ticker,
                    trade.asset_class.value,
                    trade.side.value,
                    round((trade.entry_low + trade.entry_high) / 2, 4),
                    None,
                    None,
                    trade.opened_at,
                    None,
                    "OPEN",
                    None,
                    None,
                    None,
                    trade.confidence,
                    int(trade.scores.get("edge_score", 0)),
                    trade.signal_quality,
                    trade.market_session,
                    None,
                    self.namespace,
                    json.dumps(payload),
                ),
            )
            self._conn.commit()

    def record_trade_close(
        self,
        trade: TrackedTrade,
        signal: Signal,
        outcome: TradeStage,
        metrics: dict[str, float | None],
    ) -> None:
        closed_at = datetime.now(timezone.utc).isoformat()
        pnl_value = float(metrics.get("dollar_pnl") or 0.0)
        if metrics.get("dollar_pnl") is None and metrics.get("return_pct") is not None:
            pnl_value = float(metrics.get("return_pct") or 0.0)
        metadata = {
            "trade_id": trade.trade_id,
            "close_reason": outcome.value,
            "current_price": signal.current_price,
            "scores": trade.scores,
        }
        with self._lock:
            self._conn.execute(
                f"""
                UPDATE {self._trades_table}
                SET exit_price = ?,
                    exit_time = ?,
                    status = ?,
                    profit_loss = ?,
                    return_pct = ?,
                    r_multiple = ?,
                    signal_quality = ?,
                    edge_score = ?,
                    confidence = ?,
                    win = ?,
                    metadata_json = ?
                WHERE signal_id = ?
                """,
                (
                    signal.current_price,
                    closed_at,
                    "CLOSED",
                    pnl_value,
                    metrics.get("return_pct"),
                    metrics.get("r_multiple"),
                    signal.signal_quality,
                    signal.edge_score,
                    signal.confidence,
                    1 if outcome == TradeStage.CLOSED_SUCCESS else 0,
                    json.dumps(metadata),
                    trade.trade_id,
                ),
            )
            self._conn.commit()
        self.refresh_performance_metrics()

    def refresh_performance_metrics(self) -> None:
        windows = (
            ("daily", 1),
            ("weekly", 7),
        )
        for period_type, days in windows:
            self._refresh_window(period_type, days)

    def _refresh_window(self, period_type: str, days: int) -> None:
        now = datetime.now(timezone.utc)
        start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        if period_type == "weekly":
            start = start - timedelta(days=start.weekday())
        end = start + timedelta(days=days)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT profit_loss, return_pct, r_multiple, win
                FROM {self._trades_table}
                WHERE status = 'CLOSED'
                  AND exit_time >= ?
                  AND exit_time < ?
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()

            total_trades = len(rows)
            winning_trades = sum(1 for row in rows if int(row["win"] or 0) == 1)
            gross_profit = sum(max(float(row["profit_loss"] or 0.0), 0.0) for row in rows)
            gross_loss = sum(abs(min(float(row["profit_loss"] or 0.0), 0.0)) for row in rows)
            total_pnl = sum(float(row["profit_loss"] or 0.0) for row in rows)
            roi = sum(float(row["return_pct"] or 0.0) for row in rows)
            wins = [float(row["profit_loss"] or 0.0) for row in rows if float(row["profit_loss"] or 0.0) > 0]
            losses = [float(row["profit_loss"] or 0.0) for row in rows if float(row["profit_loss"] or 0.0) < 0]
            avg_win = sum(wins) / len(wins) if wins else 0.0
            avg_loss = sum(losses) / len(losses) if losses else 0.0
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)
            expectancy = (
                sum(float(row["r_multiple"] or 0.0) for row in rows) / total_trades if total_trades else 0.0
            )
            win_rate = (winning_trades / total_trades) * 100 if total_trades else 0.0

            self._conn.execute(
                f"""
                INSERT INTO {self._performance_table} (
                    period_type, period_start, period_end, total_trades, winning_trades, win_rate,
                    total_pnl, roi, avg_win, avg_loss, profit_factor, expectancy, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(period_type, period_start) DO UPDATE SET
                    period_end=excluded.period_end,
                    total_trades=excluded.total_trades,
                    winning_trades=excluded.winning_trades,
                    win_rate=excluded.win_rate,
                    total_pnl=excluded.total_pnl,
                    roi=excluded.roi,
                    avg_win=excluded.avg_win,
                    avg_loss=excluded.avg_loss,
                    profit_factor=excluded.profit_factor,
                    expectancy=excluded.expectancy,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    period_type,
                    start.date().isoformat(),
                    end.date().isoformat(),
                    total_trades,
                    winning_trades,
                    round(win_rate, 2),
                    round(total_pnl, 2),
                    round(roi, 2),
                    round(avg_win, 2),
                    round(avg_loss, 2),
                    round(profit_factor, 2),
                    round(expectancy, 2),
                ),
            )
            self._conn.commit()

    def metrics_summary(self, period_type: str = "daily") -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT period_type, period_start, period_end, total_trades, winning_trades, win_rate,
                       total_pnl, roi, avg_win, avg_loss, profit_factor, expectancy
                FROM {self._performance_table}
                WHERE period_type = ?
                ORDER BY period_start DESC
                LIMIT 1
                """,
                (period_type,),
            ).fetchone()
        if row is None:
            return {
                "period_type": period_type,
                "period_start": "",
                "period_end": "",
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "hits": 0,
                "misses": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "roi": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "expectancy": 0.0,
            }
        payload = dict(row)
        losing_trades = max(0, int(payload["total_trades"]) - int(payload["winning_trades"]))
        payload["losing_trades"] = losing_trades
        payload["hits"] = int(payload["winning_trades"])
        payload["misses"] = losing_trades
        return payload

    def leaderboard(self, period_type: str = "weekly", group_by: str = "ticker", limit: int = 5) -> list[dict[str, Any]]:
        group_map = {
            "ticker": "ticker",
            "session": "market_session",
            "asset": "asset_class",
        }
        group_column = group_map.get(group_by, "ticker")
        start, end = self._period_bounds(period_type)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT {group_column} AS label,
                       COUNT(*) AS total_trades,
                       SUM(CASE WHEN win = 1 THEN 1 ELSE 0 END) AS winning_trades,
                       AVG(COALESCE(return_pct, 0)) AS avg_return_pct,
                       AVG(COALESCE(r_multiple, 0)) AS avg_r,
                       SUM(COALESCE(profit_loss, 0)) AS total_pnl
                FROM {self._trades_table}
                WHERE status = 'CLOSED'
                  AND exit_time >= ?
                  AND exit_time < ?
                GROUP BY {group_column}
                HAVING COUNT(*) > 0
                ORDER BY AVG(COALESCE(r_multiple, 0)) DESC, SUM(COALESCE(profit_loss, 0)) DESC
                LIMIT ?
                """,
                (start.isoformat(), end.isoformat(), limit),
            ).fetchall()
        leaderboard: list[dict[str, Any]] = []
        for row in rows:
            total = int(row["total_trades"] or 0)
            wins = int(row["winning_trades"] or 0)
            leaderboard.append(
                {
                    "label": row["label"],
                    "total_trades": total,
                    "winning_trades": wins,
                    "losing_trades": max(0, total - wins),
                    "win_rate": round((wins / total) * 100, 2) if total else 0.0,
                    "avg_return_pct": round(float(row["avg_return_pct"] or 0.0), 2),
                    "avg_r": round(float(row["avg_r"] or 0.0), 2),
                    "total_pnl": round(float(row["total_pnl"] or 0.0), 2),
                }
            )
        return leaderboard

    def import_json_history(self, history: dict[str, list[dict[str, Any]]]) -> None:
        for signal_event in history.get("signals", []):
            signal_id = str(signal_event.get("trade_id", ""))
            if not signal_id:
                continue
            scores = signal_event.get("scores", {}) or {}
            entry_low = float(signal_event.get("entry_low", 0.0) or 0.0)
            entry_high = float(signal_event.get("entry_high", 0.0) or 0.0)
            metadata = {
                "trade_id": signal_id,
                "chat_id": signal_event.get("chat_id"),
                "stop_loss": signal_event.get("stop_loss"),
                "take_profit_1": signal_event.get("take_profit_1"),
                "take_profit_2": signal_event.get("take_profit_2"),
                "opened_at": signal_event.get("opened_at"),
                "migrated": True,
            }
            with self._lock:
                self._conn.execute(
                    f"""
                    INSERT INTO {self._signals_table} (
                        signal_id, timestamp, source_bot, wallet_address, transaction_hash, token_address,
                        ticker, asset_class, amount_usd, signal_type, side, confidence, edge_score,
                        confluence_count, signal_quality, market_session, scores_json, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(signal_id) DO NOTHING
                    """,
                    (
                        signal_id,
                        str(signal_event.get("opened_at") or signal_event.get("timestamp") or ""),
                        self.namespace,
                        signal_event.get("wallet_address"),
                        signal_event.get("transaction_hash"),
                        signal_event.get("token_address"),
                        str(signal_event.get("ticker", "")),
                        str(signal_event.get("asset_class", "")),
                        signal_event.get("amount_usd"),
                        str(signal_event.get("stage", signal_event.get("signal_type", "SIGNAL"))),
                        str(signal_event.get("side", "")),
                        int(signal_event.get("confidence", 0) or 0),
                        int(scores.get("edge_score", 0) or 0),
                        int(scores.get("confluence_count", 0) or 0),
                        str(signal_event.get("signal_quality", "watchlist")),
                        str(signal_event.get("market_session", "")),
                        json.dumps(scores),
                        json.dumps(metadata),
                    ),
                )
                self._conn.execute(
                    f"""
                    INSERT INTO {self._trades_table} (
                        signal_id, wallet_address, token_address, ticker, asset_class, action, entry_price,
                        exit_price, position_size, entry_time, exit_time, status, profit_loss, return_pct,
                        r_multiple, confidence, edge_score, signal_quality, market_session, win, source_bot, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(signal_id) DO NOTHING
                    """,
                    (
                        signal_id,
                        signal_event.get("wallet_address"),
                        signal_event.get("token_address"),
                        str(signal_event.get("ticker", "")),
                        str(signal_event.get("asset_class", "")),
                        str(signal_event.get("side", "")),
                        round((entry_low + entry_high) / 2, 4) if entry_low or entry_high else signal_event.get("entry_price"),
                        None,
                        signal_event.get("position_size"),
                        str(signal_event.get("opened_at") or ""),
                        None,
                        "OPEN",
                        None,
                        None,
                        None,
                        int(signal_event.get("confidence", 0) or 0),
                        int(scores.get("edge_score", 0) or 0),
                        str(signal_event.get("signal_quality", "watchlist")),
                        str(signal_event.get("market_session", "")),
                        None,
                        self.namespace,
                        json.dumps(metadata),
                    ),
                )

        for closure in history.get("closures", []):
            signal_id = str(closure.get("trade_id", ""))
            if not signal_id:
                continue
            metadata = {
                "trade_id": signal_id,
                "close_reason": closure.get("outcome"),
                "migrated": True,
            }
            outcome = str(closure.get("outcome", ""))
            with self._lock:
                self._conn.execute(
                    f"""
                    UPDATE {self._trades_table}
                    SET exit_price = COALESCE(?, exit_price),
                        exit_time = COALESCE(?, exit_time),
                        status = 'CLOSED',
                        profit_loss = ?,
                        return_pct = ?,
                        r_multiple = ?,
                        confidence = ?,
                        signal_quality = ?,
                        market_session = ?,
                        win = ?,
                        metadata_json = ?
                    WHERE signal_id = ?
                    """,
                    (
                        closure.get("close_price"),
                        str(closure.get("closed_at") or ""),
                        closure.get("dollar_pnl"),
                        closure.get("return_pct"),
                        closure.get("r_multiple"),
                        int(closure.get("confidence", 0) or 0),
                        str(closure.get("signal_quality", "watchlist")),
                        str(closure.get("market_session", "")),
                        1 if outcome == TradeStage.CLOSED_SUCCESS.value else 0,
                        json.dumps(metadata),
                        signal_id,
                    ),
                )
                self._conn.execute(
                    f"""
                    INSERT INTO {self._signals_table} (
                        signal_id, timestamp, source_bot, wallet_address, transaction_hash, token_address,
                        ticker, asset_class, amount_usd, signal_type, side, confidence, edge_score,
                        confluence_count, signal_quality, market_session, scores_json, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(signal_id) DO NOTHING
                    """,
                    (
                        signal_id,
                        str(closure.get("opened_at") or closure.get("closed_at") or ""),
                        self.namespace,
                        closure.get("wallet_address"),
                        closure.get("transaction_hash"),
                        closure.get("token_address"),
                        str(closure.get("ticker", "")),
                        str(closure.get("asset_class", "")),
                        closure.get("amount_usd"),
                        outcome,
                        str(closure.get("side", "")),
                        int(closure.get("confidence", 0) or 0),
                        0,
                        0,
                        str(closure.get("signal_quality", "watchlist")),
                        str(closure.get("market_session", "")),
                        json.dumps(closure.get("scores", {}) or {}),
                        json.dumps(metadata),
                    ),
                )
            self._conn.commit()
        self.refresh_performance_metrics()

    def _period_bounds(self, period_type: str) -> tuple[datetime, datetime]:
        now = datetime.now(timezone.utc)
        start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        if period_type == "weekly":
            start = start - timedelta(days=start.weekday())
            end = start + timedelta(days=7)
        else:
            end = start + timedelta(days=1)
        return start, end
