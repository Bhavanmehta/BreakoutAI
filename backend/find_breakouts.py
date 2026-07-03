"""
The analysis "brain": turn a clean price history into the numbers the website shows.

Given a tidy OHLCV DataFrame for one stock, we compute:
  * EMA stack        -> is price above/below its 10/20/50/200-day averages?
  * ADX              -> how strong is the current trend?
  * Resistance       -> the nearby ceiling, how far away, how many times tested
  * Volatility (VCP) -> is the daily range contracting (coiling) or expanding?
  * Breakouts        -> flag today's breakout, and score how past breakouts played out
  * Sentiment        -> a simple bullish / neutral / bearish read
  * Entry guidance   -> plain-English trigger, suggested entry, risk cutoff

All windows/thresholds come from settings.py.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

import settings
from patterns import detect_pattern
from analogs import detect_analog


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Attach EMA, ATR, ADX, resistance and breakout columns to a price frame."""
    df = df.sort_values("date").reset_index(drop=True).copy()

    # --- EMAs ---
    for w in settings.EMA_WINDOWS:
        df[f"ema{w}"] = df["close"].ewm(span=w, adjust=False).mean()

    # --- True Range + ATR (short & long) for volatility contraction ---
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["tr"] = tr
    df["atr_short"] = tr.rolling(settings.ATR_SHORT).mean().shift(1)  # exclude today
    df["atr_long"] = tr.rolling(settings.ATR_LONG).mean().shift(1)
    df["vol_contraction"] = df["atr_short"] / df["atr_long"]

    # --- ADX (Wilder smoothing) ---
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    p = settings.ADX_PERIOD
    alpha = 1.0 / p
    atr_w = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=alpha, adjust=False).mean() / atr_w
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=alpha, adjust=False).mean() / atr_w
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx"] = dx.ewm(alpha=alpha, adjust=False).mean()

    # --- Trend filter (Stage 2): above a rising long EMA and above the mid EMA ---
    long_ema = df[f"ema{settings.TREND_EMA_LONG}"]
    mid_ema = df[f"ema{settings.TREND_EMA_MID}"]
    ema_long_rising = long_ema > long_ema.shift(settings.EMA200_SLOPE_LOOKBACK)
    df["uptrend"] = (df["close"] > long_ema) & ema_long_rising & (df["close"] > mid_ema)

    # --- Proximity to the 52-week high (a real high, not a bounce mid-decline) ---
    high_52w = df["high"].rolling(252, min_periods=50).max()
    df["dist_from_52w_high"] = (high_52w - df["close"]) / high_52w * 100

    # --- Resistance = highest high of prior N days ---
    df["resistance"] = df["high"].rolling(settings.LOOKBACK_HIGH).max().shift(1)
    df["avg_vol"] = df["volume"].rolling(settings.VOL_AVG_WINDOW).mean().shift(1)

    # --- Breakout = new high + volume surge + (optionally) uptrend + near 52w high ---
    cond = (df["close"] > df["resistance"]) & (df["volume"] > df["avg_vol"] * settings.VOL_SURGE_MULT)
    if settings.REQUIRE_UPTREND:
        cond = cond & df["uptrend"] & (df["dist_from_52w_high"] <= settings.MAX_DIST_FROM_52W_HIGH)
    df["is_breakout"] = cond

    # --- Forward returns (context) ---
    for w in settings.FORWARD_WINDOWS:
        df[f"fwd_ret_{w}d"] = df["close"].shift(-w) / df["close"] - 1

    # --- Follow-through: did price hit +1R before -1R (stop) within WINDOW days? ---
    # R = entry - stop, where stop = resistance * STOP_LOSS_FRACTION — the same stop the
    # entry guidance shows. This scales per-stock/event automatically (unlike a fixed %
    # target) and checks which level is hit FIRST, so a trade that drops through the stop
    # and later recovers past the old target no longer counts as "worked".
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    resistances = df["resistance"].values
    n = len(df)
    W = settings.FOLLOWTHROUGH_WINDOW
    worked = np.full(n, np.nan, dtype=object)
    r_multiple = np.full(n, np.nan)
    for i in range(n):
        if i + W >= n:  # need a full forward window to judge fairly
            continue
        res = resistances[i]
        if not res or np.isnan(res):
            continue
        entry = closes[i]
        stop = res * settings.STOP_LOSS_FRACTION
        risk = entry - stop
        if risk <= 0:
            continue
        target = entry + risk
        outcome = False  # neither level hit within the window -> target not reached
        for j in range(i + 1, i + 1 + W):
            if lows[j] <= stop:
                outcome = False
                break
            if highs[j] >= target:
                outcome = True
                break
        worked[i] = outcome
        r_multiple[i] = risk
    df["followthrough"] = worked
    df["r_multiple"] = r_multiple
    return df


