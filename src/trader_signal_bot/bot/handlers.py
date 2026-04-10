from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from uuid import uuid4

from telegram import ParseMode, Update
from telegram.ext import CallbackContext, CommandHandler, Updater

from trader_signal_bot.config import SCAN_PRESETS, Settings
from trader_signal_bot.domain import (
    AssetClass,
    Gameplan,
    Signal,
    SignalSide,
    TrackedTrade,
    TradeStage,
)
from trader_signal_bot.services.learning import LearningService
from trader_signal_bot.services.macro_risk import MacroRiskService
from trader_signal_bot.services.regime import write_market_regime
from trader_signal_bot.services.signal_engine import SignalEngine
from trader_signal_bot.services.state import UserStateStore


def _parse_tickers(args: list[str]) -> list[str]:
    tickers: list[str] = []
    for raw in args:
        for item in raw.split(","):
            normalized = item.strip().upper()
            if normalized:
                tickers.append(normalized)

    deduped: list[str] = []
    seen: set[str] = set()
    for ticker in tickers:
        if ticker not in seen:
            seen.add(ticker)
            deduped.append(ticker)
    return deduped


def _authorized(settings: Settings, chat_id: int) -> bool:
    if not settings.allowed_chat_ids:
        return True
    return chat_id in settings.allowed_chat_ids


def _position_size_line(signal: Signal, settings: Settings) -> str:
    """Calculate position size based on account size and 1% risk rule."""
    if signal.side == SignalSide.NEUTRAL:
        return ""
    try:
        entry_price = (signal.entry_low + signal.entry_high) / 2
        stop_distance = abs(entry_price - signal.stop_loss)
        if stop_distance <= 0 or entry_price <= 0:
            return ""
        risk_amount = settings.account_size_usd * settings.default_risk_per_trade
        units = risk_amount / stop_distance
        position_value = units * entry_price
        stop_pct = (stop_distance / entry_price) * 100
        tp1_pct = (abs(signal.take_profit_1 - entry_price) / entry_price) * 100
        rr = tp1_pct / stop_pct if stop_pct > 0 else 0
        return (
            f"💰 Size: {units:.4f} units (${position_value:.2f} notional)\n"
            f"   Risk: ${risk_amount:.2f} ({settings.default_risk_per_trade:.0%} of ${settings.account_size_usd:,.0f}) "
            f"| Stop: {stop_pct:.2f}% | R/R: 1:{rr:.1f}\n"
        )
    except Exception:
        return ""


def _signal_text(signal: Signal, settings: Settings | None = None) -> str:
    learning_line = (
        f"Learning: base {signal.base_confidence}% | adjustment {signal.learning_adjustment:+d} | "
        f"expectancy {signal.learned_expectancy:+.2f}R | support {signal.learned_sample_size}\n"
        if signal.side != SignalSide.NEUTRAL
        else ""
    )
    edge_line = (
        f"Edge: {signal.edge_score}/100 | confluence {signal.confluence_count}/5\n"
        if signal.side != SignalSide.NEUTRAL
        else ""
    )
    size_line = _position_size_line(signal, settings) if settings is not None else ""
    trade_type = getattr(signal, "trade_type", "DAY TRADE")
    type_icon = {"SCALP": "⚡", "DAY TRADE": "🔥", "SWING": "📊", "LONG PLAY": "🏹"}.get(trade_type, "📈")
    return (
        f"{type_icon} <b>{trade_type} — {signal.side.value}</b> - {signal.ticker}\n"
        f"Asset: {signal.asset_class.value} | Quality: {signal.signal_quality}\n"
        f"Session: {signal.market_session}\n"
        f"Market: {signal.price_source} {signal.pricing_symbol} ({signal.pricing_currency})\n"
        f"Price now: {signal.current_price}\n"
        f"Entry: {signal.entry_low} to {signal.entry_high}\n"
        f"Stop: {signal.stop_loss}\n"
        f"TP1: {signal.take_profit_1} | TP2: {signal.take_profit_2}\n"
        f"Confidence: {signal.confidence}%\n"
        f"{edge_line}"
        f"{size_line}"
        f"{learning_line}"
        f"Timeframe: {signal.timeframe}\n"
        f"Rationale:\n- " + "\n- ".join(signal.rationale[:4]) + "\n\n"
        f"Scores: {signal.scores}\n"
        f"<i>{signal.disclaimer}</i>"
    )


def _is_actionable_signal(signal: Signal, settings: Settings) -> bool:
    if signal.side == SignalSide.NEUTRAL or signal.confidence < settings.live_alert_min_confidence:
        return False
    if settings.edge_over_speed_mode:
        if signal.confluence_count < settings.signal_min_confluence:
            return False
        if signal.edge_score < settings.edge_score_min_alert:
            return False
    return True


def _is_strong_signal(signal: Signal, settings: Settings) -> bool:
    if not _is_actionable_signal(signal, settings):
        return False
    if signal.confidence < settings.strong_play_min_confidence:
        return False
    if settings.edge_over_speed_mode:
        if signal.confluence_count < settings.high_quality_min_confluence:
            return False
        if signal.edge_score < settings.edge_score_min_high_quality:
            return False
    if settings.live_alert_high_quality_only and signal.signal_quality != "high":
        return False
    return True


