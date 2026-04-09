from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from zoneinfo import ZoneInfo

from trader_signal_bot.domain import AnalysisScore, AssetClass, PortfolioPosition, PriceSnapshot

METALS_TICKERS = {"GC=F", "SI=F", "GLD", "SLV"}
ENERGY_TICKERS = {"CL=F", "BZ=F", "NG=F", "XOM", "CVX"}


def _pct_change(current: float, base: float) -> float:
    if not base:
        return 0.0
    return ((current - base) / base) * 100


def _simple_rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) <= period:
        return 50.0

    gains: list[float] = []
    losses: list[float] = []
    for idx in range(1, period + 1):
        delta = prices[-idx] - prices[-idx - 1]
        gains.append(max(delta, 0.0))
        losses.append(abs(min(delta, 0.0)))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _asset_bucket(snapshot: PriceSnapshot) -> str:
    ticker = snapshot.ticker.upper()
    if ticker in METALS_TICKERS:
        return "metals"
    if ticker in ENERGY_TICKERS:
        return "energy"
    if snapshot.asset_class == AssetClass.CRYPTO:
        return "crypto"
    if snapshot.asset_class in {AssetClass.STOCK, AssetClass.ETF}:
        return "stocks"
    if snapshot.asset_class == AssetClass.FOREX:
        return "forex"
    if snapshot.asset_class == AssetClass.FUTURES:
        return "futures"
    return snapshot.asset_class.value


