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
from analogs import detect_analog
from universe import build_universe
from methods import add_all_methods, fetch_benchmark
from score import reliability_estimate, breakout_quality

MIN_BUCKET_N = 20  # below this, a bucket's hit rate is noise, not signal

# How to keep a permissive method's overlapping fires (e.g. "new N-day RS high" can
# stay true for days in a row during a smooth run) from being counted as independent
# trials: after a fire, that method+stock has to go this many bars quiet before its
# next fire counts. Same window as the followthrough grading itself, since overlapping
# 10-day forward windows aren't independent outcomes anyway.
FIRE_COOLDOWN = settings.FOLLOWTHROUGH_WINDOW


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

# Method A is the existing, already-validated Donchian/Minervini breakout
# (find_breakouts.add_indicators's `is_breakout`); B-F are the candidates from
# methods.py. Every method is graded against the SAME followthrough/r_multiple rule.
BASE_METHODS = {
    "A_donchian_minervini": "is_breakout",
    "B_vcp": "is_breakout_b",
    "C_squeeze": "is_breakout_c",
    "D_trend_inception": "is_breakout_d",
    "D2_trend_inception_loose": "is_breakout_d_loose",
    "E_relative_strength": "is_breakout_e",
    "E2_relative_strength_uptrend": "is_breakout_e2",
    "F_episodic_pivot": "is_breakout_f",
    "G_pre_breakout_composite": "is_breakout_g",
    "G2_pre_breakout_retuned": "is_breakout_g2",
    "H_pressure_cooker": "is_breakout_h",
    "HC_tier1_high_conviction": "is_high_conviction",
    "SB_tier2_strong_breakout": "is_strong_breakout",
    "I_volume_profile": "is_breakout_i",
    "J_ttm_squeeze": "is_breakout_j",
    "K_anchored_vwap": "is_breakout_k",
    "L_sb_deep_base": "is_sb_deep_base",
    "L2_hc_deep_base": "is_hc_deep_base",
    "M_shakeout_rebreak": "is_breakout_m",
    "SB_after_G2_alert": "is_sb_after_g2",
    "SB_after_H_alert": "is_sb_after_h",
    "HC_after_G2_alert": "is_hc_after_g2",
    "HC_after_H_alert": "is_hc_after_h",
}

# Combos: does requiring 2-3 methods to agree on the SAME day beat any of them alone?
# Picked A+E (both individually solid, only 17% overlap - largely independent votes),
# A+D and E+D (D has the best solo hit rate, worth pairing with the two frequent ones),
# and all three together. SB/HC_and_* (2026-07-06): does requiring the NEW pre-breakout
# composites (G2/H) to agree, same-day, with the ALREADY-SHIPPED confirmation tiers add
# anything on top of those tiers alone -- i.e. "keep the existing scores, does adding
# the new one as a same-day confirmation filter help" rather than replacing anything.
COMBOS = {
    "AE_combo": ("A_donchian_minervini", "E_relative_strength"),
    "AD_combo": ("A_donchian_minervini", "D_trend_inception"),
    "ED_combo": ("E_relative_strength", "D_trend_inception"),
    "AED_combo": ("A_donchian_minervini", "E_relative_strength", "D_trend_inception"),
    "SB_and_G2": ("SB_tier2_strong_breakout", "G2_pre_breakout_retuned"),
    "SB_and_H": ("SB_tier2_strong_breakout", "H_pressure_cooker"),
    "HC_and_G2": ("HC_tier1_high_conviction", "G2_pre_breakout_retuned"),
    "HC_and_H": ("HC_tier1_high_conviction", "H_pressure_cooker"),
    # Round-2 (2026-07, US): does the price-level break agreeing with the volume-at-
    # price read (I) or the squeeze release (J) beat either alone?
    "AI_combo": ("A_donchian_minervini", "I_volume_profile"),
    "AJ_combo": ("A_donchian_minervini", "J_ttm_squeeze"),
    # Round-2b (2026-07, US): I/J/K are all independent reads on the same underlying
    # price/volume action (volume-at-price, squeeze release, anchored VWAP) -- do any
    # PAIRS of them agreeing same-day beat the individual signals, without requiring
    # the price-level break (A) too? Also test the triple-agreement case.
    "IJ_combo": ("I_volume_profile", "J_ttm_squeeze"),
    "IK_combo": ("I_volume_profile", "K_anchored_vwap"),
    "JK_combo": ("J_ttm_squeeze", "K_anchored_vwap"),
    "IJK_combo": ("I_volume_profile", "J_ttm_squeeze", "K_anchored_vwap"),
}