def _signal_expires_at(signal: Signal) -> str:
    """Parse the signal timeframe string and return an ISO expiry timestamp."""
    timeframe = signal.timeframe
    match = re.search(r"(\d+)", timeframe.split("-")[-1] if "-" in timeframe else timeframe)
    days = int(match.group(1)) if match else 7
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _build_tracked_trade(
    chat_id: int,
    signal: Signal,
    stage: TradeStage,
    trade_id: str | None = None,
) -> TrackedTrade:
    scores = signal.scores.copy()
    scores["edge_score"] = float(signal.edge_score)
    scores["confluence_count"] = float(signal.confluence_count)
    scores["learned_expectancy"] = float(signal.learned_expectancy)
    scores["learned_win_rate"] = float(signal.learned_win_rate)
    return TrackedTrade(
        trade_id=trade_id or str(uuid4()),
        chat_id=chat_id,
        ticker=signal.ticker,
        asset_class=signal.asset_class,
        side=signal.side,
        stage=stage,
        entry_low=signal.entry_low,
        entry_high=signal.entry_high,
        stop_loss=signal.stop_loss,
        take_profit_1=signal.take_profit_1,
        take_profit_2=signal.take_profit_2,
        confidence=signal.confidence,
        market_session=signal.market_session,
        signal_quality=signal.signal_quality,
        scores=scores,
        opened_at=datetime.now(timezone.utc).isoformat(),
        expires_at=_signal_expires_at(signal),
    )


def _trade_close_outcome(trade: TrackedTrade, signal: Signal) -> tuple[TradeStage | None, str]:
    price = signal.current_price
    if trade.side == SignalSide.LONG:
        if price <= trade.stop_loss:
            return TradeStage.CLOSED_FAILURE, f"Stop loss hit at {price}."
        if price >= trade.take_profit_2:
            return TradeStage.CLOSED_SUCCESS, f"Take profit 2 hit at {price}."
        if price >= trade.take_profit_1:
            return TradeStage.CLOSED_SUCCESS, f"Take profit 1 hit at {price}."
    elif trade.side == SignalSide.SHORT:
        if price >= trade.stop_loss:
            return TradeStage.CLOSED_FAILURE, f"Stop loss hit at {price}."
        if price <= trade.take_profit_2:
            return TradeStage.CLOSED_SUCCESS, f"Take profit 2 hit at {price}."
        if price <= trade.take_profit_1:
            return TradeStage.CLOSED_SUCCESS, f"Take profit 1 hit at {price}."
    return None, ""


def _tracked_position_entry_and_size(profile, ticker: str) -> tuple[float | None, float | None]:
    normalized = ticker.upper()
    for position in profile.portfolio:
        if position.ticker.upper() == normalized:
            return float(position.entry_price), float(position.size)
    return None, None


def _trade_close_metrics(
    trade: TrackedTrade,
    signal: Signal,
    entry_reference: float | None = None,
    position_size: float | None = None,
) -> dict[str, float | None]:
    entry_price = float(entry_reference if entry_reference is not None else (trade.entry_low + trade.entry_high) / 2)
    risk_per_unit = abs(entry_price - trade.stop_loss)

    if trade.side == SignalSide.LONG:
        pnl_per_unit = signal.current_price - entry_price
    else:
        pnl_per_unit = entry_price - signal.current_price

    return_pct = 0.0 if entry_price == 0 else (pnl_per_unit / entry_price) * 100
    r_multiple = 0.0 if risk_per_unit == 0 else pnl_per_unit / risk_per_unit
    dollar_pnl = None if position_size is None else pnl_per_unit * position_size

    return {
        "entry_price": round(entry_price, 4),
        "return_pct": round(return_pct, 2),
        "r_multiple": round(r_multiple, 2),
        "dollar_pnl": None if dollar_pnl is None else round(dollar_pnl, 2),
        "position_size": position_size,
    }


def _arming_text(signal: Signal, settings: Settings | None = None) -> str:
    return "🟡 <b>ARMING</b> - get ready\n" + _signal_text(signal, settings=settings)


def _signal_live_text(signal: Signal, settings: Settings | None = None) -> str:
    action_label = "buy now" if signal.side == SignalSide.LONG else "sell now"
    return f"🚨 <b>SIGNAL</b> - {action_label}\n" + _signal_text(signal, settings=settings)


def _closed_trade_text(
    trade: TrackedTrade,
    signal: Signal,
    outcome: TradeStage,
    reason: str,
    entry_reference: float | None = None,
    position_size: float | None = None,
) -> str:
    verdict = "SUCCESS" if outcome == TradeStage.CLOSED_SUCCESS else "FAILURE"
    icon = "✅" if outcome == TradeStage.CLOSED_SUCCESS else "❌"
    metrics = _trade_close_metrics(
        trade,
        signal,
        entry_reference=entry_reference,
        position_size=position_size,
    )
    pnl_line = (
        f"Estimated P/L on tracked size: {metrics['dollar_pnl']}\n"
        if metrics["dollar_pnl"] is not None
        else f"Estimated P/L per unit: {round(signal.current_price - metrics['entry_price'], 4) if trade.side == SignalSide.LONG else round(metrics['entry_price'] - signal.current_price, 4)}\n"
    )
    return (
        f"{icon} <b>SIGNAL CLOSED</b> - {verdict}\n"
        f"Ticker: {trade.ticker}\n"
        f"Direction: {trade.side.value}\n"
        f"Opened: {trade.opened_at}\n"
        f"Entry: {trade.entry_low} to {trade.entry_high}\n"
        f"Assumed fill: {metrics['entry_price']}\n"
        f"Stop: {trade.stop_loss}\n"
        f"TP1: {trade.take_profit_1}\n"
        f"TP2: {trade.take_profit_2}\n"
        f"Close price: {signal.current_price}\n"
        f"Return: {metrics['return_pct']}%\n"
        f"R multiple: {metrics['r_multiple']}R\n"
        f"{pnl_line}"
        f"Result: {reason}\n"
        f"<i>{signal.disclaimer}</i>"
    )


