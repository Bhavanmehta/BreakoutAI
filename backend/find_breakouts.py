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

    # --- Resistance = highest high of prior N days; breakout = close above it on volume ---
    df["resistance"] = df["high"].rolling(settings.LOOKBACK_HIGH).max().shift(1)
    df["avg_vol"] = df["volume"].rolling(settings.VOL_AVG_WINDOW).mean().shift(1)
    df["is_breakout"] = (
        (df["close"] > df["resistance"]) &
        (df["volume"] > df["avg_vol"] * settings.VOL_SURGE_MULT)
    )

    # --- Forward returns (to score historical breakouts) ---
    for w in settings.FORWARD_WINDOWS:
        df[f"fwd_ret_{w}d"] = df["close"].shift(-w) / df["close"] - 1
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
    df = df.dropna(subset=["ema200"])  # need enough history for the slowest EMA
    if len(df) < 2:
        return None
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    price = round(float(latest["close"]), 2)
    change_pct = round((float(latest["close"]) / float(prev["close"]) - 1) * 100, 2)

    ema_stack = {f"ema{w}": ("ABOVE" if latest["close"] > latest[f"ema{w}"] else "BELOW")
                 for w in settings.EMA_WINDOWS}

    adx_val = float(latest["adx"]) if not np.isnan(latest["adx"]) else None
    resistance = float(latest["resistance"]) if not np.isnan(latest["resistance"]) else None
    dist_pct = round((resistance / price - 1) * 100, 2) if resistance else None
    touches = _count_touches(df, resistance)

    vc = float(latest["vol_contraction"]) if not np.isnan(latest["vol_contraction"]) else None
    vol_state = "Coiling (squeeze)" if (vc is not None and vc < 1) else "Expanding"

    # Base depth = drawdown from the recent high to the lowest low since (the "cup" depth)
    recent = df.tail(settings.LOOKBACK_HIGH)
    base_depth = round((recent["low"].min() / recent["high"].max() - 1) * 100, 1)

    # Historical breakout scoring for THIS stock
    events = df[df["is_breakout"] == True].dropna(subset=[f"fwd_ret_{w}d" for w in settings.FORWARD_WINDOWS])
    if len(events) > 0:
        winrate_20d = round(float((events["fwd_ret_20d"] > 0).mean()), 3)
        avg_fwd_20d = round(float(events["fwd_ret_20d"].mean()) * 100, 2)
    else:
        winrate_20d = None
        avg_fwd_20d = None

    sentiment = _sentiment(latest, adx_val)
    broke_out_today = bool(latest["is_breakout"])

    # Plain-English guidance derived from the computed state
    if resistance:
        trigger = (f"Watch for a close above ₹{resistance:,.2f} on above-average volume "
                   f"to confirm a breakout.")
        suggested_entry = f"₹{resistance:,.2f}+ (breakout close on volume)"
        stop = round(resistance * 0.94, 2)
        stop_loss = f"₹{stop:,.2f} (~-6% below the trigger)"
    else:
        trigger = "Not enough history to define a clear resistance level yet."
        suggested_entry = "—"
        stop_loss = "—"

    return {
        "symbol": symbol,
        "name": meta.get("name", symbol),
        "sector": meta.get("sector", ""),
        "as_of": latest["date"].strftime("%Y-%m-%d"),
        "price": price,
        "change_pct": change_pct,
        "ema_stack": ema_stack,
        "adx": {"value": round(adx_val, 1) if adx_val else None, "label": _adx_label(adx_val)},
        "resistance": {"level": round(resistance, 2) if resistance else None,
                       "distance_pct": dist_pct, "touches": touches},
        "base_depth_pct": base_depth,
        "volatility": {"contraction_ratio": round(vc, 2) if vc else None, "state": vol_state},
        "breakout": {"today": broke_out_today, "sentiment": sentiment},
        "history": {"past_breakouts": int(len(events)),
                    "winrate_20d": winrate_20d, "avg_fwd_return_20d_pct": avg_fwd_20d},
        "entry": {"trigger": trigger, "suggested_entry": suggested_entry, "stop_loss": stop_loss},
    }
