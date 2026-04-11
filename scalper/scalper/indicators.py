from __future__ import annotations

from statistics import mean


def ema(prices: list[float], period: int) -> float:
    if not prices:
        return 0.0
    k = 2.0 / (period + 1)
    result = prices[0]
    for p in prices[1:]:
        result = p * k + result * (1 - k)
    return result


def rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, period + 1):
        d = prices[-i] - prices[-i - 1]
        gains.append(max(d, 0.0))
        losses.append(abs(min(d, 0.0)))
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l == 0:
        return 100.0
    return 100 - (100 / (1 + avg_g / avg_l))


def atr(closes: list[float], highs: list[float], lows: list[float], period: int = 14) -> float:
    n = min(len(closes), len(highs), len(lows))
    if n < 2:
        return 0.0
    trs = []
    for i in range(max(1, n - period), n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return mean(trs) if trs else 0.0


def vwap(closes: list[float], highs: list[float], lows: list[float], volumes: list[float]) -> float:
    n = min(len(closes), len(highs), len(lows), len(volumes))
    if n == 0:
        return 0.0
    total_vol = sum(volumes[-n:])
    if not total_vol:
        return closes[-1]
    typical = [(closes[-n + i] + highs[-n + i] + lows[-n + i]) / 3.0 for i in range(n)]
    return sum(tp * v for tp, v in zip(typical, volumes[-n:])) / total_vol


def vol_ratio(volumes: list[float], period: int = 20) -> float:
    if len(volumes) < 2:
        return 1.0
    avg = mean(volumes[-period:]) if len(volumes) >= period else mean(volumes[:-1])
    return volumes[-1] / avg if avg else 1.0


def macd(prices: list[float], fast: int = 12, slow: int = 26, signal_period: int = 9) -> tuple[float, float, float]:
    """Returns (macd_line, signal_line, histogram)."""
    if len(prices) < slow:
        return 0.0, 0.0, 0.0
    fast_ema = ema(prices, fast)
    slow_ema = ema(prices, slow)
    macd_line = fast_ema - slow_ema
    # Signal line: EMA of last signal_period MACD values
    macd_series = []
    step = max(1, len(prices) // signal_period)
    for i in range(signal_period):
        idx = len(prices) - (signal_period - i) * step
        if idx < slow:
            continue
        f = ema(prices[:idx], fast)
        s = ema(prices[:idx], slow)
        macd_series.append(f - s)
    macd_series.append(macd_line)
    signal_line = ema(macd_series, signal_period) if len(macd_series) >= signal_period else macd_line
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def is_choppy(closes: list[float], highs: list[float], lows: list[float], atr_val: float, window: int = 20) -> bool:
    if atr_val <= 0 or len(closes) < window:
        return False
    recent_high = max(highs[-window:])
    recent_low = min(lows[-window:])
    return (recent_high - recent_low) < atr_val * 3.0


def score_signal(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    current_price: float,
) -> dict:
    """
    Score a potential scalp signal. Returns dict with:
    - side: 'LONG', 'SHORT', or None
    - score: 0-100
    - atr_val, vwap_val, ema9, ema21, rsi, vol_ratio, macd_hist
    - details: list of reason strings
    """
    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    rsi_val = rsi(closes)
    atr_val = atr(closes, highs, lows)
    vwap_val = vwap(closes, highs, lows, volumes)
    vr = vol_ratio(volumes)
    choppy = is_choppy(closes, highs, lows, atr_val)
    macd_line, macd_signal, macd_hist = macd(closes)

    score = 50.0
    details: list[str] = []

    if choppy:
        return {"side": None, "score": 50.0, "atr_val": atr_val, "vwap_val": vwap_val,
                "details": ["Market is in chop — no scalp edge."]}

    # --- EMA trend direction (+/-12) ---
    ema_bull = e9 > e21
    if ema_bull:
        score += 12
        details.append(f"EMA9 > EMA21 ({e9:.4f} > {e21:.4f})")
    else:
        score -= 12
        details.append(f"EMA9 < EMA21 ({e9:.4f} < {e21:.4f})")

    # --- MACD momentum confirmation (+/-8) ---
    if macd_hist > 0:
        score += 8
        details.append(f"MACD bullish (hist={macd_hist:.4f})")
    elif macd_hist < 0:
        score -= 8
        details.append(f"MACD bearish (hist={macd_hist:.4f})")

    # --- VWAP price location (+/-8) ---
    if vwap_val > 0:
        if current_price > vwap_val:
            score += 8
            details.append(f"Price above VWAP ({vwap_val:.4f})")
        else:
            score -= 8
            details.append(f"Price below VWAP ({vwap_val:.4f})")

    # --- RSI: reward confirmation, punish contradiction (+/-10) ---
    # For LONG signals (score trending up): RSI 45-70 is ideal, <35 or >75 is bad
    # For SHORT signals (score trending down): RSI 30-55 is ideal
    # Evaluate after EMA/MACD so we know which direction score is leaning
    tentative_long = score >= 50
    if tentative_long:
        if 45 <= rsi_val <= 70:
            score += 10
            details.append(f"RSI confirms LONG ({rsi_val:.1f})")
        elif rsi_val > 75:
            score -= 8
            details.append(f"RSI overbought ({rsi_val:.1f}) — late entry risk")
        elif rsi_val < 35:
            score -= 12
            details.append(f"RSI bearish ({rsi_val:.1f}) — contradicts LONG")
        else:
            details.append(f"RSI neutral ({rsi_val:.1f})")
    else:
        if 30 <= rsi_val <= 55:
            score += 10
            details.append(f"RSI confirms SHORT ({rsi_val:.1f})")
        elif rsi_val < 25:
            score -= 8
            details.append(f"RSI oversold ({rsi_val:.1f}) — late entry risk")
        elif rsi_val > 65:
            score -= 12
            details.append(f"RSI bullish ({rsi_val:.1f}) — contradicts SHORT")
        else:
            details.append(f"RSI neutral ({rsi_val:.1f})")

    # --- Volume (+/-6) ---
    if vr >= 1.3:
        score += 6
        details.append(f"Volume {vr:.1f}x avg — participation confirmed")
    elif vr >= 1.1:
        score += 3
        details.append(f"Volume {vr:.1f}x avg — moderate participation")
    elif vr < 0.7:
        score -= 6
        details.append(f"Volume {vr:.1f}x avg — weak participation")

    # --- Hard filters: block contradicting signals entirely ---
    # Don't go LONG when price is below VWAP AND MACD is bearish
    if score >= 68 and current_price < vwap_val and macd_hist < 0:
        return {"side": None, "score": round(score, 2), "atr_val": round(atr_val, 6),
                "vwap_val": round(vwap_val, 6), "details": ["LONG blocked: price below VWAP with bearish MACD"]}
    # Don't go SHORT when price is above VWAP AND MACD is bullish
    if score <= 32 and current_price > vwap_val and macd_hist > 0:
        return {"side": None, "score": round(score, 2), "atr_val": round(atr_val, 6),
                "vwap_val": round(vwap_val, 6), "details": ["SHORT blocked: price above VWAP with bullish MACD"]}

    # --- Determine side ---
    if score >= 70:
        side = "LONG"
    elif score <= 30:
        side = "SHORT"
    else:
        side = None

    return {
        "side": side,
        "score": round(score, 2),
        "atr_val": round(atr_val, 6),
        "vwap_val": round(vwap_val, 6),
        "ema9": round(e9, 6),
        "ema21": round(e21, 6),
        "rsi": round(rsi_val, 2),
        "vol_ratio": round(vr, 2),
        "macd_hist": round(macd_hist, 6),
        "details": details,
    }