# --------------------------------------------------------------------------- #
# Data collection
# --------------------------------------------------------------------------- #
def collect_events(watchlist: dict):
    """Recompute indicators + methods B-F for every watchlist stock and pull out every
    graded event (i.e. `followthrough` is defined) per method, tagged by which method
    fired. Also tallies per-method fire counts and pairwise overlap (Jaccard), so we can
    tell whether two methods are actually independent signals or mostly re-detecting the
    same days. Returns (events_df, fire_counts, overlap_counts)."""
    symbols = list(watchlist)
    if settings.PRICE_SOURCE == "yfinance":
        print(f"  batch-fetching {len(symbols)} symbols...")
        prices_by_symbol = fetch_prices_yfinance_batch(symbols)
    else:
        prices_by_symbol = {s: p for s in symbols if (p := get_prices(s)) is not None and len(p) > 0}
    print(f"  got prices for {len(prices_by_symbol)}/{len(symbols)}; fetching benchmark...")

    benchmark = fetch_benchmark()
    print(f"  benchmark ({settings.RS_BENCHMARK}): "
          f"{'ok, ' + str(len(benchmark)) + ' bars' if benchmark is not None else 'FAILED - method E will be empty'}")
    print("  computing events...\n")

    fire_counts = {m: 0 for m in BASE_METHODS}
    overlap_counts = {m1: {m2: 0 for m2 in BASE_METHODS} for m1 in BASE_METHODS}

    rows = []
    for symbol in symbols:
        prices = prices_by_symbol.get(symbol)
        if prices is None or len(prices) == 0:
            continue
        feat = add_indicators(prices)
        feat = add_all_methods(feat, benchmark=benchmark)
        gradeable = feat["followthrough"].notna()

        raw = {m: feat[col].fillna(False).astype(bool) for m, col in BASE_METHODS.items()}
        deduped = {m: pd.Series(_dedup_with_cooldown(raw[m].values, FIRE_COOLDOWN), index=feat.index)
                   for m in BASE_METHODS}
        fired = {m: (deduped[m] & gradeable) for m in BASE_METHODS}
        for m1 in BASE_METHODS:
            fire_counts[m1] += int(fired[m1].sum())
            for m2 in BASE_METHODS:
                overlap_counts[m1][m2] += int((fired[m1] & fired[m2]).sum())

        # Combos are built from the already-deduped base columns, so they inherit the
        # same minimum spacing automatically (a combo can't fire more often than its
        # rarest, already-cooldown-respecting member).
        for combo_name, parts in COMBOS.items():
            col = fired[parts[0]]
            for p in parts[1:]:
                col = col & fired[p]
            fired[combo_name] = col

        lb = settings.LOOKBACK_HIGH
        for method_name in list(BASE_METHODS) + list(COMBOS):
            events = feat[fired[method_name]]
            is_method_a = method_name == "A_donchian_minervini"
            for idx, ev in events.iterrows():
                window = feat.iloc[max(0, idx - lb + 1): idx + 1]
                base_depth = float(window["low"].min() / window["high"].max() - 1) * 100
                pattern = detect_pattern(feat.iloc[: idx + 1])
                avg_vol = ev["avg_vol"]
                resistance = float(ev["resistance"]) if pd.notna(ev["resistance"]) else np.nan
                stop = settings.stop_from(resistance, ev.get("atr_short"))
                if stop is None:
                    stop = np.nan
                price = float(ev["close"])
                # Do the two "expensive"/score-only extras (nearest analog, co-firing
                # of the validated method signals) only for Method-A events — that's the
                # population the score + analog tests below run on, and it bounds cost.
                analog_worked = analog_sim = np.nan
                rs_cofire = bool(ev.get("is_breakout_e2", False))
                d_cofire = bool(ev.get("is_breakout_d", False))
                l_cofire = bool(ev.get("is_sb_deep_base", False))
                if is_method_a:
                    a = detect_analog(feat.iloc[: idx + 1])
                    if a is not None:
                        analog_sim = a["similarity"]
                        analog_worked = (1.0 if a["worked"] is True
                                         else 0.0 if a["worked"] is False else np.nan)
                rows.append({
                    "method": method_name,
                    "symbol": symbol,
                    "date": ev["date"],
                    "worked": bool(ev["followthrough"]),
                    "price": price,
                    "resistance": resistance,
                    "stop": stop,
                    "target": price + (price - stop) if pd.notna(stop) else np.nan,
                    "fwd_ret_10d_pct": float(ev["fwd_ret_10d"]) * 100 if pd.notna(ev["fwd_ret_10d"]) else np.nan,
                    "r_multiple": float(ev["r_multiple"]) if pd.notna(ev["r_multiple"]) else np.nan,
                    "adx": float(ev["adx"]) if pd.notna(ev["adx"]) else np.nan,
                    "plus_di": float(ev["plus_di"]) if pd.notna(ev["plus_di"]) else np.nan,
                    "minus_di": float(ev["minus_di"]) if pd.notna(ev["minus_di"]) else np.nan,
                    "rs_ratio": float(ev["rs_ratio"]) if "rs_ratio" in ev.index and pd.notna(ev["rs_ratio"]) else np.nan,
                    "ep_gap_pct": float(ev["ep_gap_pct"]) if "ep_gap_pct" in ev.index and pd.notna(ev["ep_gap_pct"]) else np.nan,
                    "ep_vol_ratio": float(ev["ep_vol_ratio"]) if "ep_vol_ratio" in ev.index and pd.notna(ev["ep_vol_ratio"]) else np.nan,
                    "vol_contraction": float(ev["vol_contraction"]) if pd.notna(ev["vol_contraction"]) else np.nan,
                    "dist_from_52w_high": float(ev["dist_from_52w_high"]) if pd.notna(ev["dist_from_52w_high"]) else np.nan,
                    "vol_surge": float(ev["volume"] / avg_vol) if pd.notna(avg_vol) and avg_vol else np.nan,
                    "base_depth_pct": base_depth,
                    "pattern": pattern["name"],
                    "rs_cofire": rs_cofire,
                    "d_cofire": d_cofire,
                    "l_cofire": l_cofire,
                    "analog_worked": analog_worked,
                    "analog_sim": analog_sim,
                    "score_g": float(ev["pre_breakout_score_g"]) if "pre_breakout_score_g" in ev.index and pd.notna(ev["pre_breakout_score_g"]) else np.nan,
                    "score_g2": float(ev["pre_breakout_score_g2"]) if "pre_breakout_score_g2" in ev.index and pd.notna(ev["pre_breakout_score_g2"]) else np.nan,
                    "score_h": float(ev["pressure_cooker_score_h"]) if "pressure_cooker_score_h" in ev.index and pd.notna(ev["pressure_cooker_score_h"]) else np.nan,
                })
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values(["method", "symbol", "date"]).reset_index(drop=True)
    return df, fire_counts, overlap_counts


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
        "score_g": "Pre-breakout composite score (Method G, 0-100) on Method-A's own event days",
        "score_g2": "Retuned pre-breakout composite (Method G2, 0-100) on Method-A's own event days",
        "score_h": "Pressure Cooker score (Method H, 0-100) on Method-A's own event days",
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
# 2b. Composite score: does blending the VALIDATED features (shrunk trailing rate +
#     base depth + method confirmation) into one number stratify follow-through better
#     than any single feature? This is what the UI's ranking score is built from, so it
#     has to be replayed on history exactly the way it would've been known at the time
#     (trailing counts = prior breakouts only — no lookahead).
# --------------------------------------------------------------------------- #
def _attach_trailing_reliability(df: pd.DataFrame) -> pd.DataFrame:
    """Per stock, in date order, attach the Bayesian-shrunk follow-through estimate
    computed from ONLY that stock's prior breakouts (the number the score would have
    used live)."""
    df = df.copy().sort_values(["symbol", "date"]).reset_index(drop=True)
    rel = np.empty(len(df))
    for _, g in df.groupby("symbol"):
        worked = total = 0
        for idx in g.index:
            rel[idx] = reliability_estimate(worked, total)
            total += 1
            worked += 1 if bool(df.loc[idx, "worked"]) else 0
    df["rel_est"] = rel
    return df


