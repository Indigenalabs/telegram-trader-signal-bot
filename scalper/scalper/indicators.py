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
    - atr_val: float
    - vwap_val: float
    - details: list of reason strings
    """
    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    rsi_val = rsi(closes)
    atr_val = atr(closes, highs, lows)
    vwap_val = vwap(closes, highs, lows, volumes)
    vr = vol_ratio(volumes)
    choppy = is_choppy(closes, highs, lows, atr_val)

    score = 50.0
    details: list[str] = []

    if choppy:
        return {"side": None, "score": 50.0, "atr_val": atr_val, "vwap_val": vwap_val,
                "details": ["Market is in chop — no scalp edge."]}

    # EMA crossover
    if e9 > e21:
        score += 12
        details.append(f"EMA9 > EMA21 ({e9:.4f} > {e21:.4f})")
    else:
        score -= 12
        details.append(f"EMA9 < EMA21 ({e9:.4f} < {e21:.4f})")

    # VWAP
    if vwap_val > 0:
        if current_price > vwap_val:
            score += 10
            details.append(f"Price above VWAP ({vwap_val:.4f})")
        else:
            score -= 10
            details.append(f"Price below VWAP ({vwap_val:.4f})")

    # RSI
    if 45 <= rsi_val <= 68:
        score += 8
        details.append(f"RSI bullish zone ({rsi_val:.1f})")
    elif 32 <= rsi_val <= 55:
        score -= 8
        details.append(f"RSI bearish zone ({rsi_val:.1f})")
    else:
        details.append(f"RSI neutral ({rsi_val:.1f})")

    # Volume
    if vr >= 1.2:
        score += 8
        details.append(f"Volume {vr:.1f}x avg — participation confirmed")
    elif vr < 0.7:
        score -= 5
        details.append(f"Volume {vr:.1f}x avg — weak participation")

    # Determine side
    if score >= 68:
        side = "LONG"
    elif score <= 32:
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
        "details": details,
    }