def _adx_label(v: float) -> str:
    if v is None or np.isnan(v):
        return "N/A"
    if v >= 30:
        return "EXPLOSIVE"
    if v >= 25:
        return "STRONG"
    if v >= 20:
        return "ACTIVE"
    return "CHOP"


def _count_touches(df: pd.DataFrame, level: float) -> int:
    """How many of the recent LOOKBACK_HIGH days came within RESISTANCE_TOUCH_PCT of `level`."""
    if not level or np.isnan(level):
        return 0
    recent = df.tail(settings.LOOKBACK_HIGH)
    near = (recent["high"] >= level * (1 - settings.RESISTANCE_TOUCH_PCT / 100))
    return int(near.sum())


def _sentiment(latest, adx_val) -> str:
    """Simple, explainable rule combining trend structure and strength."""
    above_all = all(latest["close"] > latest[f"ema{w}"] for w in settings.EMA_WINDOWS)
    above_key = latest["close"] > latest["ema50"] and latest["close"] > latest["ema200"]
    below_key = latest["close"] < latest["ema50"] and latest["close"] < latest["ema200"]
    strong = (adx_val or 0) >= 20
    if above_all and strong:
        return "Bullish"
    if above_key:
        return "Bullish"
    if below_key:
        return "Bearish"
    return "Neutral"