def test_score(df_all: pd.DataFrame, method: str = "A_donchian_minervini", section: str = "2b"):
    """Replay the score.breakout_quality() candidates on `method`'s own event
    population, no lookahead (trailing reliability computed from ONLY that
    method's own prior fires, per stock -- what the score would have known live
    if I/K shipped as their own tracked signal). Defaults to Method-A (the
    original validation population); pass method="I_volume_profile" etc. to check
    whether the SAME shipped weights generalize to a different signal's events,
    or whether that signal would need its own weight mix."""
    print("\n" + "=" * 72)
    print(f"{section}. COMPOSITE SCORE on {method} - does blending validated features into")
    print("    ONE number stratify follow-through? (no lookahead)")
    print("=" * 72)

    df = df_all[df_all["method"] == method].copy()
    if len(df) < 100:
        print(f"  too few {method} events to test a composite score.")
        return
    df = _attach_trailing_reliability(df)

    # Candidate scores, cheapest -> richest. Each is a column of ordering values.
    candidates = {
        "shrunk trailing reliability alone": df["rel_est"],
        "rel(0.7) + base_depth(0.3)": df.apply(
            lambda r: breakout_quality(r["rel_est"], r["base_depth_pct"],
                                       w_rel=0.7, w_depth=0.3, w_method=0.0), axis=1),
        "rel(0.6)+depth(0.25)+method(0.15) [shipped default]": df.apply(
            lambda r: breakout_quality(r["rel_est"], r["base_depth_pct"],
                                       bool(r["rs_cofire"]), bool(r["d_cofire"])), axis=1),
        "rel(0.6)+depth(0.25)+method(0.15)+l_cofire [with Method L]": df.apply(
            lambda r: breakout_quality(r["rel_est"], r["base_depth_pct"],
                                       bool(r["rs_cofire"]), bool(r["d_cofire"]),
                                       bool(r["l_cofire"])), axis=1),
    }
    for name, col in candidates.items():
        tmp = df.assign(_score=col)
        table = bucket_hit_rates(tmp, "_score", q=3)
        report_bucket_table(name, table)
        rows = list(table.itertuples())
        if len(rows) >= 2:
            print(f"    -> spread (highest - lowest bucket): "
                  f"{(rows[-1].hit_rate - rows[0].hit_rate) * 100:+.1f} pts")


