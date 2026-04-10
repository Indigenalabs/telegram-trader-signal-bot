from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator


class Database:
    def __init__(self, db_path: str) -> None:
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        self.db_path = db_path
        self._init()

    @contextmanager
    def conn(self) -> Generator[sqlite3.Connection, None, None]:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _init(self) -> None:
        with self.conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    position_size REAL NOT NULL,
                    risk_amount REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    atr REAL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT,
                    status TEXT DEFAULT 'OPEN',
                    exit_reason TEXT,
                    pnl REAL,
                    pnl_pct REAL,
                    r_multiple REAL,
                    win INTEGER,
                    regime TEXT,
                    signal_score REAL,
                    metadata TEXT DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_pt_status ON paper_trades(status);
                CREATE INDEX IF NOT EXISTS idx_pt_ticker ON paper_trades(ticker);
                CREATE INDEX IF NOT EXISTS idx_pt_entry ON paper_trades(entry_time);

                CREATE TABLE IF NOT EXISTS performance_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_time TEXT NOT NULL,
                    period TEXT NOT NULL,
                    total_trades INTEGER,
                    winning_trades INTEGER,
                    losing_trades INTEGER,
                    win_rate REAL,
                    total_pnl REAL,
                    avg_win REAL,
                    avg_loss REAL,
                    profit_factor REAL,
                    avg_r REAL,
                    expectancy REAL,
                    capital REAL
                );
            """)

    # --- Paper trade CRUD ---

    def open_trade(
        self,
        ticker: str,
        side: str,
        entry_price: float,
        position_size: float,
        risk_amount: float,
        stop_loss: float,
        take_profit: float,
        atr: float,
        regime: str,
        signal_score: float,
        metadata: dict | None = None,
    ) -> int:
        with self.conn() as c:
            cur = c.execute(
                """INSERT INTO paper_trades
                   (ticker, side, entry_price, position_size, risk_amount,
                    stop_loss, take_profit, atr, entry_time, regime, signal_score, metadata)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ticker, side, entry_price, position_size, risk_amount,
                    stop_loss, take_profit, atr,
                    datetime.now(timezone.utc).isoformat(),
                    regime, signal_score,
                    json.dumps(metadata or {}),
                ),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def close_trade(
        self,
        trade_id: int,
        exit_price: float,
        exit_reason: str,
    ) -> dict[str, Any] | None:
        with self.conn() as c:
            row = c.execute(
                "SELECT * FROM paper_trades WHERE id=?", (trade_id,)
            ).fetchone()
            if not row:
                return None

            entry = float(row["entry_price"])
            size = float(row["position_size"])
            risk = float(row["risk_amount"])
            side = row["side"]

            if side == "LONG":
                pnl_per_unit = exit_price - entry
            else:
                pnl_per_unit = entry - exit_price

            pnl = pnl_per_unit * size
            pnl_pct = (pnl_per_unit / entry) * 100
            r_multiple = pnl / risk if risk else 0.0
            win = 1 if pnl > 0 else 0

            c.execute(
                """UPDATE paper_trades SET
                   exit_price=?, exit_time=?, status='CLOSED',
                   exit_reason=?, pnl=?, pnl_pct=?, r_multiple=?, win=?
                   WHERE id=?""",
                (
                    exit_price,
                    datetime.now(timezone.utc).isoformat(),
                    exit_reason, round(pnl, 4), round(pnl_pct, 4),
                    round(r_multiple, 4), win, trade_id,
                ),
            )
            return {
                "trade_id": trade_id,
                "ticker": row["ticker"],
                "side": side,
                "entry": entry,
                "exit": exit_price,
                "pnl": round(pnl, 4),
                "pnl_pct": round(pnl_pct, 4),
                "r_multiple": round(r_multiple, 4),
                "win": bool(win),
                "exit_reason": exit_reason,
            }

    def get_open_trades(self) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY entry_time"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_stats(self, days: int = 7) -> dict[str, Any]:
        with self.conn() as c:
            row = c.execute(
                f"""SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN win=1 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN win=0 THEN 1 ELSE 0 END) as losses,
                    SUM(pnl) as total_pnl,
                    AVG(CASE WHEN win=1 THEN pnl ELSE NULL END) as avg_win,
                    AVG(CASE WHEN win=0 THEN pnl ELSE NULL END) as avg_loss,
                    AVG(r_multiple) as avg_r
                FROM paper_trades
                WHERE status='CLOSED'
                AND entry_time >= datetime('now', '-{days} days')"""
            ).fetchone()
            if not row or not row["total"]:
                return {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0.0,
                        "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                        "profit_factor": 0.0, "avg_r": 0.0, "expectancy": 0.0}
            total = row["total"] or 0
            wins = row["wins"] or 0
            avg_win = float(row["avg_win"] or 0.0)
            avg_loss = float(row["avg_loss"] or 0.0)
            win_rate = wins / total if total else 0.0
            profit_factor = (
                (avg_win * wins) / (abs(avg_loss) * (total - wins))
                if avg_loss and (total - wins) > 0
                else 0.0
            )
            avg_r = float(row["avg_r"] or 0.0)
            expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
            return {
                "total": total, "wins": wins, "losses": total - wins,
                "total_pnl": round(float(row["total_pnl"] or 0.0), 2),
                "win_rate": round(win_rate * 100, 2),
                "avg_win": round(avg_win, 4),
                "avg_loss": round(avg_loss, 4),
                "profit_factor": round(profit_factor, 2),
                "avg_r": round(avg_r, 4),
                "expectancy": round(expectancy, 4),
            }

    def get_leaderboard(self, limit: int = 5) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                """SELECT ticker,
                   COUNT(*) as total,
                   SUM(CASE WHEN win=1 THEN 1 ELSE 0 END) as wins,
                   SUM(pnl) as total_pnl,
                   AVG(r_multiple) as avg_r
                FROM paper_trades WHERE status='CLOSED'
                GROUP BY ticker HAVING total >= 3
                ORDER BY total_pnl DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def save_performance_snapshot(self, stats: dict, capital: float, period: str = "daily") -> None:
        with self.conn() as c:
            c.execute(
                """INSERT INTO performance_snapshots
                   (snapshot_time, period, total_trades, winning_trades, losing_trades,
                    win_rate, total_pnl, avg_win, avg_loss, profit_factor, avg_r, expectancy, capital)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    datetime.now(timezone.utc).isoformat(), period,
                    stats.get("total", 0), stats.get("wins", 0), stats.get("losses", 0),
                    stats.get("win_rate", 0.0), stats.get("total_pnl", 0.0),
                    stats.get("avg_win", 0.0), stats.get("avg_loss", 0.0),
                    stats.get("profit_factor", 0.0), stats.get("avg_r", 0.0),
                    stats.get("expectancy", 0.0), round(capital, 2),
                ),
            )
