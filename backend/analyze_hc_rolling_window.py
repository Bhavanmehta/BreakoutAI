"""
Rolling-window entry-lag test for the existing US high-conviction tiers
(HC_tier1_high_conviction / SB_tier2_strong_breakout, methods.py's
add_existing_high_conviction_tiers) -- copied from analyze_reliability.py rather
than edited in place, since that file has unrelated in-flight G/H method work that
shouldn't be touched for this side-question.

Motivating question (2026-07-06): both tiers currently only flag a stock on the
exact day of a FRESH, cooldown-deduped fire (find_breakouts._last_is_fresh_fire /
this file's _dedup_with_cooldown) -- the live badge disappears the very next day
even if nothing about the setup has changed, which is most of why the "high
conviction" list looks almost completely different day to day. If the list stayed
"flagged" for a rolling week instead (so it reads as a stable watchlist rather than
a one-day alert), would a user who acts on day+1..+6 after the fire still get a
good hit rate, or does the edge decay too fast within the week to justify it?

Shortcut used: find_breakouts.add_indicators() already computes `followthrough` /
`r_multiple` UNCONDITIONALLY for every bar (entry = that day's close, stop = that
day's resistance * STOP_LOSS_FRACTION) -- so grading a lagged entry at
fire_idx + lag is just reading feat.iloc[fire_idx + lag]['followthrough']; no new
grading math needed, only the lagged lookup + re-aggregation.

Run (US only -- these tiers are gated to US; settings.HC_ENABLED = MARKET == 'US',
and the thresholds were validated on US data only):
  cd backend
  BREAKOUTAI_MARKET=US python analyze_hc_rolling_window.py
"""
from __future__ import annotations
import math

import numpy as np
import pandas as pd

import settings
from get_prices import get_prices, fetch_prices_yfinance_batch
from find_breakouts import add_indicators
from universe import build_universe
from methods import add_all_methods, fetch_benchmark

MIN_BUCKET_N = 20  # below this, a bucket's hit rate is noise, not signal

# Same cooldown as analyze_reliability.py's FIRE_COOLDOWN: keeps a fire that stays
# true for several consecutive bars (one continuous move) from being counted as
# multiple independent trials.
FIRE_COOLDOWN = settings.FOLLOWTHROUGH_WINDOW

# Test entry on the fire day itself (lag=0, today's production behavior) through
# this many trading days later -- "a week" of possible entry days if the badge
# stayed visible instead of disappearing after one day.
ROLLING_LAG_DAYS = 7

HC_METHODS = {
    "HC_tier1_high_conviction": "is_high_conviction",
    "SB_tier2_strong_breakout": "is_strong_breakout",
}


def _dedup_with_cooldown(mask: np.ndarray, cooldown: int) -> np.ndarray:
    """Keep only the first fire in each cluster of True values closer than `cooldown`
    bars apart."""
    out = np.zeros(len(mask), dtype=bool)
    last_fire = -cooldown - 1
    for i in np.flatnonzero(mask):
        if i - last_fire > cooldown:
            out[i] = True
            last_fire = i
    return out


