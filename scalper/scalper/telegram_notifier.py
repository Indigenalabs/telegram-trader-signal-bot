from __future__ import annotations

import logging
import threading

import requests

log = logging.getLogger(__name__)

_LOCK = threading.Lock()


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id
        self._base = f"https://api.telegram.org/bot{token}"

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.token or not self.chat_id:
            return False
        with _LOCK:
            try:
                r = requests.post(
                    f"{self._base}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode},
                    timeout=10.0,
                )
                return r.status_code == 200
            except Exception as e:
                log.warning("Telegram send failed: %s", e)
                return False

    # ------------------------------------------------------------------
    # Formatted messages
    # ------------------------------------------------------------------

    def trade_opened(self, t: dict) -> None:
        side_emoji = "🟢" if t["side"] == "LONG" else "🔴"
        msg = (
            f"{side_emoji} <b>SCALP ENTRY — {t['ticker']}</b>\n"
            f"Side: <b>{t['side']}</b>  |  Score: {t['score']:.0f}\n"
            f"Entry: <code>{t['entry']:.4f}</code>\n"
            f"SL: <code>{t['stop_loss']:.4f}</code>  |  TP: <code>{t['take_profit']:.4f}</code>\n"
        )
        if t.get("details"):
            msg += "\n<i>" + " · ".join(t["details"][:3]) + "</i>"
        self.send(msg)

    def trade_closed(self, t: dict) -> None:
        win = t.get("win", False)
        emoji = "✅" if win else "❌"
        reason = t.get("exit_reason", "?")
        msg = (
            f"{emoji} <b>SCALP CLOSED — {t['ticker']}</b>\n"
            f"Side: {t['side']}  |  Reason: {reason}\n"
            f"Entry: <code>{t['entry']:.4f}</code>  →  Exit: <code>{t['exit']:.4f}</code>\n"
            f"P&L: <b>{t['pnl']:+.4f}</b> ({t['pnl_pct']:+.2f}%)  |  R: {t['r_multiple']:+.2f}R\n"
        )
        self.send(msg)

    def daily_report(self, stats: dict, capital: float, leaderboard: list[dict]) -> None:
        if stats["total"] == 0:
            return  # Nothing to report — don't spam

        wr = stats["win_rate"]
        pf = stats["profit_factor"]
        ar = stats["avg_r"]
        exp = stats["expectancy"]
        pnl = stats["total_pnl"]

        msg = (
            "📊 <b>Scalper Daily Report</b>\n\n"
            f"Trades: {stats['total']}  |  Wins: {stats['wins']}  |  Losses: {stats['losses']}\n"
            f"Win Rate: <b>{wr:.1f}%</b>  |  Profit Factor: <b>{pf:.2f}</b>\n"
            f"Avg R: <b>{ar:+.2f}R</b>  |  Expectancy: {exp:+.4f}\n"
            f"Total P&L: <b>{pnl:+.2f}</b>  |  Capital: <b>{capital:.2f}</b>\n"
        )

        if leaderboard:
            msg += "\n<b>Top Tickers (all time, ≥3 trades)</b>\n"
            for i, row in enumerate(leaderboard[:5], 1):
                ticker = row["ticker"]
                total_pnl = row.get("total_pnl") or 0.0
                wins = row.get("wins") or 0
                total = row.get("total") or 1
                msg += f"  {i}. {ticker} — {total_pnl:+.2f} ({wins}/{total} W)\n"

        self.send(msg)

    def startup_message(self, tickers: list[str], paper: bool) -> None:
        mode = "PAPER" if paper else "LIVE"
        self.send(
            f"🚀 <b>Scalper Bot Started [{mode}]</b>\n"
            f"Watching: {', '.join(tickers)}\n"
            f"Mode: {mode} trading"
        )
