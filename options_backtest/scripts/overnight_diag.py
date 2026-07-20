"""
overnight_diag.py -- two honesty checks on the overnight short-premium result.

Q1: Is the loss just a handful of gap nights, or is the MEDIAN night also a loser?
    (If the median night is a small winner and only the tail loses, then the theta
     edge is real but unhedgeable with EOD data -- a very different conclusion.)

Q2: Is the unreliable single "open print" biasing the test? Re-exit the SAME trades at
    next-day OPEN vs next-day CLOSE vs next-day SETTLE (settle/close are far more robust
    prices than a thin pre-open auction print) and compare.
"""
import numpy as np
import pandas as pd

from backtest import (
    load_data, build_spot_series, build_option_index,
    trading_days, front_week_expiry_map, find_tradable_strike,
    LOT_SIZE, CAPITAL,
)


def leg_exit(opt_idx, day, xpry, strike, tp, field):
    row = opt_idx.get((day, xpry, int(strike), tp))
    if row is None:
        return None
    v = row.get(field)
    return float(v) if (v is not None and v > 0) else None


def collect(opt_idx, spot, days, expiry_map, otm_pct):
    """Build ATM/OTM short strangles; record per-night gross PnL under 3 exit prices."""
    recs = []
    for i in range(len(days) - 1):
        d, nxt = days[i], days[i + 1]
        if d not in expiry_map or d not in spot.index:
            continue
        xpry = expiry_map[d]
        s = float(spot.loc[d])
        cs, c_row = find_tradable_strike(opt_idx, d, xpry, s * (1 + otm_pct), "CE")
        ps, p_row = find_tradable_strike(opt_idx, d, xpry, s * (1 - otm_pct), "PE")
        if cs is None or ps is None:
            continue
        credit = c_row["close"] + p_row["close"]
        row = {"entry_date": d}
        for field in ("open", "close", "settle"):
            ce = leg_exit(opt_idx, nxt, xpry, cs, "CE", field)
            pe = leg_exit(opt_idx, nxt, xpry, ps, "PE", field)
            if ce is None or pe is None:
                row[field] = np.nan
            else:
                row[field] = (credit - (ce + pe)) * LOT_SIZE  # gross, per share * lot
        recs.append(row)
    return pd.DataFrame(recs)


def main():
    df = load_data()
    spot = build_spot_series(df)
    opt_idx, _ = build_option_index(df)
    days = trading_days(df)
    expiry_map = front_week_expiry_map(df)

    for otm, tag in [(0.0, "ATM straddle"), (0.015, "1.5% strangle")]:
        d = collect(opt_idx, spot, days, expiry_map, otm)
        print("=" * 100)
        print(f"{tag}: GROSS overnight PnL (Rs, 1 lot, zero cost) under 3 exit prices")
        print("=" * 100)
        for field in ("open", "close", "settle"):
            x = d[field].dropna()
            if len(x) == 0:
                continue
            worst5_sum = x.nsmallest(5).sum()
            ex_worst5 = x.sum() - worst5_sum
            print(f"  exit@{field:6s}: n={len(x):3d}  "
                  f"total={x.sum():>11,.0f}  mean={x.mean():>8,.0f}  median={x.median():>7,.0f}  "
                  f"win%={100*(x>0).mean():4.1f}  worst={x.min():>10,.0f}  "
                  f"total_ex_worst5={ex_worst5:>11,.0f}")
        print()


if __name__ == "__main__":
    main()