# --------------------------------------------------------------------------- #
# Stats helpers (no scipy dependency -- normal-approx two-proportion z-test)
# --------------------------------------------------------------------------- #
def _norm_sf(z: float) -> float:
    """One-sided upper-tail survival function of the standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2))


def two_proportion_p(worked_a, n_a, worked_b, n_b):
    if n_a == 0 or n_b == 0:
        return None
    p_a, p_b = worked_a / n_a, worked_b / n_b
    p_pool = (worked_a + worked_b) / (n_a + n_b)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))
    if se == 0:
        return None
    z = (p_a - p_b) / se
    return 2 * _norm_sf(abs(z))  # two-sided


# --------------------------------------------------------------------------- #
# Data collection
# --------------------------------------------------------------------------- #
def collect_lagged_events(watchlist: dict) -> pd.DataFrame:
    """For every fresh (deduped) HC/SB fire, look up the ALREADY-COMPUTED
    followthrough/r_multiple at fire_idx+lag for lag in 0..ROLLING_LAG_DAYS-1 --
    i.e. "what would the hit rate be if a user entered this many trading days after
    the fire, instead of exactly on the fire day itself."
    """
    symbols = list(watchlist)
    if settings.PRICE_SOURCE == "yfinance":
        print(f"  batch-fetching {len(symbols)} symbols...")
        prices_by_symbol = fetch_prices_yfinance_batch(symbols)
    else:
        prices_by_symbol = {s: p for s in symbols if (p := get_prices(s)) is not None and len(p) > 0}
    print(f"  got prices for {len(prices_by_symbol)}/{len(symbols)}; fetching benchmark...")

    benchmark = fetch_benchmark()
    print(f"  benchmark ({settings.RS_BENCHMARK}): "
          f"{'ok, ' + str(len(benchmark)) + ' bars' if benchmark is not None else 'FAILED'}")
    print("  computing lagged entries...\n")

    rows = []
    for symbol in symbols:
        prices = prices_by_symbol.get(symbol)
        if prices is None or len(prices) == 0:
            continue
        feat = add_indicators(prices)
        feat = add_all_methods(feat, benchmark=benchmark)
        n = len(feat)
        followthrough = feat["followthrough"].values
        r_multiple = feat["r_multiple"].values
        closes = feat["close"].values
        dates = feat["date"].values

        for method_name, col in HC_METHODS.items():
            raw = feat[col].fillna(False).values if col in feat.columns else np.zeros(n, dtype=bool)
            fire_idx = np.flatnonzero(_dedup_with_cooldown(raw, FIRE_COOLDOWN))
            for idx in fire_idx:
                fire_date = dates[idx]
                for lag in range(ROLLING_LAG_DAYS):
                    entry_idx = idx + lag
                    if entry_idx >= n:
                        continue
                    wt = followthrough[entry_idx]
                    if wt is None or (isinstance(wt, float) and np.isnan(wt)):
                        continue
                    rows.append({
                        "method": method_name,
                        "symbol": symbol,
                        "fire_date": fire_date,
                        "lag": lag,
                        "entry_date": dates[entry_idx],
                        "entry_price": float(closes[entry_idx]),
                        "worked": bool(wt),
                        "r_multiple": float(r_multiple[entry_idx]) if pd.notna(r_multiple[entry_idx]) else np.nan,
                    })
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values(["method", "symbol", "fire_date", "lag"]).reset_index(drop=True)
    return df


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def report_lag_table(df: pd.DataFrame, method: str):
    print("\n" + "=" * 72)
    print(f"{method}: hit rate by entry lag (trading days after the fresh fire)")
    print("=" * 72)
    sub = df[df["method"] == method]
    if sub.empty:
        print("  no events.")
        return

    baseline = sub[sub["lag"] == 0]
    n0 = len(baseline)
    hr0 = baseline["worked"].mean() if n0 else float("nan")
    print(f"  lag=0 is today's production behavior (entry on the fire day itself): "
          f"n={n0}  hit_rate={hr0:.1%}\n")

    for lag in range(ROLLING_LAG_DAYS):
        g = sub[sub["lag"] == lag]
        n = len(g)
        hr = g["worked"].mean() if n else float("nan")
        avg_r = g["r_multiple"].mean() if n else float("nan")
        flag = "" if n >= MIN_BUCKET_N else f"  (n<{MIN_BUCKET_N}, noisy)"
        sig = ""
        if lag > 0 and n and n0:
            delta = (hr - hr0) * 100
            p = two_proportion_p(hr * n, n, hr0 * n0, n0)
            if p is not None:
                sig = (f"  vs lag=0: {delta:+.1f}pt, p={p:.3f}"
                       f"{'  (significant)' if p < 0.05 else '  (not significant)'}")
        print(f"    lag={lag}  n={n:5d}  hit_rate={hr:5.1%}  avg_R_at_entry={avg_r:5.2f}{flag}{sig}")

    pooled_n, pooled_hr = len(sub), sub["worked"].mean()
    print(f"\n  Pooled across all lags 0-{ROLLING_LAG_DAYS - 1} (as if a user acted on a random"
          f"\n  day within the week the flag would have stayed up): n={pooled_n}  hit_rate={pooled_hr:.1%}")


def print_lag_examples(df: pd.DataFrame, method: str, n: int = 2):
    sub = df[df["method"] == method]
    if sub.empty:
        return
    print(f"\n  {method} -- concrete examples (lag=0 entry vs. lag={ROLLING_LAG_DAYS - 1} entry, "
          f"same fire event):")
    fires = sub[["symbol", "fire_date"]].drop_duplicates().tail(n * 3)
    shown = 0
    for _, key in fires.iterrows():
        ev = sub[(sub["symbol"] == key["symbol"]) & (sub["fire_date"] == key["fire_date"])]
        e0 = ev[ev["lag"] == 0]
        e6 = ev[ev["lag"] == ROLLING_LAG_DAYS - 1]
        if e0.empty or e6.empty:
            continue
        e0, e6 = e0.iloc[0], e6.iloc[0]
        fire_date_str = pd.Timestamp(key["fire_date"]).strftime("%Y-%m-%d")
        print(f"    {key['symbol']} fired on {fire_date_str}: "
              f"lag=0 entry ${e0['entry_price']:.2f} -> {'WORKED' if e0['worked'] else 'faded'}; "
              f"lag={ROLLING_LAG_DAYS - 1} entry ${e6['entry_price']:.2f} -> "
              f"{'WORKED' if e6['worked'] else 'faded'}")
        shown += 1
        if shown >= n:
            break


# --------------------------------------------------------------------------- #
def main():
    watchlist = build_universe()
    print(f"Recomputing features for {len(watchlist)} stocks "
          f"(source: {settings.PRICE_SOURCE}, market: {settings.MARKET})...\n")
    if not settings.HC_ENABLED:
        print("  ** WARNING ** settings.HC_ENABLED is False (BREAKOUTAI_MARKET is not 'US') -- "
              "these tiers were only validated on US data; run with BREAKOUTAI_MARKET=US.\n")

    df = collect_lagged_events(watchlist)
    if df.empty:
        print("\nNo graded events found - nothing to analyze.")
        return

    print(f"\nTotal graded (fire, lag) pairs: {len(df)} across {df['symbol'].nunique()} stocks.")

    for method in HC_METHODS:
        report_lag_table(df, method)
        print_lag_examples(df, method)

    print("\n" + "=" * 72)
    print("Done. This script does not write any files - it's a research check, not")
    print("part of run_scan.py.")
    print("=" * 72)


if __name__ == "__main__":
    main()
