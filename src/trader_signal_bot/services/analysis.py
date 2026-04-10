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


def _ema(prices: list[float], period: int) -> float:
    """Exponential moving average of the last N prices."""
    if not prices:
        return 0.0
    k = 2.0 / (period + 1)
    result = prices[0]
    for price in prices[1:]:
        result = price * k + result * (1 - k)
    return result


def _atr(closes: list[float], highs: list[float], lows: list[float], period: int = 14) -> float:
    """Average True Range over the last `period` candles."""
    n = min(len(closes), len(highs), len(lows))
    if n < 2:
        return 0.0
    trs: list[float] = []
    for i in range(max(1, n - period), n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return mean(trs) if trs else 0.0


def _macd(prices: list[float], fast: int = 12, slow: int = 26, signal_period: int = 9) -> tuple[float, float, float]:
    """
    Return (macd_line, signal_line, histogram).
    Returns (0, 0, 0) when there is not enough history.
    """
    if len(prices) < slow + signal_period:
        return 0.0, 0.0, 0.0
    ema_fast = _ema(prices, fast)
    ema_slow = _ema(prices, slow)
    macd_line = ema_fast - ema_slow
    # Approximate signal line as EMA of the last signal_period MACD values
    # Build a short series of MACD values using a sliding window
    macd_series: list[float] = []
    for i in range(signal_period + 1):
        idx = len(prices) - (signal_period - i)
        if idx < slow:
            macd_series.append(0.0)
            continue
        ef = _ema(prices[:idx], fast)
        es = _ema(prices[:idx], slow)
        macd_series.append(ef - es)
    signal_line = _ema(macd_series, signal_period)
    histogram = macd_line - signal_line
    return round(macd_line, 6), round(signal_line, 6), round(histogram, 6)


def _vwap(closes: list[float], highs: list[float], lows: list[float], volumes: list[float]) -> float:
    """Volume-weighted average price over available history."""
    n = min(len(closes), len(highs), len(lows), len(volumes))
    if n == 0:
        return 0.0
    total_vol = sum(volumes[-n:])
    if not total_vol:
        return closes[-1] if closes else 0.0
    typical_prices = [
        (closes[-n + i] + highs[-n + i] + lows[-n + i]) / 3.0
        for i in range(n)
    ]
    return sum(tp * v for tp, v in zip(typical_prices, volumes[-n:])) / total_vol


def find_support_resistance(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    current_price: float,
    swing_lookback: int = 5,
    max_levels: int = 3,
    cluster_pct: float = 0.8,
) -> dict[str, list[float]]:
    """
    Detect key support and resistance levels from swing highs/lows.

    Algorithm:
    1. Find swing highs (local max where high[i] > all highs within ±swing_lookback bars)
       and swing lows (local min where low[i] < all lows within ±swing_lookback bars).
    2. Cluster nearby levels that are within cluster_pct% of each other — multiple
       touches at the same zone make it a stronger level.
    3. Return the closest levels above (resistance) and below (support) current price,
       sorted by proximity, with strength (touch count) attached.

    Returns dict with keys:
      "support"    — list of (price, touches) tuples below current price, nearest first
      "resistance" — list of (price, touches) tuples above current price, nearest first
    """
    n = min(len(closes), len(highs), len(lows))
    if n < swing_lookback * 2 + 1:
        return {"support": [], "resistance": []}

    raw_highs: list[float] = []
    raw_lows: list[float] = []

    for i in range(swing_lookback, n - swing_lookback):
        left_h = highs[i - swing_lookback: i]
        right_h = highs[i + 1: i + swing_lookback + 1]
        if highs[i] >= max(left_h) and highs[i] >= max(right_h):
            raw_highs.append(highs[i])

        left_l = lows[i - swing_lookback: i]
        right_l = lows[i + 1: i + swing_lookback + 1]
        if lows[i] <= min(left_l) and lows[i] <= min(right_l):
            raw_lows.append(lows[i])

    def _cluster(prices_list: list[float]) -> list[tuple[float, int]]:
        """Merge levels within cluster_pct% into a single level, count touches."""
        if not prices_list:
            return []
        sorted_levels = sorted(prices_list)
        clusters: list[list[float]] = [[sorted_levels[0]]]
        for price in sorted_levels[1:]:
            centroid = sum(clusters[-1]) / len(clusters[-1])
            if abs(price - centroid) / centroid * 100 <= cluster_pct:
                clusters[-1].append(price)
            else:
                clusters.append([price])
        return [(round(sum(c) / len(c), 6), len(c)) for c in clusters]

    all_levels = _cluster(raw_highs + raw_lows)

    support = sorted(
        [(p, t) for p, t in all_levels if p < current_price],
        key=lambda x: current_price - x[0],
    )[:max_levels]

    resistance = sorted(
        [(p, t) for p, t in all_levels if p > current_price],
        key=lambda x: x[0] - current_price,
    )[:max_levels]

    return {
        "support": [(round(p, 6), t) for p, t in support],
        "resistance": [(round(p, 6), t) for p, t in resistance],
    }


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
    highs = snapshot.history_high
    lows = snapshot.history_low
    volumes = snapshot.history_volume

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

    # EMA 9 / 21 crossover
    ema_9 = _ema(prices, 9)
    ema_21 = _ema(prices, 21)

    # MACD (12, 26, 9)
    macd_line, macd_signal, macd_hist = _macd(prices)

    # VWAP (uses full available history)
    vwap = 0.0
    has_vwap = bool(highs and lows and volumes)
    if has_vwap:
        vwap = _vwap(prices, highs, lows, volumes)

    # ATR for chop detection
    atr = 0.0
    has_ohlcv = bool(highs and lows)
    if has_ohlcv:
        atr = _atr(prices, highs, lows, period=14)

    # Volume ratio vs 20-period average
    vol_ratio = 1.0
    if volumes and len(volumes) >= 2:
        avg_vol = mean(volumes[-20:]) if len(volumes) >= 20 else mean(volumes[:-1])
        vol_ratio = volumes[-1] / avg_vol if avg_vol else 1.0

    # Momentum thresholds scale with candle interval — hourly moves are ~12x smaller than daily
    candle_interval = str(snapshot.meta.get("candle_interval", "1d"))
    _intraday_scale = {
        "1m": 0.04, "5m": 0.08, "15m": 0.12, "30m": 0.18,
        "1h": 0.25, "2h": 0.35, "4h": 0.55, "1d": 1.0, "1w": 2.5,
    }
    scale = _intraday_scale.get(candle_interval, 1.0)
    momentum_thresholds = {
        "crypto": 3.0 * scale,
        "stocks": 1.8 * scale,
        "forex": 0.45 * scale,
        "metals": 1.0 * scale,
        "energy": 1.6 * scale,
        "futures": 1.2 * scale,
    }
    threshold = momentum_thresholds.get(bucket, 1.5 * scale)

    score = 50.0
    rationale: list[str] = []

    # --- SMA trend stack ---
    if prices[-1] > sma_10 > sma_20 > sma_50:
        score += 20
        rationale.append("Trend stack is cleanly bullish across short, medium, and swing horizons.")
    elif prices[-1] < sma_10 < sma_20 < sma_50:
        score -= 20
        rationale.append("Trend stack is cleanly bearish across short, medium, and swing horizons.")
    elif prices[-1] > sma_20 and sma_10 > sma_20:
        score += 8
        rationale.append("Price is holding above the dominant short-term trend structure.")
    elif prices[-1] < sma_20 and sma_10 < sma_20:
        score -= 8
        rationale.append("Price is trading below the dominant short-term trend structure.")

    # --- EMA 9 / 21 crossover (+/- 8) ---
    if ema_9 > ema_21:
        score += 8
        rationale.append(f"EMA 9 is above EMA 21 ({ema_9:.4f} vs {ema_21:.4f}), confirming bullish short-term momentum.")
    else:
        score -= 8
        rationale.append(f"EMA 9 is below EMA 21 ({ema_9:.4f} vs {ema_21:.4f}), confirming bearish short-term momentum.")

    # --- MACD (+/- 6): histogram direction + crossover ---
    if macd_line != 0.0 or macd_signal != 0.0:
        if macd_line > macd_signal and macd_hist > 0:
            score += 6
            rationale.append(f"MACD is bullish ({macd_line:.4f} > signal {macd_signal:.4f}, hist {macd_hist:+.4f}).")
        elif macd_line < macd_signal and macd_hist < 0:
            score -= 6
            rationale.append(f"MACD is bearish ({macd_line:.4f} < signal {macd_signal:.4f}, hist {macd_hist:+.4f}).")
        elif macd_hist > 0:
            score += 3
            rationale.append(f"MACD histogram is positive ({macd_hist:+.4f}), suggesting building momentum.")
        elif macd_hist < 0:
            score -= 3
            rationale.append(f"MACD histogram is negative ({macd_hist:+.4f}), suggesting fading momentum.")

    # --- VWAP position (+/- 6) ---
    if has_vwap and vwap > 0:
        if prices[-1] > vwap:
            score += 6
            rationale.append(f"Price is trading above VWAP ({vwap:.4f}), keeping buyers in control.")
        else:
            score -= 6
            rationale.append(f"Price is trading below VWAP ({vwap:.4f}), keeping sellers in control.")

    # --- Volume confirmation (+/- 5) ---
    if vol_ratio >= 1.5:
        score += 5
        rationale.append(f"Volume is {vol_ratio:.1f}x the recent average, confirming participation behind the move.")
    elif vol_ratio < 0.6:
        score -= 5
        rationale.append(f"Volume is only {vol_ratio:.1f}x the recent average, signalling weak conviction.")

    # --- Momentum ---
    if momentum_5 > threshold:
        score += 8
        rationale.append(f"Five-session momentum is strong at {momentum_5:.2f}%.")
    elif momentum_5 < -threshold:
        score -= 8
        rationale.append(f"Five-session momentum is weak at {momentum_5:.2f}%.")

    # --- RSI ---
    if 55 <= rsi <= 68:
        score += 6
        rationale.append(f"RSI confirms constructive upside pressure at {rsi:.1f}.")
    elif 32 <= rsi <= 45:
        score -= 6
        rationale.append(f"RSI confirms downside pressure at {rsi:.1f}.")
    elif rsi > 72:
        score -= 3
        rationale.append(f"RSI is extended at {rsi:.1f}, so upside follow-through may be less efficient.")
    else:
        rationale.append(f"RSI is balanced at {rsi:.1f}.")

    # --- Range position ---
    if range_position > 0.8:
        score += 5
        rationale.append("Price is trading near the top of its recent range, which supports breakout continuation.")
    elif range_position < 0.2:
        score -= 5
        rationale.append("Price is trapped near the bottom of its recent range, which keeps downside pressure alive.")

    # --- Chop / regime filter ---
    # If 20-period price range is less than 3x ATR the market is in a tight chop — pull score toward 50
    is_choppy = False
    if has_ohlcv and atr > 0:
        price_range_20 = recent_high - recent_low
        if price_range_20 < atr * 3.0:
            is_choppy = True
            # Dampen any directional bias — blend 60% back toward neutral
            score = score * 0.40 + 50.0 * 0.60
            rationale.append(
                f"Market is in a tight range (range {price_range_20:.4f} < 3x ATR {atr:.4f}), so directional edge is reduced."
            )

    facts: dict = {
        "sma_10": round(sma_10, 4),
        "sma_20": round(sma_20, 4),
        "sma_50": round(sma_50, 4),
        "ema_9": round(ema_9, 4),
        "ema_21": round(ema_21, 4),
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "rsi": round(rsi, 2),
        "range_position": round(range_position, 2),
        "vol_ratio": round(vol_ratio, 2),
        "is_choppy": is_choppy,
    }
    if has_ohlcv:
        facts["atr"] = round(atr, 4)
    if has_vwap:
        facts["vwap"] = round(vwap, 4)

    return AnalysisScore(
        name="technical",
        score=max(0.0, min(score, 100.0)),
        rationale=rationale,
        facts=facts,
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

    candle_interval_sent = str(snapshot.meta.get("candle_interval", "1d"))
    _sent_scale = {
        "1m": 0.04, "5m": 0.08, "15m": 0.12, "30m": 0.18,
        "1h": 0.25, "2h": 0.35, "4h": 0.55, "1d": 1.0, "1w": 2.5,
    }
    sent_scale = _sent_scale.get(candle_interval_sent, 1.0)
    day_change_pct = float(snapshot.meta.get("day_change_pct", 0.0))
    if snapshot.asset_class == AssetClass.CRYPTO and volatility > 12 * sent_scale:
        if day_change_pct >= 0:
            score += 10
            rationale.append("Crypto participation proxy is elevated from recent range expansion.")
        else:
            score -= 10
            rationale.append("High volatility with downside direction signals panic selling pressure in crypto.")
    elif bucket == "stocks" and volatility > 6 * sent_scale:
        score -= 8
        rationale.append("Elevated equity volatility signals fear and increased selling pressure.")
    elif bucket == "forex" and 0.6 * sent_scale <= volatility <= 2.5 * sent_scale:
        score += 4
        rationale.append("FX participation proxy is healthy enough for cleaner directional moves.")
    elif bucket in {"metals", "energy"} and volatility >= 4 * sent_scale:
        score += 4
        rationale.append("Commodity volatility is active enough to create follow-through if direction is clean.")
    elif volatility < 4 * sent_scale:
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
    candle_interval_risk = str(snapshot.meta.get("candle_interval", "1d"))
    _risk_scale = {
        "1m": 0.04, "5m": 0.08, "15m": 0.12, "30m": 0.18,
        "1h": 0.25, "2h": 0.35, "4h": 0.55, "1d": 1.0, "1w": 2.5,
    }
    risk_scale = _risk_scale.get(candle_interval_risk, 1.0)
    thresholds = {
        "crypto": (18 * risk_scale, 10 * risk_scale),
        "stocks": (12 * risk_scale, 7 * risk_scale),
        "forex": (3.0 * risk_scale, 1.6 * risk_scale),
        "metals": (8 * risk_scale, 4 * risk_scale),
        "energy": (12 * risk_scale, 7 * risk_scale),
        "futures": (10 * risk_scale, 5 * risk_scale),
    }
    high_threshold, mid_threshold = thresholds.get(bucket, (10 * risk_scale, 5 * risk_scale))

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