# --------------------------------------------------------------------------- #
# 2c. Is the single "closest historical analog" outcome actually predictive, or is it
#     the one-day anecdote the score deliberately excludes? (The UI shows a worked/faded
#     badge on it — this checks whether that badge deserves the weight it visually implies.)
# --------------------------------------------------------------------------- #
def test_analog_predictiveness(df_all: pd.DataFrame):
    print("\n" + "=" * 72)
    print("2c. ANALOG PREDICTIVENESS - does the single closest-past-analog's outcome")
    print("    predict whether THIS breakout follows through? (Method-A events)")
    print("=" * 72)

    df = df_all[(df_all["method"] == "A_donchian_minervini")
                & (df_all["analog_worked"].notna())].copy()
    if len(df) < 100:
        print("  too few events with a defined analog to test.")
        return

    worked_when_analog_worked = df[df["analog_worked"] == 1.0]["worked"]
    worked_when_analog_faded = df[df["analog_worked"] == 0.0]["worked"]
    n1, n0 = len(worked_when_analog_worked), len(worked_when_analog_faded)
    r1 = worked_when_analog_worked.mean() if n1 else float("nan")
    r0 = worked_when_analog_faded.mean() if n0 else float("nan")
    print(f"\n  When the analog WORKED   (n={n1:5d}): this breakout followed through {r1:5.1%}")
    print(f"  When the analog FADED    (n={n0:5d}): this breakout followed through {r0:5.1%}")
    if n1 and n0:
        p = two_proportion_p(r1 * n1, n1, r0 * n0, n0)
        print(f"  difference: {(r1 - r0) * 100:+.1f} pts, p={p:.3f}"
              f"{'  (significant)' if p is not None and p < 0.05 else '  (NOT significant)'}")
        print("  Interpretation: if this is small / not significant, the worked/faded badge")
        print("  is an anecdote, not a signal — it should not be visually weighted like the")
        print("  aggregate track record, and it's correctly excluded from the score.")

    # Does a TIGHTER analog match carry more signal than a loose one?
    table = bucket_hit_rates(df, "analog_sim", q=3)
    report_bucket_table("Follow-through by analog similarity (does a closer match matter?)", table)


