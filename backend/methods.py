"""
Alternative breakout-detection methods (B-H), for backtesting against the existing
Method A (find_breakouts.add_indicators's `is_breakout` — Donchian/Minervini) — see
analyze_reliability.py, which grades all of them against the same followthrough/
r_multiple outcome rule so the comparison is apples-to-apples.

Research-only for B, C, D, D2, F, G, H — nothing calls those from run_scan.py or the
served site. E and E2 (`add_method_e_relative_strength` / `add_method_e2_relative_
strength_uptrend`) are the exception: after backtesting confirmed E2 (E masked by the
`uptrend` column) matches E's accuracy at no cost, `run_scan.py` now calls them
directly (not via `add_all_methods()`, to avoid computing the other, unshipped
methods) to feed a production readiness tier in `find_breakouts.build_summary()`.

G and H are PRE-breakout methods (requested 2026-07-06 for the US market) — they're
meant to fire BEFORE a price-level break, unlike A-F which all trigger at/after one.
G is a comprehensive composite (Minervini Trend Template + CAN SLIM RS + VCP +
institutional-accumulation volume reads); H is the "Pressure Cooker" score, a
narrower composite of the specific behaviors discretionary momentum traders describe
seeing right before the strongest breakouts. Both attach a continuous 0-100 score
column (`pre_breakout_score_g` / `pressure_cooker_score_h`) in addition to their
boolean trigger, so analyze_reliability.py can test the score as a graded feature too,
not just the boolean fire. G2 (`add_method_g2_pre_breakout_retuned`) is a retune of G
built after G's own whole-market backtest showed its "tighter is better" weighting
fighting the data (see its docstring) — same shared `_add_method_g_impl`, different
base-depth shape and category weights, so G vs G2 can be compared directly.

`add_existing_high_conviction_tiers` replicates the ALREADY-SHIPPED US
`high_conviction`/`strong_breakout` tiers (find_breakouts.build_summary) as boolean
columns purely for backtest comparison — not a new method, a vectorized port of the
production rule, so the harness can check whether G/G2/H add anything on top of what's
already live.

Each `add_method_*` function takes a df that already has `add_indicators()` run on it
(needs its EMA/ADX/resistance columns) and returns it with one more boolean trigger
column added. `add_all_methods()` runs everything above and is the one entry point
analyze_reliability.py needs.
"""
from __future__ import annotations
from datetime import date, timedelta

import numpy as np
import pandas as pd

import settings
from patterns import find_pivots


# --------------------------------------------------------------------------- #
# B — true multi-leg VCP: a sequence of progressively smaller pivot-high-to-trough
# contractions, each on declining volume, then a break above the final pivot high.
# --------------------------------------------------------------------------- #
def add_method_b_vcp(df: pd.DataFrame) -> pd.DataFrame:
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    volumes = df["volume"].values
    n = len(df)
    trigger = np.zeros(n, dtype=bool)

    ph, _ = find_pivots(highs, lows, k=settings.VCP_PIVOT_K)
    min_legs = settings.VCP_MIN_LEGS
    max_legs = settings.VCP_MAX_LOOKBACK_LEGS

    for m in range(min_legs, len(ph)):
        start = max(0, m - max_legs)
        window_idx = ph[start:m + 1]  # consecutive pivot highs, oldest -> newest
        legs = []
        for a, b in zip(window_idx, window_idx[1:]):
            seg_low = lows[a:b + 1].min()
            depth_pct = (highs[a] - seg_low) / highs[a] * 100.0
            seg_vol = volumes[a:b + 1].mean()
            legs.append((depth_pct, seg_vol))
        if len(legs) < min_legs:
            continue
        depths = [l[0] for l in legs]
        vols = [l[1] for l in legs]
        contracting = all(depths[i + 1] < depths[i] for i in range(len(depths) - 1))
        vol_declining = all(vols[i + 1] <= vols[i] for i in range(len(vols) - 1))
        if not (contracting and vol_declining):
            continue

        idx_end = window_idx[-1]
        pivot_level = highs[idx_end]
        avg_vol_before = volumes[max(0, idx_end - 20):idx_end].mean() if idx_end > 0 else np.nan
        search_end = min(n, idx_end + 1 + settings.VCP_BREAKOUT_SEARCH_DAYS)
        for t in range(idx_end + 1, search_end):
            if closes[t] > pivot_level and (np.isnan(avg_vol_before)
                                             or volumes[t] > avg_vol_before * settings.VCP_VOL_CONFIRM_MULT):
                trigger[t] = True
                break

    df = df.copy()
    df["is_breakout_b"] = trigger
    return df


def latest_vcp_structure(df: pd.DataFrame) -> dict | None:
    """Chart support: the most recent qualifying multi-leg VCP contraction, as drawable
    geometry for the annotated chart (export_ohlc.py). Mirrors add_method_b_vcp's
    window qualification EXACTLY (same settings, same contracting-depth + declining-
    volume rules) but returns the structure instead of stamping trigger days:

        {"pivots":  [int, ...],   # row positions of the consecutive pivot highs
         "troughs": [int, ...],   # row position of each leg's low (len = legs)
         "pivot_level": float,    # final pivot high = the buy point
         "confirmed": int|None}   # position of the volume-confirmed close above it

    Positions are 0-based row offsets into df's date order, so callers map them to
    dates with .iloc. Returns None when no window qualifies. If the qualification
    rules in add_method_b_vcp ever change, keep this in sync —
    scratchpad/verify_vcp_structure.py cross-checks the two on real DuckDB data.
    """
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    volumes = df["volume"].values
    n = len(df)

    ph, _ = find_pivots(highs, lows, k=settings.VCP_PIVOT_K)
    min_legs = settings.VCP_MIN_LEGS
    max_legs = settings.VCP_MAX_LOOKBACK_LEGS

    best = None
    for m in range(min_legs, len(ph)):
        start = max(0, m - max_legs)
        window_idx = ph[start:m + 1]  # consecutive pivot highs, oldest -> newest
        legs = []
        for a, b in zip(window_idx, window_idx[1:]):
            trough = a + int(np.argmin(lows[a:b + 1]))
            depth_pct = (highs[a] - lows[trough]) / highs[a] * 100.0
            seg_vol = volumes[a:b + 1].mean()
            legs.append((depth_pct, seg_vol, trough))
        if len(legs) < min_legs:
            continue
        depths = [l[0] for l in legs]
        vols = [l[1] for l in legs]
        contracting = all(depths[i + 1] < depths[i] for i in range(len(depths) - 1))
        vol_declining = all(vols[i + 1] <= vols[i] for i in range(len(vols) - 1))
        if not (contracting and vol_declining):
            continue

        idx_end = window_idx[-1]
        pivot_level = highs[idx_end]
        avg_vol_before = volumes[max(0, idx_end - 20):idx_end].mean() if idx_end > 0 else np.nan
        confirmed = None
        search_end = min(n, idx_end + 1 + settings.VCP_BREAKOUT_SEARCH_DAYS)
        for t in range(idx_end + 1, search_end):
            if closes[t] > pivot_level and (np.isnan(avg_vol_before)
                                             or volumes[t] > avg_vol_before * settings.VCP_VOL_CONFIRM_MULT):
                confirmed = t
                break
        # ph is ascending, so later m = more recent structure; keep the last qualifier
        best = {"pivots": list(window_idx), "troughs": [l[2] for l in legs],
                "pivot_level": float(pivot_level), "confirmed": confirmed}
    return best


