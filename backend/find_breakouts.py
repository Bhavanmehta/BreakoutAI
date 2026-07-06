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
from levels import resolve_display_levels
from score import reliability_estimate, conviction


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
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di

    # --- RSI (Wilder smoothing, same alpha=1/period convention as ADX above) ---
    # Display-only context on the annotated chart -- not an input to breakout detection
    # or conviction scoring (not part of the validated feature set in analyze_reliability.py).
    delta = df["close"].diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    rsi_alpha = 1.0 / settings.RSI_PERIOD
    avg_gain = gain.ewm(alpha=rsi_alpha, adjust=False).mean()
    avg_loss = loss.ewm(alpha=rsi_alpha, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    df.loc[avg_loss == 0, "rsi"] = 100.0   # no losses in the window -> maxed out, not undefined

    # --- Trend filter (Stage 2): above a rising long EMA and above the mid EMA ---
    long_ema = df[f"ema{settings.TREND_EMA_LONG}"]
    mid_ema = df[f"ema{settings.TREND_EMA_MID}"]
    ema_long_rising = long_ema > long_ema.shift(settings.EMA200_SLOPE_LOOKBACK)
    df["uptrend"] = (df["close"] > long_ema) & ema_long_rising & (df["close"] > mid_ema)

    # --- Proximity to the 52-week high (a real high, not a bounce mid-decline) ---
    high_52w = df["high"].rolling(252, min_periods=50).max()
    df["dist_from_52w_high"] = (high_52w - df["close"]) / high_52w * 100

    # --- Resistance = highest high of prior N days (internal is_breakout input only;
    # the support/resistance the user SEES comes from swing-pivot clustering, levels.py) ---
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


# Minimum past events before we'll make ANY negative reliability claim. One or two
# breakouts can't establish that a stock is "unreliable" — this is the fix for a single
# bad occurrence flashing a red flag. Below this we say "limited history" instead.
RELIABILITY_MIN_SAMPLE = 3


def _last_is_fresh_fire(mask: pd.Series, cooldown: int) -> bool:
    """Was the LAST bar a fresh fire of `mask`, under the same cooldown-dedup rule
    analyze_reliability.py's `_dedup_with_cooldown` uses to grade backtested events
    (kept fires must be > cooldown bars apart -- NOT "raw signal must go quiet";
    a long continuously-true run is still re-counted every cooldown+1 bars)? A
    squeeze release can stay true for several consecutive days during one continuous
    move (confirmed: e.g. ATYR fired raw is_breakout_c on 18 days across only 12
    distinct backtest-counted clusters) -- without this, a live badge would repeat
    daily through a single move and both overstate cadence and dilute the hit rate
    below the validated ~51%/n=190 stat. Local re-implementation (not imported from
    analyze_reliability.py) to avoid a circular import -- that module imports
    add_indicators from here."""
    arr = mask.fillna(False).values
    last_fire = -cooldown - 1
    fresh_at_end = False
    for i in np.flatnonzero(arr):
        fresh_at_end = (i - last_fire > cooldown)
        if fresh_at_end:
            last_fire = i
    return fresh_at_end and bool(arr[-1])


def _reliability_note(worked: int, total: int, kind: str = "breakouts"):
    """Turn a stock's own past follow-through record into (text, reliable_flag) using a
    Bayesian-shrunk estimate (score.reliability_estimate) so a tiny sample can neither
    over- nor under-sell. The shrinkage means 0-of-1 reads ~31% (neutral), not 0%.
    Returns (None, None) when there's no history at all."""
    if total <= 0:
        return None, None
    if total < RELIABILITY_MIN_SAMPLE:
        return (f"Limited history — only {total} past {kind} on record, "
                f"not enough to judge reliability yet.", None)
    rel = reliability_estimate(worked, total)
    pct = round(rel * 100)
    # Bands sit +-6pts around the market's own measured base rate (settings' score-
    # calibration block): India 0.33/0.45 (identical to the old hardcoded values),
    # US 0.21/0.33 — US breakouts resolve the fixed band far less often, so judging a
    # US stock against India's 39% base rate would mislabel nearly everything "weak".
    if rel < settings.RELIABILITY_CAUTION_BELOW:
        return (f"Caution: a weak track record — about {pct}% of its {total} past "
                f"{kind} followed through.", False)
    if rel >= settings.RELIABILITY_GOOD_AT:
        # In the US a "reliable" stock can still read ~34% — above its market's ~27%
        # average but jarring without that context, so spell the baseline out there.
        avg_note = (f", above the ~{round(settings.SCORE_BASE_RATE * 100)}% market average"
                    if settings.MARKET == "US" else "")
        return (f"Reliable — about {pct}% of its {total} past {kind} followed through{avg_note}.", True)
    return (f"About {pct}% of its {total} past {kind} have followed through — "
            f"roughly the market average.", None)


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

    # Today's volume vs its own VOL_AVG_WINDOW-day average -- context for *why* a
    # breakout fired (the trigger itself already requires VOL_SURGE_MULT), not a new
    # standalone signal (analyze_reliability.py found surge magnitude alone isn't
    # predictive of follow-through).
    avg_vol = float(latest["avg_vol"]) if not np.isnan(latest["avg_vol"]) else None
    vol_ratio = round(float(latest["volume"]) / avg_vol, 2) if avg_vol else None
    volume_today = {
        "today": int(latest["volume"]), "avg": round(avg_vol) if avg_vol else None,
        "ratio": vol_ratio, "surge": bool(vol_ratio and vol_ratio >= settings.VOL_SURGE_MULT),
    }

    vc = float(latest["vol_contraction"]) if not np.isnan(latest["vol_contraction"]) else None
    vol_state = "Coiling (squeeze)" if (vc is not None and vc < 1) else "Expanding"

    # Base depth = drawdown from the recent high to the lowest low since (the "cup" depth)
    recent = df.tail(settings.LOOKBACK_HIGH)
    base_depth = round((recent["low"].min() / recent["high"].max() - 1) * 100, 1)

    # Historical breakout scoring for THIS stock. "Worked" = followed through:
    # price hit +1R before -1R (stop) within FOLLOWTHROUGH_WINDOW days (see add_indicators).
    events = df[(df["is_breakout"] == True) & (df["followthrough"].notna())]
    worked_a, total_a = int(events["followthrough"].astype(bool).sum()), len(events)
    if total_a > 0:
        followthrough_rate = round(float(events["followthrough"].astype(bool).mean()), 3)
        avg_fwd_20d = round(float(events["fwd_ret_20d"].dropna().mean()) * 100, 2) if events["fwd_ret_20d"].notna().any() else None
    else:
        followthrough_rate = None
        avg_fwd_20d = None

    # Same follow-through stat, but for this stock's own relative-strength-vs-Nifty
    # breakouts (Method E2, see methods.py) — kept separate from Method A's history
    # above so the readiness caution below never mixes the two signals' track records.
    events_rs = df[(df.get("is_breakout_e2", False) == True) & (df["followthrough"].notna())]
    worked_rs, total_rs = int(events_rs["followthrough"].astype(bool).sum()), len(events_rs)
    rs_followthrough_rate = (round(float(events_rs["followthrough"].astype(bool).mean()), 3)
                              if total_rs > 0 else None)
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
    rs_breakout_today = bool(latest.get("is_breakout_e2", False))
    if broke_out_today:
        readiness = {"label": "Breaking out now", "watch": True, "score": "high"}
    elif not in_uptrend:
        readiness = {"label": "Not in an uptrend — breakouts unreliable here", "watch": False, "score": "low"}
    elif rs_breakout_today:
        # Independent of the resistance/coiling ladder below — a stock's price÷Nifty
        # ratio just hit a fresh 50-day high (Method E2). Backtested standalone
        # (whole-market, 2026-07-04): 41.6% follow-through hit rate vs Method A's
        # 38.8%, only 22% event-overlap with A, so it's kept as its own tier rather
        # than blended into "Primed"/"Approaching resistance" (see methods.py).
        readiness = {"label": f"Outperforming the market — new relative-strength high vs {settings.RS_BENCHMARK_LABEL}",
                     "watch": True, "score": "high", "signal": "relative_strength"}
    elif near and coiling:
        readiness = {"label": "Primed — coiling below resistance in an uptrend", "watch": True, "score": "high"}
    elif near:
        readiness = {"label": "Approaching resistance", "watch": True, "score": "medium"}
    elif coiling:
        readiness = {"label": "Coiling — building a base", "watch": False, "score": "medium"}
    else:
        readiness = {"label": "In an uptrend, no setup yet", "watch": False, "score": "low"}
    readiness.setdefault("signal", None)

    # --- US high-conviction tiers (train/test-validated; IMPLEMENT_US_HIGH_CONVICTION.md).
    # Tier 1: a volatility squeeze releasing into a confirmed breakout, bought near the
    # trigger, in a name with enough daily range to plausibly move +-1R inside the 10-day
    # grading window, above the liquidity floor. Historically ~51% follow-through (n=190)
    # vs the 26.7% US base. Tier 2: today's Method-A breakout with the same energy + floor
    # gates (~46%, n=3,215). Both tiers deliberately reuse already-computed columns.
    if settings.HC_ENABLED:
        atr_v = float(latest["atr_short"]) if pd.notna(latest["atr_short"]) else None
        atr_pct = (atr_v / price * 100) if atr_v and price else None
        ext_pct = (price / resistance - 1) * 100 if resistance else None
        a_recent = bool(df["is_breakout"].tail(settings.HC_COFIRE_BARS).any())
        liquid = (avg_vol is not None and avg_vol >= settings.HC_MIN_AVG_VOL_SHARES
                  and price >= settings.HC_MIN_PRICE)
        energetic = atr_pct is not None and atr_pct >= settings.HC_ATR_MIN_PCT
        # Require today to be a FRESH squeeze-release fire, under the identical
        # cooldown-dedup rule the backtest used to count events (see
        # _last_is_fresh_fire) — otherwise a live badge would repeat every day of a
        # single continuous move and both overstate cadence and dilute the hit rate
        # below the validated ~51%/n=190 stat.
        c_fresh = ("is_breakout_c" in df.columns
                   and _last_is_fresh_fire(df["is_breakout_c"], settings.FOLLOWTHROUGH_WINDOW))
        if (c_fresh and a_recent and liquid and energetic
                and ext_pct is not None and ext_pct <= settings.HC_EXT_MAX_PCT):
            readiness.update({"label": "High-conviction setup — volatility squeeze released "
                                        "into a confirmed breakout, entry still near the trigger",
                              "watch": True, "score": "high", "signal": "high_conviction"})
        elif (broke_out_today and liquid and energetic
              and _last_is_fresh_fire(df["is_breakout"], settings.FOLLOWTHROUGH_WINDOW)):
            # Same fresh-fire requirement as tier 1 (Method-A breakouts cluster on
            # consecutive days too — the backtest's n=3,215/45.3% counted deduped A
            # events, not raw daily fires; broke_out_today alone would double-count).
            readiness["signal"] = "strong_breakout"   # label stays "Breaking out now"

    # Fold the historical follow-through rate into the read, so a flagged setup never
    # oversells: a stock whose breakouts rarely work gets a caution — but only with
    # enough sample to justify it (see _reliability_note; a single bad breakout no
    # longer flashes red). The relative-strength tier uses ITS OWN history, never
    # Method A's — mixing the two would misrepresent which signal is actually on watch.
    if readiness["signal"] == "relative_strength":
        note, flag = _reliability_note(worked_rs, total_rs, "relative-strength breakouts")
    elif readiness["watch"]:
        note, flag = _reliability_note(worked_a, total_a, "breakouts")
    else:
        note, flag = None, None
    readiness["reliability"], readiness["reliable"] = note, flag

    # Single 0..100 "conviction" the UI ranks on: how imminent the setup is (readiness
    # tier) blended with how reliable it is if it triggers (the validated composite
    # score — shrunk track record + base depth + signal confirmation, see score.py).
    # Backtested: the quality half stratifies follow-through 34.5%->41.0% across buckets.
    if broke_out_today:
        imminence = "breaking"
    elif readiness["score"] == "high":
        imminence = "high"
    elif readiness["score"] == "medium" and readiness["watch"]:
        imminence = "medium_watch"
    elif readiness["score"] == "medium":
        imminence = "medium"
    else:
        imminence = "low"
    rel_for_score = (reliability_estimate(worked_rs, total_rs)
                     if readiness["signal"] == "relative_strength"
                     else reliability_estimate(worked_a, total_a))
    readiness["conviction"] = conviction(rel_for_score, base_depth, imminence,
                                         rs_on=rs_breakout_today)

    # Rank floors, not probabilities: held-out hit rates are 52% (tier 1) and 46%
    # (tier 2) vs 43% for the score's own top decile — a badge stock must outrank
    # any pure-score stock. max() keeps ordering within each tier quality-driven.
    if readiness["signal"] == "high_conviction":
        readiness["conviction"] = max(readiness["conviction"], 90)
    elif readiness["signal"] == "strong_breakout":
        readiness["conviction"] = max(readiness["conviction"], 80)

    # Named chart pattern (real geometry — see patterns.py)
    pattern = detect_pattern(df)

    # Historical analog: the past bar on this stock most similar to today, and what
    # happened next — the evidence behind "The Read" (see analogs.py).
    analog = detect_analog(df)

    # Displayed support/resistance zones (levels.py) — the trader-style horizontal
    # levels drawn on the chart + shown in the Key Levels card. These are *distinct*
    # from the rolling `resistance` above (an internal is_breakout input): they're
    # swing-pivot clusters price has actually reversed at (3-point rule), with a
    # rising-EMA dynamic-support fallback for stocks extended above their structure.
    levels = resolve_display_levels(df)

    # Plain-English guidance derived from the computed state
    cur = settings.CURRENCY_SYMBOL
    if resistance:
        trigger = (f"Watch for a close above {cur}{resistance:,.2f} on above-average volume "
                   f"to confirm a breakout.")
        suggested_entry = f"{cur}{resistance:,.2f}+ (breakout close on volume)"
        stop = round(resistance * settings.STOP_LOSS_FRACTION, 2)
        stop_loss = f"{cur}{stop:,.2f} (~-6% below the trigger)"
    else:
        trigger = "Not enough history to define a clear resistance level yet."
        suggested_entry = "—"
        stop_loss = "—"

    return {
        "symbol": symbol,
        "name": meta.get("name", symbol),
        "sector": meta.get("sector", ""),
        "industry": meta.get("industry", ""),
        "exchange": meta.get("exchange", ""),
        "as_of": latest["date"].strftime("%Y-%m-%d"),
        "price": price,
        "change_pct": change_pct,
        "ema_stack": ema_stack,
        "adx": {"value": round(adx_val, 1) if adx_val else None, "label": _adx_label(adx_val)},
        "resistance": {"level": round(resistance, 2) if resistance else None,
                       "distance_pct": dist_pct, "touches": touches},
        "volume": volume_today,
        "levels": levels,
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