def build_summary(df: pd.DataFrame, symbol: str, meta: dict) -> dict:
    """Roll a fully-indicated frame into the compact record the website consumes."""
    if len(df) < settings.MIN_HISTORY_BARS:
        return None
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    price = round(float(latest["close"]), 2)
    change_pct = round((float(latest["close"]) / float(prev["close"]) - 1) * 100, 2)

    # Each EMA carries its actual value, its position vs price, and a friendly label.
    ema_stack = {}
    for w in settings.EMA_WINDOWS:
        val = float(latest[f"ema{w}"])
        ema_stack[f"ema{w}"] = {
            "period": w,
            "value": round(val, 2),
            "position": "ABOVE" if latest["close"] > val else "BELOW",
            "label": settings.EMA_LABELS.get(w, ""),
        }

    adx_val = float(latest["adx"]) if not np.isnan(latest["adx"]) else None
    resistance = float(latest["resistance"]) if not np.isnan(latest["resistance"]) else None
    dist_pct = round((resistance / price - 1) * 100, 2) if resistance else None
    touches = _count_touches(df, resistance)

    vc = float(latest["vol_contraction"]) if not np.isnan(latest["vol_contraction"]) else None
    vol_state = "Coiling (squeeze)" if (vc is not None and vc < 1) else "Expanding"

    # Base depth = drawdown from the recent high to the lowest low since (the "cup" depth)
    recent = df.tail(settings.LOOKBACK_HIGH)
    base_depth = round((recent["low"].min() / recent["high"].max() - 1) * 100, 1)

    # Historical breakout scoring for THIS stock. "Worked" = followed through:
    # price hit +1R before -1R (stop) within FOLLOWTHROUGH_WINDOW days (see add_indicators).
    events = df[(df["is_breakout"] == True) & (df["followthrough"].notna())]
    if len(events) > 0:
        followthrough_rate = round(float(events["followthrough"].astype(bool).mean()), 3)
        avg_fwd_20d = round(float(events["fwd_ret_20d"].dropna().mean()) * 100, 2) if events["fwd_ret_20d"].notna().any() else None
    else:
        followthrough_rate = None
        avg_fwd_20d = None
    followthrough_label = (f"hit +1R (a risk-defined target, not a fixed %) before the stop, "
                           f"within {settings.FOLLOWTHROUGH_WINDOW} trading days")

    # Concrete "last time this happened" examples — the most recent past breakouts
    # on THIS stock and how the price moved in the days after each.
    examples = []
    for _, ev in events.sort_values("date", ascending=False).head(3).iterrows():
        examples.append({
            "date": ev["date"].strftime("%Y-%m-%d"),
            "price_then": round(float(ev["close"]), 2),
            "worked": bool(ev["followthrough"]),
            "fwd_5d_pct": round(float(ev["fwd_ret_5d"]) * 100, 1) if pd.notna(ev["fwd_ret_5d"]) else None,
            "fwd_10d_pct": round(float(ev["fwd_ret_10d"]) * 100, 1) if pd.notna(ev["fwd_ret_10d"]) else None,
            "fwd_20d_pct": round(float(ev["fwd_ret_20d"]) * 100, 1) if pd.notna(ev["fwd_ret_20d"]) else None,
        })

    sentiment = _sentiment(latest, adx_val)
    broke_out_today = bool(latest["is_breakout"])
    in_uptrend = bool(latest["uptrend"])
    trend = {
        "in_uptrend": in_uptrend,
        "label": "Uptrend (above rising 200-day avg)" if in_uptrend else "Not in an uptrend",
    }

    # "Breakout soon?" readiness — proximity to resistance + coiling, gated by trend.
    # A coil below resistance only counts as "primed" if the stock is actually trending up.
    coiling = (vc is not None and vc < 1)
    near = (dist_pct is not None and 0 <= dist_pct <= 3)   # within 3% below resistance
    if broke_out_today:
        readiness = {"label": "Breaking out now", "watch": True, "score": "high"}
    elif not in_uptrend:
        readiness = {"label": "Not in an uptrend — breakouts unreliable here", "watch": False, "score": "low"}
    elif near and coiling:
        readiness = {"label": "Primed — coiling below resistance in an uptrend", "watch": True, "score": "high"}
    elif near:
        readiness = {"label": "Approaching resistance", "watch": True, "score": "medium"}
    elif coiling:
        readiness = {"label": "Coiling — building a base", "watch": False, "score": "medium"}
    else:
        readiness = {"label": "In an uptrend, no setup yet", "watch": False, "score": "low"}

    # Fold the historical follow-through rate into the read, so "primed" never
    # oversells: a setup on a stock whose breakouts rarely work gets a caution.
    if readiness["watch"] and followthrough_rate is not None:
        pct = round(followthrough_rate * 100)
        if followthrough_rate < 0.4:
            readiness["reliability"] = (f"Caution: only {pct}% of this stock's past breakouts "
                                        f"followed through — historically unreliable.")
            readiness["reliable"] = False
        else:
            readiness["reliability"] = f"{pct}% of its past breakouts followed through historically."
            readiness["reliable"] = True
    else:
        readiness["reliability"] = None
        readiness["reliable"] = None

    # Named chart pattern (real geometry — see patterns.py)
    pattern = detect_pattern(df)

    # Historical analog: the past bar on this stock most similar to today, and what
    # happened next — the evidence behind "The Read" (see analogs.py).
    analog = detect_analog(df)

    # Plain-English guidance derived from the computed state
    if resistance:
        trigger = (f"Watch for a close above ₹{resistance:,.2f} on above-average volume "
                   f"to confirm a breakout.")
        suggested_entry = f"₹{resistance:,.2f}+ (breakout close on volume)"
        stop = round(resistance * settings.STOP_LOSS_FRACTION, 2)
        stop_loss = f"₹{stop:,.2f} (~-6% below the trigger)"
    else:
        trigger = "Not enough history to define a clear resistance level yet."
        suggested_entry = "—"
        stop_loss = "—"

    return {
        "symbol": symbol,
        "name": meta.get("name", symbol),
        "sector": meta.get("sector", ""),
        "industry": meta.get("industry", ""),
        "as_of": latest["date"].strftime("%Y-%m-%d"),
        "price": price,
        "change_pct": change_pct,
        "ema_stack": ema_stack,
        "adx": {"value": round(adx_val, 1) if adx_val else None, "label": _adx_label(adx_val)},
        "resistance": {"level": round(resistance, 2) if resistance else None,
                       "distance_pct": dist_pct, "touches": touches},
        "base_depth_pct": base_depth,
        "volatility": {"contraction_ratio": round(vc, 2) if vc else None, "state": vol_state},
        "trend": trend,
        "pattern": pattern,
        "analog": analog,
        "breakout": {"today": broke_out_today, "sentiment": sentiment},
        "readiness": readiness,
        "history": {"past_breakouts": int(len(events)),
                    "followthrough_rate": followthrough_rate,
                    "followthrough_label": followthrough_label,
                    "avg_fwd_return_20d_pct": avg_fwd_20d,
                    "examples": examples},
        "entry": {"trigger": trigger, "suggested_entry": suggested_entry, "stop_loss": stop_loss},
    }
