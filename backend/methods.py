"""
Alternative breakout-detection methods (B-F), for backtesting against the existing
Method A (find_breakouts.add_indicators's `is_breakout` — Donchian/Minervini) — see
analyze_reliability.py, which grades all of them against the same followthrough/
r_multiple outcome rule so the comparison is apples-to-apples.

Research-only for B, C, D, D2, F — nothing calls those from run_scan.py or the served
site. E and E2 (`add_method_e_relative_strength` / `add_method_e2_relative_strength_
uptrend`) are the exception: after backtesting confirmed E2 (E masked by the `uptrend`
column) matches E's accuracy at no cost, `run_scan.py` now calls them directly (not via
`add_all_methods()`, to avoid computing the other, unshipped methods) to feed a
production readiness tier in `find_breakouts.build_summary()`.

Each `add_method_*` function takes a df that already has `add_indicators()` run on it
(needs its EMA/ADX/resistance columns) and returns it with one more boolean trigger
column added. `add_all_methods()` runs all seven and is the one entry point
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
def add_all_methods(df: pd.DataFrame, benchmark: pd.DataFrame | None = None) -> pd.DataFrame:
    """Run methods B-F and attach all their trigger columns. `df` must already have
    add_indicators() applied (needs ema/adx/resistance)."""
    df = add_method_b_vcp(df)
    df = add_method_c_squeeze(df)
    df = add_method_d_trend_inception(df)
    df = add_method_d2_trend_inception_loose(df)
    df = add_method_e_relative_strength(df, benchmark)
    df = add_method_e2_relative_strength_uptrend(df)
    df = add_method_f_episodic_pivot(df)
    return df
