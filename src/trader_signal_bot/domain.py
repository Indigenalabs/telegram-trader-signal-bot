from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AssetClass(str, Enum):
    STOCK = "stock"
    FOREX = "forex"
    CRYPTO = "crypto"
    OPTIONS = "options"
    FUTURES = "futures"
    STAKING = "staking"
    ETF = "etf"
    UNKNOWN = "unknown"


class SignalSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class TradeStage(str, Enum):
    ARMING = "ARMING"
    SIGNAL = "SIGNAL"
    CLOSED_SUCCESS = "CLOSED_SUCCESS"
    CLOSED_FAILURE = "CLOSED_FAILURE"


@dataclass(slots=True)
class PriceSnapshot:
    ticker: str
    asset_class: AssetClass
    currency: str
    current_price: float
    previous_close: float
    high: float
    low: float
    volume: float
    history: list[float]
    history_high: list[float] = field(default_factory=list)
    history_low: list[float] = field(default_factory=list)
    history_volume: list[float] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AnalysisScore:
    name: str
    score: float
    rationale: list[str]
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Signal:
    ticker: str
    asset_class: AssetClass
    side: SignalSide
    current_price: float
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    confidence: int
    timeframe: str
    rationale: list[str]
    scores: dict[str, float]
    base_confidence: int = 0
    learning_adjustment: int = 0
    learning_notes: list[str] = field(default_factory=list)
    confluence_count: int = 0
    confluence_signals: list[str] = field(default_factory=list)
    edge_score: int = 0
    learned_expectancy: float = 0.0
    learned_win_rate: float = 0.0
    learned_sample_size: int = 0
    price_source: str = ""
    pricing_symbol: str = ""
    pricing_currency: str = ""
    market_session: str = ""
    signal_quality: str = "standard"
    trade_type: str = "DAY TRADE"
    support_levels: list[tuple[float, int]] = field(default_factory=list)
    resistance_levels: list[tuple[float, int]] = field(default_factory=list)
    disclaimer: str = "Not financial advice. Trading involves risk of loss."


@dataclass(slots=True)
class UniverseScenario:
    name: str
    probability: int
    description: str
    portfolio_bias: str
    triggers: list[str]
    trade_ideas: list[str]


@dataclass(slots=True)
class Gameplan:
    generated_for: str
    macro_oracle: list[str]
    top_trades: list[Signal]
    scenarios: list[UniverseScenario]
    staking_notes: list[str]
    hedges: list[str]
    entropy_score: int
    superposition_risk: int


@dataclass(slots=True)
class PortfolioPosition:
    ticker: str
    entry_price: float
    size: float


@dataclass(slots=True)
class PortfolioRiskReport:
    total_positions: int
    gross_exposure: float
    concentration_risk: str
    warnings: list[str]


@dataclass(slots=True)
class UserProfile:
    chat_id: int
    watchlist: list[str] = field(default_factory=list)
    portfolio: list[PortfolioPosition] = field(default_factory=list)
    risk_per_trade: float = 0.01
    alert_mode: str = "high"


@dataclass(slots=True)
class TrackedTrade:
    trade_id: str
    chat_id: int
    ticker: str
    asset_class: AssetClass
    side: SignalSide
    stage: TradeStage
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    confidence: int
    market_session: str
    signal_quality: str
    opened_at: str
    scores: dict[str, float] = field(default_factory=dict)
    expires_at: str = ""
