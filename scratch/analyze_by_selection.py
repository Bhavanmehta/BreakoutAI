"""
Answers the actual question: does HOW we picked/filtered a breakout candidate
(which detection method fired, combo overlap, relative strength, vol contraction,
distance from 52w high, analog match quality, pattern type, cofire confirmation)
predict follow-through -- as opposed to just front-end display fields (score/stop%/price).
Reuses cached scratch/events_<mkt>.parquet, no re-fetching.
"""
import sys
import numpy as np
import pandas as pd

pd.set_option("display.width", 160)
MIN_N = 30

def hr(g):
    n = len(g)
    if n == 0:
        return "n=0"
    return f"n={n:<6} hit={100*g['worked'].mean():5.1f}%"

def method_table(df):
    rows = []
    base_rate = df["worked"].mean()
    for m, g in df.groupby("method"):
        n = len(g)
        rate = g["worked"].mean()
        rows.append((m, n, rate * 100, (rate - base_rate) * 100))
    t = pd.DataFrame(rows, columns=["method", "n", "hit%", "vs_baseline_pp"]).sort_values("n", ascending=False)
    return t

def bucket(df, col, edges, labels):
    d = df.dropna(subset=[col])
    print(f"\n-- {col} (n with data: {len(d)}) --")
    for lo_hi, lbl in zip(edges, labels):
        lo, hi = lo_hi
        m = (d[col] >= lo) & (d[col] < hi)
        print(f"  {lbl:<14} {hr(d[m])}")

def analyze(mkt):
    path = f"scratch/events_{mkt.lower()}.parquet"
    df = pd.read_parquet(path)
    base = df["worked"].mean()
    print("\n" + "=" * 78)
    print(f"{mkt}: {len(df)} events, {df['symbol'].nunique()} stocks, "
          f"{df['method'].nunique()} distinct methods/combos, baseline hit {base:.1%}")
    print("=" * 78)

    print("\n### 1. HIT RATE BY METHOD/COMBO (sorted by n; * = n<%d, treat as noise) ###" % MIN_N)
    t = method_table(df)
    for _, r in t.iterrows():
        flag = " *" if r["n"] < MIN_N else ""
        print(f"  {r['method']:<32} n={int(r['n']):<7} hit={r['hit%']:5.1f}%  ({r['vs_baseline_pp']:+.1f}pp vs base){flag}")

    print("\n### 2. SINGLE METHOD vs COMBO: does stacking signals help? ###")
    base_methods = [m for m in t["method"] if "_combo" not in m and "_and_" not in m and "_after_" not in m and not m.startswith("SB") and not m.startswith("HC")]
    combos = [m for m in t["method"] if m not in base_methods]
    bt = t[t["method"].isin(base_methods)]
    ct = t[t["method"].isin(combos)]
    print(f"  base methods (n={int(bt['n'].sum())}): weighted hit = {100*(df[df['method'].isin(base_methods)]['worked'].mean()):.1f}%")
    print(f"  combos       (n={int(ct['n'].sum())}): weighted hit = {100*(df[df['method'].isin(combos)]['worked'].mean() if ct['n'].sum() else float('nan')):.1f}%")

    print("\n### 3. COFIRE CONFIRMATION (multiple independent signals agree same day) ###")
    for col in ["rs_cofire", "d_cofire", "l_cofire"]:
        if col in df.columns:
            print(f"  {col}: True {hr(df[df[col]==True])}  |  False {hr(df[df[col]==False])}")

    print("\n### 4. SELECTION FEATURES (the actual filter criteria, not display fields) ###")
    if "rs_ratio" in df.columns:
        d = df.dropna(subset=["rs_ratio"])
        q = d["rs_ratio"].quantile([0, .25, .5, .75, 1.0]).values
        bucket(df, "rs_ratio", list(zip(q[:-1], q[1:])), ["Q1(weak RS)", "Q2", "Q3", "Q4(strong RS)"])
    if "vol_contraction" in df.columns:
        d = df.dropna(subset=["vol_contraction"])
        q = d["vol_contraction"].quantile([0, .25, .5, .75, 1.0]).values
        bucket(df, "vol_contraction", list(zip(q[:-1], q[1:])), ["Q1(tight)", "Q2", "Q3", "Q4(loose)"])
    if "dist_from_52w_high" in df.columns:
        bucket(df, "dist_from_52w_high", [(-1e9, 0), (0, 5), (5, 15), (15, 1e9)],
               ["at/above high", "0-5% off", "5-15% off", ">15% off"])
    if "adx" in df.columns:
        bucket(df, "adx", [(0, 20), (20, 30), (30, 40), (40, 1e9)], ["<20 weak trend", "20-30", "30-40", ">40 strong"])
    if "vol_surge" in df.columns:
        d = df.dropna(subset=["vol_surge"])
        q = d["vol_surge"].quantile([0, .25, .5, .75, 1.0]).values
        bucket(df, "vol_surge", list(zip(q[:-1], q[1:])), ["Q1(low vol)", "Q2", "Q3", "Q4(high vol surge)"])
    if "base_depth_pct" in df.columns:
        d = df.dropna(subset=["base_depth_pct"])
        q = d["base_depth_pct"].quantile([0, .25, .5, .75, 1.0]).values
        bucket(df, "base_depth_pct", list(zip(q[:-1], q[1:])), ["Q1(shallow base)", "Q2", "Q3", "Q4(deep base)"])

    print("\n### 5. ANALOG MATCH (does a similar historical pattern match predict outcome?) ###")
    if "analog_sim" in df.columns:
        d = df.dropna(subset=["analog_sim", "analog_worked"])
        print(f"  events with an analog: n={len(d)}")
        if len(d):
            q = d["analog_sim"].quantile([0, .33, .66, 1.0]).values
            bucket(df, "analog_sim", list(zip(q[:-1], q[1:])), ["low sim", "mid sim", "high sim"])
            print(f"  analog_worked==1 (prior similar case worked): {hr(d[d['analog_worked']==1])}")
            print(f"  analog_worked==0 (prior similar case failed): {hr(d[d['analog_worked']==0])}")

    print("\n### 6. PATTERN TYPE ###")
    if "pattern" in df.columns:
        pc = df.groupby("pattern")["worked"].agg(["count", "mean"]).sort_values("count", ascending=False)
        for pat, row in pc.iterrows():
            flag = " *" if row["count"] < MIN_N else ""
            print(f"  {str(pat):<28} n={int(row['count']):<7} hit={100*row['mean']:5.1f}%{flag}")

if __name__ == "__main__":
    for mkt in ["IN", "US"]:
        analyze(mkt)
