"""
Robustness checks for the promising iron-condor configs.

A single +57% number off ~13 months and 57 trades is not evidence of an edge --
it's a hypothesis. Before trusting any config we check three things that
typically kill premium-selling backtests:

  1. Cost sensitivity  -- tight-strike weekly condors trade a lot of premium and
     rely on frequent small wins; if real slippage is worse than the flat Rs 600
     assumption, the edge should visibly decay. We rerun at 1x / 1.5x / 2x cost.
  2. Out-of-sample split -- does the edge exist in BOTH halves of the sample, or
     is the whole result one lucky stretch?
  3. Tail -- how much of capital does the single worst trade / worst 5 trades
     represent? Manage-at-50% + high win rate hides fat left tails.

Run:  python scripts/iron_condor_validate.py
"""
import numpy as np
import pandas as pd

import iron_condor as ic
from backtest import (
    CAPITAL,
    load_data, build_spot_series, build_fut_ohlc, build_option_index,
    trading_days, front_week_expiry_map, compute_stats,
)

CONFIGS_TO_CHECK = [
    "IC-1pct-manage50",          # flashy headline, tight strikes -> cost-fragile
    "IC-1.5pct-manage50-sl1.5",  # robust candidate: tight stop, moderate strikes
    "IC-1.5pct-hold2expiry",     # simplest: no active management
]


def total_return(trades):
    if not trades:
        return 0.0
    return sum(t["pnl"] for t in trades) / CAPITAL * 100


def main():
    df = load_data()
    spot = build_spot_series(df)
    fut_ohlc = build_fut_ohlc(df)
    opt_idx, _ = build_option_index(df)
    days = trading_days(df)
    expiry_map = front_week_expiry_map(df)

    by_name = {s.name: s for s in ic.IC_STRATEGIES}
    base_cost = ic.IC_ROUND_TRIP_COST

    print("=" * 78)
    print("1) COST SENSITIVITY  (total return %, Rs per round trip)")
    print("=" * 78)
    print(f"{'config':30s} {'x1='+str(int(base_cost)):>12} "
          f"{'x1.5='+str(int(base_cost*1.5)):>12} {'x2='+str(int(base_cost*2)):>12}")
    for name in CONFIGS_TO_CHECK:
        strat = by_name[name]
        rets = []
        for mult in (1.0, 1.5, 2.0):
            ic.IC_ROUND_TRIP_COST = base_cost * mult
            trades = ic.run_iron_condor(spot, fut_ohlc, opt_idx, days, expiry_map, strat)
            rets.append(total_return(trades))
        print(f"{name:30s} {rets[0]:>12.2f} {rets[1]:>12.2f} {rets[2]:>12.2f}")
    ic.IC_ROUND_TRIP_COST = base_cost  # restore

    print("\n" + "=" * 78)
    print("2) OUT-OF-SAMPLE SPLIT  (chronological first half vs second half)")
    print("=" * 78)
    for name in CONFIGS_TO_CHECK:
        strat = by_name[name]
        trades = ic.run_iron_condor(spot, fut_ohlc, opt_idx, days, expiry_map, strat)
        tdf = pd.DataFrame(trades).sort_values("entry_date").reset_index(drop=True)
        mid = len(tdf) // 2
        h1, h2 = tdf.iloc[:mid], tdf.iloc[mid:]
        r1 = h1["pnl"].sum() / CAPITAL * 100
        r2 = h2["pnl"].sum() / CAPITAL * 100
        wr1 = (h1["pnl"] > 0).mean() * 100
        wr2 = (h2["pnl"] > 0).mean() * 100
        d1 = f"{h1['entry_date'].min():%Y-%m-%d}->{h1['exit_date'].max():%Y-%m-%d}"
        d2 = f"{h2['entry_date'].min():%Y-%m-%d}->{h2['exit_date'].max():%Y-%m-%d}"
        print(f"{name}")
        print(f"    H1 ({len(h1)} trades, {d1}): ret={r1:+.2f}%  win={wr1:.0f}%")
        print(f"    H2 ({len(h2)} trades, {d2}): ret={r2:+.2f}%  win={wr2:.0f}%")

    print("\n" + "=" * 78)
    print("3) TAIL  (worst trades as % of capital)")
    print("=" * 78)
    for name in CONFIGS_TO_CHECK:
        strat = by_name[name]
        trades = ic.run_iron_condor(spot, fut_ohlc, opt_idx, days, expiry_map, strat)
        tdf = pd.DataFrame(trades)
        worst1 = tdf["pnl"].min() / CAPITAL * 100
        worst5 = tdf.nsmallest(5, "pnl")["pnl"].sum() / CAPITAL * 100
        avg_win = tdf.loc[tdf.pnl > 0, "pnl"].mean()
        avg_loss = tdf.loc[tdf.pnl <= 0, "pnl"].mean()
        # how many average wins does it take to recover one worst trade?
        recover = abs(tdf["pnl"].min() / avg_win) if avg_win else float("nan")
        print(f"{name}")
        print(f"    worst single = {worst1:+.2f}% of capital | worst 5 sum = {worst5:+.2f}%")
        print(f"    avg win = Rs {avg_win:,.0f} | avg loss = Rs {avg_loss:,.0f} | "
              f"wins to recover worst = {recover:.1f}")


if __name__ == "__main__":
    main()
