from __future__ import annotations

from datetime import datetime, timezone

from trader_signal_bot.domain import (
    Gameplan,
    PortfolioRiskReport,
    PriceSnapshot,
    Signal,
    SignalSide,
    UniverseScenario,
)
from trader_signal_bot.services.analysis import (
    fundamental_analysis,
    market_session_label,
    macro_analysis,
    portfolio_risk_summary,
    risk_analysis,
    sentiment_analysis,
    technical_analysis,
)
from trader_signal_bot.services.learning import LearningService
from trader_signal_bot.services.market_data import MarketDataProvider
from trader_signal_bot.services.news import NewsService


class SignalEngine:
    def __init__(
        self,
        provider: MarketDataProvider,
        news_service: NewsService | None = None,
        learning_service: LearningService | None = None,
    ) -> None:
        self.provider = provider
        self.news_service = news_service or NewsService()
        self.learning_service = learning_service
        self.weights = {
            "technical": 0.30,
            "fundamental": 0.25,
            "sentiment": 0.20,
            "risk": 0.15,
            "macro": 0.10,
        }

    def analyze(self, ticker: str) -> tuple[PriceSnapshot, dict[str, object]]:
        snapshot = self.provider.get_snapshot(ticker)
        analyses = {
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

        if headlines:
            rationale.append(f"News flow is active with {len(headlines)} recent headlines in the feed.")

        technical_score = analyses["technical"].score
        risk_score = analyses["risk"].score
        macro_score = analyses["macro"].score
        current = snapshot.current_price
        stop_distance = max(abs(snapshot.high - snapshot.low), current * 0.015)
        market_session = market_session_label(snapshot)

        if snapshot.asset_class.value in {"stock", "etf"}:
            stop_distance = max(stop_distance, current * 0.012)
            long_threshold = 62
            short_threshold = 38
            min_technical = 60
            min_risk = 58
        elif snapshot.asset_class.value == "forex":
            stop_distance = max(abs(snapshot.high - snapshot.low), current * 0.0045)
            long_threshold = 60
            short_threshold = 40
            min_technical = 58
            min_risk = 55
        elif snapshot.asset_class.value == "futures":
            stop_distance = max(abs(snapshot.high - snapshot.low), current * 0.01)
            long_threshold = 61
            short_threshold = 39
            min_technical = 59
            min_risk = 55
        elif snapshot.asset_class.value == "crypto":
            long_threshold = 63
            short_threshold = 37
            min_technical = 60
            min_risk = 52
        else:
            long_threshold = 60
            short_threshold = 40
            min_technical = 58
            min_risk = 55

        if (
            weighted_score >= long_threshold
            and technical_score >= min_technical
            and risk_score >= min_risk
            and macro_score >= 48
        ):
            side = SignalSide.LONG
            entry_low = current * 0.997
            entry_high = current * 1.003
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

        confidence = round((weighted_score * 0.7) + (risk_score * 0.3))
        base_confidence = max(0, min(confidence, 100))
        if confidence >= 68 and side != SignalSide.NEUTRAL:
            signal_quality = "high"
        elif confidence >= 58 and side != SignalSide.NEUTRAL:
            signal_quality = "tradable"
        else:
            signal_quality = "watchlist"

        if snapshot.asset_class.value in {"stock", "etf"}:
            timeframe = "2-7 days"
        elif snapshot.asset_class.value == "forex":
            timeframe = "1-4 days"
        elif snapshot.asset_class.value == "futures":
            timeframe = "1-5 days"
        else:
            timeframe = "2-5 days"

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
            scores={name: round(score.score, 2) for name, score in analyses.items()},
            base_confidence=base_confidence,
            price_source=str(snapshot.meta.get("price_source", snapshot.meta.get("exchange", ""))),
            pricing_symbol=str(snapshot.meta.get("pricing_symbol", snapshot.ticker)),
            pricing_currency=snapshot.currency,
            market_session=market_session,
            signal_quality=signal_quality,
        )
        if self.learning_service is not None:
            signal = self.learning_service.apply_to_signal(signal)
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

        ranked = sorted(signals, key=lambda item: item.confidence, reverse=True)[:5]
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
