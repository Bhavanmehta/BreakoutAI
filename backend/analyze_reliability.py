"""
Standalone analysis (not part of the daily pipeline): is a stock's past breakout
follow-through actually predictive, or just noise pretending to be a track record?

find_breakouts.py stamps a "Caution: only X% of this stock's past breakouts followed
through" note onto the readiness card. That's only an honest thing to say if history
predicts the NEXT breakout. This script checks two things, pooled across the whole
watchlist (single-stock samples are too small to mean anything on their own):

  1. Persistence: does a stock's trailing follow-through rate (computed only from its
     PRIOR breakouts) predict whether its NEXT breakout works?
  2. Features: do ADX, vol_contraction, distance-from-52w-high, volume-surge size,
     base depth, or pattern type predict follow-through, pooled across all stocks?
     This is the version with real statistical power if persistence isn't enough.

Must run AFTER the item-1 fix in find_breakouts.py (R-multiple, order-aware
`followthrough`) — testing persistence against the old volatility-biased fixed-%
target would just measure volatility, not signal.

Run: python analyze_reliability.py
"""
from __future__ import annotations
import math

import numpy as np
import pandas as pd

import settings
from get_prices import get_prices, fetch_prices_yfinance_batch
from find_breakouts import add_indicators
from patterns import detect_pattern
from universe import build_universe

MIN_BUCKET_N = 20  # below this, a bucket's hit rate is noise, not signal


# --------------------------------------------------------------------------- #
# Data collection
# --------------------------------------------------------------------------- #
def collect_events(watchlist: dict) -> pd.DataFrame:
    """Recompute indicators for every watchlist stock and pull out every graded
    breakout event (i.e. `followthrough` is defined) with its features at the time."""
    symbols = list(watchlist)
    if settings.PRICE_SOURCE == "yfinance":
        print(f"  batch-fetching {len(symbols)} symbols...")
        prices_by_symbol = fetch_prices_yfinance_batch(symbols)
    else:
        prices_by_symbol = {s: p for s in symbols if (p := get_prices(s)) is not None and len(p) > 0}
    print(f"  got prices for {len(prices_by_symbol)}/{len(symbols)}; computing events...\n")

    rows = []
    for symbol in symbols:
        prices = prices_by_symbol.get(symbol)
        if prices is None or len(prices) == 0:
            continue
        feat = add_indicators(prices)
        events = feat[(feat["is_breakout"] == True) & (feat["followthrough"].notna())]
        for idx, ev in events.iterrows():
            lb = settings.LOOKBACK_HIGH
            window = feat.iloc[max(0, idx - lb + 1): idx + 1]
            base_depth = float(window["low"].min() / window["high"].max() - 1) * 100
            pattern = detect_pattern(feat.iloc[: idx + 1])
            avg_vol = ev["avg_vol"]
            rows.append({
                "symbol": symbol,
                "date": ev["date"],
                "worked": bool(ev["followthrough"]),
                "r_multiple": float(ev["r_multiple"]) if pd.notna(ev["r_multiple"]) else np.nan,
                "adx": float(ev["adx"]) if pd.notna(ev["adx"]) else np.nan,
                "vol_contraction": float(ev["vol_contraction"]) if pd.notna(ev["vol_contraction"]) else np.nan,
                "dist_from_52w_high": float(ev["dist_from_52w_high"]) if pd.notna(ev["dist_from_52w_high"]) else np.nan,
                "vol_surge": float(ev["volume"] / avg_vol) if pd.notna(avg_vol) and avg_vol else np.nan,
                "base_depth_pct": base_depth,
                "pattern": pattern["name"],
            })
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
    return df


# --------------------------------------------------------------------------- #
# Stats helpers (no scipy dependency — normal-approx two-proportion z-test)
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


def bucket_hit_rates(df: pd.DataFrame, col: str, q=3) -> pd.DataFrame:
    """Pooled tertile buckets of `col` -> n and hit rate per bucket. Falls back to
    fewer buckets if there aren't enough distinct values."""
    valid = df.dropna(subset=[col, "worked"])
    if len(valid) < 6:
        return pd.DataFrame()
    for k in range(q, 1, -1):
        try:
            buckets = pd.qcut(valid[col], q=k, duplicates="drop")
        except ValueError:
            continue
        if buckets.nunique() >= 2:
            break
    else:
        return pd.DataFrame()
    out = valid.groupby(buckets, observed=True)["worked"].agg(["count", "mean"])
    out = out.rename(columns={"count": "n", "mean": "hit_rate"})
    return out