# --------------------------------------------------------------------------- #
# 3. Method comparison: hit rate / avg R / frequency, Method A vs candidates B-F
# --------------------------------------------------------------------------- #
def test_methods(df: pd.DataFrame):
    print("\n" + "=" * 72)
    print("3. METHOD COMPARISON - hit rate, avg R-multiple, and frequency per")
    print("   breakout-detection method, pooled across the whole universe")
    print("=" * 72)

    summary = df.groupby("method").agg(
        n=("worked", "count"),
        hit_rate=("worked", "mean"),
        n_stocks=("symbol", "nunique"),
    )
    order = [m for m in list(BASE_METHODS) + list(COMBOS) if m in summary.index]
    summary = summary.loc[order]

    baseline = "A_donchian_minervini"
    baseline_row = summary.loc[baseline] if baseline in summary.index else None
    for method, row in summary.iterrows():
        flag = "" if row["n"] >= MIN_BUCKET_N else f"  (n<{MIN_BUCKET_N}, noisy)"
        events_per_stock = row["n"] / row["n_stocks"] if row["n_stocks"] else 0
        print(f"    {method:22s} n={int(row['n']):5d}  stocks={int(row['n_stocks']):4d}  "
              f"events/stock={events_per_stock:4.1f}  hit_rate={row['hit_rate']:5.1%}{flag}")
        if baseline_row is not None and method != baseline and row["n"] > 0 and baseline_row["n"] > 0:
            p = two_proportion_p(row["hit_rate"] * row["n"], row["n"],
                                  baseline_row["hit_rate"] * baseline_row["n"], baseline_row["n"])
            if p is not None:
                print(f"      vs {baseline}: p={p:.3f}"
                      f"{'  (not significant at 0.05)' if p >= 0.05 else '  (significant at 0.05)'}")


