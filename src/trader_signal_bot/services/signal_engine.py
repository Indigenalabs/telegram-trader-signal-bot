from __future__ import annotations

from datetime import datetime, timezone

from trader_signal_bot.config import Settings
from trader_signal_bot.domain import (
    Gameplan,
    PortfolioRiskReport,
    PriceSnapshot,
    Signal,
    SignalSide,
    UniverseScenario,
)
from trader_signal_bot.services.analysis import (
    _atr,
    find_support_resistance,
    fundamental_analysis,
    market_session_label,
    macro_analysis,
    portfolio_risk_summary,
    risk_analysis,
    sentiment_analysis,
    smc_technical_analysis,
    technical_analysis,
)
from trader_signal_bot.services.learning import LearningService
from trader_signal_bot.services.market_data import MarketDataProvider
from trader_signal_bot.services.news import NewsService


class SignalEngine:
    def __init__(
        self,
        provider: MarketDataProvider,
        settings: Settings,
        news_service: NewsService | None = None,
        learning_service: LearningService | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings
        self.news_service = news_service or NewsService()
        self.learning_service = learning_service
        # SMC (Ajna) gets the largest weight; traditional analysis provides context
        self.weights = {
            "smc": 0.40,
            "technical": 0.20,
            "fundamental": 0.15,
            "sentiment": 0.10,
            "risk": 0.10,
            "macro": 0.05,
        }

    def _confluence_for_side(
        self,
        side: SignalSide,
        analyses: dict[str, object],
        min_technical: int,
        min_risk: int,
    ) -> tuple[int, list[str]]:
        thresholds_long = {
            "smc": 58,
            "technical": min_technical,
            "fundamental": 52,
            "sentiment": 52,
            "risk": min_risk,
            "macro": 50,
        }
        thresholds_short = {
            "smc": 42,
            "technical": 100 - min_technical,
            "fundamental": 48,
            "sentiment": 48,
            "risk": max(45, min_risk - 5),
            "macro": 45,
        }
        if side == SignalSide.LONG:
            checks = {
                "smc": (
                    analyses["smc"].score >= thresholds_long["smc"],
                    "SMC structure is bullish with a valid confirmation block or zone.",
                ),
                "technical": (
                    analyses["technical"].score >= thresholds_long["technical"],
                    "Trend stack is aligned with the long side.",
                ),
                "fundamental": (
                    analyses["fundamental"].score >= thresholds_long["fundamental"],
                    "Underlying structure is supportive enough to hold a long bias.",
                ),
                "sentiment": (
                    analyses["sentiment"].score >= thresholds_long["sentiment"],
                    "Sentiment is leaning constructive instead of fighting the move.",
                ),
                "risk": (
                    analyses["risk"].score >= thresholds_long["risk"],
                    "Risk conditions are controlled enough for structured exposure.",
                ),
                "macro": (
                    analyses["macro"].score >= thresholds_long["macro"],
                    "Macro backdrop is not materially working against the setup.",
                ),
            }
        else:
            checks = {
                "smc": (
                    analyses["smc"].score <= thresholds_short["smc"],
                    "SMC structure is bearish with a valid confirmation block or zone.",
                ),
                "technical": (
                    analyses["technical"].score <= thresholds_short["technical"],
                    "Trend structure is weak enough to justify a short bias.",
                ),
                "fundamental": (
                    analyses["fundamental"].score <= thresholds_short["fundamental"],
                    "Underlying structure is soft enough to support downside pressure.",
                ),
                "sentiment": (
                    analyses["sentiment"].score <= thresholds_short["sentiment"],
                    "Sentiment is stretched or deteriorating into the short side.",
                ),
                "risk": (
                    analyses["risk"].score >= thresholds_short["risk"],
                    "Risk conditions are orderly enough to define a short cleanly.",
                ),
                "macro": (
                    analyses["macro"].score >= thresholds_short["macro"],
                    "Macro backdrop leaves room for downside continuation.",
                ),
            }

        confluence_signals = [reason for passed, reason in checks.values() if passed]
        return len(confluence_signals), confluence_signals

    def _edge_score(self, signal: Signal) -> int:
        confluence_pct = min(100.0, (signal.confluence_count / 5) * 100)
        expectancy_boost = max(-8.0, min(12.0, signal.learned_expectancy * 10))
        # When model is cold (no samples), treat win rate as neutral 50 — not 0.
        # A win_rate of 0 means "no data", not "always loses". Penalising cold signals
        # creates a death spiral where the bot never fires to collect the data it needs.
        effective_win_rate = signal.learned_win_rate if signal.learned_sample_size >= 3 else 50.0
        win_rate_boost = max(-6.0, min(10.0, (effective_win_rate - 50.0) * 0.22))
        support_boost = min(8.0, signal.learned_sample_size * 0.6)
        raw = (
            signal.confidence * 0.52
            + confluence_pct * 0.28
            + expectancy_boost
            + win_rate_boost
            + support_boost
        )
        return max(0, min(100, round(raw)))

    def analyze(self, ticker: str) -> tuple[PriceSnapshot, dict[str, object]]:
        snapshot = self.provider.get_snapshot(ticker)
        # Inject candle_interval from settings so analysis functions can scale thresholds
        snapshot.meta.setdefault("candle_interval", self.settings.candle_interval)
        analyses = {
            "smc": smc_technical_analysis(snapshot),
            "technical": technical_analysis(snapshot),
            "fundamental": fundamental_analysis(snapshot),
            "sentiment": sentiment_analysis(snapshot),
            "risk": risk_analysis(snapshot),
            "macro": macro_analysis(snapshot),
        }
        return snapshot, analyses

    def generate_signal(self, ticker: str) -> Signal:
        snapshot, analyses = self.analyze(ticker)
        headlines = self.news_service.get_headlines(ticker, page_size=3)

        weighted_score = 0.0
        rationale: list[str] = []
        for name, score in analyses.items():
            weighted_score += score.score * self.weights[name]
            if score.rationale:
                rationale.append(score.rationale[0])

        news_risk = False
        if headlines:
            rationale.append(f"News flow is active with {len(headlines)} recent headlines in the feed.")
            news_risk = True

        smc_score = analyses["smc"].score
        technical_score = analyses["technical"].score
        risk_score = analyses["risk"].score
        macro_score = analyses["macro"].score
        current = snapshot.current_price
        candle_interval = self.settings.candle_interval

        # Pull the latest SMC confirmation block if present
        smc_facts = analyses["smc"].facts
        smc_conf_block = smc_facts.get("confirmation_block")
        smc_trend = smc_facts.get("smc_trend", "ranging")
        smc_confluences = smc_facts.get("confluences", [])

        # Stop distance scales with candle resolution — intraday needs tighter stops
        _stop_scale = {
            "1m": 0.12, "5m": 0.18, "15m": 0.25, "30m": 0.35,
            "1h": 0.45, "2h": 0.60, "4h": 0.75, "1d": 1.0, "1w": 1.5,
        }
        stop_scale = _stop_scale.get(candle_interval, 1.0)

        # Timeframe label shown in signal messages
        _timeframe_labels = {
            "1m": "5-20 minutes", "5m": "15-60 minutes", "15m": "30-120 minutes",
            "30m": "1-3 hours", "1h": "2-8 hours", "2h": "4-12 hours",
            "4h": "8-24 hours", "1d": "2-5 days", "1w": "1-3 weeks",
        }

        # ATR-based stop distance — more adaptive than fixed % of price
        atr_value = 0.0
        if snapshot.history_high and snapshot.history_low:
            atr_value = _atr(snapshot.history, snapshot.history_high, snapshot.history_low, period=14)
        if atr_value > 0:
            stop_distance = atr_value * 1.5 * stop_scale
        else:
            stop_distance = max(abs(snapshot.high - snapshot.low), current * 0.015 * stop_scale)

        market_session = market_session_label(snapshot)

        if snapshot.asset_class.value in {"stock", "etf"}:
            stop_distance = max(stop_distance, current * 0.012 * stop_scale)
            long_threshold = 62
            short_threshold = 44
            min_technical = 60
            min_risk = 58
        elif snapshot.asset_class.value == "forex":
            stop_distance = max(abs(snapshot.high - snapshot.low), current * 0.0045 * stop_scale)
            long_threshold = 60
            short_threshold = 44
            min_technical = 58
            min_risk = 55
        elif snapshot.asset_class.value == "futures":
            stop_distance = max(abs(snapshot.high - snapshot.low), current * 0.01 * stop_scale)
            long_threshold = 61
            short_threshold = 44
            min_technical = 59
            min_risk = 55
        elif snapshot.asset_class.value == "crypto":
            stop_distance = max(abs(snapshot.high - snapshot.low), current * 0.012 * stop_scale)
            long_threshold = 63
            short_threshold = 44
            min_technical = 60
            min_risk = 52
        else:
            long_threshold = 60
            short_threshold = 44
            min_technical = 58
            min_risk = 55

        # Use SMC Confirmation Block for structure-based SL/TP when available
        smc_sl: float | None = None
        smc_tp: float | None = None
        smc_entry: float | None = None
        if smc_conf_block:
            smc_sl = smc_conf_block.get("sl")
            smc_tp = smc_conf_block.get("tp")
            smc_entry = smc_conf_block.get("entry")

        if (
            weighted_score >= long_threshold
            and technical_score >= min_technical
            and risk_score >= min_risk
            and macro_score >= 48
        ):
            side = SignalSide.LONG
            entry_low = current * 0.997
            entry_high = current * 1.003
            if smc_sl and smc_conf_block.get("type") == "long" and smc_sl < current:
                # Use Ajna structure-based stop: reversal candle low * 0.999
                stop_loss = smc_sl
                stop_distance = current - stop_loss
                take_profit_1 = current + stop_distance * 2.0   # 2R (Ajna default)
                take_profit_2 = current + stop_distance * 3.5
                rationale.append(f"Ajna SL set at reversal candle low {stop_loss:.4f} (2R TP = {take_profit_1:.4f}).")
            else:
                stop_loss = current - stop_distance
                take_profit_1 = current + (stop_distance * 1.8)
                take_profit_2 = current + (stop_distance * 3.0)
        elif (
            weighted_score <= short_threshold
            and technical_score <= (100 - min_technical)
            and risk_score >= max(45, min_risk - 5)
            and macro_score >= 45
        ):
            side = SignalSide.SHORT
            entry_low = current * 0.997
            entry_high = current * 1.003
            if smc_sl and smc_conf_block.get("type") == "short" and smc_sl > current:
                # Use Ajna structure-based stop: reversal candle high * 1.001
                stop_loss = smc_sl
                stop_distance = stop_loss - current
                take_profit_1 = current - stop_distance * 2.0   # 2R
                take_profit_2 = current - stop_distance * 3.5
                rationale.append(f"Ajna SL set at reversal candle high {stop_loss:.4f} (2R TP = {take_profit_1:.4f}).")
            else:
                stop_loss = current + stop_distance
                take_profit_1 = current - (stop_distance * 1.8)
                take_profit_2 = current - (stop_distance * 3.0)
        else:
            side = SignalSide.NEUTRAL
            entry_low = current * 0.998
            entry_high = current * 1.002
            stop_loss = current - (stop_distance * 0.8)
            take_profit_1 = current + (stop_distance * 0.8)
            take_profit_2 = current + (stop_distance * 1.2)
            rationale.insert(0, "Signal quality is mixed, so the engine is staying selective.")

        # Append SMC context to rationale when relevant
        if smc_trend != "ranging" and side != SignalSide.NEUTRAL:
            if (smc_trend == "bullish" and side == SignalSide.LONG) or (smc_trend == "bearish" and side == SignalSide.SHORT):
                rationale.append(f"SMC swing bias aligns with the signal ({smc_trend}).")
            else:
                rationale.append(f"⚠️ SMC swing bias ({smc_trend}) diverges from signal direction — treat with caution.")
        if smc_confluences:
            rationale.append(f"SMC zone confluence: {', '.join(smc_confluences[:3])}.")

        if side == SignalSide.SHORT:
            confidence = round(((100.0 - weighted_score) * 0.7) + (risk_score * 0.3))
        else:
            confidence = round((weighted_score * 0.7) + (risk_score * 0.3))

        # News risk flag — active headlines increase uncertainty, penalise confidence slightly
        if news_risk:
            confidence = max(0, confidence - 3)
            rationale.insert(0, "⚠️ NEWS RISK: Active headlines detected — treat levels as approximate until news resolves.")

        base_confidence = max(0, min(confidence, 100))
        confluence_count = 0
        confluence_signals: list[str] = []
        if side != SignalSide.NEUTRAL:
            confluence_count, confluence_signals = self._confluence_for_side(
                side,
                analyses,
                min_technical=min_technical,
                min_risk=min_risk,
            )
        signal_quality = "watchlist"

        timeframe = _timeframe_labels.get(candle_interval, "2-8 hours")
        # Trade type label shown in signal message header
        _trade_type_labels = {
            "1m": "SCALP", "5m": "SCALP", "15m": "DAY TRADE",
            "30m": "DAY TRADE", "1h": "DAY TRADE", "2h": "DAY TRADE",
            "4h": "SWING", "1d": "SWING", "1w": "LONG PLAY",
        }
        trade_type = _trade_type_labels.get(candle_interval, "DAY TRADE")

        # --- Support / Resistance ---
        sr_levels: dict[str, list] = {"support": [], "resistance": []}
        if snapshot.history_high and snapshot.history_low:
            sr_levels = find_support_resistance(
                closes=snapshot.history,
                highs=snapshot.history_high,
                lows=snapshot.history_low,
                current_price=current,
            )

        support_levels = sr_levels.get("support", [])
        resistance_levels = sr_levels.get("resistance", [])

        # Snap stop-loss to a nearby S/R level when it makes geometric sense.
        # For LONG: if the nearest support is between our calculated stop and entry,
        #   place the stop just below that support (more natural level).
        # For SHORT: if the nearest resistance is between entry and our calculated stop,
        #   place the stop just above that resistance.
        if side == SignalSide.LONG and support_levels:
            nearest_sup_price = support_levels[0][0]
            # Use the support level as stop if it sits between current stop and entry
            if stop_loss < nearest_sup_price < entry_low:
                snapped_stop = nearest_sup_price * 0.998  # just below the level
                stop_loss = snapped_stop
                rationale.append(
                    f"Stop snapped to just below support at {round(nearest_sup_price, 4)} "
                    f"({round(abs(current - nearest_sup_price) / current * 100, 2)}% away)."
                )
                # Recalculate TPs to maintain ratio with new stop distance
                new_stop_dist = current - stop_loss
                take_profit_1 = current + (new_stop_dist * 1.8)
                take_profit_2 = current + (new_stop_dist * 3.0)

        elif side == SignalSide.SHORT and resistance_levels:
            nearest_res_price = resistance_levels[0][0]
            if entry_high < nearest_res_price < stop_loss:
                snapped_stop = nearest_res_price * 1.002  # just above the level
                stop_loss = snapped_stop
                rationale.append(
                    f"Stop snapped to just above resistance at {round(nearest_res_price, 4)} "
                    f"({round(abs(nearest_res_price - current) / current * 100, 2)}% away)."
                )
                new_stop_dist = stop_loss - current
                take_profit_1 = current - (new_stop_dist * 1.8)
                take_profit_2 = current - (new_stop_dist * 3.0)

        # S/R proximity scoring — adjust confidence based on level context
        if side == SignalSide.LONG and support_levels:
            nearest_sup_price = support_levels[0][0]
            dist_pct = abs(current - nearest_sup_price) / current * 100
            touches = support_levels[0][1]
            if dist_pct <= 1.5:
                base_confidence = min(100, base_confidence + (4 if touches >= 2 else 2))
                rationale.append(
                    f"Entry is within {dist_pct:.1f}% of support at {round(nearest_sup_price, 4)} "
                    f"({touches} touch{'es' if touches > 1 else ''}) — natural long zone."
                )
            if resistance_levels:
                nearest_res_price = resistance_levels[0][0]
                res_dist_pct = abs(nearest_res_price - current) / current * 100
                if res_dist_pct <= 1.0:
                    base_confidence = max(0, base_confidence - 5)
                    rationale.append(
                        f"⚠️ Resistance at {round(nearest_res_price, 4)} is only {res_dist_pct:.1f}% above entry — tight ceiling on upside."
                    )

        elif side == SignalSide.SHORT and resistance_levels:
            nearest_res_price = resistance_levels[0][0]
            dist_pct = abs(nearest_res_price - current) / current * 100
            touches = resistance_levels[0][1]
            if dist_pct <= 1.5:
                base_confidence = min(100, base_confidence + (4 if touches >= 2 else 2))
                rationale.append(
                    f"Entry is within {dist_pct:.1f}% of resistance at {round(nearest_res_price, 4)} "
                    f"({touches} touch{'es' if touches > 1 else ''}) — natural short zone."
                )
            if support_levels:
                nearest_sup_price = support_levels[0][0]
                sup_dist_pct = abs(current - nearest_sup_price) / current * 100
                if sup_dist_pct <= 1.0:
                    base_confidence = max(0, base_confidence - 5)
                    rationale.append(
                        f"⚠️ Support at {round(nearest_sup_price, 4)} is only {sup_dist_pct:.1f}% below entry — tight floor for shorts."
                    )

        scores = {name: round(s.score, 2) for name, s in analyses.items()}
        scores["candle_interval"] = candle_interval  # type: ignore[assignment]
        signal = Signal(
            ticker=snapshot.ticker,
            asset_class=snapshot.asset_class,
            side=side,
            current_price=round(current, 4),
            entry_low=round(entry_low, 4),
            entry_high=round(entry_high, 4),
            stop_loss=round(stop_loss, 4),
            take_profit_1=round(take_profit_1, 4),
            take_profit_2=round(take_profit_2, 4),
            confidence=base_confidence,
            timeframe=timeframe,
            rationale=rationale,
            scores=scores,
            base_confidence=base_confidence,
            confluence_count=confluence_count,
            confluence_signals=confluence_signals,
            price_source=str(snapshot.meta.get("price_source", snapshot.meta.get("exchange", ""))),
            pricing_symbol=str(snapshot.meta.get("pricing_symbol", snapshot.ticker)),
            pricing_currency=snapshot.currency,
            market_session=market_session,
            signal_quality=signal_quality,
            trade_type=trade_type,
            support_levels=support_levels,
            resistance_levels=resistance_levels,
        )
        if self.learning_service is not None:
            signal = self.learning_service.apply_to_signal(signal)
        signal.edge_score = self._edge_score(signal)

        if side != SignalSide.NEUTRAL and self.settings.edge_over_speed_mode:
            if (
                signal.confluence_count < self.settings.signal_min_confluence
                or signal.edge_score < self.settings.edge_score_min_alert
            ):
                signal.side = SignalSide.NEUTRAL
                signal.signal_quality = "watchlist"
                signal.learning_adjustment = 0
                signal.edge_score = max(0, min(signal.edge_score, self.settings.edge_score_min_alert - 1))
                signal.rationale.insert(
                    0,
                    "Edge filter is standing down here because confluence and historical support are not strong enough yet.",
                )
            elif (
                signal.confidence >= self.settings.strong_play_min_confidence
                and signal.confluence_count >= self.settings.high_quality_min_confluence
                and signal.edge_score >= self.settings.edge_score_min_high_quality
            ):
                signal.signal_quality = "high"
            elif signal.confidence >= self.settings.live_alert_min_confidence:
                signal.signal_quality = "tradable"
            else:
                signal.signal_quality = "watchlist"
        elif side != SignalSide.NEUTRAL:
            if signal.confidence >= 68:
                signal.signal_quality = "high"
            elif signal.confidence >= 58:
                signal.signal_quality = "tradable"
            else:
                signal.signal_quality = "watchlist"
        return signal

    def get_news_brief(self, ticker: str, page_size: int = 5) -> list[dict[str, str]]:
        return self.news_service.get_headlines(ticker, page_size=page_size)

    def generate_gameplan(self, tickers: list[str]) -> Gameplan:
        signals: list[Signal] = []
        for ticker in tickers:
            try:
                signal = self.generate_signal(ticker)
                signals.append(signal)
            except Exception:
                continue

        ranked = sorted(
            signals,
            key=lambda item: (item.edge_score, item.confidence, item.confluence_count),
            reverse=True,
        )[:5]
        avg_confidence = round(sum(item.confidence for item in ranked) / len(ranked)) if ranked else 50
        entropy_score = max(15, min(90, 100 - avg_confidence))
        superposition_risk = min(95, 30 + (len([item for item in ranked if item.side != SignalSide.NEUTRAL]) * 10))

        scenarios = [
            UniverseScenario(
                name="Universe A",
                probability=68,
                description="Base case continuation with current leadership intact.",
                portfolio_bias="Balanced with selective trend exposure.",
                triggers=["Momentum leaders hold 20-day structure", "Volatility stays contained"],
                trade_ideas=[f"{signal.side.value} {signal.ticker}" for signal in ranked[:2]] or ["Wait for cleaner alignment"],
            ),
            UniverseScenario(
                name="Universe B",
                probability=14,
                description="Bull fracture driven by liquidity expansion and short covering.",
                portfolio_bias="Overweight momentum, reduce hedges gradually.",
                triggers=["Breadth broadens materially", "Breakouts hold above prior resistance"],
                trade_ideas=[f"Add on breakout strength in {signal.ticker}" for signal in ranked[:2]] or ["Favor beta leaders"],
            ),
            UniverseScenario(
                name="Universe C",
                probability=10,
                description="Black swan shock with rapid de-risking across cyclicals.",
                portfolio_bias="Raise cash, own defensives and explicit hedges.",
                triggers=["VIX regime shift", "Gap-down risk accelerates across indices"],
                trade_ideas=["Buy downside protection", "Rotate to defensive assets"],
            ),
            UniverseScenario(
                name="Universe D",
                probability=8,
                description="Crypto or thematic decoupling driven by idiosyncratic catalysts.",
                portfolio_bias="Barbell: core hedges plus selective thematic aggressors.",
                triggers=["Crypto breadth improves while equities stall", "On-chain flow diverges from macro tape"],
                trade_ideas=["Own strongest crypto leaders", "Avoid weak correlation assumptions"],
            ),
        ]

        staking_notes = [
            "Prefer liquid staking only when underlying trend is neutral-to-bullish and redemption liquidity is healthy.",
            "Treat yield as secondary to asset drawdown risk; avoid chasing APY during unstable market structure.",
        ]
        hedges = [
            "Trim correlated longs if portfolio concentration rises above moderate.",
            "Use options or inverse exposure when Universe C triggers activate.",
        ]
        macro_oracle = [
            f"Signal breadth across tracked assets is {len(ranked)} tradable setups.",
            f"Entropy score is {entropy_score}, which implies {'elevated' if entropy_score > 55 else 'contained'} regime uncertainty.",
            f"Top confidence cluster centers around {ranked[0].ticker if ranked else 'no dominant leader today'}.",
        ]

        return Gameplan(
            generated_for=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            macro_oracle=macro_oracle,
            top_trades=ranked,
            scenarios=scenarios,
            staking_notes=staking_notes,
            hedges=hedges,
            entropy_score=entropy_score,
            superposition_risk=superposition_risk,
        )

    def build_portfolio_risk_report(self, positions: list) -> PortfolioRiskReport:
        concentration, warnings, gross_exposure = portfolio_risk_summary(positions)
        return PortfolioRiskReport(
            total_positions=len(positions),
            gross_exposure=round(gross_exposure, 2),
            concentration_risk=concentration,
            warnings=warnings,
        )