def report_bucket_table(name: str, table: pd.DataFrame):
    print(f"\n  {name}:")
    if table.empty:
        print("    not enough data to bucket")
        return
    for label, row in table.iterrows():
        n, hr = int(row["n"]), row["hit_rate"]
        flag = "" if n >= MIN_BUCKET_N else "  (n<{}, noisy)".format(MIN_BUCKET_N)
        print(f"    {str(label):24s} n={n:4d}  hit_rate={hr:5.1%}{flag}")
    rows = list(table.itertuples())
    if len(rows) >= 2:
        lo, hi = rows[0], rows[-1]
        p = two_proportion_p(lo.hit_rate * lo.n, lo.n, hi.hit_rate * hi.n, hi.n)
        if p is not None:
            print(f"    lowest vs highest bucket: p={p:.3f}"
                  f"{'  (not significant at 0.05)' if p >= 0.05 else '  (significant at 0.05)'}")


# --------------------------------------------------------------------------- #
# 1. Persistence: does a stock's OWN trailing follow-through rate predict its next?
# --------------------------------------------------------------------------- #
def test_persistence(df: pd.DataFrame):
    print("\n" + "=" * 72)
    print("1. PERSISTENCE - does a stock's trailing follow-through rate predict")
    print("   whether its NEXT breakout works? (pooled across all stocks)")
    print("=" * 72)

    df = df.copy()
    df["trailing_rate"] = np.nan
    for sym, g in df.groupby("symbol"):
        history = []
        for idx in g.index:
            if history:
                df.loc[idx, "trailing_rate"] = np.mean(history)
            history.append(bool(df.loc[idx, "worked"]))

    valid = df.dropna(subset=["trailing_rate"])
    print(f"\n  Events usable (excludes each stock's 1st breakout, which has no "
          f"trailing history): {len(valid)} of {len(df)}")

    table = bucket_hit_rates(valid, "trailing_rate", q=3)
    report_bucket_table("Next-breakout hit rate by trailing follow-through bucket", table)


# --------------------------------------------------------------------------- #
# 2. Features: pooled predictiveness of ADX / vol_contraction / etc.
# --------------------------------------------------------------------------- #
def test_features(df: pd.DataFrame):
    print("\n" + "=" * 72)
    print("2. FEATURES - pooled predictiveness across the whole universe")
    print("=" * 72)

    numeric_features = {
        "adx": "ADX (trend strength)",
        "vol_contraction": "Volatility contraction ratio (ATR short/long)",
        "dist_from_52w_high": "Distance from 52-week high (%)",
        "vol_surge": "Volume surge multiple on breakout day",
        "base_depth_pct": "Base depth (%, drawdown into the base)",
    }
    for col, label in numeric_features.items():
        table = bucket_hit_rates(df, col, q=3)
        report_bucket_table(label, table)

    print("\n  Pattern type at breakout:")
    pat = df.groupby("pattern")["worked"].agg(["count", "mean"]).rename(
        columns={"count": "n", "mean": "hit_rate"}).sort_values("n", ascending=False)
    for label, row in pat.iterrows():
        n, hr = int(row["n"]), row["hit_rate"]
        flag = "" if n >= MIN_BUCKET_N else f"  (n<{MIN_BUCKET_N}, noisy)"
        print(f"    {str(label):24s} n={n:4d}  hit_rate={hr:5.1%}{flag}")


# --------------------------------------------------------------------------- #
def main():
    watchlist = build_universe()
    print(f"Recomputing features for {len(watchlist)} stocks "
          f"(source: {settings.PRICE_SOURCE})...\n")
    df = collect_events(watchlist)
    if df.empty:
        print("\nNo graded breakout events found - nothing to analyze.")
        return

    n_stocks = df["symbol"].nunique()
    n_events = len(df)
    overall_rate = df["worked"].mean()
    print(f"\nTotal: {n_events} graded breakout events across {n_stocks} stocks "
          f"(overall hit rate {overall_rate:.1%}).")
    print(f"Rule of thumb used below: a bucket needs n>={MIN_BUCKET_N} before its "
          f"hit rate is treated as more than noise.")
    if n_events < 150:
        print(f"\n  ** SAMPLE SIZE WARNING **\n"
              f"  {n_events} events is thin for pooled statistics, and each bucket below\n"
              f"  gets even fewer. Treat every result here as a hypothesis, not a\n"
              f"  conclusion - if it's still thin, raise settings.UNIVERSE_SIZE rather than\n"
              f"  forcing a verdict from this data.")

    test_persistence(df)
    test_features(df)

    print("\n" + "=" * 72)
    print("Done. This script does not write any files - it's a research check, run")
    print("manually, not part of run_scan.py.")
    print("=" * 72)


if __name__ == "__main__":
    main()
