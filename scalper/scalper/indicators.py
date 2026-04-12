from __future__ import annotations

from statistics import mean


# ── Basic helpers ─────────────────────────────────────────────────────────────

def ema(prices: list[float], period: int) -> float:
    if not prices:
        return 0.0
    k = 2.0 / (period + 1)
    result = prices[0]
    for p in prices[1:]:
        result = p * k + result * (1 - k)
    return result


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
    if len(prices) < slow:
        return 0.0, 0.0, 0.0
    fast_ema = ema(prices, fast)
    slow_ema = ema(prices, slow)
    macd_line = fast_ema - slow_ema
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


# ── Ajna SMC Detection ────────────────────────────────────────────────────────

def _detect_swing_points(
    highs: list[float], lows: list[float], lookback: int = 5
) -> dict[str, list[dict]]:
    swing_highs: list[dict] = []
    swing_lows: list[dict] = []
    n = len(highs)
    for i in range(lookback, n - lookback):
        if all(highs[j] < highs[i] for j in range(i - lookback, i + lookback + 1) if j != i):
            swing_highs.append({"idx": i, "price": highs[i]})
        if all(lows[j] > lows[i] for j in range(i - lookback, i + lookback + 1) if j != i):
            swing_lows.append({"idx": i, "price": lows[i]})
    return {"highs": swing_highs, "lows": swing_lows}


def _determine_trend(swings: dict[str, list[dict]]) -> str:
    highs = swings["highs"]
    lows = swings["lows"]
    if len(highs) < 2 or len(lows) < 2:
        return "ranging"
    rh = highs[-3:]
    rl = lows[-3:]
    if rh[-1]["price"] > rh[0]["price"] and rl[-1]["price"] > rl[0]["price"]:
        return "bullish"
    if rh[-1]["price"] < rh[0]["price"] and rl[-1]["price"] < rl[0]["price"]:
        return "bearish"
    return "ranging"


def _detect_reversal_candles(
    opens: list[float], highs: list[float], lows: list[float], closes: list[float]
) -> list[dict]:
    result: list[dict] = []
    n = len(closes)
    for i in range(1, n):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        po, pc = opens[i - 1], closes[i - 1]
        body = abs(c - o)
        rng = h - l
        if rng == 0:
            continue
        upper = h - max(o, c)
        lower = min(o, c) - l

        if lower >= body * 2 and upper <= body * 0.5 and body / rng > 0.1:
            result.append({"type": "hammer", "idx": i, "dir": "bullish", "price": l})
        elif upper >= body * 2 and lower <= body * 0.5 and body / rng > 0.1:
            result.append({"type": "shooting_star", "idx": i, "dir": "bearish", "price": h})
        elif pc < po and c > o and c > po and o < pc:
            result.append({"type": "bullish_engulf", "idx": i, "dir": "bullish", "price": l})
        elif pc > po and c < o and c < po and o > pc:
            result.append({"type": "bearish_engulf", "idx": i, "dir": "bearish", "price": h})
        elif body / rng > 0.6:
            d = "bullish" if c > o else "bearish"
            result.append({"type": "strong_body", "idx": i, "dir": d, "price": l if d == "bullish" else h})
    return result[-12:]


def _detect_confirmation_blocks(
    opens: list[float], highs: list[float], lows: list[float],
    closes: list[float], volumes: list[float],
) -> list[dict]:
    """
    Ajna Confirmation Block: reversal candle followed by a breakout candle
    with volume >= 1.5x 20-period average. SL = reversal extreme; TP = 2R.
    """
    if len(closes) < 22:
        return []
    reversals = _detect_reversal_candles(opens, highs, lows, closes)
    n = len(closes)
    blocks: list[dict] = []
    fallback_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else (sum(volumes) / len(volumes) if volumes else 1.0)

    for rev in reversals:
        next_idx = rev["idx"] + 1
        if next_idx >= n:
            continue
        curr_c = closes[next_idx]
        curr_v = volumes[next_idx] if next_idx < len(volumes) else 0.0
        start = max(0, next_idx - 20)
        vol_slice = volumes[start:next_idx]
        avg_vol = sum(vol_slice) / len(vol_slice) if vol_slice else fallback_vol
        threshold = avg_vol * 1.5

        if rev["dir"] == "bullish" and curr_c > highs[rev["idx"]] and curr_v >= threshold:
            entry = curr_c
            sl = lows[rev["idx"]] * 0.999
            tp = entry + (entry - sl) * 2.0
            blocks.append({
                "type": "long", "entry": entry, "sl": sl, "tp": tp,
                "idx": next_idx, "rev_idx": rev["idx"], "rev_type": rev["type"],
            })
        elif rev["dir"] == "bearish" and curr_c < lows[rev["idx"]] and curr_v >= threshold:
            entry = curr_c
            sl = highs[rev["idx"]] * 1.001
            tp = entry - (sl - entry) * 2.0
            blocks.append({
                "type": "short", "entry": entry, "sl": sl, "tp": tp,
                "idx": next_idx, "rev_idx": rev["idx"], "rev_type": rev["type"],
            })
    return blocks[-5:]


