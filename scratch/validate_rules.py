"""Validate the 4 candidate live-ledger rules against the retrospective DuckDB
history, using the exact same event generator as analyze_reliability.py.

Rules under test (found on 2 weeks of live picks, need historical confirmation):
  R1  high score is NOT better (live: conviction>=80 underperformed)
  R2  stop width matters      (IN live: >=6% bad; US live: <3% bad)
  R3  price level             (US live: >=$100 bad, <$10 good)
  R4  re-pick after a loss is toxic (both markets live: 0-20% hit)

Run:  BREAKOUTAI_MARKET=IN|US python scratch/validate_rules.py
Writes nothing to production; caches events to scratch/events_<mkt>.parquet.
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
MKT = os.environ.get("BREAKOUTAI_MARKET", "IN")

import numpy as np
import pandas as pd

CACHE = os.path.join(os.path.dirname(__file__), f"events_{MKT.lower()}.parquet")


def get_events() -> pd.DataFrame:
    if os.path.exists(CACHE):
        print(f"[cache] {CACHE}")
        return pd.read_parquet(CACHE)
    from analyze_reliability import collect_events
    import settings
    from universe import build_universe  # same universe builder as production
    wl = build_universe()
    print(f"Recomputing events for {len(wl)} stocks ({MKT})...")
    df, _, _ = collect_events(wl)
    df.to_parquet(CACHE)
    return df


def hr(g):
    return f"n={len(g):<6} hit={100 * g['worked'].mean():5.1f}%" if len(g) else "n=0"


def main():
    df = get_events()
    df["date"] = pd.to_datetime(df["date"])
    df["stop_w"] = (df["price"] - df["stop"]) / df["price"] * 100
    print(f"\n========== {MKT}: {len(df)} events, {df['symbol'].nunique()} stocks, "
          f"base hit {100 * df['worked'].mean():.1f}% ==========")

    # ---- R1: score_g2 threshold (proxy for live conviction) ----
    d = df.dropna(subset=["score_g2"])
    print(f"\nR1 score_g2 (n with score: {len(d)})")
    for lbl, m in (("  <50 ", d["score_g2"] < 50), ("  50-64", (d["score_g2"] >= 50) & (d["score_g2"] < 65)),
                   ("  65-79", (d["score_g2"] >= 65) & (d["score_g2"] < 80)), ("  >=80", d["score_g2"] >= 80)):
        print(f"  {lbl:<8} {hr(d[m])}")

    # ---- R2: stop width ----
    print("\nR2 stop width %")
    for lbl, m in (("  <3", df["stop_w"] < 3), ("  3-4.5", (df["stop_w"] >= 3) & (df["stop_w"] < 4.5)),
                   ("  4.5-6", (df["stop_w"] >= 4.5) & (df["stop_w"] < 6)), ("  >=6", df["stop_w"] >= 6)):
        print(f"  {lbl:<8} {hr(df[m])}")

    # ---- R3: price level ----
    print("\nR3 entry price")
    cuts = [(0, 10), (10, 30), (30, 100), (100, 1e12)] if MKT == "US" else [(0, 100), (100, 500), (500, 2000), (2000, 1e12)]
    for lo, hi in cuts:
        print(f"  [{lo}-{hi if hi < 1e12 else 'inf'})   {hr(df[(df['price'] >= lo) & (df['price'] < hi)])}")

    # ---- R4: re-pick after loss ----
    # prior event of same symbol (any method) old enough that its outcome was
    # known (>= W days earlier); classify current event by that prior outcome.
    from settings import FOLLOWTHROUGH_WINDOW as W
    day = pd.Timedelta(days=1)
    print(f"\nR4 re-pick after prior outcome (prior resolved: >= {W} calendar days earlier)")
    ev = df.sort_values(["symbol", "date"]).reset_index()
    first, after_won, after_lost = [], [], []
    for sym, g in ev.groupby("symbol"):
        dates = g["date"].to_numpy()
        worked = g["worked"].to_numpy()
        for i in range(len(g)):
            prior = np.where(dates[: i] <= dates[i] - W * day)[0]
            if len(prior) == 0:
                first.append(worked[i])
            elif worked[prior[-1]]:
                after_won.append(worked[i])
            else:
                after_lost.append(worked[i])
    for lbl, a in (("first pick", first), ("after won", after_won), ("after lost", after_lost)):
        if a:
            print(f"  {lbl:<12} n={len(a):<6} hit={100 * np.mean(a):5.1f}%")

    # ---- headline: combined rule counterfactual ----
    print("\nCombined counterfactual (historical):")
    base = df
    print(f"  baseline              {hr(base)}")
    if MKT == "IN":
        keep = base[(base["stop_w"] < 6)]
        print(f"  cut stop_w>=6         {hr(keep)}")
    else:
        keep = base[(base["stop_w"] >= 3) & (base["price"] < 100)]
        print(f"  cut stop<3 & px>=100  {hr(keep)}")
    if base["score_g2"].notna().any():
        keep2 = keep[~(keep["score_g2"] >= 80)]
        print(f"  ... also cut g2>=80   {hr(keep2)}")


if __name__ == "__main__":
    main()
