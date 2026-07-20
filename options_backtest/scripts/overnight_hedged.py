"""
overnight_hedged.py -- the HEDGED (defined-risk iron condor) overnight question, done right.

You always meant hedged, not naked. Fair. So this tests short iron condors ONLY
(short strangle + long protective wings = capped risk), entered at today's CLOSE and
exited either at:
    - next-day OPEN   ("pure overnight": sell at close, buy back at open), or
    - next-day CLOSE  ("hold a day":     sell at close, buy back next close, ~1 session
                       of theta captured while the market is actually open)

For every variant we show GROSS (zero cost) and NET (realistic Rs 75/fill => Rs 600 for a
4-leg condor round trip), the win rate, the worst night (the capped tail), and the total
with the 5 worst nights removed (to see whether the edge is real-but-tail-dominated).

Strict: a trade is only counted if a REAL traded price (>0) exists for every one of the
4 legs at exit -- otherwise it is dropped and counted (never silently filled at close).

Run:  python scripts/overnight_hedged.py
"""
import numpy as np
import pandas as pd

from backtest import (
    load_data, build_spot_series, build_option_index,
    trading_days, front_week_expiry_map, find_tradable_strike,
    LOT_SIZE, CAPITAL, OUT_DIR,
)

COST_PER_FILL = 75.0
N_LEGS = 4  # iron condor


def px(opt_idx, day, xpry, strike, tp, field):
    row = opt_idx.get((day, xpry, int(strike), tp))
    if row is None:
        return None
    v = row.get(field)
    return float(v) if (v is not None and v > 0) else None


def run_condor(opt_idx, spot, days, expiry_map, *, otm_pct, wing_pct, exit_field,
               skip_expiry_day=False):
    trades = []
    dropped = 0
    for i in range(len(days) - 1):
        d, nxt = days[i], days[i + 1]
        if d not in expiry_map or d not in spot.index:
            continue
        xpry = expiry_map[d]
        if skip_expiry_day and pd.Timestamp(xpry) == pd.Timestamp(d):
            continue
        s = float(spot.loc[d])

        cs, c_row = find_tradable_strike(opt_idx, d, xpry, s * (1 + otm_pct), "CE")
        ps, p_row = find_tradable_strike(opt_idx, d, xpry, s * (1 - otm_pct), "PE")
        wc, wc_row = find_tradable_strike(opt_idx, d, xpry, s * (1 + otm_pct + wing_pct), "CE")
        wp, wp_row = find_tradable_strike(opt_idx, d, xpry, s * (1 - otm_pct - wing_pct), "PE")
        if None in (cs, ps, wc, wp):
            continue
        # avoid degenerate wing == short strike
        if wc == cs or wp == ps:
            continue

        credit = c_row["close"] + p_row["close"]        # sell inner
        debit_wing = wc_row["close"] + wp_row["close"]  # buy outer
        net_credit = credit - debit_wing
        if net_credit <= 0:
            continue

        ce = px(opt_idx, nxt, xpry, cs, "CE", exit_field)
        pe = px(opt_idx, nxt, xpry, ps, "PE", exit_field)
        wce = px(opt_idx, nxt, xpry, wc, "CE", exit_field)
        wpe = px(opt_idx, nxt, xpry, wp, "PE", exit_field)
        if None in (ce, pe, wce, wpe):
            dropped += 1
            continue

        exit_debit = ce + pe          # buy back inner
        exit_wing_credit = wce + wpe  # sell outer
        pnl_share = (credit - exit_debit) - (debit_wing - exit_wing_credit)
        gross = pnl_share * LOT_SIZE
        cost = N_LEGS * 2 * COST_PER_FILL
        trades.append({
            "entry_date": d, "exit_date": nxt,
            "net_credit_rs": round(net_credit * LOT_SIZE, 0),
            "pnl_gross": gross, "pnl": gross - cost,
        })
    return pd.DataFrame(trades), dropped


def stats_row(name, tdf, dropped):
    if len(tdf) == 0:
        return {"variant": name, "trades": 0}
    g = tdf["pnl_gross"]
    n = tdf["pnl"]
    worst5 = g.nsmallest(5).sum()
    return {
        "variant": name,
        "trades": len(tdf),
        "win%_net": round(100 * (n > 0).mean(), 1),
        "gross_ret%": round(g.sum() / CAPITAL * 100, 1),
        "net_ret%": round(n.sum() / CAPITAL * 100, 1),
        "median_net": round(n.median(), 0),
        "worst_night": round(g.min(), 0),
        "best_night": round(g.max(), 0),
        "gross_ex_worst5%": round((g.sum() - worst5) / CAPITAL * 100, 1),
        "dropped": dropped,
    }


def main():
    df = load_data()
    spot = build_spot_series(df)
    opt_idx, _ = build_option_index(df)
    days = trading_days(df)
    expiry_map = front_week_expiry_map(df)
    print(f"Spot: {len(spot)} days, {spot.index.min().date()} -> {spot.index.max().date()}\n")

    # (short OTM %, wing width %)
    widths = [
        ("0.5% short / 0.5% wing", 0.005, 0.005),
        ("1.0% short / 0.5% wing", 0.010, 0.005),
        ("1.0% short / 1.0% wing", 0.010, 0.010),
        ("1.5% short / 1.0% wing", 0.015, 0.010),
        ("2.0% short / 1.0% wing", 0.020, 0.010),
    ]

    rows = []
    for exit_field in ("open", "close"):
        for name, otm, wing in widths:
            tdf, dropped = run_condor(opt_idx, spot, days, expiry_map,
                                      otm_pct=otm, wing_pct=wing, exit_field=exit_field)
            r = stats_row(f"[exit@{exit_field}] {name}", tdf, dropped)
            rows.append(r)

    out = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 40)
    print("=" * 140)
    print("HEDGED OVERNIGHT (iron condor, capped risk): enter at CLOSE, exit at next OPEN vs next CLOSE")
    print("GROSS = zero cost | NET = Rs 600/condor round trip | 1 lot on Rs 1,00,000 | gross_ex_worst5 = raw edge minus the 5 worst gap nights")
    print("=" * 140)
    print(out.to_string(index=False))

    out.to_csv(OUT_DIR / "overnight_hedged_summary.csv", index=False)
    print(f"\nSaved -> {OUT_DIR / 'overnight_hedged_summary.csv'}")


if __name__ == "__main__":
    main()
