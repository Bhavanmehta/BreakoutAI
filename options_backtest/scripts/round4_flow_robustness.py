#!/usr/bin/env python3
"""
Round 4: apply the same robustness treatment from Round 3 (entry-offset sweep +
bootstrap resample) to the 6 profitable options-flow x expiry-hold variants found in
Round 3 (section 7c) -- especially FlowExpiry-pcr-Medium, whose PF 123x / 21-trade
sample was flagged as a likely overfit.

Reuses backtest.py's engine + zen_sweep.py's pcr/oi_bias signal builders + round3's
offset_sweep/bootstrap_return helpers, all unmodified.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest as bt
from zen_sweep import build_fut_oi, build_pcr, sig_oi_bias, sig_pcr
from round3_followups import offset_sweep, bootstrap_return

OUT_DIR = bt.OUT_DIR

# The 6 profitable combos from round3_flow_expiry_summary.csv (total_return_pct > 0)
WIDTHS = {"Tight": (0.010, 0.007), "Medium": (0.012, 0.008),
          "Wide": (0.015, 0.010), "VeryWide": (0.020, 0.012)}
PROFITABLE_COMBOS = [
    ("pcr", "Medium"), ("pcr", "Wide"), ("pcr", "VeryWide"), ("pcr", "Tight"),
    ("oi_bias", "Medium"), ("oi_bias", "Wide"),
]


def main():
    df = bt.load_data()
    spot = bt.build_spot_series(df)
    fut_ohlc = bt.build_fut_ohlc(df)
    opt_idx, _ = bt.build_option_index(df)
    days = bt.trading_days(df)
    expiry_map = bt.front_week_expiry_map(df)

    fut_oi = build_fut_oi(df)
    pcr = build_pcr(df, expiry_map)
    bt.SIGNAL_FNS["oi_bias"] = lambda spot, fut_ohlc, opt_idx, d, xpry: sig_oi_bias(spot, fut_oi, d)
    bt.SIGNAL_FNS["pcr"] = lambda spot, fut_ohlc, opt_idx, d, xpry: sig_pcr(pcr, d)

    strategies = []
    for sig_name, width_name in PROFITABLE_COMBOS:
        otm, wing = WIDTHS[width_name]
        strategies.append(bt.Strategy(
            name=f"FlowExpiry-{sig_name}-{width_name}", signal_fn=sig_name,
            hold_type="expiry", short_otm_pct=otm, wing_pct=wing,
            sl_mult=1.5, target_frac=0.6, entry_offset_days=2,
            notes=f"Round 4 robustness check on Round 3 flow winner ({sig_name}/{width_name}).",
        ))

    print("\n=== 1. Entry-offset sweep (1/2/3/4 days pre-expiry) on the 6 flow winners ===")
    offset_frames = []
    for strat in strategies:
        odf = offset_sweep(strat, spot, fut_ohlc, opt_idx, days, expiry_map)
        offset_frames.append(odf)
        print(odf[["base_strategy", "entry_offset_days", "trades", "win_rate_pct",
                    "total_return_pct", "max_drawdown_pct", "profit_factor"]].to_string(index=False))
    offset_summary = pd.concat(offset_frames, ignore_index=True)
    offset_summary.to_csv(OUT_DIR / "round4_flow_offset_sweep.csv", index=False)

    print("\n=== 2. Bootstrap resample (5000 draws) on the 6 flow winners at original offset=2 ===")
    boot_rows = []
    for strat in strategies:
        trades = bt.run_expiry_hold(spot, fut_ohlc, opt_idx, days, expiry_map, strat)
        _, tdf = bt.compute_stats(trades, strat.name)
        if tdf is None or len(tdf) < 2:
            print(f"{strat.name}: skipped (fewer than 2 trades)")
            continue
        b = bootstrap_return(tdf)
        b["strategy"] = strat.name
        boot_rows.append(b)
        print(f"{strat.name}: {b}")
    boot_summary = pd.DataFrame(boot_rows)
    boot_summary.to_csv(OUT_DIR / "round4_flow_bootstrap.csv", index=False)

    print(f"\nSaved -> {OUT_DIR}/round4_flow_offset_sweep.csv, round4_flow_bootstrap.csv")


if __name__ == "__main__":
    main()
