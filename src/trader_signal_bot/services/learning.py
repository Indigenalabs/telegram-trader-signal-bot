from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trader_signal_bot.domain import AssetClass, Signal, SignalSide, TrackedTrade, TradeStage
from trader_signal_bot.services.sqlite_learning_store import SQLiteLearningStore


class LearningService:
    def __init__(
        self,
        data_dir: str,
        namespace: str = "default",
        sqlite_database_path: str | None = None,
        min_sample_size: int = 3,
        max_confidence_adjustment: int = 8,
        block_negative_edges: bool = True,
        weak_edge_threshold: int = -4,
        weak_edge_min_samples: int = 4,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.namespace = namespace.strip().lower() or "default"
        history_name = "trade_history.json" if self.namespace == "default" else f"{self.namespace}_trade_history.json"
        model_name = "learning_model.json" if self.namespace == "default" else f"{self.namespace}_learning_model.json"
        self.history_path = self.data_dir / history_name
        self.model_path = self.data_dir / model_name
        self.min_sample_size = max(2, min_sample_size)
        self.max_confidence_adjustment = max(2, max_confidence_adjustment)
        self.block_negative_edges = block_negative_edges
        self.weak_edge_threshold = weak_edge_threshold
        self.weak_edge_min_samples = max(self.min_sample_size, weak_edge_min_samples)
        self.sqlite_store = SQLiteLearningStore(sqlite_database_path, namespace=self.namespace) if sqlite_database_path else None
        self.history: dict[str, list[dict[str, Any]]] = {"signals": [], "closures": []}
        self.model: dict[str, dict[str, Any]] = {
            "asset_class": {},
            "asset_session": {},
            "ticker": {},
            "side": {},
            "interval": {},
        }
        self._load()

    def close(self) -> None:
        if self.sqlite_store is not None:
            self.sqlite_store.close()

    def _load(self) -> None:
        if self.history_path.exists():
            try:
                self.history = json.loads(self.history_path.read_text(encoding="utf-8"))
            except Exception:
                self.history = {"signals": [], "closures": []}
        if self.model_path.exists():
            try:
                self.model = json.loads(self.model_path.read_text(encoding="utf-8"))
            except Exception:
                self.model = {"asset_class": {}, "asset_session": {}, "ticker": {}, "side": {}}
        if self.sqlite_store is not None and (self.history.get("signals") or self.history.get("closures")):
            self.sqlite_store.import_json_history(self.history)

    def _save_history(self) -> None:
        self.history_path.write_text(json.dumps(self.history, indent=2), encoding="utf-8")

    def _save_model(self) -> None:
        self.model_path.write_text(json.dumps(self.model, indent=2), encoding="utf-8")

    def record_signal_event(self, trade: TrackedTrade, stage: TradeStage) -> None:
        record = {
            "trade_id": trade.trade_id,
            "chat_id": trade.chat_id,
            "ticker": trade.ticker,
            "asset_class": trade.asset_class.value,
            "side": trade.side.value,
            "stage": stage.value,
            "entry_low": trade.entry_low,
            "entry_high": trade.entry_high,
            "stop_loss": trade.stop_loss,
            "take_profit_1": trade.take_profit_1,
            "take_profit_2": trade.take_profit_2,
            "confidence": trade.confidence,
            "market_session": trade.market_session,
            "signal_quality": trade.signal_quality,
            "candle_interval": str(trade.scores.get("candle_interval", "")),
            "scores": trade.scores,
            "opened_at": trade.opened_at,
        }
        existing = next(
            (
                item
                for item in self.history["signals"]
                if item.get("trade_id") == trade.trade_id and item.get("stage") == stage.value
            ),
            None,
        )
        if existing is None:
            self.history["signals"].append(record)
            self._save_history()
        if self.sqlite_store is not None:
            self.sqlite_store.record_signal_event(trade, stage)

    def record_trade_close(
        self,
        trade: TrackedTrade,
        signal: Signal,
        outcome: TradeStage,
        metrics: dict[str, float | None],
    ) -> None:
        record = {
            "trade_id": trade.trade_id,
            "chat_id": trade.chat_id,
            "ticker": trade.ticker,
            "asset_class": trade.asset_class.value,
            "side": trade.side.value,
            "market_session": trade.market_session,
            "signal_quality": trade.signal_quality,
            "candle_interval": str(trade.scores.get("candle_interval", "")),
            "confidence": trade.confidence,
            "scores": trade.scores,
            "opened_at": trade.opened_at,
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "outcome": outcome.value,
            "close_price": signal.current_price,
            "entry_price": metrics.get("entry_price"),
            "return_pct": metrics.get("return_pct"),
            "r_multiple": metrics.get("r_multiple"),
            "dollar_pnl": metrics.get("dollar_pnl"),
        }
        self.history["closures"] = [
            item for item in self.history["closures"] if item.get("trade_id") != trade.trade_id
        ]
        self.history["closures"].append(record)
        self._save_history()
        if self.sqlite_store is not None:
            self.sqlite_store.record_trade_close(trade, signal, outcome, metrics)
        self.refresh_model()

    def _bucket_key(self, asset_class: str, market_session: str, ticker: str, side: str, candle_interval: str = "") -> dict[str, str]:
        return {
            "asset_class": asset_class,
            "asset_session": f"{asset_class}|{market_session}",
            "ticker": ticker,
            "side": side,
            "interval": f"{asset_class}|{candle_interval}" if candle_interval else asset_class,
        }

    def refresh_model(self) -> None:
        buckets: dict[str, dict[str, list[dict[str, Any]]]] = {
            "asset_class": defaultdict(list),
            "asset_session": defaultdict(list),
            "ticker": defaultdict(list),
            "side": defaultdict(list),
            "interval": defaultdict(list),
        }
        for item in self.history.get("closures", []):
            keys = self._bucket_key(
                asset_class=str(item.get("asset_class", "")),
                market_session=str(item.get("market_session", "")),
                ticker=str(item.get("ticker", "")),
                side=str(item.get("side", "")),
                candle_interval=str(item.get("candle_interval", "")),
            )
            for bucket_name, bucket_key in keys.items():
                buckets[bucket_name][bucket_key].append(item)

        model: dict[str, dict[str, Any]] = {
            "asset_class": {},
            "asset_session": {},
            "ticker": {},
            "side": {},
            "interval": {},
        }
        for bucket_name, entries in buckets.items():
            for key, rows in entries.items():
                if len(rows) < self.min_sample_size:
                    continue
                wins = sum(1 for row in rows if row.get("outcome") == TradeStage.CLOSED_SUCCESS.value)
                avg_r = sum(float(row.get("r_multiple", 0.0) or 0.0) for row in rows) / len(rows)
                avg_return = sum(float(row.get("return_pct", 0.0) or 0.0) for row in rows) / len(rows)
                expectancy = (wins / len(rows)) * max(avg_r, 0.0) - ((len(rows) - wins) / len(rows)) * abs(min(avg_r, 0.0))
                raw_adjustment = round((avg_r * 2.2) + ((wins / len(rows) - 0.5) * 10))
                adjustment = max(-self.max_confidence_adjustment, min(self.max_confidence_adjustment, raw_adjustment))
                model[bucket_name][key] = {
                    "samples": len(rows),
                    "win_rate": round((wins / len(rows)) * 100, 2),
                    "avg_r": round(avg_r, 2),
                    "avg_return_pct": round(avg_return, 2),
                    "expectancy": round(expectancy, 2),
                    "adjustment": int(adjustment),
                }

        self.model = model
        self._save_model()

    def adjustment_for_signal(self, signal: Signal) -> tuple[int, list[str]]:
        edge = self.edge_context_for_signal(signal)
        return int(edge["adjustment"]), list(edge["notes"])

    def edge_context_for_signal(self, signal: Signal) -> dict[str, Any]:
        candle_interval = str(signal.scores.get("candle_interval", ""))
        keys = self._bucket_key(
            asset_class=signal.asset_class.value,
            market_session=signal.market_session,
            ticker=signal.ticker,
            side=signal.side.value,
            candle_interval=candle_interval,
        )
        weighted_adjustment = 0.0
        weighted_expectancy = 0.0
        weighted_win_rate = 0.0
        weighted_avg_r = 0.0
        weighted_samples = 0.0
        total_weight = 0.0
        notes: list[str] = []
        weights = {
            "asset_class": 0.25,
            "asset_session": 0.25,
            "ticker": 0.20,
            "side": 0.15,
            "interval": 0.15,
        }
        for bucket_name, key in keys.items():
            payload = self.model.get(bucket_name, {}).get(key)
            if not payload:
                continue
            weight = weights[bucket_name]
            weighted_adjustment += float(payload.get("adjustment", 0)) * weight
            weighted_expectancy += float(payload.get("expectancy", 0.0)) * weight
            weighted_win_rate += float(payload.get("win_rate", 0.0)) * weight
            weighted_avg_r += float(payload.get("avg_r", 0.0)) * weight
            weighted_samples += float(payload.get("samples", 0)) * weight
            total_weight += weight
            notes.append(
                f"{bucket_name.replace('_', ' ')} learned edge: {int(payload.get('adjustment', 0)):+d} from {int(payload.get('samples', 0))} samples"
            )
        if total_weight == 0:
            return {
                "adjustment": 0,
                "notes": [],
                "expectancy": 0.0,
                "win_rate": 0.0,
                "avg_r": 0.0,
                "samples": 0,
            }
        adjustment = round(weighted_adjustment / total_weight)
        adjustment = max(-self.max_confidence_adjustment, min(self.max_confidence_adjustment, adjustment))
        return {
            "adjustment": int(adjustment),
            "notes": notes[:2],
            "expectancy": round(weighted_expectancy / total_weight, 2),
            "win_rate": round(weighted_win_rate / total_weight, 2),
            "avg_r": round(weighted_avg_r / total_weight, 2),
            "samples": max(self.min_sample_size, round(weighted_samples / total_weight)),
        }

    def apply_to_signal(self, signal: Signal) -> Signal:
        if signal.side == SignalSide.NEUTRAL:
            signal.base_confidence = signal.confidence
            signal.learning_adjustment = 0
            signal.learning_notes = []
            signal.learned_expectancy = 0.0
            signal.learned_win_rate = 0.0
            signal.learned_sample_size = 0
            return signal
        edge = self.edge_context_for_signal(signal)
        adjustment = int(edge["adjustment"])
        notes = list(edge["notes"])
        signal.base_confidence = signal.confidence
        signal.learning_adjustment = adjustment
        signal.confidence = max(0, min(100, signal.confidence + adjustment))
        signal.learning_notes = notes
        signal.learned_expectancy = float(edge["expectancy"])
        signal.learned_win_rate = float(edge["win_rate"])
        signal.learned_sample_size = int(edge["samples"])
        if signal.side != SignalSide.NEUTRAL:
            if signal.confidence >= 68:
                signal.signal_quality = "high"
            elif signal.confidence >= 58:
                signal.signal_quality = "tradable"
            else:
                signal.signal_quality = "watchlist"
        if notes:
            signal.rationale = notes + signal.rationale
        return signal

    def should_block_signal(self, signal: Signal) -> tuple[bool, str]:
        if not self.block_negative_edges or signal.side == SignalSide.NEUTRAL:
            return False, ""
        candle_interval = str(signal.scores.get("candle_interval", ""))
        keys = self._bucket_key(
            asset_class=signal.asset_class.value,
            market_session=signal.market_session,
            ticker=signal.ticker,
            side=signal.side.value,
            candle_interval=candle_interval,
        )
        bucket_order = ("ticker", "interval", "asset_session", "asset_class")
        for bucket_name in bucket_order:
            payload = self.model.get(bucket_name, {}).get(keys[bucket_name])
            if not payload:
                continue
            samples = int(payload.get("samples", 0))
            adjustment = int(payload.get("adjustment", 0))
            avg_r = float(payload.get("avg_r", 0.0))
            if samples >= self.weak_edge_min_samples and adjustment <= self.weak_edge_threshold and avg_r < 0:
                return (
                    True,
                    f"Learned filter blocked {signal.ticker}: {bucket_name.replace('_', ' ')} edge is weak "
                    f"({adjustment:+d}, {samples} samples, avgR {avg_r}).",
                )
        return False, ""

    def summary(self, ticker: str | None = None, asset_class: str | None = None) -> dict[str, Any]:
        closures = self.history.get("closures", [])
        if ticker:
            closures = [item for item in closures if str(item.get("ticker", "")).upper() == ticker.upper()]
        if asset_class:
            closures = [item for item in closures if str(item.get("asset_class", "")) == asset_class]
        total = len(closures)
        if total == 0:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "avg_r": 0.0,
                "avg_return_pct": 0.0,
                "expectancy": 0.0,
            }
        wins = sum(1 for item in closures if item.get("outcome") == TradeStage.CLOSED_SUCCESS.value)
        avg_r = sum(float(item.get("r_multiple", 0.0) or 0.0) for item in closures) / total
        avg_return = sum(float(item.get("return_pct", 0.0) or 0.0) for item in closures) / total
        expectancy = sum(float(item.get("r_multiple", 0.0) or 0.0) for item in closures) / total
        return {
            "total_trades": total,
            "win_rate": round((wins / total) * 100, 2),
            "avg_r": round(avg_r, 2),
            "avg_return_pct": round(avg_return, 2),
            "expectancy": round(expectancy, 2),
        }

    def model_snapshot(self) -> dict[str, dict[str, Any]]:
        return self.model

    def metrics_summary(self, period_type: str = "daily") -> dict[str, Any]:
        if self.sqlite_store is not None:
            return self.sqlite_store.metrics_summary(period_type)
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

    def leaderboard(self, period_type: str = "weekly", group_by: str = "ticker", limit: int = 5) -> list[dict[str, Any]]:
        if self.sqlite_store is not None:
            return self.sqlite_store.leaderboard(period_type=period_type, group_by=group_by, limit=limit)
        return []

    def dashboard(self, ticker: str) -> dict[str, Any]:
        normalized = ticker.upper()
        signals = [
            item for item in self.history.get("signals", []) if str(item.get("ticker", "")).upper() == normalized
        ]
        closures = [
            item for item in self.history.get("closures", []) if str(item.get("ticker", "")).upper() == normalized
        ]
        summary = self.summary(ticker=normalized)
        stage_counts: dict[str, int] = defaultdict(int)
        for item in signals:
            stage_counts[str(item.get("stage", "UNKNOWN"))] += 1
        recent_closures = sorted(
            closures,
            key=lambda item: str(item.get("closed_at", "")),
            reverse=True,
        )[:5]
        model_entries: list[dict[str, Any]] = []
        for bucket_name, bucket in self.model.items():
            payload = bucket.get(normalized)
            if payload:
                model_entries.append({"bucket": bucket_name, "key": normalized, **payload})
        return {
            "ticker": normalized,
            "summary": summary,
            "signal_events": len(signals),
            "stage_counts": dict(stage_counts),
            "recent_closures": recent_closures,
            "model_entries": model_entries,
        }
