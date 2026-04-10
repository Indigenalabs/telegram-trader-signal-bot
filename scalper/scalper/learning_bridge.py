from __future__ import annotations

"""
Writes scalper trade outcomes into the signal bot's trade_history.json
so both bots share the same learned edge model.

The signal bot calls refresh_model() on every closure — it will
automatically pick up records written here on its next cycle.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Outcome values must match TradeStage enum in the signal bot
_OUTCOME_WIN = "CLOSED_SUCCESS"
_OUTCOME_LOSS = "CLOSED_FAILURE"


def record_scalper_closure(
    signal_bot_data_dir: str,
    ticker: str,
    side: str,
    entry_price: float,
    exit_price: float,
    r_multiple: float,
    return_pct: float,
    win: bool,
    opened_at: str,
    candle_interval: str = "5m",
    confidence: float = 0.0,
) -> None:
    """
    Append one scalper trade closure to the signal bot's trade_history.json.
    Safe to call from any thread — uses file-level locking via read-modify-write.
    """
    history_path = Path(signal_bot_data_dir) / "trade_history.json"

    record = {
        "trade_id": f"scalper_{uuid.uuid4().hex[:12]}",
        "chat_id": "",
        "ticker": ticker.upper(),
        "asset_class": "crypto",
        "side": side,
        "market_session": "24h",
        "candle_interval": candle_interval,
        "confidence": round(confidence),
        "scores": {"candle_interval": candle_interval, "source": "scalper"},
        "opened_at": opened_at,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "outcome": _OUTCOME_WIN if win else _OUTCOME_LOSS,
        "r_multiple": round(r_multiple, 4),
        "return_pct": round(return_pct, 4),
        "entry_price": entry_price,
        "close_price": exit_price,
        "signal_quality": "tradable",
        "source": "scalper",
    }

    try:
        if history_path.exists():
            history = json.loads(history_path.read_text(encoding="utf-8"))
        else:
            history = {"signals": [], "closures": []}

        if "closures" not in history:
            history["closures"] = []

        history["closures"].append(record)
        history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")
        log.info(
            "Learning bridge: wrote %s %s %s R=%.2f to %s",
            "WIN" if win else "LOSS", side, ticker, r_multiple, history_path,
        )
    except Exception as e:
        log.warning("Learning bridge write failed: %s", e)
