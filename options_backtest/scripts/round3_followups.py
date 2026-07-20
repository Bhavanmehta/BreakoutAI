#!/usr/bin/env python3
"""
Round 3 follow-ups (see FINDINGS.md section 5 "Open questions"):
  1. Entry-offset sweep (1/2/3/4 trading days pre-expiry) on the 2 winners, to check
     whether "2 days" was actually optimal or a lucky pick.
  2. Bootstrap resample (5000 draws, i.i.d. resampling of realized trade PnLs) of the 2
     winners at their original offset=2 config, to gauge how much the headline return
     depends on the specific 56/18-trade sequence observed vs. sampling noise.
     ponytail: i.i.d. resampling ignores trade-to-trade/regime autocorrelation (a real
     block-bootstrap or the "stress-test around known high-vol events" item would be the
     upgrade) -- fine for "is the sign/magnitude fragile" but not a substitute for that.
  3. Options-flow signals (pcr, oi_bias) crossed with the expiry-hold engine instead of
     overnight hold -- that combination was never run in Round 1 or 2.

Reuses backtest.py's validated engine unmodified (bt.run_expiry_hold, bt.compute_stats)
and zen_sweep.py's pcr/oi_bias signal builders unmodified.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest as bt
from zen_sweep import build_fut_oi, build_pcr, sig_oi_bias, sig_pcr

OUT_DIR = bt.OUT_DIR
RNG = np.random.default_rng(42)

WINNERS = {s.name: s for s in bt.STRATEGIES
           if s.name in ("Momentum-Expiry-HoldToSettle", "MeanReversion-Expiry")}


def bootstrap_return(trades_df, n_boot=5000):
    pnl = trades_df["pnl"].values
    n = len(pnl)
    draws = RNG.choice(pnl, size=(n_boot, n), replace=True)
    total_ret = draws.sum(axis=1) / bt.CAPITAL * 100
    return {
        "n_trades": n, "n_boot": n_boot,
        "mean_return_pct": round(total_ret.mean(), 2),
        "median_return_pct": round(float(np.median(total_ret)), 2),
        "p5_return_pct": round(float(np.percentile(total_ret, 5)), 2),
        "p95_return_pct": round(float(np.percentile(total_ret, 95)), 2),
        "pct_boot_profitable": round(float((total_ret > 0).mean() * 100), 1),
    }


def offset_sweep(base_strat, spot, fut_ohlc, opt_idx, days, expiry_map, offsets=(1, 2, 3, 4)):
    rows = []
    for off in offsets:
        fields = {**base_strat.__dict__, "entry_offset_days": off,
                  "name": f"{base_strat.name}-off{off}"}
        strat = bt.Strategy(**fields)
        trades = bt.run_expiry_hold(spot, fut_ohlc, opt_idx, days, expiry_map, strat)
        stats, _ = bt.compute_stats(trades, strat.name)
        stats["base_strategy"] = base_strat.name
        stats["entry_offset_days"] = off
        rows.append(stats)
    return pd.DataFrame(rows)


def main():
    df = bt.load_data()
    spot = bt.build_spot_series(df)
    fut_ohlc = bt.build_fut_ohlc(df)
    opt_idx, _ = bt.build_option_index(df)
    days = bt.trading_days(df)
    expiry_map = bt.front_week_expiry_map(df)

    print("\n=== 1. Entry-offset sweep (1/2/3/4 days pre-expiry) on the 2 winners ===")
    offset_frames = []
    for name, strat in WINNERS.items():
        odf = offset_sweep(strat, spot, fut_ohlc, opt_idx, days, expiry_map)
        offset_frames.append(odf)
        print(odf[["base_strategy", "entry_offset_days", "trades", "win_rate_pct",
                    "total_return_pct", "max_drawdown_pct", "profit_factor"]].to_string(index=False))
    offset_summary = pd.concat(offset_frames, ignore_index=True)
    offset_summary.to_csv(OUT_DIR / "round3_offset_sweep.csv", index=False)

    print("\n=== 2. Bootstrap resample (5000 draws) of the 2 winners at original offset=2 ===")
    boot_rows = []
    for name, strat in WINNERS.items():
        trades = bt.run_expiry_hold(spot, fut_ohlc, opt_idx, days, expiry_map, strat)
        _, tdf = bt.compute_stats(trades, name)
        b = bootstrap_return(tdf)
        b["strategy"] = name
        boot_rows.append(b)
        print(f"{name}: {b}")
    boot_summary = pd.DataFrame(boot_rows)
    boot_summary.to_csv(OUT_DIR / "round3_bootstrap.csv", index=False)

    print("\n=== 3. Options-flow signals (pcr, oi_bias) x expiry-hold engine ===")
    fut_oi = build_fut_oi(df)
    pcr = build_pcr(df, expiry_map)
    bt.SIGNAL_FNS["oi_bias"] = lambda spot, fut_ohlc, opt_idx, d, xpry: sig_oi_bias(spot, fut_oi, d)
    bt.SIGNAL_FNS["pcr"] = lambda spot, fut_ohlc, opt_idx, d, xpry: sig_pcr(pcr, d)

    widths = [
        ("Tight", 0.010, 0.007), ("Medium", 0.012, 0.008),
        ("Wide", 0.015, 0.010), ("VeryWide", 0.020, 0.012),
    ]
    flow_rows = []
    for sig_name in ("pcr", "oi_bias"):
        for width_name, otm, wing in widths:
            strat = bt.Strategy(
                name=f"FlowExpiry-{sig_name}-{width_name}", signal_fn=sig_name,
                hold_type="expiry", short_otm_pct=otm, wing_pct=wing,
                sl_mult=1.5, target_frac=0.6, entry_offset_days=2,
                notes=f"Options-flow signal ({sig_name}) timed entry, held to settlement (Round 3).",
            )
            trades = bt.run_expiry_hold(spot, fut_ohlc, opt_idx, days, expiry_map, strat)
            stats, tdf = bt.compute_stats(trades, strat.name)
            stats["signal"] = sig_name
            stats["width"] = width_name
            flow_rows.append(stats)
            if tdf is not None:
                tdf.to_csv(OUT_DIR / f"trades_{strat.name}.csv", index=False)

    flow_summary = pd.DataFrame(flow_rows).sort_values("total_return_pct", ascending=False)
    flow_summary.to_csv(OUT_DIR / "round3_flow_expiry_summary.csv", index=False)
    print(flow_summary[["strategy", "trades", "win_rate_pct", "total_return_pct",
                         "max_drawdown_pct", "profit_factor"]].to_string(index=False))

    print(f"\nSaved -> {OUT_DIR}/round3_offset_sweep.csv, round3_bootstrap.csv, "
          f"round3_flow_expiry_summary.csv")


if __name__ == "__main__":
    main()