# --------------------------------------------------------------------------- #
# C — volatility-squeeze breakout: Bollinger Band width compresses to a multi-month
# low, then expands with a directional, volume-confirmed close.
# --------------------------------------------------------------------------- #
def add_method_c_squeeze(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    mid = df["close"].rolling(settings.SQUEEZE_BB_WINDOW).mean()
    std = df["close"].rolling(settings.SQUEEZE_BB_WINDOW).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    width = (upper - lower) / mid

    roll_min = width.rolling(settings.SQUEEZE_RANGE_LOOKBACK).min()
    roll_max = width.rolling(settings.SQUEEZE_RANGE_LOOKBACK).max()
    span = (roll_max - roll_min).replace(0, np.nan)
    position = (width - roll_min) / span  # 0 = tightest in range, 1 = widest

    is_squeeze = position <= settings.SQUEEZE_POSITION_MAX
    squeeze_recent = is_squeeze.rolling(settings.SQUEEZE_CONFIRM_DAYS).max().astype(bool).shift(1)

    avg_vol = df["volume"].rolling(settings.VOL_AVG_WINDOW).mean().shift(1)
    vol_confirmed = df["volume"] > avg_vol * settings.SQUEEZE_VOL_CONFIRM_MULT
    expansion = df["close"] > upper.shift(1)

    df["bb_width"] = width
    df["is_breakout_c"] = (squeeze_recent.fillna(False) & expansion & vol_confirmed).fillna(False)
    return df


# --------------------------------------------------------------------------- #
# D — trend-inception / momentum: +DI crosses above -DI while ADX is rising through
# a threshold and the EMA stack is aligned. No price level (resistance) involved.
# --------------------------------------------------------------------------- #
def add_method_d_trend_inception(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    di_cross_up = (df["plus_di"] > df["minus_di"]) & (df["plus_di"].shift(1) <= df["minus_di"].shift(1))
    adx_rising = df["adx"] > df["adx"].shift(settings.DI_ADX_RISING_LOOKBACK)
    adx_strong = df["adx"] >= settings.DI_ADX_THRESHOLD
    stack_aligned = (df["ema8"] > df["ema21"]) & (df["ema21"] > df["ema50"]) & (df["ema50"] > df["ema200"])
    df["is_breakout_d"] = (di_cross_up & adx_rising & adx_strong & stack_aligned).fillna(False)
    return df


def add_method_d2_trend_inception_loose(df: pd.DataFrame) -> pd.DataFrame:
    """Same 'DI just crossed up' inception idea as Method D, but loosened: a lower ADX
    bar (settings.DI_ADX_THRESHOLD_LOOSE) and the broader `uptrend` filter already
    computed in add_indicators, instead of requiring the full 4-EMA stack in perfect
    order. Tests whether D's edge survives with a bigger sample."""
    df = df.copy()
    di_cross_up = (df["plus_di"] > df["minus_di"]) & (df["plus_di"].shift(1) <= df["minus_di"].shift(1))
    adx_rising = df["adx"] > df["adx"].shift(settings.DI_ADX_RISING_LOOKBACK)
    adx_ok = df["adx"] >= settings.DI_ADX_THRESHOLD_LOOSE
    df["is_breakout_d_loose"] = (di_cross_up & adx_rising & adx_ok & df["uptrend"]).fillna(False)
    return df


# --------------------------------------------------------------------------- #
# E — relative-strength breakout: stock-price / Nifty ratio line makes a new N-day
# high, independent of the stock's own absolute chart (classic IBD-style "RS line").
# --------------------------------------------------------------------------- #
def fetch_benchmark(ticker: str | None = None, years: int | None = None) -> pd.DataFrame | None:
    """Fetch the benchmark index once per run (shared across every stock)."""
    import yfinance as yf
    ticker = ticker or settings.RS_BENCHMARK
    years = years or settings.HISTORY_YEARS
    start = (date.today() - timedelta(days=int(years * 365.25) + 5)).isoformat()
    df = yf.download(ticker, start=start, interval="1d", auto_adjust=True, progress=False)
    if df is None or len(df) == 0:
        return None
    df = df.reset_index()
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.rename(columns={"Date": "date", "Close": "close"})
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    return df[["date", "close"]].rename(columns={"close": "bm_close"}).sort_values("date").reset_index(drop=True)


def add_method_e_relative_strength(df: pd.DataFrame, benchmark: pd.DataFrame | None) -> pd.DataFrame:
    df = df.copy()
    if benchmark is None or len(benchmark) == 0:
        df["is_breakout_e"] = False
        return df
    merged = df.merge(benchmark, on="date", how="left").sort_values("date").reset_index(drop=True)
    merged["bm_close"] = merged["bm_close"].ffill()
    rs_ratio = merged["close"] / merged["bm_close"]
    rs_prior_high = rs_ratio.rolling(settings.RS_LOOKBACK).max().shift(1)
    df["rs_ratio"] = rs_ratio.values
    df["is_breakout_e"] = (rs_ratio > rs_prior_high).fillna(False).values
    return df


def add_method_e2_relative_strength_uptrend(df: pd.DataFrame) -> pd.DataFrame:
    """Same RS-line-new-high trigger as Method E, but additionally gated on the
    stock already being in an uptrend (the same `uptrend` column Method A requires).
    Built to test whether E's edge survives adding this gate, before deciding whether
    a production 'RS breakout' readiness tier should carry it too."""
    df = df.copy()
    if "rs_ratio" not in df.columns or "is_breakout_e" not in df.columns:
        df["is_breakout_e2"] = False
        return df
    df["is_breakout_e2"] = (df["is_breakout_e"] & df["uptrend"]).fillna(False)
    return df


# --------------------------------------------------------------------------- #
# F — episodic pivot: a massive gap up on extreme volume — the TECHNICAL PROXY for a
# fundamental-catalyst move (e.g. an earnings surprise). This only tests the gap +
# volume shock; confirming it against an actual earnings/catalyst calendar needs a new
# data source this pipeline doesn't have yet (see settings.py's note on this method).
# --------------------------------------------------------------------------- #
def add_method_f_episodic_pivot(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    prev_close = df["close"].shift(1)
    gap_pct = (df["open"] - prev_close) / prev_close * 100.0
    avg_vol_50 = df["volume"].rolling(settings.EP_VOL_AVG_WINDOW).mean().shift(1)
    vol_ratio = df["volume"] / avg_vol_50

    df["ep_gap_pct"] = gap_pct
    df["ep_vol_ratio"] = vol_ratio
    df["is_breakout_f"] = ((gap_pct >= settings.EP_MIN_GAP_PCT)
                            & (vol_ratio >= settings.EP_MIN_VOL_MULT)).fillna(False)
    return df


# --------------------------------------------------------------------------- #
# Shared scoring helpers for G/H (piecewise-linear 0-100 scores, so every sub-signal
# below is expressed the same way instead of one-off clipping logic per line).
# --------------------------------------------------------------------------- #
def _band_score(x: pd.Series, lo: float, hi: float, feather: float) -> pd.Series:
    """100 anywhere inside [lo, hi], decaying linearly to 0 over `feather` beyond
    either edge. For metrics with an IDEAL RANGE (e.g. base depth, distance to
    resistance) rather than a monotonic "more/less is better" relationship."""
    x = x.astype(float)
    inside = (x >= lo) & (x <= hi)
    below = np.clip(100 * (1 - (lo - x) / feather), 0, 100)
    above = np.clip(100 * (1 - (x - hi) / feather), 0, 100)
    return pd.Series(np.where(inside, 100.0, np.where(x < lo, below, above)), index=x.index)


def _ramp_score(x: pd.Series, lo: float, hi: float) -> pd.Series:
    """0 at/below `lo`, 100 at/above `hi`, linear in between. For monotonic
    "more is better" (or, with lo>hi, "less is better") metrics."""
    x = x.astype(float)
    return pd.Series(np.clip((x - lo) / (hi - lo) * 100, 0, 100), index=x.index)


def _common_exclusions(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Shared hard-exclusion gates for G and H (settings.py's section 10 "exclusions"
    list): already extended past the pivot, illiquid, too erratic to be a coil, a
    gap+volume shock that looks news-driven rather than organic, or already up a huge
    amount in the last 6 months. Returns (excluded, ext_pct) -- ext_pct is reused by
    G's readiness sub-score so it isn't computed twice."""
    close = df["close"]
    ext_pct = (close / df["resistance"] - 1) * 100
    already_extended = ext_pct > settings.G_MAX_EXTENSION_PCT
    illiquid = (df["avg_vol"] < settings.HC_MIN_AVG_VOL_SHARES) | (close < settings.HC_MIN_PRICE)
    atr_pct = df["atr_short"] / close * 100
    too_volatile = atr_pct > settings.G_ATR_CEILING_PCT
    # Technical proxy for a news-driven spike (no historical news feed to check
    # against) -- reuses Method F's own gap/volume thresholds.
    prev_close = close.shift(1)
    gap_pct = (df["open"] - prev_close) / prev_close * 100
    news_spike = (gap_pct >= settings.EP_MIN_GAP_PCT) & (df["volume"] / df["avg_vol"] >= settings.EP_MIN_VOL_MULT)
    already_ran = (close / close.shift(126) - 1) * 100 >= settings.G_MAX_6M_RUN_PCT
    excluded = (already_extended | illiquid | too_volatile | news_spike | already_ran).fillna(False)
    return excluded, ext_pct


# --------------------------------------------------------------------------- #
# G — comprehensive PRE-breakout composite: Minervini Trend Template + CAN SLIM-style
# relative strength + VCP volatility contraction + institutional-accumulation volume
# reads, blended into one 0-100 readiness score. Unlike A-F above, which all trigger
# AT or AFTER a price-level break, this is meant to fire BEFORE one -- graded by the
# exact same followthrough/r_multiple rule as everything else (entry = the day the
# composite fires, so an early "primed" read only gets credit if the eventual move
# actually pays off within FOLLOWTHROUGH_WINDOW days; this is a fair test since
# resistance/stop/target are already computed for every bar, not just breakout bars).
#
# Deliberately excluded from the score: sector strength and fundamentals (spec
# sections 7-8) -- see settings.py's comment above G_SMA_SHORT for why (no
# point-in-time history cached for either, only current snapshots). A true
# cross-sectional "RS Rank >85 vs the whole universe" (IBD-style) is also NOT
# implemented -- that needs a two-pass whole-market computation (rank every stock
# against every other stock on the same date), which is a bigger lift than this
# exploration's scope; RS here is measured vs the benchmark only (3/6/12-month
# outperformance + acceleration + a new relative-strength high, reusing Method E's
# already-validated "RS line new high" idea). See the write-up for what a follow-up
# pass would add.
# --------------------------------------------------------------------------- #
def _add_method_g_impl(df: pd.DataFrame, benchmark: pd.DataFrame | None, *,
                        depth_ramp: bool, depth_lo: float, depth_hi: float, depth_feather: float,
                        weights: dict, score_col: str, trigger_col: str, fire_threshold: float,
                        prefix: str) -> pd.DataFrame:
    """Shared implementation behind G and G2 -- same detection logic, different base-
    depth scoring shape and category weights (see add_method_g2_pre_breakout_retuned's
    docstring for why). Parameterized rather than duplicated so a future retune only
    needs a new set of arguments, not a second 120-line copy."""
    df = df.copy()
    W = settings.G_BASE_WINDOW
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]

    # --- 1. Trend filter (Minervini Trend Template): hard gate + partial credit ---
    sma50 = close.rolling(settings.G_SMA_SHORT).mean()
    sma150 = close.rolling(settings.G_SMA_MID).mean()
    sma200 = close.rolling(settings.G_SMA_LONG).mean()
    low_52w = low.rolling(252, min_periods=50).min()
    pct_above_52w_low = (close / low_52w - 1) * 100
    trend_conditions = [
        close > sma50,
        close > sma150,
        close > sma200,
        (sma50 > sma150) & (sma150 > sma200),
        sma200 > sma200.shift(settings.G_SMA_LONG_RISING_LOOKBACK),
        df["dist_from_52w_high"] <= settings.G_MAX_DIST_FROM_52W_HIGH,
        pct_above_52w_low >= settings.G_MIN_DIST_FROM_52W_LOW,
    ]
    trend_pass = trend_conditions[0]
    for c in trend_conditions[1:]:
        trend_pass = trend_pass & c
    trend_pass = trend_pass.fillna(False)
    trend_score = sum(c.astype(float) for c in trend_conditions) / len(trend_conditions) * 100

    # --- 2. Base quality: depth, higher lows, support consistency ---
    win_high, win_low = high.rolling(W).max(), low.rolling(W).min()
    base_depth_pct = (win_low / win_high - 1) * 100  # negative
    if depth_ramp:
        # Deeper = monotonically better (up to the cap) -- see G2's docstring: the
        # data shows no "too deep" penalty within observed ranges, unlike G's original
        # ideal-band assumption.
        depth_score = _ramp_score(-base_depth_pct, lo=depth_lo, hi=depth_hi)
    else:
        depth_score = _band_score(base_depth_pct, lo=depth_lo, hi=depth_hi, feather=depth_feather)
    half = max(W // 2, 5)
    recent_min, prior_min = low.rolling(half).min(), low.rolling(half).min().shift(half)
    higher_low_pct = (recent_min / prior_min - 1) * 100
    higher_lows_score = _ramp_score(higher_low_pct, lo=-2, hi=3)
    roll_min5 = low.rolling(5).min()
    support_cv = roll_min5.rolling(W).std() / roll_min5.rolling(W).mean().abs()
    consistency_score = _ramp_score(-support_cv, lo=-0.20, hi=-0.03)
    base_score = (depth_score + higher_lows_score + consistency_score) / 3

    # --- 3. Volatility contraction: ATR ratio, ATR trend, BB width, daily range ---
    atr_ratio_score = _ramp_score(-df["vol_contraction"], lo=-1.0, hi=-0.5)
    atr_decline_pct = (df["atr_short"].shift(W) - df["atr_short"]) / df["atr_short"].shift(W) * 100
    atr_declining_score = _ramp_score(atr_decline_pct, lo=0, hi=30)
    bb_mid = close.rolling(settings.SQUEEZE_BB_WINDOW).mean()
    bb_std = close.rolling(settings.SQUEEZE_BB_WINDOW).std()
    bb_width = 4 * bb_std / bb_mid
    bb_roll_min = bb_width.rolling(settings.SQUEEZE_RANGE_LOOKBACK).min()
    bb_roll_max = bb_width.rolling(settings.SQUEEZE_RANGE_LOOKBACK).max()
    bb_position = (bb_width - bb_roll_min) / (bb_roll_max - bb_roll_min).replace(0, np.nan)
    bb_score = ((1 - bb_position) * 100).clip(0, 100)
    daily_range = (high - low) / close
    range_shrink_pct = (daily_range.rolling(W).mean() - daily_range.rolling(10).mean()) / daily_range.rolling(W).mean() * 100
    range_score = _ramp_score(range_shrink_pct, lo=0, hi=40)
    vol_score = (atr_ratio_score.fillna(0) + atr_declining_score.fillna(0)
                 + bb_score.fillna(0) + range_score.fillna(0)) / 4

    # --- 4. Volume behavior: dry-up, up/down volume ratio, accumulation vs distribution days ---
    long_avg_vol = volume.rolling(100, min_periods=30).mean()
    dryup_pct_below = (1 - df["avg_vol"] / long_avg_vol) * 100
    dryup_score = _ramp_score(dryup_pct_below, lo=0, hi=40)
    up_day = close > close.shift(1)
    up_vol = volume.where(up_day).rolling(W, min_periods=5).mean()
    down_vol = volume.where(~up_day).rolling(W, min_periods=5).mean()
    updown_ratio_score = _ramp_score(up_vol / down_vol, lo=0.9, hi=1.4)
    accum_day = up_day & (volume > df["avg_vol"])
    dist_day = (~up_day) & (volume > df["avg_vol"] * settings.G_DISTRIBUTION_VOL_MULT)
    net_days = accum_day.rolling(W).sum() - dist_day.rolling(W).sum()
    net_days_score = _ramp_score(net_days, lo=-5, hi=8)
    volume_score = (dryup_score.fillna(0) + updown_ratio_score.fillna(0) + net_days_score.fillna(0)) / 3

    # --- 5. Price action: tight range, small bodies, few gap-downs, respects 21 EMA, small upper wicks ---
    body = (close - df["open"]).abs() / close
    hist_range = daily_range.rolling(150, min_periods=60).mean()
    tight_pct = (hist_range - daily_range.rolling(10).mean()) / hist_range * 100
    tight_score = _ramp_score(tight_pct, lo=0, hi=35)
    hist_body = body.rolling(150, min_periods=60).mean()
    body_pct = (hist_body - body.rolling(10).mean()) / hist_body * 100
    body_score = _ramp_score(body_pct, lo=0, hi=35)
    gap_down = df["open"] < close.shift(1) * 0.98
    gap_score = _ramp_score(-gap_down.rolling(W).sum(), lo=-5, hi=0)
    ema_violation = close < df["ema21"] * 0.99
    ema_score = _ramp_score(-(ema_violation.rolling(W).sum() / W * 100), lo=-25, hi=0)
    upper_wick = (high - pd.concat([df["open"], close], axis=1).max(axis=1)) / (high - low).replace(0, np.nan)
    wick_score = _ramp_score(-(upper_wick.rolling(W).mean() * 100), lo=-50, hi=-10)
    price_score = (tight_score.fillna(0) + body_score.fillna(0) + gap_score.fillna(0)
                   + ema_score.fillna(0) + wick_score.fillna(0)) / 5

    # --- 6. Relative strength vs benchmark: outperformance, acceleration, RS-line new high ---
    rs_ratio = df["rs_ratio"] if "rs_ratio" in df.columns else None
    if rs_ratio is None and benchmark is not None and len(benchmark):
        merged = df.merge(benchmark, on="date", how="left").sort_values("date").reset_index(drop=True)
        merged["bm_close"] = merged["bm_close"].ffill()
        rs_ratio = merged["close"] / merged["bm_close"]
        rs_ratio.index = df.index
    if rs_ratio is not None:
        mom3 = rs_ratio / rs_ratio.shift(63) - 1
        mom6 = rs_ratio / rs_ratio.shift(126) - 1
        mom12 = rs_ratio / rs_ratio.shift(252) - 1
        outperform_score = ((mom3 > 0).astype(float) + (mom6 > 0).astype(float) + (mom12 > 0).astype(float)) / 3 * 100
        accel_score = (((mom3 / 3) > (mom6 / 6)).astype(float) + ((mom6 / 6) > (mom12 / 12)).astype(float)) / 2 * 100
        rs_prior_high = rs_ratio.rolling(settings.RS_LOOKBACK).max().shift(1)
        rs_high_score = _band_score((rs_ratio / rs_prior_high - 1) * 100, lo=-5, hi=0, feather=5)
        rs_score = (outperform_score.fillna(50) + accel_score.fillna(50) + rs_high_score.fillna(50)) / 3
    else:
        rs_score = pd.Series(50.0, index=df.index)  # neutral: no benchmark available

    # --- 7. Breakout readiness: proximity to resistance, touch count, failed attempts ---
    excluded, ext_pct = _common_exclusions(df)
    proximity_score = _band_score(ext_pct, lo=-3, hi=0, feather=10)
    near_resistance = (high >= df["resistance"] * (1 - settings.G_RESISTANCE_TOUCH_PCT / 100)) & \
                       (high <= df["resistance"] * 1.02)
    touches_score = _ramp_score(near_resistance.rolling(W).sum(), lo=0, hi=4)
    failed_attempt = near_resistance & (close < df["resistance"])
    failed_score = _ramp_score(failed_attempt.rolling(W).sum(), lo=0, hi=3)
    readiness_score = (proximity_score.fillna(0) + touches_score.fillna(0) + failed_score.fillna(0)) / 3

    composite = (trend_score * weights["trend"] + base_score * weights["base"]
                 + vol_score * weights["volatility"] + volume_score * weights["volume"]
                 + price_score * weights["price"] + rs_score * weights["rs"]
                 + readiness_score * weights["readiness"]) / 100

    df[score_col] = composite
    df[trigger_col] = (trend_pass & ~excluded & (composite >= fire_threshold)).fillna(False)
    # Per-component scores + gate/exclusion flags, kept as columns (not just locals)
    # so explain_g_score()/explain_g2_score() below can report WHY a stock scored the
    # way it did -- the "individual component scores / reasons / weaknesses" the spec
    # asked for.
    df[f"{prefix}_trend_pass"] = trend_pass
    df[f"{prefix}_excluded"] = excluded
    df[f"{prefix}_score_trend"] = trend_score
    df[f"{prefix}_score_base"] = base_score
    df[f"{prefix}_score_volatility"] = vol_score
    df[f"{prefix}_score_volume"] = volume_score
    df[f"{prefix}_score_price"] = price_score
    df[f"{prefix}_score_rs"] = rs_score
    df[f"{prefix}_score_readiness"] = readiness_score
    return df


def add_method_g_pre_breakout(df: pd.DataFrame, benchmark: pd.DataFrame | None = None) -> pd.DataFrame:
    return _add_method_g_impl(
        df, benchmark, depth_ramp=False, depth_lo=-35, depth_hi=-10, depth_feather=15,
        weights={"trend": settings.G_W_TREND, "base": settings.G_W_BASE,
                 "volatility": settings.G_W_VOLATILITY, "volume": settings.G_W_VOLUME,
                 "price": settings.G_W_PRICE, "rs": settings.G_W_RS, "readiness": settings.G_W_READINESS},
        score_col="pre_breakout_score_g", trigger_col="is_breakout_g",
        fire_threshold=settings.G_FIRE_THRESHOLD, prefix="g")


def add_method_g2_pre_breakout_retuned(df: pd.DataFrame, benchmark: pd.DataFrame | None = None) -> pd.DataFrame:
    """Retuned G, built after the whole-market US backtest of G (2026-07-06) showed
    two things fighting the original composite's own design:
    (1) volatility CONTRACTION is counterintuitively NEGATIVE in this data (tightest-
        ATR tercile 23.5% follow-through vs loosest-ATR tercile 30.4% -- the same
        direction CLAUDE.md already documents for India). G originally put 17% of
        the composite's weight on "tighter is better." G2 cuts that to 5 rather than
        zeroing it -- the marginal pooled effect is negative, but an interaction
        effect (contraction combined with a genuinely strong base) may still exist,
        and one backtest run isn't grounds to delete the idea outright.
    (2) base depth is the single strongest, most MONOTONIC feature measured anywhere
        in this project (39.1% vs 14.7% across terciles) -- deeper kept winning even
        past G's original "-35% is deep enough" band edge. G2 replaces the band with
        a ramp (deeper is always better, capped at -55%) and raises the base-quality
        weight from 17 to 30, the biggest single change here.
    The freed-up weight also nudges relative strength up slightly (11->13, one of
    the most consistently validated ideas in this project via Method E/E2)."""
    return _add_method_g_impl(
        df, benchmark, depth_ramp=True, depth_lo=15, depth_hi=55, depth_feather=0,
        weights={"trend": 22, "base": 30, "volatility": 5, "volume": 17, "price": 8, "rs": 13, "readiness": 5},
        score_col="pre_breakout_score_g2", trigger_col="is_breakout_g2",
        fire_threshold=settings.G_FIRE_THRESHOLD, prefix="g2")


def _component_labels(prefix: str) -> dict:
    return {
        f"{prefix}_score_trend": "Trend (Minervini template)",
        f"{prefix}_score_base": "Base quality",
        f"{prefix}_score_volatility": "Volatility contraction",
        f"{prefix}_score_volume": "Volume behavior",
        f"{prefix}_score_price": "Price action",
        f"{prefix}_score_rs": "Relative strength",
        f"{prefix}_score_readiness": "Breakout readiness / proximity to resistance",
    }


G_COMPONENT_LABELS = _component_labels("g")
G2_COMPONENT_LABELS = _component_labels("g2")
G_EXCLUSION_REASON = ("Excluded: already extended past the pivot, illiquid, too erratic, "
                       "a likely news-driven spike, or already up too much in 6 months.")


def _explain_g_family(latest: pd.Series, *, prefix: str, score_col: str, labels: dict) -> dict:
    components = {label: round(float(latest[col]), 1) for col, label in labels.items()}
    final = round(float(latest[score_col]), 1)
    trend_pass = bool(latest.get(f"{prefix}_trend_pass", False))
    excluded = bool(latest.get(f"{prefix}_excluded", False))
    reasons = [f"{label} ({v:.0f}/100)" for label, v in components.items() if v >= 65]
    weaknesses = [f"{label} ({v:.0f}/100)" for label, v in components.items() if v < 50]
    if not trend_pass:
        weaknesses.insert(0, "Fails the mandatory Minervini trend filter (not all 7 conditions hold)")
    if excluded:
        weaknesses.insert(0, G_EXCLUSION_REASON)
    n_strong = sum(1 for v in components.values() if v >= 70)
    if not trend_pass or excluded:
        confidence = "Low"
    elif final >= 80 and n_strong >= 5:
        confidence = "High"
    elif final >= 60:
        confidence = "Medium"
    else:
        confidence = "Low"
    return {
        "final_score": final,
        "trend_filter_passed": trend_pass,
        "excluded": excluded,
        "component_scores": components,
        "reasons": reasons or ["No component is currently a standout strength."],
        "weaknesses": weaknesses or ["No major weaknesses flagged."],
        "confidence": confidence,
    }


def explain_g_score(latest: pd.Series) -> dict:
    """The section-11 output contract for Method G, applied to one row (typically
    `df.iloc[-1]` for "today"): final score, named component scores, plain-language
    reasons/weaknesses, and a confidence level."""
    return _explain_g_family(latest, prefix="g", score_col="pre_breakout_score_g", labels=G_COMPONENT_LABELS)


def explain_g2_score(latest: pd.Series) -> dict:
    """Same output contract as explain_g_score, for the retuned G2."""
    return _explain_g_family(latest, prefix="g2", score_col="pre_breakout_score_g2", labels=G2_COMPONENT_LABELS)


# --------------------------------------------------------------------------- #
# H — "Pressure Cooker" score: quantifies the specific behaviors discretionary
# momentum traders describe seeing right before the strongest breakouts (contracting
# weekly range, falling ATR, unusually dry volume, higher lows, repeated resistance
# tests without breaking down, closes in the upper half of the daily range, seller
# exhaustion). An unweighted average of those seven reads -- the user didn't specify
# relative weights for this one, and there's no backtested basis yet to prefer any
# sub-signal over another. Gated on `uptrend` from the start (see settings.py's note
# on this method: Method E2 already showed this costs ~0 accuracy).
# --------------------------------------------------------------------------- #
def add_method_h_pressure_cooker(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    W = settings.H_WINDOW
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]

    # 1. Weekly range contracting
    week_range = high.rolling(5).max() - low.rolling(5).min()
    range_contract_pct = (week_range.shift(5) - week_range) / week_range.shift(5) * 100
    range_score = _ramp_score(range_contract_pct, lo=0, hi=40)

    # 2. ATR keeps falling
    atr_decline_pct = (df["atr_short"].shift(W) - df["atr_short"]) / df["atr_short"].shift(W) * 100
    atr_score = _ramp_score(atr_decline_pct, lo=0, hi=30)

    # 3. Volume dries up to unusually low levels (vs its own longer-term average)
    long_avg_vol = volume.rolling(100, min_periods=30).mean()
    dryup_pct_below = (1 - df["avg_vol"] / long_avg_vol) * 100
    dryup_score = _ramp_score(dryup_pct_below, lo=0, hi=40)

    # 4. Higher lows
    half = max(W // 2, 5)
    recent_min, prior_min = low.rolling(half).min(), low.rolling(half).min().shift(half)
    higher_lows_score = _ramp_score((recent_min / prior_min - 1) * 100, lo=-2, hi=3)

    # 5. Resistance tested multiple times without breaking down
    near_resistance = (high >= df["resistance"] * (1 - settings.G_RESISTANCE_TOUCH_PCT / 100)) & \
                       (high <= df["resistance"] * 1.02)
    touches_score = _ramp_score(near_resistance.rolling(W).sum(), lo=0, hi=3)

    # 6. Closes in the upper half of the daily range
    range_position = (close - low) / (high - low).replace(0, np.nan)
    upper_half_score = (range_position > 0.5).astype(float).rolling(W).mean() * 100

    # 7. Seller exhaustion: little downside follow-through after down days. Uses
    # tomorrow's return, so it's shifted by 1 extra bar -- today's score may only use
    # information available through YESTERDAY's close (the last down-day counted used
    # today's own close for its "next day" return, which is legitimately known by now).
    down_day = close < close.shift(1)
    next_ret = close.shift(-1) / close - 1
    exhaustion_raw = next_ret.where(down_day).rolling(W, min_periods=3).mean()
    exhaustion_pct = exhaustion_raw.shift(1) * 100
    exhaustion_score = _ramp_score(exhaustion_pct, lo=-2.0, hi=2.0)

    composite = (range_score.fillna(0) + atr_score.fillna(0) + dryup_score.fillna(0)
                 + higher_lows_score.fillna(0) + touches_score.fillna(0)
                 + upper_half_score.fillna(0) + exhaustion_score.fillna(50)) / 7

    excluded, _ = _common_exclusions(df)
    df["pressure_cooker_score_h"] = composite
    df["is_breakout_h"] = (df["uptrend"].fillna(False) & ~excluded & (composite >= settings.H_FIRE_THRESHOLD)).fillna(False)
    # Per-component scores + gate/exclusion flags -- same reporting contract as G.
    df["h_uptrend"] = df["uptrend"]
    df["h_excluded"] = excluded
    df["h_score_range_contraction"] = range_score
    df["h_score_atr_declining"] = atr_score
    df["h_score_volume_dryup"] = dryup_score
    df["h_score_higher_lows"] = higher_lows_score
    df["h_score_resistance_tests"] = touches_score
    df["h_score_upper_half_close"] = upper_half_score
    df["h_score_seller_exhaustion"] = exhaustion_score
    return df


H_COMPONENT_LABELS = {
    "h_score_range_contraction": "Weekly range contracting",
    "h_score_atr_declining": "ATR declining",
    "h_score_volume_dryup": "Volume drying up",
    "h_score_higher_lows": "Higher lows",
    "h_score_resistance_tests": "Resistance tested without breaking down",
    "h_score_upper_half_close": "Closing in the upper half of the daily range",
    "h_score_seller_exhaustion": "Seller exhaustion (little downside follow-through)",
}
H_EXCLUSION_REASON = G_EXCLUSION_REASON


def explain_h_score(latest: pd.Series) -> dict:
    """The section-11-style output contract for the Pressure Cooker score: final
    score, the 7 named component scores, plain-language reasons/weaknesses, and a
    confidence level."""
    components = {label: round(float(latest[col]), 1) for col, label in H_COMPONENT_LABELS.items()}
    final = round(float(latest["pressure_cooker_score_h"]), 1)
    in_uptrend = bool(latest.get("h_uptrend", False))
    excluded = bool(latest.get("h_excluded", False))
    reasons = [f"{label} ({v:.0f}/100)" for label, v in components.items() if v >= 65]
    weaknesses = [f"{label} ({v:.0f}/100)" for label, v in components.items() if v < 50]
    if not in_uptrend:
        weaknesses.insert(0, "Not currently in an uptrend (above a rising 200-day average)")
    if excluded:
        weaknesses.insert(0, H_EXCLUSION_REASON)
    n_strong = sum(1 for v in components.values() if v >= 70)
    if not in_uptrend or excluded:
        confidence = "Low"
    elif final >= 80 and n_strong >= 5:
        confidence = "High"
    elif final >= 60:
        confidence = "Medium"
    else:
        confidence = "Low"
    return {
        "final_score": final,
        "in_uptrend": in_uptrend,
        "excluded": excluded,
        "component_scores": components,
        "reasons": reasons or ["No component is currently a standout strength."],
        "weaknesses": weaknesses or ["No major weaknesses flagged."],
        "confidence": confidence,
    }


# --------------------------------------------------------------------------- #
# Existing, ALREADY-SHIPPED US high_conviction/strong_breakout tiers (see
# find_breakouts.build_summary, gated by settings.HC_ENABLED), replicated here as
# vectorized boolean columns purely so they can sit in the SAME backtest harness as
# G/G2/H -- not a redefinition, a direct port of the exact production rule (see
# IMPLEMENT_US_HIGH_CONVICTION.md for the thresholds' provenance). Needed to (a)
# sanity-check this harness reproduces the already-published ~51.1%/45.3% numbers,
# and (b) test whether requiring G2/H agreement adds anything on top of them.
# --------------------------------------------------------------------------- #
def add_existing_high_conviction_tiers(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["close"]
    atr_pct = df["atr_short"] / close * 100
    ext_pct = (close / df["resistance"] - 1) * 100
    a_recent = df["is_breakout"].rolling(settings.HC_COFIRE_BARS, min_periods=1).max().astype(bool)
    liquid = (df["avg_vol"] >= settings.HC_MIN_AVG_VOL_SHARES) & (close >= settings.HC_MIN_PRICE)
    energetic = atr_pct >= settings.HC_ATR_MIN_PCT
    c_fired = df["is_breakout_c"] if "is_breakout_c" in df.columns else pd.Series(False, index=df.index)
    tier1 = (c_fired & a_recent & liquid & energetic & (ext_pct <= settings.HC_EXT_MAX_PCT)).fillna(False)
    tier2 = (df["is_breakout"].fillna(False) & liquid & energetic).fillna(False)
    df["is_high_conviction"] = tier1
    df["is_strong_breakout"] = tier2 & ~tier1  # if/elif in production -- tier1 takes precedence
    return df


# --------------------------------------------------------------------------- #
# Sequential confirmation (2026-07-06, follow-up to the SAME-DAY agreement combos
# above): those came back inconclusive because G2/H and the existing HC/SB tiers
# almost never fire on the SAME day (0-1% Jaccard) -- mechanically, HC/SB require
# Method A to have ALREADY broken out, while G2 requires it NOT to have yet, so
# same-day overlap was close to structurally impossible. This asks the sequential
# version instead: if G2 or H fired as an "early radar" alert in the PRECEDING
# FOLLOWTHROUGH_WINDOW days, and the existing tier confirms TODAY, is that confirmed
# entry better than an unconditional one? Graded from the CONFIRMATION day's own
# entry/stop/target (the actual actionable entry point a trader would use), not the
# earlier G2/H day's -- this is the "early alert, then later confirmation" product
# idea, kept as its own thing since same-day agreement measures something different.
# --------------------------------------------------------------------------- #
def add_sequential_confirmation_signals(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    W = settings.FOLLOWTHROUGH_WINDOW
    g2_recent = df["is_breakout_g2"].shift(1).rolling(W, min_periods=1).max().fillna(0).astype(bool)
    h_recent = df["is_breakout_h"].shift(1).rolling(W, min_periods=1).max().fillna(0).astype(bool)
    df["is_sb_after_g2"] = df["is_strong_breakout"] & g2_recent
    df["is_sb_after_h"] = df["is_strong_breakout"] & h_recent
    df["is_hc_after_g2"] = df["is_high_conviction"] & g2_recent
    df["is_hc_after_h"] = df["is_high_conviction"] & h_recent
    return df


# --------------------------------------------------------------------------- #
# I — Volume Profile value-area breakout (2026-07 US research round 2): trailing
# price-by-volume histogram (each day's volume placed at its typical price), POC =
# highest-volume bin, Value Area = highest-volume bins covering VP_VALUE_AREA of
# total volume. Fire when close crosses UP through the Value Area High on a volume
# surge in an uptrend — "price accepted above value" in profile terms, a genuinely
# different signal basis (volume-at-price) from anything in A-H (all time-series).
# Profile is computed over bars [t-W, t) — today excluded, no lookahead.
# --------------------------------------------------------------------------- #
def add_method_i_volume_profile(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    n = len(df)
    tp = ((df["high"] + df["low"] + df["close"]) / 3).values
    vol = df["volume"].values.astype(float)
    W, nbins = settings.VP_WINDOW, settings.VP_BINS
    vah = np.full(n, np.nan)
    for t in range(W, n):
        p, v = tp[t - W:t], vol[t - W:t]
        hist, edges = np.histogram(p, bins=nbins, weights=v)
        total = hist.sum()
        if total <= 0:
            continue
        # ponytail: value area = top-volume bins until 70% covered (the classic
        # POC-outward-expansion algorithm differs only at the margins)
        order = np.argsort(hist)[::-1]
        cum = np.cumsum(hist[order])
        take = order[:int(np.searchsorted(cum, settings.VP_VALUE_AREA * total)) + 1]
        vah[t] = edges[int(take.max()) + 1]
    s_vah = pd.Series(vah, index=df.index)
    vol_ok = df["volume"] > df["avg_vol"] * settings.VP_VOL_CONFIRM_MULT
    cross = (df["close"] > s_vah) & (df["close"].shift(1) <= s_vah.shift(1))
    df["vp_vah"] = vah
    df["is_breakout_i"] = (cross & vol_ok & df["uptrend"]).fillna(False)
    return df


# --------------------------------------------------------------------------- #
# J — true TTM Squeeze: Bollinger(20,2) fully inside Keltner(20, KC_MULT*ATR20) =
# squeeze on; fire when the squeeze RELEASES (was on within KC_CONFIRM_DAYS, off now)
# with price above the 20-day mean, short-term momentum up, volume confirm, uptrend.
# Method C only asked "is band width near its own low"; this is the canonical
# BB-inside-KC definition plus an explicit release trigger.
# --------------------------------------------------------------------------- #
def add_method_j_ttm_squeeze(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["close"]
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - close.shift(1)).abs(),
                    (df["low"] - close.shift(1)).abs()], axis=1).max(axis=1)
    atr20 = tr.rolling(20).mean()
    squeeze_on = (2 * std) < (settings.KC_MULT * atr20)   # BB inside KC (both bands, symmetric)
    released = squeeze_on.shift(1).rolling(settings.KC_CONFIRM_DAYS).max().astype(bool) & ~squeeze_on
    vol_ok = df["volume"] > df["avg_vol"] * settings.KC_VOL_CONFIRM_MULT
    momentum_up = (close > mid) & (close > close.shift(5))
    df["is_breakout_j"] = (released & momentum_up & vol_ok & df["uptrend"]).fillna(False)
    return df


# --------------------------------------------------------------------------- #
# K — Anchored VWAP breakout: anchor the VWAP at the highest-volume day of the
# trailing year (the last institutional repositioning event, no event calendar
# needed) and fire when close crosses UP through it — the average holder since that
# event just went from red to green, a supply/psychology level A-H never look at.
# Anchor chosen over bars [t-L, t) (today excluded); AVWAP must be at least
# AVWAP_MIN_AGE_BARS old so a fresh anchor (≈ current price) can't spray crosses.
# --------------------------------------------------------------------------- #
def add_method_k_anchored_vwap(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    n = len(df)
    tp = ((df["high"] + df["low"] + df["close"]) / 3).values
    vol = df["volume"].values.astype(float)
    cs_pv = np.cumsum(tp * vol)
    cs_v = np.cumsum(vol)
    L, min_age = settings.AVWAP_ANCHOR_LOOKBACK, settings.AVWAP_MIN_AGE_BARS
    av = np.full(n, np.nan)
    for t in range(min_age, n):
        lo = max(0, t - L)
        a = lo + int(np.argmax(vol[lo:t]))          # anchor day (excludes today)
        if t - a < min_age:
            continue
        pv = cs_pv[t] - (cs_pv[a - 1] if a > 0 else 0.0)
        vv = cs_v[t] - (cs_v[a - 1] if a > 0 else 0.0)
        av[t] = pv / vv if vv > 0 else np.nan
    s_av = pd.Series(av, index=df.index)
    vol_ok = df["volume"] > df["avg_vol"] * settings.AVWAP_VOL_CONFIRM_MULT
    cross = (df["close"] > s_av) & (df["close"].shift(1) <= s_av.shift(1))
    df["avwap"] = av
    df["is_breakout_k"] = (cross & vol_ok & df["uptrend"]).fillna(False)
    return df


# --------------------------------------------------------------------------- #
# L — deep-base gate on the already-shipped US tiers: pure intersection of the two
# strongest measured US results — the SB/HC tiers (45.3%/51.1%) and base depth (the
# most monotonic feature in the whole project: deepest tercile 39.1% vs 14.7%).
# No new indicator; just "same tier, but only when the base is >= 20% deep".
# Must run AFTER add_existing_high_conviction_tiers.
# --------------------------------------------------------------------------- #
def add_method_l_deep_base_tiers(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    W = settings.LOOKBACK_HIGH
    depth_pct = (df["low"].rolling(W).min() / df["high"].rolling(W).max() - 1) * 100
    deep = depth_pct <= -settings.DEEP_BASE_MIN_DEPTH_PCT
    df["is_sb_deep_base"] = (df["is_strong_breakout"] & deep).fillna(False)
    df["is_hc_deep_base"] = (df["is_high_conviction"] & deep).fillna(False)
    return df


# --------------------------------------------------------------------------- #
# M — shakeout re-break: today closes back above `resistance` (prior 50-day high)
# for the SECOND-or-later time within REBREAK_LOOKBACK bars — a prior break failed
# and price is reclaiming the level after the flush. Trader lore says the second
# attempt, with weak hands shaken out, outperforms the first; this tests it.
# --------------------------------------------------------------------------- #
def add_method_m_shakeout_rebreak(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    cross_up = (df["close"] > df["resistance"]) & (df["close"].shift(1) <= df["resistance"].shift(1))
    prior_crosses = cross_up.shift(1).rolling(settings.REBREAK_LOOKBACK, min_periods=1).sum()
    vol_ok = df["volume"] > df["avg_vol"] * settings.VOL_SURGE_MULT
    df["is_breakout_m"] = (cross_up & (prior_crosses >= 1) & vol_ok & df["uptrend"]).fillna(False)
    return df


def add_all_methods(df: pd.DataFrame, benchmark: pd.DataFrame | None = None) -> pd.DataFrame:
    """Run methods B-H (plus G2, the replicated existing HC tiers, and the sequential
    confirmation signals) and attach all their trigger columns. `df` must already have
    add_indicators() applied (needs ema/adx/resistance)."""
    df = add_method_b_vcp(df)
    df = add_method_c_squeeze(df)
    df = add_method_d_trend_inception(df)
    df = add_method_d2_trend_inception_loose(df)
    df = add_method_e_relative_strength(df, benchmark)
    df = add_method_e2_relative_strength_uptrend(df)
    df = add_method_f_episodic_pivot(df)
    df = add_method_g_pre_breakout(df, benchmark)
    df = add_method_g2_pre_breakout_retuned(df, benchmark)
    df = add_method_h_pressure_cooker(df)
    df = add_method_i_volume_profile(df)
    df = add_method_j_ttm_squeeze(df)
    df = add_method_k_anchored_vwap(df)
    df = add_method_m_shakeout_rebreak(df)
    df = add_existing_high_conviction_tiers(df)
    df = add_method_l_deep_base_tiers(df)   # needs the HC/SB tier columns above
    df = add_sequential_confirmation_signals(df)
    return df
