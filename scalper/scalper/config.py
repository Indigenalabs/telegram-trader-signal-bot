from __future__ import annotations

import os


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


class Config:
    # Telegram — same bot token, pushes messages directly to chat
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("ALLOWED_CHAT_IDS", "").split(",")[0].strip()

    # Binance
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")

    # Database
    DB_PATH: str = os.getenv("SCALPER_DB_PATH", "data/scalper.db")

    # Signal bot data directory — scalper writes outcomes here so both bots share learning
    SIGNAL_BOT_DATA_DIR: str = os.getenv("SIGNAL_BOT_DATA_DIR", "/opt/telegram-trader-signal-bot/data")

    # Regime file written by the signal bot hourly
    REGIME_FILE: str = os.getenv("REGIME_FILE", "/opt/telegram-trader-signal-bot/data/market_regime.json")

    # Trading universe — top crypto only (5m scalps, 24/7)
    TICKERS: list[str] = [
        t.strip() for t in os.getenv(
            "SCALPER_TICKERS",
            "BTC-USD,ETH-USD,SOL-USD,BNB-USD,XRP-USD"
        ).split(",") if t.strip()
    ]

    # Candle settings
    CANDLE_INTERVAL: str = "5m"
    CANDLE_LIMIT: int = 100

    # Paper trading
    PAPER_TRADING: bool = os.getenv("PAPER_TRADING", "true").lower() in {"1", "true", "yes"}
    INITIAL_CAPITAL: float = float(os.getenv("SCALPER_CAPITAL", "5000"))

    # Risk per trade as fraction of capital
    RISK_PER_TRADE: float = float(os.getenv("SCALPER_RISK_PER_TRADE", "0.01"))  # 1%

    # Exit rules (scalp — tight and fast)
    TAKE_PROFIT_ATR_MULT: float = float(os.getenv("SCALPER_TP_ATR", "1.5"))
    STOP_LOSS_ATR_MULT: float = float(os.getenv("SCALPER_SL_ATR", "1.0"))
    MAX_HOLD_MINUTES: int = int(os.getenv("SCALPER_MAX_HOLD_MINUTES", "30"))

    # Signal filters
    MIN_VOLUME_RATIO: float = float(os.getenv("SCALPER_MIN_VOL_RATIO", "1.2"))
    MAX_CONCURRENT_TRADES: int = int(os.getenv("SCALPER_MAX_TRADES", "3"))

    # Scan interval
    SCAN_INTERVAL_SECONDS: int = int(os.getenv("SCALPER_SCAN_INTERVAL", "60"))

    # Daily report time (UTC hour)
    REPORT_HOUR_UTC: int = int(os.getenv("SCALPER_REPORT_HOUR_UTC", "8"))

    # Min paper trades before recommending live
    MIN_PAPER_TRADES_FOR_LIVE: int = int(os.getenv("MIN_PAPER_TRADES_FOR_LIVE", "100"))
