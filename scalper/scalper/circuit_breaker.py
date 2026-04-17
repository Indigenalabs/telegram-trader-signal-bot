"""
Circuit Breaker — halts new trade entries when drawdown thresholds are hit.

Trips when EITHER:
  - Consecutive losses >= MAX_CONSECUTIVE_LOSSES (default 3)
  - Daily drawdown >= MAX_DAILY_DRAWDOWN_PCT (default 3.0%)

Auto-resets at midnight UTC (new trading day).
Consecutive loss counter resets on any winning trade.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


class CircuitBreaker:
    def __init__(
        self,
        max_consecutive_losses: int = 3,
        max_daily_drawdown_pct: float = 3.0,
    ) -> None:
        self.max_consecutive_losses = max_consecutive_losses
        self.max_daily_drawdown_pct = max_daily_drawdown_pct

        self._consecutive_losses: int = 0
        self._daily_start_capital: float | None = None
        self._tripped: bool = False
        self._trip_reason: str = ""
        self._last_day: str = ""

    # ── Public API ────────────────────────────────────────────────────────────

    def check(self, current_capital: float) -> tuple[bool, str]:
        """
        Call before opening any new trade.
        Returns (is_tripped, reason). If tripped, skip the trade.
        """
        self._maybe_reset(current_capital)

        if self._tripped:
            return True, self._trip_reason

        # Consecutive loss check
        if self._consecutive_losses >= self.max_consecutive_losses:
            self._trip(f"{self._consecutive_losses} consecutive losses — pausing new entries.")
            return True, self._trip_reason

        # Daily drawdown check
        if self._daily_start_capital and self._daily_start_capital > 0:
            dd_pct = (self._daily_start_capital - current_capital) / self._daily_start_capital * 100
            if dd_pct >= self.max_daily_drawdown_pct:
                self._trip(f"{dd_pct:.1f}% daily drawdown hit — pausing new entries.")
                return True, self._trip_reason

        return False, ""

    def record_trade_result(self, win: bool) -> None:
        """Call after every trade close to update the consecutive loss counter."""
        if win:
            if self._consecutive_losses > 0:
                log.info("Circuit breaker: win recorded — consecutive loss counter reset.")
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
            log.info(
                "Circuit breaker: loss recorded — consecutive losses = %d / %d",
                self._consecutive_losses, self.max_consecutive_losses,
            )

    def is_tripped(self) -> bool:
        return self._tripped

    def status(self) -> dict:
        return {
            "tripped": self._tripped,
            "trip_reason": self._trip_reason,
            "consecutive_losses": self._consecutive_losses,
            "daily_start_capital": self._daily_start_capital,
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _trip(self, reason: str) -> None:
        self._tripped = True
        self._trip_reason = reason
        log.warning("CIRCUIT BREAKER TRIPPED: %s", reason)

    def _maybe_reset(self, current_capital: float) -> None:
        """Auto-reset at the start of a new UTC day."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._last_day:
            if self._tripped:
                log.info("Circuit breaker: new day — resetting (was: %s).", self._trip_reason)
            self._last_day = today
            self._daily_start_capital = current_capital
            self._tripped = False
            self._trip_reason = ""
            # Consecutive losses carry over into the new day — a bad streak
            # doesn't reset just because midnight passed