def market_session_label(snapshot: PriceSnapshot, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    utc_hour = current.hour
    weekday = current.weekday()

    if snapshot.asset_class == AssetClass.CRYPTO:
        return "24/7 crypto market"

    if snapshot.asset_class in {AssetClass.STOCK, AssetClass.ETF}:
        eastern = current.astimezone(ZoneInfo("America/New_York"))
        if eastern.weekday() >= 5:
            return "U.S. market closed"
        minutes = (eastern.hour * 60) + eastern.minute
        if 570 <= minutes < 960:
            return "U.S. regular session"
        if 240 <= minutes < 570:
            return "U.S. premarket"
        if 960 <= minutes < 1200:
            return "U.S. after-hours"
        return "U.S. market closed"

    if snapshot.asset_class == AssetClass.FOREX:
        if weekday >= 5:
            return "FX weekend pause"
        if 7 <= utc_hour < 12:
            return "London session"
        if 12 <= utc_hour < 16:
            return "London/New York overlap"
        if 16 <= utc_hour < 21:
            return "New York session"
        return "Asia session"

    if snapshot.asset_class == AssetClass.FUTURES:
        if weekday >= 5:
            return "Globex weekend pause"
        bucket = _asset_bucket(snapshot)
        if bucket == "metals":
            return "Global metals futures session"
        if bucket == "energy":
            return "Global energy futures session"
        return "Global futures session"

    return "Active market"


def session_bias(snapshot: PriceSnapshot) -> tuple[float, str]:
    label = market_session_label(snapshot)
    if label in {"U.S. regular session", "London/New York overlap", "New York session"}:
        return 6.0, f"Liquidity regime is supportive during the {label.lower()}."
    if label in {"U.S. premarket", "Asia session"}:
        return -2.0, f"Liquidity is thinner during the {label.lower()}, so false breaks are more common."
    if "closed" in label.lower() or "weekend" in label.lower():
        return -10.0, f"The {label.lower()} reduces execution quality and follow-through."
    return 2.0, f"{label} supports continuous price discovery."


def technical_analysis(snapshot: PriceSnapshot) -> AnalysisScore:
    prices = snapshot.history
    sma_10 = mean(prices[-10:]) if len(prices) >= 10 else mean(prices)
    sma_20 = mean(prices[-20:]) if len(prices) >= 20 else mean(prices)
    sma_50 = mean(prices[-50:]) if len(prices) >= 50 else mean(prices)
    momentum_5 = _pct_change(prices[-1], prices[-6]) if len(prices) >= 6 else 0.0
    rsi = _simple_rsi(prices)
    bucket = _asset_bucket(snapshot)
    recent_window = prices[-20:] if len(prices) >= 20 else prices
    recent_high = max(recent_window)
    recent_low = min(recent_window)
    range_position = (
        (prices[-1] - recent_low) / (recent_high - recent_low)
        if recent_high != recent_low
        else 0.5
    )
    momentum_thresholds = {
        "crypto": 3.0,
        "stocks": 1.8,
        "forex": 0.45,
        "metals": 1.0,
        "energy": 1.6,
        "futures": 1.2,
    }
    threshold = momentum_thresholds.get(bucket, 1.5)

    score = 50.0
    rationale: list[str] = []

    if prices[-1] > sma_10 > sma_20 > sma_50:
        score += 24
        rationale.append("Trend stack is cleanly bullish across short, medium, and swing horizons.")
    elif prices[-1] < sma_10 < sma_20 < sma_50:
        score -= 24
        rationale.append("Trend stack is cleanly bearish across short, medium, and swing horizons.")
    elif prices[-1] > sma_20 and sma_10 > sma_20:
        score += 10
        rationale.append("Price is holding above the dominant short-term trend structure.")
    elif prices[-1] < sma_20 and sma_10 < sma_20:
        score -= 10
        rationale.append("Price is trading below the dominant short-term trend structure.")

    if momentum_5 > threshold:
        score += 10
        rationale.append(f"Five-session momentum is strong at {momentum_5:.2f}%.")
    elif momentum_5 < -threshold:
        score -= 10
        rationale.append(f"Five-session momentum is weak at {momentum_5:.2f}%.")

    if 55 <= rsi <= 68:
        score += 8
        rationale.append(f"RSI confirms constructive upside pressure at {rsi:.1f}.")
    elif 32 <= rsi <= 45:
        score -= 8
        rationale.append(f"RSI confirms downside pressure at {rsi:.1f}.")
    elif rsi > 72:
        score -= 3
        rationale.append(f"RSI is extended at {rsi:.1f}, so upside follow-through may be less efficient.")
    else:
        rationale.append(f"RSI is balanced at {rsi:.1f}.")

    if range_position > 0.8:
        score += 6
        rationale.append("Price is trading near the top of its recent range, which supports breakout continuation.")
    elif range_position < 0.2:
        score -= 6
        rationale.append("Price is trapped near the bottom of its recent range, which keeps downside pressure alive.")

    return AnalysisScore(
        name="technical",
        score=max(0.0, min(score, 100.0)),
        rationale=rationale,
        facts={
            "sma_10": round(sma_10, 4),
            "sma_20": round(sma_20, 4),
            "sma_50": round(sma_50, 4),
            "rsi": round(rsi, 2),
            "range_position": round(range_position, 2),
        },
    )


def fundamental_analysis(snapshot: PriceSnapshot) -> AnalysisScore:
    score = 50.0
    rationale: list[str] = []
    day_change = float(snapshot.meta.get("day_change_pct", 0.0))
    market_cap = snapshot.meta.get("market_cap")
    bucket = _asset_bucket(snapshot)

    if snapshot.asset_class in {AssetClass.STOCK, AssetClass.ETF}:
        if market_cap and market_cap > 10_000_000_000:
            score += 8
            rationale.append("Large-cap liquidity profile reduces execution fragility.")
        if 0.25 <= abs(day_change) <= 2.5:
            score += 4
            rationale.append("Price action is active without looking disorderly.")
        elif day_change < -2.5:
            score -= 12
            rationale.append(f"Price is under significant selling pressure with a {day_change:.1f}% daily move.")
    elif snapshot.asset_class == AssetClass.CRYPTO:
        if day_change > 2:
            score += 6
            rationale.append("Crypto tape shows constructive daily expansion.")
        elif day_change < -3:
            score -= 6
            rationale.append("Crypto tape shows significant selling pressure today.")
        rationale.append("On-chain factors are placeholder heuristics until premium feeds are added.")
    elif snapshot.asset_class == AssetClass.FOREX:
        if 0.2 <= abs(day_change) <= 1.0:
            score += 6
            rationale.append("FX move is meaningful without looking like a one-bar exhaustion event.")
        elif abs(day_change) > 1.2:
            score -= 5
            rationale.append("FX move is stretched enough to demand extra caution around reversal risk.")
        rationale.append("Macro and rates inputs are still inferred from market structure only.")
    elif snapshot.asset_class == AssetClass.FUTURES:
        if bucket == "metals":
            score += 5
            rationale.append("Metals usually trend best when macro conviction and risk aversion align.")
        elif bucket == "energy":
            score += 4
            rationale.append("Energy futures carry stronger event risk, so only directional persistence is rewarded.")
        rationale.append("Term structure and roll yield adapters are not wired yet.")
    elif snapshot.asset_class == AssetClass.STAKING:
        score += 5
        rationale.append("Staking assets are evaluated with yield-sensitive heuristics.")

    return AnalysisScore(
        name="fundamental",
        score=max(0.0, min(score, 100.0)),
        rationale=rationale or ["No major fundamental edge detected."],
        facts={"market_cap": market_cap, "day_change_pct": round(day_change, 2)},
    )


def sentiment_analysis(snapshot: PriceSnapshot) -> AnalysisScore:
    prices = snapshot.history
    volatility = abs(_pct_change(max(prices[-10:]), min(prices[-10:]))) if len(prices) >= 10 else 0.0
    score = 50.0
    rationale: list[str] = []
    bucket = _asset_bucket(snapshot)

    day_change_pct = float(snapshot.meta.get("day_change_pct", 0.0))
    if snapshot.asset_class == AssetClass.CRYPTO and volatility > 12:
        if day_change_pct >= 0:
            score += 10
            rationale.append("Crypto participation proxy is elevated from recent range expansion.")
        else:
            score -= 10
            rationale.append("High volatility with downside direction signals panic selling pressure in crypto.")
    elif bucket == "stocks" and volatility > 6:
        score -= 8
        rationale.append("Elevated equity volatility signals fear and increased selling pressure.")
    elif bucket == "forex" and 0.6 <= volatility <= 2.5:
        score += 4
        rationale.append("FX participation proxy is healthy enough for cleaner directional moves.")
    elif bucket in {"metals", "energy"} and volatility >= 4:
        score += 4
        rationale.append("Commodity volatility is active enough to create follow-through if direction is clean.")
    elif volatility < 4:
        score -= 5
        rationale.append("Compressed recent range suggests muted attention and weaker follow-through.")
    else:
        rationale.append("Sentiment proxy is neutral because external NLP feeds are not configured yet.")

    return AnalysisScore(
        name="sentiment",
        score=max(0.0, min(score, 100.0)),
        rationale=rationale,
        facts={"volatility_proxy": round(volatility, 2)},
    )


def macro_analysis(snapshot: PriceSnapshot) -> AnalysisScore:
    day_change = float(snapshot.meta.get("day_change_pct", 0.0))
    score = 50.0
    rationale = ["Macro regime is approximated from market structure and current session context."]
    session_score, session_note = session_bias(snapshot)
    bucket = _asset_bucket(snapshot)
    score += session_score
    rationale.append(session_note)

    if snapshot.asset_class in {AssetClass.FOREX, AssetClass.FUTURES} and abs(day_change) > 1:
        score += 8
        rationale.append("Cross-asset sensitivity is elevated, which increases tactical opportunity.")
    elif snapshot.asset_class == AssetClass.CRYPTO and day_change < -3:
        score -= 6
        rationale.append("Risk appetite looks fragile in the current crypto session.")
    elif bucket == "metals" and abs(day_change) >= 1:
        score += 5
        rationale.append("Metals are moving enough to reflect a live macro impulse.")

    return AnalysisScore(
        name="macro",
        score=max(0.0, min(score, 100.0)),
        rationale=rationale,
        facts={"day_change_pct": round(day_change, 2), "market_session": market_session_label(snapshot)},
    )


def risk_analysis(snapshot: PriceSnapshot) -> AnalysisScore:
    prices = snapshot.history
    realized_range = _pct_change(max(prices[-14:]), min(prices[-14:])) if len(prices) >= 14 else 0.0
    score = 75.0
    rationale: list[str] = []
    bucket = _asset_bucket(snapshot)
    thresholds = {
        "crypto": (18, 10),
        "stocks": (12, 7),
        "forex": (3.0, 1.6),
        "metals": (8, 4),
        "energy": (12, 7),
        "futures": (10, 5),
    }
    high_threshold, mid_threshold = thresholds.get(bucket, (10, 5))

    if realized_range > high_threshold:
        score -= 20
        rationale.append("High realized range reduces sizing confidence.")
    elif realized_range > mid_threshold:
        score -= 10
        rationale.append("Moderate realized range calls for wider stops and smaller size.")
    else:
        rationale.append("Realized range remains manageable for structured risk.")

    return AnalysisScore(
        name="risk",
        score=max(0.0, min(score, 100.0)),
        rationale=rationale,
        facts={"realized_range_pct": round(realized_range, 2)},
    )


def portfolio_risk_summary(positions: list[PortfolioPosition]) -> tuple[str, list[str], float]:
    if not positions:
        return "low", ["No open positions tracked."], 0.0

    gross_exposure = sum(abs(position.entry_price * position.size) for position in positions)
    largest = max(abs(position.entry_price * position.size) for position in positions)
    warnings: list[str] = []

    concentration = largest / gross_exposure if gross_exposure else 0.0
    label = "low"
    if concentration > 0.55:
        label = "high"
        warnings.append("One position accounts for more than 55% of tracked exposure.")
    elif concentration > 0.35:
        label = "moderate"
        warnings.append("Portfolio concentration is noticeable; avoid stacking correlated trades.")
    else:
        warnings.append("Exposure is reasonably distributed across tracked positions.")

    return label, warnings, gross_exposure
