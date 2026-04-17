from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from .config import Config
from .database import Database
from .paper_trader import PaperTrader
from .telegram_notifier import TelegramNotifier

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


class ScalperBot:
    def __init__(self) -> None:
        self.cfg = Config()
        self.db = Database(self.cfg.DB_PATH)
        self.notifier = TelegramNotifier(
            token=self.cfg.TELEGRAM_BOT_TOKEN,
            chat_id=self.cfg.TELEGRAM_CHAT_ID,
        )
        self.trader = PaperTrader(
            db=self.db,
            cfg=self.cfg,
            on_trade_opened=self._on_opened,
            on_trade_closed=self._on_closed,
        )
        # Track last report date as ISO date string so it survives the hour window
        self._last_report_date: str = ""
        # Track milestone alerts already sent (set of trade counts)
        self._milestones_sent: set[int] = set()

    def _on_opened(self, trade: dict) -> None:
        self.notifier.trade_opened(trade)

    def _on_closed(self, trade: dict) -> None:
        self.notifier.trade_closed(trade)
        # Alert if circuit breaker just tripped on this close
        cb_reason = trade.get("circuit_breaker")
        if cb_reason:
            self.notifier.send(
                f"🔴 <b>Circuit Breaker Tripped</b>\n"
                f"Reason: {cb_reason}\n"
                f"New entries paused until midnight UTC or on next winning trade."
            )

    def _maybe_daily_report(self) -> None:
        now = datetime.now(timezone.utc)
        # Only fire at the configured hour, and only once per calendar date
        if now.hour != self.cfg.REPORT_HOUR_UTC:
            return
        today = now.strftime("%Y-%m-%d")
        if today == self._last_report_date:
            return
        self._last_report_date = today
        # Report on yesterday's trades (the just-completed trading day)
        stats = self.db.get_stats(days=1)
        capital = self.cfg.INITIAL_CAPITAL + self.db.get_stats(days=3650).get("total_pnl", 0.0)
        leaderboard = self.db.get_leaderboard(limit=5)
        self.notifier.daily_report(stats, capital, leaderboard)
        self.db.save_performance_snapshot(stats, capital, period="daily")
        log.info("Daily report sent for %s", today)

    def _check_live_readiness(self) -> None:
        stats = self.db.get_stats(days=3650)
        total = stats["total"]
        milestone = self.cfg.MIN_PAPER_TRADES_FOR_LIVE
        # Only fire once when we cross the milestone, never again
        if total >= milestone and milestone not in self._milestones_sent:
            self._milestones_sent.add(milestone)
            wr = stats["win_rate"]
            pf = stats["profit_factor"]
            ar = stats["avg_r"]
            self.notifier.send(
                f"🎓 <b>Paper trading milestone: {total} trades</b>\n"
                f"Win Rate: {wr:.1f}%  |  Profit Factor: {pf:.2f}  |  Avg R: {ar:+.2f}R\n\n"
                "Review results before switching to live trading."
            )

    def run(self) -> None:
        _setup_logging()
        log.info("ScalperBot initialising")
        self.notifier.startup_message(self.cfg.TICKERS, self.cfg.PAPER_TRADING)

        # Pre-populate milestone set so a restart doesn't re-fire old milestones
        stats = self.db.get_stats(days=3650)
        if stats["total"] >= self.cfg.MIN_PAPER_TRADES_FOR_LIVE:
            self._milestones_sent.add(self.cfg.MIN_PAPER_TRADES_FOR_LIVE)

        while True:
            try:
                self.trader.run_once()
                self._maybe_daily_report()
                self._check_live_readiness()
            except Exception:
                log.exception("Unhandled error in main loop")
            time.sleep(self.cfg.SCAN_INTERVAL_SECONDS)
