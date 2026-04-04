import unittest
from datetime import datetime, timezone
from tempfile import TemporaryDirectory

from trader_signal_bot.bot.handlers import (
    _build_tracked_trade,
    _is_actionable_signal,
    _is_strong_signal,
    _parse_tickers,
    _trade_close_metrics,
    _trade_close_outcome,
)
from trader_signal_bot.config import SCAN_PRESETS
from trader_signal_bot.domain import AssetClass, PriceSnapshot, Signal, SignalSide, TradeStage
from trader_signal_bot.config import Settings
from trader_signal_bot.services.analysis import market_session_label, risk_analysis, technical_analysis
from trader_signal_bot.services.learning import LearningService
from trader_signal_bot.services.market_data import _to_binance_symbol
from trader_signal_bot.services.news import NewsService, ticker_to_query
from trader_signal_bot.services.registry import get_instrument_profile
from trader_signal_bot.services.sqlite_state import SQLiteStateStore
from trader_signal_bot.services.state import UserStateStore


class AnalysisTests(unittest.TestCase):
    def _sample_signal(
        self,
        *,
        ticker: str = "BTC-USD",
        side: SignalSide = SignalSide.LONG,
        current_price: float = 100.0,
        confidence: int = 67,
        quality: str = "high",
        confluence_count: int = 4,
        edge_score: int = 78,
    ) -> Signal:
        return Signal(
            ticker=ticker,
            asset_class=AssetClass.CRYPTO,
            side=side,
            current_price=current_price,
            entry_low=99.0,
            entry_high=101.0,
            stop_loss=95.0 if side == SignalSide.LONG else 105.0,
            take_profit_1=108.0 if side == SignalSide.LONG else 92.0,
            take_profit_2=112.0 if side == SignalSide.LONG else 88.0,
            confidence=confidence,
            timeframe="2-5 days",
            rationale=["Trend is aligned."],
            scores={"technical": 70.0},
            price_source="Binance",
            pricing_symbol="BTCUSDT",
            pricing_currency="USD",
            market_session="24/7 crypto market",
            signal_quality=quality,
            confluence_count=confluence_count,
            edge_score=edge_score,
        )

    def test_binance_symbol_mapping(self) -> None:
        self.assertEqual(_to_binance_symbol("BTC-USD"), "BTCUSDT")
        self.assertEqual(_to_binance_symbol("eth-usdt"), "ETHUSDT")
        self.assertIsNone(_to_binance_symbol("SPY"))

    def test_news_query_mapping(self) -> None:
        self.assertEqual(ticker_to_query("BTC-USD"), "Bitcoin OR BTC OR crypto market")
        self.assertEqual(ticker_to_query("SPY"), "S&P 500 OR SPY ETF")
        self.assertIn("Facebook", ticker_to_query("META"))

    def test_registry_returns_company_aliases(self) -> None:
        profile = get_instrument_profile("META")
        self.assertEqual(profile.display_name, "Meta Platforms")
        self.assertIn("Instagram", profile.news_query)

    def test_parse_tickers_supports_spaces_and_commas(self) -> None:
        self.assertEqual(
            _parse_tickers(["btc-usd,eth-usd", "spy", "ETH-USD"]),
            ["BTC-USD", "ETH-USD", "SPY"],
        )

    def test_watchlist_set_and_clear(self) -> None:
        store = UserStateStore(default_risk_per_trade=0.01)
        profile = store.set_watchlist(1, ["BTC-USD", "ETH-USD", "BTC-USD"])
        self.assertEqual(profile.watchlist, ["BTC-USD", "ETH-USD"])
        cleared = store.clear_watchlist(1)
        self.assertEqual(cleared.watchlist, [])
        updated = store.set_alert_mode(1, "off")
        self.assertEqual(updated.alert_mode, "off")
        self.assertEqual(store.backend_name(), "memory")
        self.assertFalse(store.persistence_enabled())

    def test_alert_deduping_requires_change(self) -> None:
        store = UserStateStore(default_risk_per_trade=0.01)
        self.assertTrue(store.should_send_alert(1, "BTC-USD", "LONG", 61))
        self.assertFalse(store.should_send_alert(1, "BTC-USD", "LONG", 61))
        self.assertFalse(store.should_send_alert(1, "BTC-USD", "LONG", 64))
        self.assertTrue(store.should_send_alert(1, "BTC-USD", "LONG", 66))
        self.assertTrue(store.should_send_alert(1, "BTC-USD", "SHORT", 66))

    def test_tracked_trade_round_trip(self) -> None:
        store = UserStateStore(default_risk_per_trade=0.01)
        trade = _build_tracked_trade(1, self._sample_signal(), TradeStage.ARMING)
        store.set_tracked_trade(trade)
        loaded = store.get_tracked_trade(1, "btc-usd")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.stage, TradeStage.ARMING)
        store.clear_tracked_trade(1, "BTC-USD")
        self.assertIsNone(store.get_tracked_trade(1, "BTC-USD"))

    def test_signal_threshold_helpers(self) -> None:
        settings = Settings()
        strong_signal = self._sample_signal(confidence=69, quality="high")
        arming_signal = self._sample_signal(confidence=60, quality="tradable")
        weak_signal = self._sample_signal(confidence=54, quality="watchlist", edge_score=54, confluence_count=2)

        self.assertTrue(_is_actionable_signal(strong_signal, settings))
        self.assertTrue(_is_strong_signal(strong_signal, settings))
        self.assertTrue(_is_actionable_signal(arming_signal, settings))
        self.assertFalse(_is_strong_signal(arming_signal, settings))
        self.assertFalse(_is_actionable_signal(weak_signal, settings))

    def test_edge_mode_requires_confluence_and_edge_score(self) -> None:
        settings = Settings()
        low_edge = self._sample_signal(confidence=67, quality="tradable", confluence_count=4, edge_score=58)
        low_confluence = self._sample_signal(confidence=67, quality="tradable", confluence_count=2, edge_score=76)
        self.assertFalse(_is_actionable_signal(low_edge, settings))
        self.assertFalse(_is_actionable_signal(low_confluence, settings))

    def test_trade_close_outcome_hits_take_profit_for_long(self) -> None:
        trade = _build_tracked_trade(1, self._sample_signal(), TradeStage.SIGNAL)
        live_signal = self._sample_signal(current_price=109.0)
        outcome, reason = _trade_close_outcome(trade, live_signal)
        self.assertEqual(outcome, TradeStage.CLOSED_SUCCESS)
        self.assertIn("Take profit 1", reason)

    def test_trade_close_outcome_hits_stop_for_short(self) -> None:
        entry_signal = self._sample_signal(side=SignalSide.SHORT, current_price=100.0)
        trade = _build_tracked_trade(1, entry_signal, TradeStage.SIGNAL)
        live_signal = self._sample_signal(side=SignalSide.SHORT, current_price=106.0)
        outcome, reason = _trade_close_outcome(trade, live_signal)
        self.assertEqual(outcome, TradeStage.CLOSED_FAILURE)
        self.assertIn("Stop loss", reason)

    def test_trade_close_metrics_include_return_r_and_position_pnl(self) -> None:
        trade = _build_tracked_trade(1, self._sample_signal(), TradeStage.SIGNAL)
        live_signal = self._sample_signal(current_price=109.0)
        metrics = _trade_close_metrics(trade, live_signal, position_size=2.5)
        self.assertEqual(metrics["entry_price"], 100.0)
        self.assertEqual(metrics["return_pct"], 9.0)
        self.assertEqual(metrics["r_multiple"], 1.8)
        self.assertEqual(metrics["dollar_pnl"], 22.5)

    def test_learning_service_applies_positive_adjustment_after_enough_wins(self) -> None:
        with TemporaryDirectory() as temp_dir:
            learning = LearningService(data_dir=temp_dir, min_sample_size=3, max_confidence_adjustment=8)
            for _ in range(3):
                trade = _build_tracked_trade(1, self._sample_signal(), TradeStage.SIGNAL)
                close_signal = self._sample_signal(current_price=112.0)
                metrics = _trade_close_metrics(trade, close_signal)
                learning.record_trade_close(trade, close_signal, TradeStage.CLOSED_SUCCESS, metrics)

            signal = self._sample_signal(confidence=62, quality="tradable")
            adjusted = learning.apply_to_signal(signal)
            self.assertGreater(adjusted.confidence, 62)
            self.assertGreaterEqual(adjusted.learning_adjustment, 1)
            self.assertTrue(adjusted.learning_notes)
            self.assertGreater(adjusted.learned_expectancy, 0.0)
            self.assertGreaterEqual(adjusted.learned_sample_size, 3)

    def test_learning_service_summary_reports_closed_trade_stats(self) -> None:
        with TemporaryDirectory() as temp_dir:
            learning = LearningService(data_dir=temp_dir, min_sample_size=2, max_confidence_adjustment=8)
            winning_trade = _build_tracked_trade(1, self._sample_signal(), TradeStage.SIGNAL)
            winning_signal = self._sample_signal(current_price=109.0)
            learning.record_trade_close(
                winning_trade,
                winning_signal,
                TradeStage.CLOSED_SUCCESS,
                _trade_close_metrics(winning_trade, winning_signal),
            )
            losing_trade = _build_tracked_trade(1, self._sample_signal(side=SignalSide.SHORT), TradeStage.SIGNAL)
            losing_signal = self._sample_signal(side=SignalSide.SHORT, current_price=106.0)
            learning.record_trade_close(
                losing_trade,
                losing_signal,
                TradeStage.CLOSED_FAILURE,
                _trade_close_metrics(losing_trade, losing_signal),
            )
            summary = learning.summary()
            self.assertEqual(summary["total_trades"], 2)
            self.assertEqual(summary["win_rate"], 50.0)

    def test_learning_service_can_block_weak_negative_edge(self) -> None:
        with TemporaryDirectory() as temp_dir:
            learning = LearningService(
                data_dir=temp_dir,
                min_sample_size=3,
                max_confidence_adjustment=8,
                block_negative_edges=True,
                weak_edge_threshold=-4,
                weak_edge_min_samples=4,
            )
            for _ in range(4):
                trade = _build_tracked_trade(1, self._sample_signal(side=SignalSide.SHORT), TradeStage.SIGNAL)
                losing_signal = self._sample_signal(side=SignalSide.SHORT, current_price=106.0)
                learning.record_trade_close(
                    trade,
                    losing_signal,
                    TradeStage.CLOSED_FAILURE,
                    _trade_close_metrics(trade, losing_signal),
                )
            should_block, reason = learning.should_block_signal(self._sample_signal(side=SignalSide.SHORT))
            self.assertTrue(should_block)
            self.assertIn("weak", reason.lower())

    def test_learning_service_dashboard_returns_recent_context(self) -> None:
        with TemporaryDirectory() as temp_dir:
            learning = LearningService(data_dir=temp_dir, min_sample_size=2, max_confidence_adjustment=8)
            for _ in range(2):
                trade = _build_tracked_trade(1, self._sample_signal(ticker="ETH-USD"), TradeStage.SIGNAL)
                close_signal = self._sample_signal(ticker="ETH-USD", current_price=109.0)
                learning.record_trade_close(
                    trade,
                    close_signal,
                    TradeStage.CLOSED_SUCCESS,
                    _trade_close_metrics(trade, close_signal),
                )
            dashboard = learning.dashboard("ETH-USD")
            self.assertEqual(dashboard["ticker"], "ETH-USD")
            self.assertEqual(dashboard["summary"]["total_trades"], 2)
            self.assertTrue(dashboard["recent_closures"])

    def test_learning_service_writes_sqlite_metrics(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/learning.db"
            learning = LearningService(
                data_dir=temp_dir,
                namespace="metrics_bot",
                sqlite_database_path=db_path,
                min_sample_size=2,
                max_confidence_adjustment=8,
            )
            for _ in range(2):
                trade = _build_tracked_trade(1, self._sample_signal(ticker="SOL-USD"), TradeStage.SIGNAL)
                close_signal = self._sample_signal(ticker="SOL-USD", current_price=109.0)
                learning.record_signal_event(trade, TradeStage.SIGNAL)
                learning.record_trade_close(
                    trade,
                    close_signal,
                    TradeStage.CLOSED_SUCCESS,
                    _trade_close_metrics(trade, close_signal),
                )

            metrics = learning.metrics_summary("daily")
            self.assertEqual(metrics["total_trades"], 2)
            self.assertEqual(metrics["winning_trades"], 2)
            self.assertEqual(metrics["hits"], 2)
            self.assertEqual(metrics["misses"], 0)
            self.assertGreater(metrics["profit_factor"], 0.0)
            self.assertGreater(metrics["expectancy"], 0.0)
            leaderboard = learning.leaderboard("daily", "ticker", limit=5)
            self.assertEqual(leaderboard[0]["label"], "SOL-USD")
            self.assertEqual(leaderboard[0]["winning_trades"], 2)
            learning.close()

    def test_learning_service_migrates_json_history_into_sqlite(self) -> None:
        with TemporaryDirectory() as temp_dir:
            history_path = f"{temp_dir}/migrate_bot_trade_history.json"
            opened_at = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            closed_at = opened_at.replace(hour=3)
            history_payload = {
                "signals": [
                    {
                        "trade_id": "trade-1",
                        "chat_id": 1,
                        "ticker": "BTC-USD",
                        "asset_class": "crypto",
                        "side": "LONG",
                        "stage": "SIGNAL",
                        "entry_low": 99.0,
                        "entry_high": 101.0,
                        "stop_loss": 95.0,
                        "take_profit_1": 108.0,
                        "take_profit_2": 112.0,
                        "confidence": 68,
                        "market_session": "24/7 crypto market",
                        "signal_quality": "high",
                        "scores": {"technical": 70.0, "edge_score": 80, "confluence_count": 4},
                        "opened_at": opened_at.isoformat(),
                    }
                ],
                "closures": [
                    {
                        "trade_id": "trade-1",
                        "chat_id": 1,
                        "ticker": "BTC-USD",
                        "asset_class": "crypto",
                        "side": "LONG",
                        "market_session": "24/7 crypto market",
                        "signal_quality": "high",
                        "confidence": 68,
                        "scores": {"technical": 70.0},
                        "opened_at": opened_at.isoformat(),
                        "closed_at": closed_at.isoformat(),
                        "outcome": "CLOSED_SUCCESS",
                        "close_price": 109.0,
                        "entry_price": 100.0,
                        "return_pct": 9.0,
                        "r_multiple": 1.8,
                        "dollar_pnl": 9.0,
                    }
                ],
            }
            with open(history_path, "w", encoding="utf-8") as handle:
                import json

                json.dump(history_payload, handle)

            learning = LearningService(
                data_dir=temp_dir,
                namespace="migrate_bot",
                sqlite_database_path=f"{temp_dir}/learning.db",
                min_sample_size=2,
                max_confidence_adjustment=8,
            )
            try:
                metrics = learning.metrics_summary("daily")
                self.assertEqual(metrics["total_trades"], 1)
                self.assertEqual(metrics["hits"], 1)
                leaderboard = learning.leaderboard("daily", "ticker", limit=5)
                self.assertEqual(leaderboard[0]["label"], "BTC-USD")
            finally:
                learning.close()

    def test_sqlite_state_store_persists_profiles_and_tracked_trades(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/bot_state.db"
            store = SQLiteStateStore(default_risk_per_trade=0.01, database_path=db_path)
            store.set_watchlist(7, ["BTC-USD", "ETH-USD"])
            store.add_portfolio_position(7, "BTC-USD", 42000, 0.25)
            store.set_alert_mode(7, "all")
            trade = _build_tracked_trade(7, self._sample_signal(), TradeStage.SIGNAL)
            store.set_tracked_trade(trade)
            store.close()

            reopened = SQLiteStateStore(default_risk_per_trade=0.01, database_path=db_path)
            profile = reopened.get_profile(7)
            self.assertEqual(profile.watchlist, ["BTC-USD", "ETH-USD"])
            self.assertEqual(profile.alert_mode, "all")
            self.assertEqual(len(profile.portfolio), 1)
            loaded_trade = reopened.get_tracked_trade(7, "BTC-USD")
            self.assertIsNotNone(loaded_trade)
            self.assertEqual(loaded_trade.trade_id, trade.trade_id)
            self.assertEqual(reopened.backend_name(), "sqlite:default")
            self.assertTrue(reopened.persistence_enabled())
            reopened.close()

    def test_sqlite_state_store_can_share_one_db_across_namespaces(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/shared_state.db"
            alpha = SQLiteStateStore(default_risk_per_trade=0.01, database_path=db_path, namespace="alpha-bot")
            beta = SQLiteStateStore(default_risk_per_trade=0.01, database_path=db_path, namespace="beta-bot")

            alpha.set_watchlist(9, ["BTC-USD"])
            beta.set_watchlist(9, ["AAPL"])

            self.assertEqual(alpha.get_profile(9).watchlist, ["BTC-USD"])
            self.assertEqual(beta.get_profile(9).watchlist, ["AAPL"])
            self.assertEqual(alpha.backend_name(), "sqlite:alpha_bot")
            self.assertEqual(beta.backend_name(), "sqlite:beta_bot")

            alpha.close()
            beta.close()

    def test_scan_presets_include_metals_and_energy(self) -> None:
        self.assertIn("GC=F", SCAN_PRESETS["metals"])
        self.assertIn("SI=F", SCAN_PRESETS["metals"])
        self.assertIn("CL=F", SCAN_PRESETS["energy"])

    def test_market_session_label_for_crypto_is_24_7(self) -> None:
        snapshot = PriceSnapshot(
            ticker="BTC-USD",
            asset_class=AssetClass.CRYPTO,
            currency="USD",
            current_price=1.0,
            previous_close=1.0,
            high=1.0,
            low=1.0,
            volume=1.0,
            history=[1.0, 1.0],
        )
        self.assertEqual(market_session_label(snapshot), "24/7 crypto market")

    def test_market_session_label_for_stocks_regular_session(self) -> None:
        snapshot = PriceSnapshot(
            ticker="AAPL",
            asset_class=AssetClass.STOCK,
            currency="USD",
            current_price=1.0,
            previous_close=1.0,
            high=1.0,
            low=1.0,
            volume=1.0,
            history=[1.0, 1.0],
        )
        now = datetime(2026, 3, 24, 15, 0, tzinfo=timezone.utc)
        self.assertEqual(market_session_label(snapshot, now=now), "U.S. regular session")

    def test_technical_analysis_scores_bullish_series(self) -> None:
        snapshot = PriceSnapshot(
            ticker="TEST",
            asset_class=AssetClass.STOCK,
            currency="USD",
            current_price=30.0,
            previous_close=29.5,
            high=30.2,
            low=29.8,
            volume=1_000_000,
            history=[float(value) for value in range(1, 31)],
            meta={},
        )

        score = technical_analysis(snapshot)
        self.assertGreater(score.score, 60)

    def test_risk_analysis_penalizes_volatile_series(self) -> None:
        snapshot = PriceSnapshot(
            ticker="TEST",
            asset_class=AssetClass.CRYPTO,
            currency="USD",
            current_price=100.0,
            previous_close=90.0,
            high=110.0,
            low=80.0,
            volume=1_000_000,
            history=[100, 120, 80, 125, 78, 130, 75, 128, 82, 135, 79, 140, 85, 145, 100],
            meta={},
        )

        score = risk_analysis(snapshot)
        self.assertLess(score.score, 70)


if __name__ == "__main__":
    unittest.main()