def _expired_signal_text(
    trade: TrackedTrade,
    signal: Signal,
    outcome: TradeStage,
    metrics: dict,
) -> str:
    verdict = "SUCCESS" if outcome == TradeStage.CLOSED_SUCCESS else "FAILURE"
    icon = "✅" if outcome == TradeStage.CLOSED_SUCCESS else "❌"
    r = metrics.get("r_multiple", 0.0) or 0.0
    ret = metrics.get("return_pct", 0.0) or 0.0
    pnl_line = (
        f"Estimated P/L per unit: {round(signal.current_price - metrics['entry_price'], 4) if trade.side == SignalSide.LONG else round(metrics['entry_price'] - signal.current_price, 4)}\n"
    )
    return (
        f"{icon} <b>SIGNAL EXPIRED</b> - {verdict}\n"
        f"Ticker: {trade.ticker}\n"
        f"Direction: {trade.side.value}\n"
        f"Opened: {trade.opened_at[:10]}\n"
        f"Entry zone: {trade.entry_low} – {trade.entry_high}\n"
        f"Assumed fill: {metrics['entry_price']}\n"
        f"Stop: {trade.stop_loss} | TP1: {trade.take_profit_1} | TP2: {trade.take_profit_2}\n"
        f"Exit price: {signal.current_price}\n"
        f"Return: {ret}% | R: {r}R\n"
        f"{pnl_line}"
        f"Result: Timeframe elapsed — signal closed at market.\n"
        f"<i>This outcome has been recorded for the learning model.</i>"
    )


def _gameplan_text(gameplan: Gameplan, learning_summary: dict | None = None, settings: Settings | None = None) -> str:
    actionable = [item for item in gameplan.top_trades if item.side != SignalSide.NEUTRAL][:3]
    watchable = [item for item in gameplan.top_trades if item.side == SignalSide.NEUTRAL and item.edge_score > 0][:3]

    stats_line = ""
    if learning_summary and learning_summary.get("total_trades", 0) > 0:
        stats_line = (
            f"\n📈 Bot stats: {learning_summary['total_trades']} closed signals | "
            f"win rate {learning_summary.get('win_rate', 0)}% | "
            f"avg R {learning_summary.get('avg_r', 0)}"
        )

    if not actionable:
        watch_lines = ""
        if watchable:
            watch_lines = "\n\n👀 <b>On Watch</b> (not yet tradable):\n" + "\n".join(
                f"  • <b>{s.ticker}</b>: edge {s.edge_score} | conf {s.confidence}% | "
                f"tech {s.scores.get('technical', 0):.0f} | risk {s.scores.get('risk', 0):.0f} | {s.market_session}"
                for s in watchable
            )
        return (
            f"📬 <b>Daily Signal Digest</b> - {gameplan.generated_for}\n"
            f"No strong actionable setups right now.\n"
            f"Market note: {gameplan.macro_oracle[0]}"
            f"{watch_lines}"
            f"{stats_line}"
        )

    signal_blocks = ["🚨 <b>Signal Setup</b>\n" + _signal_text(item, settings=settings) for item in actionable]
    return (
        f"📬 <b>Daily Signal Digest</b> - {gameplan.generated_for}\n"
        f"Market note: {gameplan.macro_oracle[0]}\n\n"
        + "\n\n".join(signal_blocks)
        + stats_line
    )


def _scan_text(signals: list[Signal], source_label: str) -> str:
    if not signals:
        return f"📡 <b>Scan</b> - {source_label}\nNo valid signals returned."

    lines = [
        f"- <b>{signal.ticker}</b>: {signal.side.value} | conf {signal.confidence}% | edge {signal.edge_score} | "
        f"confluence {signal.confluence_count}/5 | {signal.signal_quality} | {signal.market_session}"
        for signal in signals
    ]
    return f"📡 <b>Scan</b> - {source_label}\n" + "\n".join(lines)


def _performance_report_text(metrics: dict[str, object], period_label: str) -> str:
    return (
        f"📊 <b>{period_label} Signal Report</b>\n"
        f"Window: {metrics['period_start']} to {metrics['period_end']}\n"
        f"Signals closed: {metrics['total_trades']}\n"
        f"Hits: {metrics['hits']}\n"
        f"Misses: {metrics['misses']}\n"
        f"Win rate: {metrics['win_rate']}%\n"
        f"Total P/L: {metrics['total_pnl']}\n"
        f"ROI: {metrics['roi']}%\n"
        f"Average win: {metrics['avg_win']}\n"
        f"Average loss: {metrics['avg_loss']}\n"
        f"Profit factor: {metrics['profit_factor']}\n"
        f"Expectancy: {metrics['expectancy']}R"
    )


def _leaderboard_text(rows: list[dict[str, object]], period_type: str, group_by: str) -> str:
    if not rows:
        return f"🏁 <b>{period_type.title()} Leaderboard</b>\nNo closed trades recorded for this window yet."
    lines = [f"🏁 <b>{period_type.title()} Leaderboard</b> - by {group_by}"]
    for item in rows:
        lines.append(
            f"- <b>{item['label']}</b>: hits {item['winning_trades']} | misses {item['losing_trades']} | "
            f"win {item['win_rate']}% | avgR {item['avg_r']} | pnl {item['total_pnl']}"
        )
    return "\n".join(lines)


