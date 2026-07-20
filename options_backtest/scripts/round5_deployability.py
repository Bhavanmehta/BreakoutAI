#!/usr/bin/env python3
"""
Round 5: deployability checks on the 2 genuinely-robust winners (MeanReversion-Expiry,
FlowExpiry-pcr-Wide) before any live execution -- closes the two most decision-relevant
open items from FINDINGS.md section 5, plus answers "what can the next few weeks realistically
look like":

  1. High-vol-event stress test: did the tiny max-DDs come from real SL discipline, or from
     no adverse shock ever landing inside a holding window (untested luck)? For every trade we
     measure the worst ADVERSE spot move during its hold (down-move for a bull put-spread,
     up-move for a bear call-spread) and check how those trades exited.
  2. Cost sensitivity: rerun at ROUND_TRIP_COST in {150, 300, 500, 750}. The SL/target triggers
     are evaluated on pre-cost pnl, so the trade SET is identical across costs -- only the
     realized pnl shifts -- which is exactly why this cleanly isolates cost fragility.
  3. Horizon bootstrap: "next few weeks" of a weekly-expiry strategy is ~3-4 trades. Bootstrap
     a fixed 3- and 4-trade horizon (5000 draws) to show the realistic near-term outcome band,
     not the full-year headline.

Engine untouched; reuses backtest.py + zen_sweep.py's pcr signal builder.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest as bt
from zen_sweep import build_pcr, sig_pcr

OUT_DIR = bt.OUT_DIR
RNG = np.random.default_rng(42)
EVENT_THRESHOLDS = (0.010, 0.015, 0.020)  # |daily spot move| that counts as an "event"


def build_strategies():
    mr = next(s for s in bt.STRATEGIES if s.name == "MeanReversion-Expiry")
    pcr_wide = bt.Strategy(
        name="FlowExpiry-pcr-Wide", signal_fn="pcr", hold_type="expiry",
        short_otm_pct=0.015, wing_pct=0.010, sl_mult=1.5, target_frac=0.6,
        entry_offset_days=2, notes="Round 4's most-robust flow winner.")
    return [mr, pcr_wide]


def annotate_adverse_moves(tdf, spot):
    """Per trade, the worst adverse spot move during its hold (exposure = after entry, thru exit)."""
    ret = spot.pct_change()
    worst = []
    for t in tdf.itertuples(index=False):
        mask = (ret.index > t.entry_date) & (ret.index <= t.exit_date)
        held = ret[mask].dropna()
        if len(held) == 0:
            worst.append(0.0)
            continue
        sign = 1 if t.direction == "bull" else -1  # bull loses on down-move; bear on up-move
        adverse = (-sign * held)  # positive = adverse magnitude that day
        worst.append(float(adverse.max()))
    out = tdf.copy()
    out["worst_adverse_move_pct"] = np.round(np.array(worst) * 100, 2)
    return out


def cost_sweep(strat, spot, fut_ohlc, opt_idx, days, expiry_map, costs=(150, 300, 500, 750)):
    rows = []
    orig = bt.ROUND_TRIP_COST
    for c in costs:
        bt.ROUND_TRIP_COST = float(c)
        trades = bt.run_expiry_hold(spot, fut_ohlc, opt_idx, days, expiry_map, strat)
        stats, _ = bt.compute_stats(trades, strat.name)
        stats["round_trip_cost"] = c
        rows.append(stats)
    bt.ROUND_TRIP_COST = orig
    return pd.DataFrame(rows)


def horizon_bootstrap(tdf, horizons=(3, 4), n_boot=5000):
    pnl = tdf["pnl"].values
    rows = []
    for h in horizons:
        draws = RNG.choice(pnl, size=(n_boot, h), replace=True).sum(axis=1) / bt.CAPITAL * 100
        rows.append({
            "horizon_trades": h,
            "p5_pct": round(float(np.percentile(draws, 5)), 2),
            "median_pct": round(float(np.median(draws)), 2),
            "p95_pct": round(float(np.percentile(draws, 95)), 2),
            "pct_profitable": round(float((draws > 0).mean() * 100), 1),
            "prob_lose_gt_5pct": round(float((draws < -5).mean() * 100), 1),
        })
    return pd.DataFrame(rows)


def main():
    df = bt.load_data()
    spot = bt.build_spot_series(df)
    fut_ohlc = bt.build_fut_ohlc(df)
    opt_idx, _ = bt.build_option_index(df)
    days = bt.trading_days(df)
    expiry_map = bt.front_week_expiry_map(df)

    # wire pcr signal for FlowExpiry-pcr-Wide
    pcr = build_pcr(df, expiry_map)
    bt.SIGNAL_FNS["pcr"] = lambda spot, fut_ohlc, opt_idx, d, xpry: sig_pcr(pcr, d)

    strategies = build_strategies()

    # --- context: the biggest spot moves that existed in-sample ---
    ret = spot.pct_change().dropna()
    top = ret.reindex(ret.abs().sort_values(ascending=False).index)[:12]
    print("\n=== Biggest single-day spot moves in-sample (top 12) ===")
    for d, r in top.items():
        print(f"  {pd.Timestamp(d).date()}  {r*100:+.2f}%")
    print("\n  Event-day counts by threshold:")
    for th in EVENT_THRESHOLDS:
        print(f"    |move| >= {th*100:.1f}%: {(ret.abs() >= th).sum()} days")

    stress_rows, boot_frames, cost_frames = [], [], []
    for strat in strategies:
        bt.ROUND_TRIP_COST = 300.0
        trades = bt.run_expiry_hold(spot, fut_ohlc, opt_idx, days, expiry_map, strat)
        _, tdf = bt.compute_stats(trades, strat.name)
        tdf = annotate_adverse_moves(tdf, spot)
        tdf.to_csv(OUT_DIR / f"round5_stress_{strat.name}.csv", index=False)

        print(f"\n\n########## {strat.name} ##########")
        print(f"trades={len(tdf)}  win_rate={ (tdf.pnl>0).mean()*100:.1f}%  "
              f"total_return={tdf.pnl.sum()/bt.CAPITAL*100:+.2f}%")
        print("exit_reason breakdown:", tdf.exit_reason.value_counts().to_dict())

        # --- 1. stress test ---
        print("\n--- 1. High-vol-event stress test ---")
        for th_pct in (1.0, 1.5):
            exposed = tdf[tdf.worst_adverse_move_pct >= th_pct]
            print(f"  trades exposed to an adverse move >= {th_pct:.1f}%: {len(exposed)}/{len(tdf)}")
            if len(exposed):
                print(f"    their exit reasons: {exposed.exit_reason.value_counts().to_dict()}")
                print(f"    their mean pnl: Rs {exposed.pnl.mean():,.0f}  "
                      f"min pnl: Rs {exposed.pnl.min():,.0f}")
        worst = tdf.loc[tdf.pnl.idxmin()]
        print(f"  WORST single trade: pnl Rs {worst.pnl:,.0f} ({worst.pnl/bt.CAPITAL*100:+.2f}%), "
              f"exit={worst.exit_reason}, worst_adverse_move={worst.worst_adverse_move_pct:+.2f}%, "
              f"entry {pd.Timestamp(worst.entry_date).date()} -> exit {pd.Timestamp(worst.exit_date).date()}")
        stress_rows.append({
            "strategy": strat.name, "trades": len(tdf),
            "n_stop_loss": int((tdf.exit_reason == "stop_loss").sum()),
            "n_adverse_ge_1pct": int((tdf.worst_adverse_move_pct >= 1.0).sum()),
            "n_adverse_ge_1_5pct": int((tdf.worst_adverse_move_pct >= 1.5).sum()),
            "worst_trade_pct": round(float(worst.pnl / bt.CAPITAL * 100), 2),
            "worst_trade_exit": worst.exit_reason,
            "max_adverse_move_pct": round(float(tdf.worst_adverse_move_pct.max()), 2),
        })

        # --- 2. cost sensitivity ---
        print("\n--- 2. Cost sensitivity (ROUND_TRIP_COST) ---")
        cdf = cost_sweep(strat, spot, fut_ohlc, opt_idx, days, expiry_map)
        cdf["strategy"] = strat.name
        cost_frames.append(cdf)
        print(cdf[["round_trip_cost", "trades", "total_return_pct",
                   "profit_factor", "max_drawdown_pct"]].to_string(index=False))

        # --- 3. horizon bootstrap ---
        print("\n--- 3. Near-term horizon bootstrap (next 3 / 4 weekly trades) ---")
        hdf = horizon_bootstrap(tdf)
        hdf["strategy"] = strat.name
        boot_frames.append(hdf)
        print(hdf.to_string(index=False))

    pd.DataFrame(stress_rows).to_csv(OUT_DIR / "round5_stress_summary.csv", index=False)
    pd.concat(cost_frames, ignore_index=True).to_csv(OUT_DIR / "round5_cost_sweep.csv", index=False)
    pd.concat(boot_frames, ignore_index=True).to_csv(OUT_DIR / "round5_horizon_bootstrap.csv", index=False)
    print(f"\n\nSaved -> {OUT_DIR}/round5_stress_summary.csv, round5_cost_sweep.csv, "
          f"round5_horizon_bootstrap.csv, round5_stress_<strategy>.csv")


if __name__ == "__main__":
    main()
