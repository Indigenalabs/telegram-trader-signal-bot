"""
Signal Bridge — reads the signal bot's SQLite state database and returns
any active SIGNAL-stage crypto trades the scalper hasn't acted on yet.

No changes required to the signal bot. The scalper reads bot_state.db
as a read-only consumer using sqlite3.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

# Keeps the last 1000 acted trade IDs so we never double-enter
_MAX_ACTED_IDS = 1000


def _load_acted_ids(acted_file: str) -> set[str]:
    path = Path(acted_file)
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_acted_ids(acted_file: str, ids: set[str]) -> None:
    path = Path(acted_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep most-recent IDs only
    trimmed = list(ids)[-_MAX_ACTED_IDS:]
    try:
        path.write_text(json.dumps(trimmed), encoding="utf-8")
    except Exception:
        pass


def mark_acted(acted_file: str, trade_id: str) -> None:
    ids = _load_acted_ids(acted_file)
    ids.add(trade_id)
    _save_acted_ids(acted_file, ids)


def get_pending_signal_bot_trades(
    bot_state_db: str,
    acted_file: str,
) -> list[dict]:
    """
    Return signal bot SIGNAL-stage crypto trades not yet acted on.
    Each item has: trade_id, ticker, side, entry, stop_loss, take_profit, confidence.
    """
    db_path = Path(bot_state_db)
    if not db_path.exists():
        return []

    acted_ids = _load_acted_ids(acted_file)

    try:
        # Read-only connection — don't lock the signal bot's database
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT trade_id, ticker, asset_class, side, entry_low, entry_high,
                   stop_loss, take_profit_1, take_profit_2, confidence, signal_quality
            FROM tracked_trades
            WHERE stage = 'SIGNAL'
              AND asset_class = 'crypto'
            """
        ).fetchall()
        conn.close()
    except Exception as exc:
        log.debug("signal_bridge: could not read bot_state.db — %s", exc)
        return []

    results = []
    for row in rows:
        trade_id = row["trade_id"]
        if trade_id in acted_ids:
            continue
        results.append({
            "trade_id": trade_id,
            "ticker": row["ticker"],
            "side": row["side"],              # "LONG" or "SHORT"
            # Use entry_high for longs, entry_low for shorts (worst-case fill)
            "entry": row["entry_high"] if row["side"] == "LONG" else row["entry_low"],
            "stop_loss": float(row["stop_loss"]),
            "take_profit": float(row["take_profit_1"]),
            "confidence": int(row["confidence"]),
            "signal_quality": row["signal_quality"],
        })

    return results
