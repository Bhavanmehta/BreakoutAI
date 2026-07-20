"""
overnight_cost.py -- the real diagnosis: for the HEDGED condor held to next close
(the best-looking variant, +40% gross), how much of the account does transaction cost
eat, and what per-fill execution cost would you need to break even?

Key number to watch: gross-edge-PER-NIGHT vs cost-PER-NIGHT. If the sliver of theta you
harvest each night is smaller than what it costs to harvest it, the strategy is dead no
matter how nice the gross % looks -- because you trade it ~210 times.
"""
import numpy as np
import pandas as pd

from backtest import (
    load_data, build_spot_series, build_option_index,
    trading_days, front_week_expiry_map,
    CAPITAL, OUT_DIR,
)
from overnight_hedged import run_condor

N_FILLS = 8  # 4-leg condor, enter+exit


def main():
    df = load_data()
    spot = build_spot_series(df)
    opt_idx, _ = build_option_index(df)
    days = trading_days(df)
    expiry_map = front_week_expiry_map(df)

    variants = [
        ("1.0% short / 1.0% wing", 0.010, 0.010),
        ("1.5% short / 1.0% wing", 0.015, 0.010),
        ("2.0% short / 1.0% wing", 0.020, 0.010),
    ]
    per_fill_levels = [0, 20, 30, 40, 60, 75]

    rows = []
    for name, otm, wing in variants:
        tdf, _ = run_condor(opt_idx, spot, days, expiry_map,
                            otm_pct=otm, wing_pct=wing, exit_field="close")
        g = tdf["pnl_gross"]
        n = len(tdf)
        gross_total = g.sum()
        gross_per_night = gross_total / n
        breakeven_per_fill = gross_total / (n * N_FILLS)
        # also the tail-robust version (drop 5 worst gap nights)
        gross_ex5 = gross_total - g.nsmallest(5).sum()
        be_per_fill_ex5 = gross_ex5 / ((n - 5) * N_FILLS)

        row = {"variant": name, "nights": n,
               "gross/night_rs": round(gross_per_night, 0),
               "breakeven_/fill": round(breakeven_per_fill, 1),
               "breakeven_/fill_ex5tail": round(be_per_fill_ex5, 1)}
        for pf in per_fill_levels:
            net = gross_total - n * N_FILLS * pf
            row[f"net%@{pf}/fill"] = round(net / CAPITAL * 100, 1)
        rows.append(row)

    out = pd.DataFrame(rows)
    pd.set_option("display.width", 240)
    pd.set_option("display.max_columns", 40)
    print("=" * 150)
    print("HEDGED CONDOR held to NEXT CLOSE -- cost sensitivity (1 lot, Rs 1,00,000, ~210 nights)")
    print("net%@X/fill = total net return if each of the 8 fills costs Rs X (slippage+brokerage+taxes all-in)")
    print("=" * 150)
    print(out.to_string(index=False))
    print("\nReality check on all-in cost per fill for NIFTY weekly options:")
    print("  brokerage ~Rs2-3 + STT/exch/GST/stamp ~Rs5-15 + HALF-SPREAD SLIPPAGE ~Rs50-110 (1-1.5pt x 75 lot)")
    print("  => realistic all-in is ~Rs40-100/fill. Compare to the breakeven_/fill column above.")

    out.to_csv(OUT_DIR / "overnight_cost_sensitivity.csv", index=False)
    print(f"\nSaved -> {OUT_DIR / 'overnight_cost_sensitivity.csv'}")


if __name__ == "__main__":
    main()
