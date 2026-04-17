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
    """
    Detect high-quality reversal candles only.
    - Hammer / shooting star: wick >= 2.5x body (stricter than before)
    - Engulfing: must fully engulf prior candle body
    - Strong body: body >= 70% of range (was 60% — filters out weak candles)
    """
    result: list[dict] = []
    n = len(closes)
    for i in range(1, n):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        po, pc = opens[i - 1], closes[i - 1]
        body = abs(c - o)
        rng = h - l
        if rng == 0 or body == 0:
            continue
        upper = h - max(o, c)
        lower = min(o, c) - l

        # Hammer: long lower wick >= 2.5x body, tiny upper wick
        if lower >= body * 2.5 and upper <= body * 0.3 and body / rng > 0.1:
            result.append({"type": "hammer", "idx": i, "dir": "bullish", "price": l})
        # Shooting star: long upper wick >= 2.5x body, tiny lower wick
        elif upper >= body * 2.5 and lower <= body * 0.3 and body / rng > 0.1:
            result.append({"type": "shooting_star", "idx": i, "dir": "bearish", "price": h})
        # Bullish engulf: prior candle is bearish, current fully engulfs it
        elif pc < po and c > o and c >= po and o <= pc:
            result.append({"type": "bullish_engulf", "idx": i, "dir": "bullish", "price": l})
        # Bearish engulf: prior candle is bullish, current fully engulfs it
        elif pc > po and c < o and c <= po and o >= pc:
            result.append({"type": "bearish_engulf", "idx": i, "dir": "bearish", "price": h})
        # Strong body: >= 70% of range (filters noisy 5M wicks)
        elif body / rng >= 0.70:
            d = "bullish" if c > o else "bearish"
            result.append({"type": "strong_body", "idx": i, "dir": d, "price": l if d == "bullish" else h})
    return result[-12:]