def build_handlers(
    updater: Updater,
    settings: Settings,
    engine: SignalEngine,
    state: UserStateStore,
    learning_service: LearningService | None = None,
    macro_risk_service: MacroRiskService | None = None,
) -> None:
    dispatcher = updater.dispatcher

    def guarded_reply(update: Update, text: str) -> None:
        chat_id = update.effective_chat.id if update.effective_chat else 0
        if chat_id:
            state.get_profile(chat_id)
        if not _authorized(settings, chat_id):
            update.effective_message.reply_text("This bot is not enabled for this chat.")
            return
        update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

    def start(update: Update, context: CallbackContext) -> None:
        guarded_reply(
            update,
            (
                f"<b>{settings.bot_name}</b>\n"
                "Commands: /signals <ticker>, /scan [tickers|preset], /analyze <ticker>, /news <ticker>, /gameplan, /pending, /watchlist, "
                "/portfolio, /risk, /settings, /mychatid, /alerts, /stats, /model, /dashboard, /metrics, /report, /leaderboard\n\n"
                "Presets: crypto, stocks, forex, metals, energy, futures\n\n"
                "This bot is for research and informational use only."
            ),
        )

    def mychatid(update: Update, context: CallbackContext) -> None:
        chat_id = update.effective_chat.id if update.effective_chat else 0
        guarded_reply(update, f"Your chat id is <code>{chat_id}</code>.")

    def alerts(update: Update, context: CallbackContext) -> None:
        chat_id = update.effective_chat.id if update.effective_chat else 0
        profile = state.get_profile(chat_id)

        if not context.args:
            guarded_reply(
                update,
                (
                    f"Alert mode: <b>{profile.alert_mode}</b>\n"
                    f"Global live alerts: {'on' if settings.live_alerts_enabled else 'off'}\n"
                    f"Strong play threshold: {settings.strong_play_min_confidence}%\n"
                    f"Tradable threshold: {settings.live_alert_min_confidence}%\n\n"
                    "Lifecycle labels:\n"
                    "- <b>ARMING</b>: get ready\n"
                    "- <b>SIGNAL</b>: buy now or sell now\n"
                    "- <b>SIGNAL CLOSED</b>: success or failure\n\n"
                    "Modes:\n"
                    "- <b>high</b>: arming plus strong buy-now signals\n"
                    "- <b>all</b>: all tradable setups plus buy-now signals\n"
                    "- <b>off</b>: mute push alerts"
                ),
            )
            return

        mode = context.args[0].lower()
        if mode not in {"high", "all", "off"}:
            guarded_reply(update, "Usage: /alerts [high|all|off]")
            return
        profile = state.set_alert_mode(chat_id, mode)
        guarded_reply(update, f"Alert mode set to <b>{profile.alert_mode}</b>.")

    def signals(update: Update, context: CallbackContext) -> None:
        ticker = context.args[0] if context.args else settings.default_tickers[0]
        try:
            signal = engine.generate_signal(ticker)
            guarded_reply(update, _signal_text(signal, settings=settings))
        except Exception as exc:
            guarded_reply(update, f"Could not generate a signal for {ticker}: {exc}")

    def analyze(update: Update, context: CallbackContext) -> None:
        ticker = context.args[0] if context.args else settings.default_tickers[0]
        try:
            snapshot, analyses = engine.analyze(ticker)
            text = (
                f"🔎 <b>Analysis</b> - {snapshot.ticker}\n"
                f"Price: {snapshot.current_price} {snapshot.currency}\n"
                f"Day Change: {round(snapshot.meta.get('day_change_pct', 0.0), 2)}%\n"
                f"Asset: {snapshot.asset_class.value}\n\n"
                + "\n".join(
                    f"<b>{name.title()}</b>: {round(score.score, 2)} | {score.rationale[0]}"
                    for name, score in analyses.items()
                )
            )
            guarded_reply(update, text)
        except Exception as exc:
            guarded_reply(update, f"Could not analyze {ticker}: {exc}")

    def gameplan(update: Update, context: CallbackContext) -> None:
        chat_id = update.effective_chat.id if update.effective_chat else 0
        profile = state.get_profile(chat_id)
        tickers = profile.watchlist or settings.default_tickers
        plan = engine.generate_gameplan(tickers)
        summary = learning_service.metrics_summary("daily") if learning_service is not None else None
        guarded_reply(update, _gameplan_text(plan, learning_summary=summary, settings=settings))

    def scan(update: Update, context: CallbackContext) -> None:
        chat_id = update.effective_chat.id if update.effective_chat else 0
        profile = state.get_profile(chat_id)
        source_label = "custom list"
        if context.args and len(context.args) == 1 and context.args[0].lower() in SCAN_PRESETS:
            preset = context.args[0].lower()
            tickers = SCAN_PRESETS[preset]
            source_label = f"{preset} preset"
        elif context.args:
            tickers = _parse_tickers(context.args)
        else:
            tickers = profile.watchlist or settings.default_tickers
            source_label = "watchlist" if profile.watchlist else "default universe"

        signals: list[Signal] = []
        for ticker in tickers[:30]:
            try:
                signals.append(engine.generate_signal(ticker))
            except Exception:
                continue
        ranked = sorted(
            signals,
            key=lambda item: (item.edge_score, item.confidence, item.confluence_count),
            reverse=True,
        )[:10]
        guarded_reply(update, _scan_text(ranked, source_label))

    def news(update: Update, context: CallbackContext) -> None:
        ticker = context.args[0] if context.args else settings.default_tickers[0]
        try:
            headlines = engine.get_news_brief(ticker)
            if not headlines:
                guarded_reply(update, f"No recent headlines available for {ticker}.")
                return
            lines = [
                f"- <b>{item['source'] or 'Unknown source'}</b>: {item['title']}"
                for item in headlines
            ]
            guarded_reply(update, f"📰 <b>News</b> - {ticker.upper()}\n" + "\n".join(lines))
        except Exception as exc:
            guarded_reply(update, f"Could not fetch news for {ticker}: {exc}")

    def watchlist(update: Update, context: CallbackContext) -> None:
        chat_id = update.effective_chat.id if update.effective_chat else 0
        profile = state.get_profile(chat_id)

        if not context.args or context.args[0] == "list":
            items = ", ".join(profile.watchlist) if profile.watchlist else "empty"
            guarded_reply(update, f"Watchlist: {items}")
            return

        action = context.args[0].lower()
        tickers = _parse_tickers(context.args[1:])

        if action == "add":
            if not tickers:
                guarded_reply(update, "Usage: /watchlist add <ticker1> <ticker2> ...")
                return
            for ticker in tickers:
                profile = state.add_watchlist(chat_id, ticker)
            guarded_reply(update, f"Added: {', '.join(tickers)}\nWatchlist: {', '.join(profile.watchlist)}")
            return
        if action == "remove":
            if not tickers:
                guarded_reply(update, "Usage: /watchlist remove <ticker1> <ticker2> ...")
                return
            for ticker in tickers:
                profile = state.remove_watchlist(chat_id, ticker)
            guarded_reply(update, f"Removed: {', '.join(tickers)}\nWatchlist: {', '.join(profile.watchlist) or 'empty'}")
            return
        if action == "set":
            if not tickers:
                guarded_reply(update, "Usage: /watchlist set <ticker1> <ticker2> ...")
                return
            profile = state.set_watchlist(chat_id, tickers)
            guarded_reply(update, f"Watchlist set: {', '.join(profile.watchlist)}")
            return
        if action == "clear":
            state.clear_watchlist(chat_id)
            guarded_reply(update, "Watchlist cleared.")
            return

        guarded_reply(update, "Usage: /watchlist add|remove|set <tickers...> | /watchlist clear | /watchlist list")

    def portfolio(update: Update, context: CallbackContext) -> None:
        chat_id = update.effective_chat.id if update.effective_chat else 0
        profile = state.get_profile(chat_id)

        if not context.args or context.args[0] == "list":
            if not profile.portfolio:
                guarded_reply(update, "Portfolio is empty.")
                return
            lines = [
                f"- {position.ticker}: entry {position.entry_price}, size {position.size}"
                for position in profile.portfolio
            ]
            guarded_reply(update, "Portfolio:\n" + "\n".join(lines))
            return

        action = context.args[0].lower()
        if action == "add" and len(context.args) == 4:
            ticker, entry_price, size = context.args[1], float(context.args[2]), float(context.args[3])
            state.add_portfolio_position(chat_id, ticker, entry_price, size)
            guarded_reply(update, f"Tracked {ticker.upper()} at {entry_price} for size {size}.")
            return
        if action == "remove" and len(context.args) == 2:
            state.remove_portfolio_position(chat_id, context.args[1])
            guarded_reply(update, f"Removed {context.args[1].upper()} from tracked portfolio.")
            return

        guarded_reply(update, "Usage: /portfolio add <ticker> <entry> <size> | /portfolio remove <ticker> | /portfolio list")

    def risk(update: Update, context: CallbackContext) -> None:
        chat_id = update.effective_chat.id if update.effective_chat else 0
        profile = state.get_profile(chat_id)
        report = engine.build_portfolio_risk_report(profile.portfolio)
        text = (
            f"⚠️ <b>Risk</b>\n"
            f"Positions: {report.total_positions}\n"
            f"Gross Exposure: {report.gross_exposure}\n"
            f"Concentration: {report.concentration_risk}\n"
            f"Notes:\n- " + "\n- ".join(report.warnings)
        )
        guarded_reply(update, text)

    def settings_cmd(update: Update, context: CallbackContext) -> None:
        text = (
            f"Bot: {settings.bot_name}\n"
            f"Default risk per trade: {settings.default_risk_per_trade:.2%}\n"
            f"Daily gameplan: {settings.gameplan_hour_utc:02d}:{settings.gameplan_minute_utc:02d} UTC\n"
            f"Live alerts: {'on' if settings.live_alerts_enabled else 'off'} every {settings.live_alert_interval_minutes}m, min confidence {settings.live_alert_min_confidence}, strong plays {settings.strong_play_min_confidence}\n"
            f"Edge mode: {'on' if settings.edge_over_speed_mode else 'off'} | min confluence {settings.signal_min_confluence} | high confluence {settings.high_quality_min_confluence} | edge floor {settings.edge_score_min_alert}/{settings.edge_score_min_high_quality}\n"
            f"Learning data: {settings.learning_data_dir} | min samples {settings.learning_min_sample_size} | max adjustment {settings.learning_max_confidence_adjustment}\n"
            f"Learning SQLite: {'on' if settings.learning_sqlite_enabled else 'off'} | path {settings.learning_sqlite_path}\n"
            f"Namespaces: bot {settings.bot_namespace} | learning {settings.learning_namespace}\n"
            f"Weak edge filter: {'on' if settings.learning_block_negative_edges else 'off'} | threshold {settings.learning_weak_edge_threshold} | min samples {settings.learning_weak_edge_min_samples}\n"
            f"Twelve Data: {'configured' if settings.twelvedata_api_key else 'fallback to Yahoo'}\n"
            f"State backend: {state.backend_name()} ({'persistent' if state.persistence_enabled() else 'ephemeral'})\n"
            f"Default tickers: {', '.join(settings.default_tickers)}\n"
            f"Scan presets: {', '.join(sorted(SCAN_PRESETS.keys()))}"
        )
        guarded_reply(update, text)

    def stats_cmd(update: Update, context: CallbackContext) -> None:
        if learning_service is None:
            guarded_reply(update, "Learning service is not active.")
            return
        scope = context.args[0].upper() if context.args else None
        asset_classes = {item.value for item in AssetClass}
        if scope and scope.lower() in asset_classes:
            summary = learning_service.summary(asset_class=scope.lower())
            label = f"asset class {scope.lower()}"
        elif scope:
            summary = learning_service.summary(ticker=scope)
            label = scope
        else:
            summary = learning_service.summary()
            label = "all closed trades"
        guarded_reply(
            update,
            (
                f"📊 <b>Learning Stats</b> - {label}\n"
                f"Closed trades: {summary['total_trades']}\n"
                f"Win rate: {summary['win_rate']}%\n"
                f"Average R: {summary['avg_r']}\n"
                f"Average return: {summary['avg_return_pct']}%\n"
                f"Expectancy: {summary['expectancy']}R"
            ),
        )

    def model_cmd(update: Update, context: CallbackContext) -> None:
        if learning_service is None:
            guarded_reply(update, "Learning service is not active.")
            return
        model = learning_service.model_snapshot()
        lines: list[str] = ["🧠 <b>Learning Model</b>"]
        for bucket_name in ("asset_class", "asset_session", "ticker", "side"):
            bucket = model.get(bucket_name, {})
            if not bucket:
                continue
            top = sorted(
                bucket.items(),
                key=lambda item: (abs(int(item[1].get("adjustment", 0))), int(item[1].get("samples", 0))),
                reverse=True,
            )[:3]
            for key, payload in top:
                lines.append(
                    f"- {bucket_name}: <b>{key}</b> | adj {int(payload.get('adjustment', 0)):+d} | "
                    f"samples {int(payload.get('samples', 0))} | win {payload.get('win_rate', 0)}% | avgR {payload.get('avg_r', 0)}"
                )
        if len(lines) == 1:
            lines.append("- No learned edges yet. The bot needs more closed trades.")
        guarded_reply(update, "\n".join(lines))

    def metrics_cmd(update: Update, context: CallbackContext) -> None:
        if learning_service is None:
            guarded_reply(update, "Learning service is not active.")
            return
        period_type = context.args[0].lower() if context.args else "daily"
        if period_type not in {"daily", "weekly"}:
            guarded_reply(update, "Usage: /metrics [daily|weekly]")
            return
        metrics = learning_service.metrics_summary(period_type)
        guarded_reply(update, _performance_report_text(metrics, period_type.title()))

    def report_cmd(update: Update, context: CallbackContext) -> None:
        if learning_service is None:
            guarded_reply(update, "Learning service is not active.")
            return
        period_type = context.args[0].lower() if context.args else "daily"
        if period_type not in {"daily", "weekly"}:
            guarded_reply(update, "Usage: /report [daily|weekly]")
            return
        metrics = learning_service.metrics_summary(period_type)
        guarded_reply(update, _performance_report_text(metrics, period_type.title()))

    def leaderboard_cmd(update: Update, context: CallbackContext) -> None:
        if learning_service is None:
            guarded_reply(update, "Learning service is not active.")
            return
        period_type = context.args[0].lower() if context.args else "weekly"
        group_by = context.args[1].lower() if len(context.args) > 1 else "ticker"
        if period_type not in {"daily", "weekly"} or group_by not in {"ticker", "session", "asset"}:
            guarded_reply(update, "Usage: /leaderboard [daily|weekly] [ticker|session|asset]")
            return
        rows = learning_service.leaderboard(period_type=period_type, group_by=group_by, limit=5)
        guarded_reply(update, _leaderboard_text(rows, period_type, group_by))

    def dashboard_cmd(update: Update, context: CallbackContext) -> None:
        if learning_service is None:
            guarded_reply(update, "Learning service is not active.")
            return
        ticker = context.args[0] if context.args else settings.default_tickers[0]
        dashboard = learning_service.dashboard(ticker)
        summary = dashboard["summary"]
        lines = [
            f"📋 <b>Dashboard</b> - {dashboard['ticker']}",
            f"Signal events: {dashboard['signal_events']}",
            f"Closed trades: {summary['total_trades']}",
            f"Win rate: {summary['win_rate']}%",
            f"Average R: {summary['avg_r']}",
            f"Average return: {summary['avg_return_pct']}%",
            f"Expectancy: {summary['expectancy']}R",
        ]
        stage_counts = dashboard.get("stage_counts", {})
        if stage_counts:
            stage_line = ", ".join(f"{name}={count}" for name, count in sorted(stage_counts.items()))
            lines.append(f"Stages: {stage_line}")
        model_entries = dashboard.get("model_entries", [])
        if model_entries:
            lines.append("Learned edges:")
            for item in model_entries[:3]:
                lines.append(
                    f"- {item['bucket']}: adj {int(item.get('adjustment', 0)):+d} | "
                    f"samples {int(item.get('samples', 0))} | win {item.get('win_rate', 0)}% | avgR {item.get('avg_r', 0)}"
                )
        recent = dashboard.get("recent_closures", [])
        if recent:
            lines.append("Recent closures:")
            for item in recent[:3]:
                lines.append(
                    f"- {item.get('outcome', '')}: return {item.get('return_pct', 0)}% | "
                    f"R {item.get('r_multiple', 0)} | closed {str(item.get('closed_at', ''))[:10]}"
                )
        guarded_reply(update, "\n".join(lines))

    def scheduled_gameplan(context: CallbackContext) -> None:
        chat_ids = settings.allowed_chat_ids
        if not chat_ids:
            return
        plan = engine.generate_gameplan(settings.default_tickers)
        summary = learning_service.metrics_summary("daily") if learning_service is not None else None
        text = _gameplan_text(plan, learning_summary=summary, settings=settings)
        for chat_id in chat_ids:
            context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)

    def scheduled_report(context: CallbackContext) -> None:
        if learning_service is None:
            return
        chat_ids = set(settings.allowed_chat_ids) | set(state.list_chat_ids())
        if not chat_ids:
            return
        metrics = learning_service.metrics_summary("daily")
        text = _performance_report_text(metrics, "Daily")
        for chat_id in sorted(chat_ids):
            context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)

    def scheduled_weekly_report(context: CallbackContext) -> None:
        if learning_service is None:
            return
        if datetime.now(timezone.utc).weekday() != 0:
            return
        chat_ids = set(settings.allowed_chat_ids) | set(state.list_chat_ids())
        if not chat_ids:
            return
        metrics = learning_service.metrics_summary("weekly")
        text = _performance_report_text(metrics, "Weekly")
        for chat_id in sorted(chat_ids):
            context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)

    def refresh_learning_model(context: CallbackContext) -> None:
        del context
        if learning_service is not None:
            learning_service.refresh_model()

    def regime_writer(context: CallbackContext) -> None:
        """Hourly job: analyse each ticker and write its market regime to disk."""
        del context
        tickers = settings.default_tickers
        for ticker in tickers:
            try:
                snapshot, analyses = engine.analyze(ticker)
                tech = analyses["technical"]
                facts = dict(tech.facts)
                facts["current_price"] = snapshot.current_price
                write_market_regime(
                    ticker=ticker,
                    technical_score=tech.score,
                    facts=facts,
                    data_dir=settings.learning_data_dir,
                )
            except Exception:
                continue

    def live_alert_scan(context: CallbackContext) -> None:
        if not settings.live_alerts_enabled:
            return

        chat_ids = set(settings.allowed_chat_ids) | set(state.list_chat_ids())
        if not chat_ids:
            return

        chat_tickers: dict[int, list[str]] = {}
        unique_tickers: list[str] = []
        seen_tickers: set[str] = set()
        for chat_id in sorted(chat_ids):
            profile = state.get_profile(chat_id)
            if profile.alert_mode == "off":
                continue
            tickers = (profile.watchlist or settings.default_tickers)[: settings.live_alert_ticker_limit]
            chat_tickers[chat_id] = tickers
            for ticker in tickers:
                normalized = ticker.upper()
                if normalized not in seen_tickers:
                    seen_tickers.add(normalized)
                    unique_tickers.append(normalized)

        signal_cache: dict[str, Signal | None] = {}
        macro_cache: dict[str, tuple[bool, str]] = {}
        for ticker in unique_tickers:
            try:
                signal_cache[ticker] = engine.generate_signal(ticker)
            except Exception:
                signal_cache[ticker] = None

        for chat_id, tickers in chat_tickers.items():
            profile = state.get_profile(chat_id)
            for ticker in tickers:
                signal = signal_cache.get(ticker.upper())
                if signal is None:
                    continue

                if signal.asset_class in {AssetClass.STOCK, AssetClass.ETF} and signal.market_session != "U.S. regular session":
                    continue

                tracked_trade = state.get_tracked_trade(chat_id, signal.ticker)

                if tracked_trade is not None and tracked_trade.stage == TradeStage.SIGNAL:
                    # Check TP/SL hit first
                    outcome, reason = _trade_close_outcome(tracked_trade, signal)
                    if outcome is not None:
                        entry_reference, position_size = _tracked_position_entry_and_size(profile, signal.ticker)
                        close_metrics = _trade_close_metrics(
                            tracked_trade,
                            signal,
                            entry_reference=entry_reference,
                            position_size=position_size,
                        )
                        if state.should_send_alert(chat_id, signal.ticker, outcome.value, tracked_trade.confidence):
                            context.bot.send_message(
                                chat_id=chat_id,
                                text=_closed_trade_text(
                                    tracked_trade,
                                    signal,
                                    outcome,
                                    reason,
                                    entry_reference=entry_reference,
                                    position_size=position_size,
                                ),
                                parse_mode=ParseMode.HTML,
                            )
                        if learning_service is not None:
                            learning_service.record_trade_close(
                                tracked_trade,
                                signal,
                                outcome,
                                close_metrics,
                            )
                        state.clear_tracked_trade(chat_id, signal.ticker)
                        continue

                    # Check if the signal has expired (timeframe elapsed, no TP/SL hit)
                    if tracked_trade.expires_at:
                        try:
                            expires_dt = datetime.fromisoformat(tracked_trade.expires_at)
                            if datetime.now(timezone.utc) >= expires_dt:
                                entry_reference, position_size = _tracked_position_entry_and_size(profile, signal.ticker)
                                close_metrics = _trade_close_metrics(
                                    tracked_trade,
                                    signal,
                                    entry_reference=entry_reference,
                                    position_size=position_size,
                                )
                                r_multiple = float(close_metrics.get("r_multiple") or 0.0)
                                expiry_outcome = (
                                    TradeStage.CLOSED_SUCCESS if r_multiple > 0 else TradeStage.CLOSED_FAILURE
                                )
                                context.bot.send_message(
                                    chat_id=chat_id,
                                    text=_expired_signal_text(tracked_trade, signal, expiry_outcome, close_metrics),
                                    parse_mode=ParseMode.HTML,
                                )
                                if learning_service is not None:
                                    learning_service.record_trade_close(
                                        tracked_trade,
                                        signal,
                                        expiry_outcome,
                                        close_metrics,
                                    )
                                state.clear_tracked_trade(chat_id, signal.ticker)
                                continue
                        except ValueError:
                            pass

                should_filter = False
                reason = ""
                if macro_risk_service is not None:
                    cached = macro_cache.get(signal.ticker)
                    if cached is None:
                        cached = macro_risk_service.should_filter_alert(signal)
                        macro_cache[signal.ticker] = cached
                    should_filter, reason = cached
                    if reason and reason not in signal.rationale:
                        signal.rationale.insert(0, reason)

                if should_filter:
                    continue

                if learning_service is not None:
                    should_block, block_reason = learning_service.should_block_signal(signal)
                    if should_block:
                        continue
                    if block_reason and block_reason not in signal.rationale:
                        signal.rationale.insert(0, block_reason)

                actionable = _is_actionable_signal(signal, settings)
                strong_signal = _is_strong_signal(signal, settings)

                if not actionable:
                    if tracked_trade is not None and tracked_trade.stage == TradeStage.ARMING:
                        state.clear_tracked_trade(chat_id, signal.ticker)
                    continue

                if tracked_trade is not None and tracked_trade.side != signal.side:
                    state.clear_tracked_trade(chat_id, signal.ticker)
                    tracked_trade = None

                if tracked_trade is not None and tracked_trade.stage == TradeStage.SIGNAL:
                    continue

                if (
                    tracked_trade is not None
                    and tracked_trade.stage == TradeStage.ARMING
                    and not strong_signal
                ):
                    continue

                if strong_signal:
                    trade_id = tracked_trade.trade_id if tracked_trade is not None else None
                    tracked_trade = _build_tracked_trade(chat_id, signal, TradeStage.SIGNAL, trade_id=trade_id)
                    state.set_tracked_trade(tracked_trade)
                    if learning_service is not None:
                        learning_service.record_signal_event(tracked_trade, TradeStage.SIGNAL)
                    if state.should_send_alert(chat_id, signal.ticker, TradeStage.SIGNAL.value, signal.confidence):
                        context.bot.send_message(
                            chat_id=chat_id,
                            text=_signal_live_text(signal, settings=settings),
                            parse_mode=ParseMode.HTML,
                        )
                        if hasattr(state, "log_alert"):
                            try:
                                state.log_alert(chat_id, signal)  # type: ignore[attr-defined]
                            except Exception:
                                pass
                    continue

                if profile.alert_mode == "high" and signal.signal_quality == "watchlist":
                    continue

                tracked_trade = _build_tracked_trade(chat_id, signal, TradeStage.ARMING)
                state.set_tracked_trade(tracked_trade)
                if learning_service is not None:
                    learning_service.record_signal_event(tracked_trade, TradeStage.ARMING)
                if state.should_send_alert(chat_id, signal.ticker, TradeStage.ARMING.value, signal.confidence):
                    context.bot.send_message(
                        chat_id=chat_id,
                        text=_arming_text(signal, settings=settings),
                        parse_mode=ParseMode.HTML,
                    )

    def pending_cmd(update: Update, context: CallbackContext) -> None:
        chat_id = update.effective_chat.id if update.effective_chat else 0
        profile = state.get_profile(chat_id)
        tickers = profile.watchlist or settings.default_tickers
        open_trades = [
            state.get_tracked_trade(chat_id, ticker)
            for ticker in tickers
            if state.get_tracked_trade(chat_id, ticker) is not None
        ]
        if not open_trades:
            guarded_reply(update, "📭 No open signals being tracked right now.")
            return
        lines = [f"📡 <b>Open Signals</b> ({len(open_trades)} tracked)"]
        for trade in open_trades:
            try:
                signal = engine.generate_signal(trade.ticker)
                metrics = _trade_close_metrics(trade, signal)
                r = float(metrics.get("r_multiple") or 0.0)
                ret = float(metrics.get("return_pct") or 0.0)
                price_now = signal.current_price
                r_icon = "🟢" if r > 0 else ("🔴" if r < 0 else "⚪")
                expires_label = ""
                if trade.expires_at:
                    try:
                        exp = datetime.fromisoformat(trade.expires_at)
                        days_left = (exp - datetime.now(timezone.utc)).days
                        expires_label = f" | expires in {max(0, days_left)}d"
                    except ValueError:
                        pass
                lines.append(
                    f"{r_icon} <b>{trade.ticker}</b> {trade.side.value} [{trade.stage.value}]\n"
                    f"   Entry: {trade.entry_low}–{trade.entry_high} | Stop: {trade.stop_loss} | TP1: {trade.take_profit_1}\n"
                    f"   Price now: {price_now} | R: {r:+.2f}R | Return: {ret:+.2f}%{expires_label}\n"
                    f"   Conf: {trade.confidence}% | Quality: {trade.signal_quality}"
                )
            except Exception:
                lines.append(f"⚪ <b>{trade.ticker}</b> {trade.side.value} — could not fetch live price")
        guarded_reply(update, "\n".join(lines))

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("mychatid", mychatid))
    dispatcher.add_handler(CommandHandler("alerts", alerts))
    dispatcher.add_handler(CommandHandler("signals", signals))
    dispatcher.add_handler(CommandHandler("scan", scan))
    dispatcher.add_handler(CommandHandler("analyze", analyze))
    dispatcher.add_handler(CommandHandler("news", news))
    dispatcher.add_handler(CommandHandler("gameplan", gameplan))
    dispatcher.add_handler(CommandHandler("watchlist", watchlist))
    dispatcher.add_handler(CommandHandler("portfolio", portfolio))
    dispatcher.add_handler(CommandHandler("risk", risk))
    dispatcher.add_handler(CommandHandler("stats", stats_cmd))
    dispatcher.add_handler(CommandHandler("model", model_cmd))
    dispatcher.add_handler(CommandHandler("dashboard", dashboard_cmd))
    dispatcher.add_handler(CommandHandler("metrics", metrics_cmd))
    dispatcher.add_handler(CommandHandler("report", report_cmd))
    dispatcher.add_handler(CommandHandler("leaderboard", leaderboard_cmd))
    dispatcher.add_handler(CommandHandler("settings", settings_cmd))
    dispatcher.add_handler(CommandHandler("pending", pending_cmd))

    if updater.job_queue is not None:
        updater.job_queue.run_daily(
            scheduled_gameplan,
            time(hour=settings.gameplan_hour_utc, minute=settings.gameplan_minute_utc),
            name="daily_gameplan",
        )
        updater.job_queue.run_repeating(
            live_alert_scan,
            interval=max(60, settings.live_alert_interval_minutes * 60),
            first=15,
            name="live_alert_scan",
        )
        updater.job_queue.run_repeating(
            refresh_learning_model,
            interval=6 * 60 * 60,
            first=30,
            name="refresh_learning_model",
        )
        updater.job_queue.run_daily(
            scheduled_report,
            time(hour=settings.gameplan_hour_utc, minute=(settings.gameplan_minute_utc + 2) % 60),
            name="daily_report",
        )
        updater.job_queue.run_daily(
            scheduled_weekly_report,
            time(hour=settings.gameplan_hour_utc, minute=(settings.gameplan_minute_utc + 4) % 60),
            name="weekly_report",
        )
        updater.job_queue.run_repeating(
            regime_writer,
            interval=60 * 60,
            first=60,
            name="regime_writer",
        )