def report_overlap(fire_counts: dict, overlap_counts: dict):
    print("\n" + "=" * 72)
    print("4. METHOD OVERLAP - Jaccard similarity between each pair (are these")
    print("   actually independent signals, or mostly re-detecting the same days?)")
    print("=" * 72)

    names = list(BASE_METHODS)
    short = {m: m.split("_")[0] for m in names}
    print("    " + "".ljust(4) + "".join(short[m].rjust(6) for m in names))
    for m1 in names:
        cells = []
        for m2 in names:
            union = fire_counts[m1] + fire_counts[m2] - overlap_counts[m1][m2]
            jaccard = overlap_counts[m1][m2] / union if union > 0 else 0.0
            cells.append(f"{jaccard:5.0%}".rjust(6))
        print(f"    {short[m1]:4s}" + "".join(cells))
    print("\n    fire counts (gradeable days each method triggered): "
          + ", ".join(f"{short[m]}={fire_counts[m]}" for m in names))


# --------------------------------------------------------------------------- #
# 5. Concrete examples - real stocks/dates, so the numbers above are legible, not
# just abstract stats. NOTE: these are hand-picked WORKED examples to illustrate the
# mechanism ("what did this method actually see, and what happened"), not a random or
# representative sample - the hit-rate table above is the honest success rate.
# --------------------------------------------------------------------------- #
# Only show the context fields that a given method's own logic actually looks at -
# these columns exist for every event regardless of method (they're computed for every
# day unconditionally), so without this a DI value would show up next to an E_relative_
# strength example even though Method E never looks at DI.
RELEVANT_EXTRAS = {
    "D_trend_inception": ["di"],
    "D2_trend_inception_loose": ["di"],
    "E_relative_strength": ["rs"],
    "E2_relative_strength_uptrend": ["rs"],
    "F_episodic_pivot": ["ep"],
    "G_pre_breakout_composite": ["score_g"],
    "G2_pre_breakout_retuned": ["score_g2"],
    "H_pressure_cooker": ["score_h"],
    "AE_combo": ["rs"],
    "AD_combo": ["di"],
    "ED_combo": ["rs", "di"],
    "AED_combo": ["rs", "di"],
    "SB_and_G2": ["score_g2"],
    "HC_and_G2": ["score_g2"],
    "SB_and_H": ["score_h"],
    "HC_and_H": ["score_h"],
    "SB_after_G2_alert": ["score_g2"],
    "HC_after_G2_alert": ["score_g2"],
    "SB_after_H_alert": ["score_h"],
    "HC_after_H_alert": ["score_h"],
}


def print_examples(df: pd.DataFrame, method: str, n: int = 2):
    subset = df[(df["method"] == method) & (df["worked"] == True)]
    print(f"\n  {method}:")
    if subset.empty:
        print("    (no successful examples in this dataset to show)")
        return
    wanted = RELEVANT_EXTRAS.get(method, [])
    for _, ev in subset.sort_values("date", ascending=False).head(n).iterrows():
        extras = []
        if "di" in wanted and pd.notna(ev.get("plus_di")):
            extras.append(f"+DI={ev['plus_di']:.1f} / -DI={ev['minus_di']:.1f}")
        if "rs" in wanted and pd.notna(ev.get("rs_ratio")):
            extras.append(f"price/Nifty ratio={ev['rs_ratio']:.4f}")
        if "ep" in wanted and pd.notna(ev.get("ep_gap_pct")):
            extras.append(f"gap={ev['ep_gap_pct']:.1f}%, volume={ev['ep_vol_ratio']:.1f}x avg")
        if "score_g" in wanted and pd.notna(ev.get("score_g")):
            extras.append(f"pre-breakout score={ev['score_g']:.0f}/100")
        if "score_g2" in wanted and pd.notna(ev.get("score_g2")):
            extras.append(f"pre-breakout score (G2)={ev['score_g2']:.0f}/100")
        if "score_h" in wanted and pd.notna(ev.get("score_h")):
            extras.append(f"pressure-cooker score={ev['score_h']:.0f}/100")
        extra_str = ("  [" + ", ".join(extras) + "]") if extras else ""
        target_pct = (ev["target"] / ev["price"] - 1) * 100
        stop_pct = (ev["stop"] / ev["price"] - 1) * 100
        print(f"    {ev['symbol']} on {ev['date'].strftime('%Y-%m-%d')}: "
              f"price=Rs.{ev['price']:.2f}, resistance=Rs.{ev['resistance']:.2f}, "
              f"ADX={ev['adx']:.1f}{extra_str}")
        print(f"      -> stop=Rs.{ev['stop']:.2f} ({stop_pct:+.1f}%), target=Rs.{ev['target']:.2f} ({target_pct:+.1f}%): "
              f"price reached the target before the stop within {settings.FOLLOWTHROUGH_WINDOW} trading days. "
              f"(For reference only, since the target can be hit intraday then pull back: the close exactly "
              f"10 trading days later was {ev['fwd_ret_10d_pct']:+.1f}%.)")


