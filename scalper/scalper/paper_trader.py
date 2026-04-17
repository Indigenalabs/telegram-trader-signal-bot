from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable

from .config import Config
from .database import Database
from .indicators import score_signal
from .learning_bridge import record_scalper_closure
from .market_data import fetch_ohlcv, fetch_price
from .regime_reader import is_tradeable_regime
from .signal_bridge import get_pending_signal_bot_trades, mark_acted
from .ticker_scanner import get_top_tickers

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
        # Mirror any new signal bot crypto signals first (higher quality — 85% WR)
        self._mirror_signal_bot_trades()

        open_trades = self.db.get_open_trades()
        if len(open_trades) >= self.cfg.MAX_CONCURRENT_TRADES:
            return
        slots = self.cfg.MAX_CONCURRENT_TRADES - len(open_trades)
        open_tickers = {t["ticker"] for t in open_trades}

        self._scan_and_trade(slots, open_tickers)

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

    def _scan_and_trade(self, slots: int, open_tickers: set[str]) -> None:
        """
        Fetch the dynamic ticker universe, score all tickers in parallel,
        rank by score, and open trades on the top setups.

        Falls back to config TICKERS if the dynamic scanner returns nothing.
        """
        universe = get_top_tickers(
            api_key=self.cfg.BINANCE_API_KEY,
            min_volume_usdt=self.cfg.UNIVERSE_MIN_VOLUME_USDT,
            max_tickers=self.cfg.UNIVERSE_MAX_TICKERS,
        )
        # Fallback to hardcoded list if Binance API unreachable
        if not universe:
            universe = self.cfg.TICKERS

        candidates = [t for t in universe if t not in open_tickers]
        if not candidates:
            return

        def _fetch_score(ticker: str) -> tuple[str, dict, dict] | None:
            data = fetch_ohlcv(
                ticker,
                interval=self.cfg.CANDLE_INTERVAL,
                limit=self.cfg.CANDLE_LIMIT,
                api_key=self.cfg.BINANCE_API_KEY,
            )
            if data is None:
                return None
            sig = score_signal(
                opens=data.get("opens", data["closes"]),
                closes=data["closes"],
                highs=data["highs"],
                lows=data["lows"],
                volumes=data["volumes"],
                current_price=data["current_price"],
            )
            if sig["side"] is None:
                return None
            return ticker, data, sig

        # Parallel fetch — 10 workers keeps total time under ~10s for 50 tickers
        scored: list[tuple[str, dict, dict]] = []
        with ThreadPoolExecutor(max_workers=self.cfg.SCAN_WORKERS) as pool:
            futures = {pool.submit(_fetch_score, t): t for t in candidates}
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        scored.append(result)
                except Exception as exc:
                    log.debug("scan worker error: %s", exc)

        if not scored:
            return

        # Rank by score descending — best setup gets first slot
        scored.sort(key=lambda x: x[2]["score"], reverse=True)
        log.info(
            "Universe scan: %d/%d tickers with signals | top: %s %.0f",
            len(scored), len(candidates),
            scored[0][0], scored[0][2]["score"],
        )

        for ticker, data, sig in scored:
            if slots <= 0:
                break
            if ticker in open_tickers:
                continue
            if not is_tradeable_regime(ticker, self.cfg.REGIME_FILE, sig["side"]):
                log.debug("%s regime blocks %s", ticker, sig["side"])
                continue
            trade_id = self._open_trade(ticker, data["current_price"], sig)
            if trade_id:
                slots -= 1
                open_tickers.add(ticker)

    def _mirror_signal_bot_trades(self) -> None:
        """
        Open paper positions for any SIGNAL-stage crypto trades from the signal bot
        that haven't been acted on yet. Signal bot runs at 85% WR — these are
        high-quality setups we want to capture in addition to the scalper's own plays.
        """
        pending = get_pending_signal_bot_trades(
            bot_state_db=self.cfg.SIGNAL_BOT_STATE_DB,
            acted_file=self.cfg.SIGNAL_BOT_ACTED_FILE,
        )
        if not pending:
            return

        open_trades = self.db.get_open_trades()
        open_tickers = {t["ticker"] for t in open_trades}

        for sig in pending:
            trade_id = sig["trade_id"]
            ticker = sig["ticker"]
            side = sig["side"]
            entry = float(sig["entry"])
            sl = float(sig["stop_loss"])
            tp = float(sig["take_profit"])

            # Always mark acted — even if we skip — so we don't re-check every scan
            mark_acted(self.cfg.SIGNAL_BOT_ACTED_FILE, trade_id)

            # Skip if at capacity or already in this ticker
            if len(open_trades) >= self.cfg.MAX_CONCURRENT_TRADES:
                break
            if ticker in open_tickers:
                continue

            # Validate levels are geometrically sound
            if side == "LONG" and not (sl < entry < tp):
                log.debug("signal_bridge: %s LONG levels invalid — skip", ticker)
                continue
            if side == "SHORT" and not (tp < entry < sl):
                log.debug("signal_bridge: %s SHORT levels invalid — skip", ticker)
                continue

            risk_per_unit = abs(entry - sl)
            if risk_per_unit <= 0:
                continue

            capital = self._estimate_capital()
            risk_amount = capital * self.cfg.RISK_PER_TRADE
            position_size = risk_amount / risk_per_unit
            regime = _safe_read_regime(ticker, self.cfg.REGIME_FILE)

            db_trade_id = self.db.open_trade(
                ticker=ticker,
                side=side,
                entry_price=entry,
                position_size=position_size,
                risk_amount=risk_amount,
                stop_loss=sl,
                take_profit=tp,
                atr=0.0,
                regime=regime,
                signal_score=float(sig["confidence"]),
                metadata={
                    "source": "signal_bot",
                    "signal_trade_id": trade_id,
                    "signal_quality": sig.get("signal_quality", ""),
                },
            )
            open_tickers.add(ticker)
            # Rebuild open_trades count for the capacity check
            open_trades = self.db.get_open_trades()

            log.info(
                "SIGNAL-BOT MIRROR %s %s @ %.4f  SL=%.4f TP=%.4f  confidence=%s",
                side, ticker, entry, sl, tp, sig["confidence"],
            )
            if self.on_trade_opened:
                self.on_trade_opened({
                    "trade_id": db_trade_id,
                    "ticker": ticker,
                    "side": side,
                    "entry": entry,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "score": float(sig["confidence"]),
                    "details": [f"Mirrored from signal bot ({sig.get('signal_quality', '')})"],
                })

    def _open_trade(self, ticker: str, price: float, sig: dict) -> int | None:
        atr_val = sig["atr_val"]
        side = sig["side"]

        # Prefer Ajna structure-based SL/TP from confirmation block (2R)
        conf_block = sig.get("conf_block")
        if conf_block and sig.get("sl") and sig.get("tp"):
            stop_loss = sig["sl"]
            take_profit = sig["tp"]
        else:
            # Fall back to ATR-based stops
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

        conf_block = sig.get("conf_block") or {}
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
                "vwap": sig.get("vwap_val"),
                "conf_block_type": conf_block.get("rev_type"),
                "conf_block_idx": conf_block.get("idx"),
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