# ── Primary Score Function ────────────────────────────────────────────────────

def score_signal(
    opens: list[float],
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    current_price: float,
) -> dict:
    """
    Ajna SMC-primary signal scoring.
    Returns dict with: side, score, atr_val, vwap_val, sl, tp, conf_block, details.
    A Confirmation Block is the primary trigger; MACD/VWAP trend filters gate it.
    """
    atr_val = atr(closes, highs, lows)
    vwap_val = vwap(closes, highs, lows, volumes)
    macd_line, macd_signal_line, macd_hist = macd(closes)
    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    vr = vol_ratio(volumes)

    # SMC detection
    swings = _detect_swing_points(highs, lows)
    trend = _determine_trend(swings)
    conf_blocks = _detect_confirmation_blocks(opens, highs, lows, closes, volumes)

    details: list[str] = []

    # Must have a fresh confirmation block (within last 3 candles) to fire
    n = len(closes)
    fresh_block: dict | None = None
    for blk in reversed(conf_blocks):
        if n - blk["idx"] <= 3:
            fresh_block = blk
            break

    if fresh_block is None:
        # No fresh confirmation block — no scalp signal
        details.append("No fresh Confirmation Block (waiting for reversal + volume breakout).")
        return {
            "side": None, "score": 50.0,
            "atr_val": round(atr_val, 6), "vwap_val": round(vwap_val, 6),
            "sl": None, "tp": None, "conf_block": None,
            "details": details,
        }

    block_side = fresh_block["type"]  # "long" or "short"

    # Hard filters: block confirmation against dominant trend/VWAP
    if block_side == "long":
        if current_price < vwap_val and macd_hist < 0:
            details.append("LONG blocked: price below VWAP and MACD bearish.")
            return {
                "side": None, "score": 50.0,
                "atr_val": round(atr_val, 6), "vwap_val": round(vwap_val, 6),
                "sl": None, "tp": None, "conf_block": fresh_block,
                "details": details,
            }
    elif block_side == "short":
        if current_price > vwap_val and macd_hist > 0:
            details.append("SHORT blocked: price above VWAP and MACD bullish.")
            return {
                "side": None, "score": 50.0,
                "atr_val": round(atr_val, 6), "vwap_val": round(vwap_val, 6),
                "sl": None, "tp": None, "conf_block": fresh_block,
                "details": details,
            }

    # Base score from confirmation block (strong signal)
    score = 70.0
    details.append(f"Confirmation Block {block_side.upper()}: {fresh_block['rev_type']} + volume breakout.")

    # Trend alignment bonus
    if (trend == "bullish" and block_side == "long") or (trend == "bearish" and block_side == "short"):
        score += 10
        details.append(f"SMC swing trend aligns ({trend}).")
    elif (trend == "bearish" and block_side == "long") or (trend == "bullish" and block_side == "short"):
        score -= 15
        details.append(f"SMC swing trend opposes block direction ({trend}) — reduced confidence.")

    # VWAP alignment
    if block_side == "long" and current_price > vwap_val:
        score += 5
        details.append(f"Price above VWAP ({vwap_val:.4f}).")
    elif block_side == "short" and current_price < vwap_val:
        score += 5
        details.append(f"Price below VWAP ({vwap_val:.4f}).")

    # MACD confirmation
    if block_side == "long" and macd_hist > 0:
        score += 5
        details.append(f"MACD bullish (hist={macd_hist:.4f}).")
    elif block_side == "short" and macd_hist < 0:
        score += 5
        details.append(f"MACD bearish (hist={macd_hist:.4f}).")

    # EMA stack
    if block_side == "long" and e9 > e21:
        score += 5
        details.append(f"EMA9 > EMA21 ({e9:.4f} > {e21:.4f}).")
    elif block_side == "short" and e9 < e21:
        score += 5
        details.append(f"EMA9 < EMA21 ({e9:.4f} < {e21:.4f}).")

    # Volume strength
    if vr >= 1.5:
        score += 5
        details.append(f"Volume {vr:.1f}x avg — strong participation.")
    elif vr < 0.8:
        score -= 5
        details.append(f"Volume {vr:.1f}x avg — weak participation.")

    # Use structure-based SL/TP from confirmation block (Ajna 2R)
    sl = fresh_block["sl"]
    tp = fresh_block["tp"]

    # Final side determination
    if score >= 68:
        side = "LONG" if block_side == "long" else "SHORT"
    else:
        side = None
        details.append(f"Score {score:.0f} below threshold 68 — no entry.")

    return {
        "side": side,
        "score": round(score, 2),
        "atr_val": round(atr_val, 6),
        "vwap_val": round(vwap_val, 6),
        "sl": round(sl, 6) if sl else None,
        "tp": round(tp, 6) if tp else None,
        "conf_block": fresh_block,
        "details": details,
    }
