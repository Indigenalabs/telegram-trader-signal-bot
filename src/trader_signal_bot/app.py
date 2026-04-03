from __future__ import annotations

from telegram.ext import Updater

from trader_signal_bot.bot.handlers import build_handlers
from trader_signal_bot.config import settings
from trader_signal_bot.services.google_sheets_state import GoogleSheetsStateStore
from trader_signal_bot.services.learning import LearningService
from trader_signal_bot.services.macro_risk import MacroRiskService
from trader_signal_bot.services.market_data import (
    BinanceMarketDataProvider,
    CompositeMarketDataProvider,
    TwelveDataMarketDataProvider,
    YahooMarketDataProvider,
)
from trader_signal_bot.services.news import NewsService
from trader_signal_bot.services.signal_engine import SignalEngine
from trader_signal_bot.services.sqlite_state import SQLiteStateStore
from trader_signal_bot.services.state import UserStateStore


def main() -> None:
    settings.require_token()

    updater = Updater(token=settings.telegram_bot_token, use_context=True)
    provider = CompositeMarketDataProvider(
        binance_provider=BinanceMarketDataProvider(api_key=settings.binance_api_key),
        yahoo_provider=YahooMarketDataProvider(),
        twelvedata_provider=TwelveDataMarketDataProvider(api_key=settings.twelvedata_api_key),
    )
    news_service = NewsService(api_key=settings.newsapi_key)
    learning_service = LearningService(
        data_dir=settings.learning_data_dir,
        namespace=settings.learning_namespace,
        min_sample_size=settings.learning_min_sample_size,
        max_confidence_adjustment=settings.learning_max_confidence_adjustment,
        block_negative_edges=settings.learning_block_negative_edges,
        weak_edge_threshold=settings.learning_weak_edge_threshold,
        weak_edge_min_samples=settings.learning_weak_edge_min_samples,
    )
    engine = SignalEngine(
        provider,
        settings=settings,
        news_service=news_service,
        learning_service=learning_service,
    )
    macro_risk_service = MacroRiskService(news_service)
    state: UserStateStore
    if settings.google_sheets_enabled:
        try:
            state = GoogleSheetsStateStore(
                default_risk_per_trade=settings.default_risk_per_trade,
                service_account_json_path=settings.google_service_account_json_path,
                spreadsheet_id=settings.google_sheets_spreadsheet_id,
            )
        except Exception as exc:
            print(f"Google Sheets state unavailable, falling back to local state store: {exc}")
            if settings.sqlite_state_enabled:
                state = SQLiteStateStore(
                    default_risk_per_trade=settings.default_risk_per_trade,
                    database_path=settings.sqlite_state_path,
                    namespace=settings.bot_namespace,
                )
            else:
                state = UserStateStore(default_risk_per_trade=settings.default_risk_per_trade)
    elif settings.sqlite_state_enabled:
        state = SQLiteStateStore(
            default_risk_per_trade=settings.default_risk_per_trade,
            database_path=settings.sqlite_state_path,
            namespace=settings.bot_namespace,
        )
    else:
        state = UserStateStore(default_risk_per_trade=settings.default_risk_per_trade)

    build_handlers(
        updater,
        settings,
        engine,
        state,
        learning_service=learning_service,
        macro_risk_service=macro_risk_service,
    )
    updater.start_polling(drop_pending_updates=True)
    updater.idle()


if __name__ == "__main__":
    main()
