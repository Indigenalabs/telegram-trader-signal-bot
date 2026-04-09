from __future__ import annotations

import os
from dataclasses import dataclass, field

def _load_local_dotenv() -> None:
    path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_local_dotenv()


def _csv_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


DEFAULT_TICKER_UNIVERSE = [
    "BTC-USD",
    "ETH-USD",
    "SOL-USD",
    "BNB-USD",
    "XRP-USD",
    "ADA-USD",
    "DOGE-USD",
    "SPY",
    "QQQ",
    "DIA",
    "IWM",
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "TSLA",
    "EURUSD=X",
    "GBPUSD=X",
    "USDJPY=X",
    "AUDUSD=X",
    "USDCAD=X",
    "USDCHF=X",
    "GC=F",
    "SI=F",
    "CL=F",
    "BZ=F",
    "NG=F",
    "GLD",
    "SLV",
]

SCAN_PRESETS = {
    "crypto": ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD", "ADA-USD", "DOGE-USD"],
    "stocks": ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "SPY", "QQQ", "DIA", "IWM"],
    "forex": ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "USDCAD=X", "USDCHF=X"],
    "metals": ["GC=F", "SI=F", "GLD", "SLV"],
    "energy": ["CL=F", "BZ=F", "NG=F", "XOM", "CVX"],
    "futures": ["GC=F", "SI=F", "CL=F", "BZ=F", "NG=F"],
}


@dataclass(slots=True)
class Settings:
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    bot_name: str = os.getenv("BOT_NAME", "Ultimate Trader Signal Bot")
    bot_namespace: str = os.getenv("BOT_NAMESPACE", "default").strip().lower() or "default"
    learning_namespace: str = os.getenv("LEARNING_NAMESPACE", "").strip().lower()
    default_risk_per_trade: float = float(os.getenv("DEFAULT_RISK_PER_TRADE", "0.01"))
    gameplan_hour_utc: int = int(os.getenv("GAMEPLAN_HOUR_UTC", "8"))
    gameplan_minute_utc: int = int(os.getenv("GAMEPLAN_MINUTE_UTC", "0"))
    live_alerts_enabled: bool = os.getenv("LIVE_ALERTS_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    live_alert_interval_minutes: int = int(os.getenv("LIVE_ALERT_INTERVAL_MINUTES", "5"))
    live_alert_min_confidence: int = int(os.getenv("LIVE_ALERT_MIN_CONFIDENCE", "58"))
    strong_play_min_confidence: int = int(os.getenv("STRONG_PLAY_MIN_CONFIDENCE", "66"))
    live_alert_high_quality_only: bool = os.getenv(
        "LIVE_ALERT_HIGH_QUALITY_ONLY", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
    live_alert_ticker_limit: int = int(os.getenv("LIVE_ALERT_TICKER_LIMIT", "25"))
    binance_api_key: str = os.getenv("BINANCE_API_KEY", "")
    newsapi_key: str = os.getenv("NEWSAPI_KEY", "")
    twelvedata_api_key: str = os.getenv("TWELVEDATA_API_KEY", "")
    google_service_account_json_path: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_PATH", "")
    google_sheets_spreadsheet_id: str = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "")
    sqlite_state_enabled: bool = os.getenv("SQLITE_STATE_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    sqlite_state_path: str = os.getenv("SQLITE_STATE_PATH", "data/bot_state.db")
    learning_sqlite_enabled: bool = os.getenv("LEARNING_SQLITE_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    learning_sqlite_path: str = os.getenv("LEARNING_SQLITE_PATH", "")
    learning_data_dir: str = os.getenv("LEARNING_DATA_DIR", "data")
    learning_min_sample_size: int = int(os.getenv("LEARNING_MIN_SAMPLE_SIZE", "3"))
    learning_max_confidence_adjustment: int = int(os.getenv("LEARNING_MAX_CONFIDENCE_ADJUSTMENT", "8"))
    edge_over_speed_mode: bool = os.getenv("EDGE_OVER_SPEED_MODE", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    signal_min_confluence: int = int(os.getenv("SIGNAL_MIN_CONFLUENCE", "3"))
    high_quality_min_confluence: int = int(os.getenv("HIGH_QUALITY_MIN_CONFLUENCE", "4"))
    edge_score_min_alert: int = int(os.getenv("EDGE_SCORE_MIN_ALERT", "55"))
    edge_score_min_high_quality: int = int(os.getenv("EDGE_SCORE_MIN_HIGH_QUALITY", "72"))
    learning_block_negative_edges: bool = os.getenv("LEARNING_BLOCK_NEGATIVE_EDGES", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    learning_weak_edge_threshold: int = int(os.getenv("LEARNING_WEAK_EDGE_THRESHOLD", "-4"))
    learning_weak_edge_min_samples: int = int(os.getenv("LEARNING_WEAK_EDGE_MIN_SAMPLES", "4"))
    allowed_chat_ids: set[int] = field(
        default_factory=lambda: {int(item) for item in _csv_env("ALLOWED_CHAT_IDS")}
    )
    default_tickers: list[str] = field(
        default_factory=lambda: _csv_env("DEFAULT_TICKERS") or DEFAULT_TICKER_UNIVERSE.copy()
    )

    def __post_init__(self) -> None:
        if not self.learning_namespace:
            self.learning_namespace = self.bot_namespace
        if not self.learning_sqlite_path:
            self.learning_sqlite_path = self.sqlite_state_path

    def require_token(self) -> None:
        if not self.telegram_bot_token:
            raise RuntimeError(
                "Missing TELEGRAM_BOT_TOKEN. Copy .env.example to .env and configure it."
            )

    @property
    def google_sheets_enabled(self) -> bool:
        return bool(self.google_service_account_json_path and self.google_sheets_spreadsheet_id)


settings = Settings()