# --------------------------------------------------------------------------- #
def main():
    watchlist = build_universe()
    print(f"Recomputing features for {len(watchlist)} stocks "
          f"(source: {settings.PRICE_SOURCE})...\n")
    df, fire_counts, overlap_counts = collect_events(watchlist)
    if df.empty:
        print("\nNo graded breakout events found - nothing to analyze.")
        return

    n_stocks = df["symbol"].nunique()
    n_events = len(df)
    overall_rate = df["worked"].mean()
    print(f"\nTotal: {n_events} graded events across {n_stocks} stocks and "
          f"{df['method'].nunique()} methods (overall hit rate {overall_rate:.1%}).")
    print(f"Rule of thumb used below: a bucket needs n>={MIN_BUCKET_N} before its "
          f"hit rate is treated as more than noise.")

    method_a = df[df["method"] == "A_donchian_minervini"]
    if len(method_a) < 150:
        print(f"\n  ** SAMPLE SIZE WARNING **\n"
              f"  {len(method_a)} Method-A events is thin for pooled statistics, and each bucket\n"
              f"  below gets even fewer. Treat every result here as a hypothesis, not a\n"
              f"  conclusion - if it's still thin, raise settings.UNIVERSE_SIZE rather than\n"
              f"  forcing a verdict from this data.")

    test_persistence(method_a)
    test_features(method_a)
    test_score(df)
    test_score(df, method="I_volume_profile", section="2c")
    test_score(df, method="K_anchored_vwap", section="2d")
    test_analog_predictiveness(df)
    test_methods(df)
    report_overlap(fire_counts, overlap_counts)

    print("\n" + "=" * 72)
    print("5. CONCRETE EXAMPLES - real stocks/dates for the methods/combos worth")
    print("   understanding in real terms (hand-picked successes to show the mechanism;")
    print("   see section 3 above for the honest hit rate)")
    print("=" * 72)
    for m in ["D_trend_inception", "D2_trend_inception_loose", "E_relative_strength",
              "E2_relative_strength_uptrend", "G_pre_breakout_composite", "G2_pre_breakout_retuned",
              "H_pressure_cooker", "AE_combo", "AD_combo", "ED_combo", "AED_combo",
              "SB_and_G2", "SB_and_H", "HC_and_G2", "HC_and_H",
              "SB_after_G2_alert", "SB_after_H_alert", "HC_after_G2_alert", "HC_after_H_alert",
              "I_volume_profile", "J_ttm_squeeze", "K_anchored_vwap",
              "L_sb_deep_base", "L2_hc_deep_base", "M_shakeout_rebreak",
              "AI_combo", "AJ_combo"]:
        print_examples(df, m)

    print("\n" + "=" * 72)
    print("Done. This script does not write any files - it's a research check, run")
    print("manually, not part of run_scan.py.")
    print("=" * 72)


if __name__ == "__main__":
    main()