def _detect_confirmation_blocks(
    opens: list[float], highs: list[float], lows: list[float],
    closes: list[float], volumes: list[float],
) -> list[dict]:
    """
    Ajna Confirmation Block: reversal candle followed immediately by a breakout
    candle with volume >= 2.0x 20-period average (was 1.5x — more selective).

    SL = reversal candle extreme + 0.3% buffer (was 0.1% — too tight for crypto wicks).
    TP = 2R from entry.
    Minimum risk: SL distance must be >= 0.3% of price (filters noise trades).
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
        # Raised to 2.0x — require genuinely elevated volume for confirmation
        threshold = avg_vol * 2.0

        if rev["dir"] == "bullish" and curr_c > highs[rev["idx"]] and curr_v >= threshold:
            entry = curr_c
            # 0.3% buffer below reversal candle low (was 0.1% — too tight for wicks)
            sl = lows[rev["idx"]] * 0.997
            risk = entry - sl
            # Minimum risk filter: skip if risk < 0.3% of entry (noise trades)
            if risk < entry * 0.003:
                continue
            tp = entry + risk * 2.0
            blocks.append({
                "type": "long", "entry": entry, "sl": sl, "tp": tp,
                "idx": next_idx, "rev_idx": rev["idx"], "rev_type": rev["type"],
                "risk_pct": round(risk / entry * 100, 3),
            })
        elif rev["dir"] == "bearish" and curr_c < lows[rev["idx"]] and curr_v >= threshold:
            entry = curr_c
            # 0.3% buffer above reversal candle high
            sl = highs[rev["idx"]] * 1.003
            risk = sl - entry
            if risk < entry * 0.003:
                continue
            tp = entry - risk * 2.0
            blocks.append({
                "type": "short", "entry": entry, "sl": sl, "tp": tp,
                "idx": next_idx, "rev_idx": rev["idx"], "rev_type": rev["type"],
                "risk_pct": round(risk / entry * 100, 3),
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

    Entry rules (all must pass):
      1. Fresh confirmation block within last 2 candles (10 min on 5M)
      2. Counter-trend hard block: if structure AND MACD both oppose → skip
      3. Score >= 76 (requires at least 2 confirming factors beyond the block itself)
      4. ATR noise filter: SL distance must be >= 0.4x ATR
    """
    atr_val = atr(closes, highs, lows)
    vwap_val = vwap(closes, highs, lows, volumes)
    macd_line, macd_signal_line, macd_hist = macd(closes)
    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    rsi_val = rsi(closes)
    vr = vol_ratio(volumes)

    # SMC detection
    swings = _detect_swing_points(highs, lows)
    trend = _determine_trend(swings)
    conf_blocks = _detect_confirmation_blocks(opens, highs, lows, closes, volumes)

    details: list[str] = []

    # ── 1. Require a fresh confirmation block (within last 2 candles = 10 min) ──
    n = len(closes)
    fresh_block: dict | None = None
    for blk in reversed(conf_blocks):
        if n - blk["idx"] <= 2:
            fresh_block = blk
            break

    if fresh_block is None:
        details.append("No fresh Confirmation Block within last 2 candles.")
        return {
            "side": None, "score": 50.0,
            "atr_val": round(atr_val, 6), "vwap_val": round(vwap_val, 6),
            "sl": None, "tp": None, "conf_block": None, "details": details,
        }

    block_side = fresh_block["type"]  # "long" or "short"

    # ── 2. Hard counter-trend block ──
    # Reject if BOTH structure AND MACD oppose the block direction
    if block_side == "long":
        if trend == "bearish" and macd_hist < 0:
            details.append("LONG blocked: bearish structure + bearish MACD — counter-trend.")
            return {
                "side": None, "score": 50.0,
                "atr_val": round(atr_val, 6), "vwap_val": round(vwap_val, 6),
                "sl": None, "tp": None, "conf_block": fresh_block, "details": details,
            }
        # Also block if price below VWAP AND EMA stack bearish
        if current_price < vwap_val and e9 < e21:
            details.append("LONG blocked: price below VWAP and EMA bearish stack.")
            return {
                "side": None, "score": 50.0,
                "atr_val": round(atr_val, 6), "vwap_val": round(vwap_val, 6),
                "sl": None, "tp": None, "conf_block": fresh_block, "details": details,
            }
    elif block_side == "short":
        if trend == "bullish" and macd_hist > 0:
            details.append("SHORT blocked: bullish structure + bullish MACD — counter-trend.")
            return {
                "side": None, "score": 50.0,
                "atr_val": round(atr_val, 6), "vwap_val": round(vwap_val, 6),
                "sl": None, "tp": None, "conf_block": fresh_block, "details": details,
            }
        if current_price > vwap_val and e9 > e21:
            details.append("SHORT blocked: price above VWAP and EMA bullish stack.")
            return {
                "side": None, "score": 50.0,
                "atr_val": round(atr_val, 6), "vwap_val": round(vwap_val, 6),
                "sl": None, "tp": None, "conf_block": fresh_block, "details": details,
            }

    # ── 3. ATR noise filter ──
    sl = fresh_block["sl"]
    tp = fresh_block["tp"]
    sl_dist = abs(current_price - sl)
    if atr_val > 0 and sl_dist < atr_val * 0.4:
        details.append(f"SL distance {sl_dist:.4f} < 0.4x ATR {atr_val:.4f} — noise trade, skipped.")
        return {
            "side": None, "score": 50.0,
            "atr_val": round(atr_val, 6), "vwap_val": round(vwap_val, 6),
            "sl": None, "tp": None, "conf_block": fresh_block, "details": details,
        }

    # ── 4. Score the setup — need >= 76 (at least 2 additional confirmations) ──
    score = 70.0
    details.append(
        f"Confirmation Block {block_side.upper()}: {fresh_block['rev_type']} "
        f"(risk {fresh_block.get('risk_pct', '?')}%)."
    )

    # Trend alignment (+12 aligned, -20 opposed, 0 ranging)
    if (trend == "bullish" and block_side == "long") or (trend == "bearish" and block_side == "short"):
        score += 12
        details.append(f"Structure aligned ({trend}).")
    elif (trend == "bearish" and block_side == "long") or (trend == "bullish" and block_side == "short"):
        score -= 20
        details.append(f"Structure opposes — counter-trend entry, heavy penalty.")

    # VWAP location (+6 aligned, -6 opposed)
    if block_side == "long":
        if current_price > vwap_val:
            score += 6
            details.append(f"Price above VWAP ({vwap_val:.4f}).")
        else:
            score -= 6
            details.append(f"Price below VWAP ({vwap_val:.4f}).")
    else:
        if current_price < vwap_val:
            score += 6
            details.append(f"Price below VWAP ({vwap_val:.4f}).")
        else:
            score -= 6
            details.append(f"Price above VWAP ({vwap_val:.4f}).")

    # MACD momentum (+6 aligned, -4 opposed)
    if block_side == "long":
        if macd_hist > 0:
            score += 6
            details.append(f"MACD bullish (hist={macd_hist:.5f}).")
        else:
            score -= 4
            details.append(f"MACD bearish (hist={macd_hist:.5f}).")
    else:
        if macd_hist < 0:
            score += 6
            details.append(f"MACD bearish (hist={macd_hist:.5f}).")
        else:
            score -= 4
            details.append(f"MACD bullish (hist={macd_hist:.5f}).")

    # EMA stack (+5 aligned)
    if block_side == "long" and e9 > e21:
        score += 5
        details.append(f"EMA9 > EMA21 — bullish stack.")
    elif block_side == "short" and e9 < e21:
        score += 5
        details.append(f"EMA9 < EMA21 — bearish stack.")

    # RSI: reward momentum zone, punish extremes
    if block_side == "long":
        if 45 <= rsi_val <= 65:
            score += 5
            details.append(f"RSI in momentum zone ({rsi_val:.1f}).")
        elif rsi_val > 72:
            score -= 8
            details.append(f"RSI overbought ({rsi_val:.1f}) — late long risk.")
    else:
        if 35 <= rsi_val <= 55:
            score += 5
            details.append(f"RSI in momentum zone ({rsi_val:.1f}).")
        elif rsi_val < 28:
            score -= 8
            details.append(f"RSI oversold ({rsi_val:.1f}) — late short risk.")

    # Volume strength at current bar
    if vr >= 1.5:
        score += 4
        details.append(f"Volume {vr:.1f}x avg.")
    elif vr < 0.7:
        score -= 4
        details.append(f"Weak volume {vr:.1f}x avg.")

    # ── 5. Final gate: score >= 76 ──
    if score >= 76:
        side = "LONG" if block_side == "long" else "SHORT"
    else:
        side = None
        details.append(f"Score {score:.0f} < 76 threshold — no entry.")

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
