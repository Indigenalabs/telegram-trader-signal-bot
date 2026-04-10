from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable

from .config import Config
from .database import Database
from .indicators import score_signal
from .learning_bridge import record_scalper_closure
from .market_data import fetch_ohlcv, fetch_price
from .regime_reader import is_tradeable_regime

log = logging.getLogger(__name__)


class PaperTrader:
    """
    Core paper trading engine.

    Scan loop (every SCAN_INTERVAL_SECONDS):
      1. Fetch OHLCV for each ticker
      2. Score signal
      3. Open new trade if criteria met and slot available
      4. Monitor open trades: close at TP / SL / timeout
    """

    def __init__(
        self,
        db: Database,
        cfg: Config,
        on_trade_opened: Callable[[dict], None] | None = None,
        on_trade_closed: Callable[[dict], None] | None = None,
    ) -> None:
        self.db = db
        self.cfg = cfg
        self.on_trade_opened = on_trade_opened
        self.on_trade_closed = on_trade_closed

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_once(self) -> None:
        """Execute one full scan-and-monitor cycle."""
        self._monitor_open_trades()
        open_trades = self.db.get_open_trades()
        if len(open_trades) >= self.cfg.MAX_CONCURRENT_TRADES:
            return
        slots = self.cfg.MAX_CONCURRENT_TRADES - len(open_trades)
        open_tickers = {t["ticker"] for t in open_trades}

        for ticker in self.cfg.TICKERS:
            if slots <= 0:
                break
            if ticker in open_tickers:
                continue
            data = fetch_ohlcv(
                ticker,
                interval=self.cfg.CANDLE_INTERVAL,
                limit=self.cfg.CANDLE_LIMIT,
                api_key=self.cfg.BINANCE_API_KEY,
            )
            if data is None:
                continue
            sig = score_signal(
                closes=data["closes"],
                highs=data["highs"],
                lows=data["lows"],
                volumes=data["volumes"],
                current_price=data["current_price"],
            )
            if sig["side"] is None:
                continue
            if sig["vol_ratio"] < self.cfg.MIN_VOLUME_RATIO:
                continue
            if not is_tradeable_regime(ticker, self.cfg.REGIME_FILE, sig["side"]):
                log.debug("%s regime blocks %s", ticker, sig["side"])
                continue

            trade_id = self._open_trade(ticker, data["current_price"], sig)
            if trade_id:
                slots -= 1
                open_tickers.add(ticker)

    def run_forever(self) -> None:
        """Blocking loop — call from main thread."""
        log.info("PaperTrader started (interval=%ss)", self.cfg.SCAN_INTERVAL_SECONDS)
        while True:
            try:
                self.run_once()
            except Exception:
                log.exception("Error in scan cycle")
            time.sleep(self.cfg.SCAN_INTERVAL_SECONDS)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_trade(self, ticker: str, price: float, sig: dict) -> int | None:
        atr_val = sig["atr_val"]
        side = sig["side"]

        if atr_val <= 0:
            log.debug("%s atr=0, skipping", ticker)
            return None

        sl_dist = atr_val * self.cfg.STOP_LOSS_ATR_MULT
        tp_dist = atr_val * self.cfg.TAKE_PROFIT_ATR_MULT

        if side == "LONG":
            stop_loss = price - sl_dist
            take_profit = price + tp_dist
        else:
            stop_loss = price + sl_dist
            take_profit = price - tp_dist

        risk_per_unit = abs(price - stop_loss)
        if risk_per_unit <= 0:
            return None

        capital = self._estimate_capital()
        risk_amount = capital * self.cfg.RISK_PER_TRADE
        position_size = risk_amount / risk_per_unit

        regime = _safe_read_regime(ticker, self.cfg.REGIME_FILE)

        trade_id = self.db.open_trade(
            ticker=ticker,
            side=side,
            entry_price=price,
            position_size=position_size,
            risk_amount=risk_amount,
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr=atr_val,
            regime=regime,
            signal_score=sig["score"],
            metadata={
                "ema9": sig.get("ema9"),
                "ema21": sig.get("ema21"),
                "rsi": sig.get("rsi"),
                "vol_ratio": sig.get("vol_ratio"),
                "vwap": sig.get("vwap_val"),
                "details": sig.get("details", []),
            },
        )
        log.info(
            "OPEN %s %s @ %.4f  SL=%.4f TP=%.4f  score=%.0f  trade_id=%s",
            side, ticker, price, stop_loss, take_profit, sig["score"], trade_id,
        )
        if self.on_trade_opened:
            self.on_trade_opened({
                "trade_id": trade_id,
                "ticker": ticker,
                "side": side,
                "entry": price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "score": sig["score"],
                "details": sig.get("details", []),
            })
        return trade_id

    def _monitor_open_trades(self) -> None:
        trades = self.db.get_open_trades()
        if not trades:
            return

        # Fetch current prices for all open tickers (fast single-call per ticker)
        prices: dict[str, float] = {}
        for trade in trades:
            ticker = trade["ticker"]
            if ticker in prices:
                continue
            price = fetch_price(ticker, api_key=self.cfg.BINANCE_API_KEY)
            if price is not None:
                prices[ticker] = price

        now = datetime.now(timezone.utc)
        for trade in trades:
            ticker = trade["ticker"]
            price = prices.get(ticker)
            if price is None:
                continue

            side = trade["side"]
            sl = float(trade["stop_loss"])
            tp = float(trade["take_profit"])
            entry_time = datetime.fromisoformat(trade["entry_time"])
            hold_minutes = (now - entry_time).total_seconds() / 60

            exit_reason: str | None = None
            if side == "LONG":
                if price <= sl:
                    exit_reason = "SL"
                elif price >= tp:
                    exit_reason = "TP"
            else:
                if price >= sl:
                    exit_reason = "SL"
                elif price <= tp:
                    exit_reason = "TP"

            if exit_reason is None and hold_minutes >= self.cfg.MAX_HOLD_MINUTES:
                exit_reason = "TIMEOUT"

            if exit_reason:
                result = self.db.close_trade(trade["id"], price, exit_reason)
                if result:
                    log.info(
                        "CLOSE %s %s @ %.4f  reason=%s  pnl=%.4f  R=%.2f",
                        result["side"], result["ticker"], price,
                        exit_reason, result["pnl"], result["r_multiple"],
                    )
                    # Feed outcome back into signal bot's learning model
                    record_scalper_closure(
                        signal_bot_data_dir=self.cfg.SIGNAL_BOT_DATA_DIR,
                        ticker=result["ticker"],
                        side=result["side"],
                        entry_price=result["entry"],
                        exit_price=result["exit"],
                        r_multiple=result["r_multiple"],
                        return_pct=result["pnl_pct"],
                        win=result["win"],
                        opened_at=trade["entry_time"],
                        candle_interval=self.cfg.CANDLE_INTERVAL,
                        confidence=float(trade.get("signal_score") or 0),
                    )
                    if self.on_trade_closed:
                        self.on_trade_closed(result)

    def _estimate_capital(self) -> float:
        """Simple capital estimate: initial + sum of all closed pnl."""
        stats = self.db.get_stats(days=3650)
        return self.cfg.INITIAL_CAPITAL + stats.get("total_pnl", 0.0)


def _safe_read_regime(ticker: str, regime_file: str) -> str:
    try:
        from .regime_reader import get_regime_label
        return get_regime_label(ticker, regime_file)
    except Exception:
        return "unknown"
