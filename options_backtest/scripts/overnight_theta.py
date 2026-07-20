"""
overnight_theta.py  --  Does "sell premium at close, buy back at next open" actually
work on NIFTY, and if so why did the earlier overnight strategies lose?

This is a DELIBERATELY non-directional test. The earlier overnight backtests in
backtest.py were *directional* (they picked a side from a signal, then held a single
short leg overnight). That conflates two bets: (a) "which way does NIFTY gap" and
(b) "does overnight theta/vol-crush pay". This script strips out bet (a) entirely by
selling BOTH sides (short strangle / short straddle), so what remains is the pure
overnight short-premium edge -- exactly the thing the Dhan overnight sellers claim.

Honesty rules baked in:
  1. Entry = today's CLOSE price (ClsPric) of each leg.  Exit = NEXT trading day's
     OPEN price (OpnPric).  This is the real close->open hold, nothing else.
  2. STRICT exit: we only keep a trade if a *real traded open print* (OpnPric > 0)
     exists for every leg at exit. If the open is missing we DROP the trade and
     count it -- we never let the engine fall back to the close (which would silently
     erase the entire overnight move and manufacture a fake profit).
  3. GROSS (zero cost) and NET (realistic per-fill cost) are both reported, so you can
     see how much of any edge is real and how much the bid/ask + slippage eats.
  4. The worst single overnight trades (the gap tail) are printed for every variant,
     because that tail is the whole risk of short premium.

Run:  python scripts/overnight_theta.py
"""
import numpy as np
import pandas as pd

from backtest import (
    load_data, build_spot_series, build_fut_ohlc, build_option_index,
    trading_days, front_week_expiry_map, find_tradable_strike, compute_stats,
    LOT_SIZE, CAPITAL, STRIKE_STEP, OUT_DIR,
)

# Cost model, consistent with backtest.py's ROUND_TRIP_COST=300 for a 2-leg vertical
# (2 legs * 2 fills * Rs 75/fill = Rs 300). So per *fill* = Rs 75.
COST_PER_FILL = 75.0


def get_open_price(opt_idx, day, xpry, strike, tp):
    """Return the REAL traded open for a leg, or None if no genuine open print exists."""
    row = opt_idx.get((day, xpry, int(strike), tp))
    if row is None:
        return None
    op = row["open"]
    if op is None or not (op > 0):
        return None
    return float(op)


def run_overnight_premium(opt_idx, spot, days, expiry_map, *,
                          otm_pct, wing_pct=None, cost_per_fill=COST_PER_FILL,
                          skip_expiry_day=False, label=""):
    """
    Non-directional overnight short premium.
      otm_pct   : how far OTM to place the short call/put (0.0 => ATM straddle).
      wing_pct  : if set, buy protective wings this far beyond the shorts (iron condor,
                  defined risk). If None => naked short strangle/straddle.
    Enter at today's close, exit at next day's open (strict real-open exits only).
    Returns (trades, skipped, dropped_no_open).
    """
    trades = []
    skipped = 0          # could not build the position at entry
    dropped_no_open = 0  # built at entry but no real open print at exit -> discarded

    naked = wing_pct is None
    n_legs = 2 if naked else 4

    for i in range(len(days) - 1):
        d = days[i]
        nxt = days[i + 1]
        if d not in expiry_map or d not in spot.index:
            continue
        xpry = expiry_map[d]
        # optionally avoid the expiry session itself (pin/settlement risk, thin quotes)
        if skip_expiry_day and pd.Timestamp(xpry) == pd.Timestamp(d):
            continue
        s = float(spot.loc[d])

        # --- build the short legs at today's close ---
        cs, c_row = find_tradable_strike(opt_idx, d, xpry, s * (1 + otm_pct), "CE")
        ps, p_row = find_tradable_strike(opt_idx, d, xpry, s * (1 - otm_pct), "PE")
        if cs is None or ps is None:
            skipped += 1
            continue
        credit = c_row["close"] + p_row["close"]  # premium collected (per share)

        # --- protective wings (iron condor) at today's close ---
        debit_wing = 0.0
        wc = wp = None
        if not naked:
            wc, wc_row = find_tradable_strike(opt_idx, d, xpry, s * (1 + otm_pct + wing_pct), "CE")
            wp, wp_row = find_tradable_strike(opt_idx, d, xpry, s * (1 - otm_pct - wing_pct), "PE")
            if wc is None or wp is None:
                skipped += 1
                continue
            debit_wing = wc_row["close"] + wp_row["close"]

        net_credit = credit - debit_wing
        if net_credit <= 0:
            skipped += 1
            continue

        # --- exit at NEXT OPEN, strict: every leg needs a real open print ---
        c_exit = get_open_price(opt_idx, nxt, xpry, cs, "CE")
        p_exit = get_open_price(opt_idx, nxt, xpry, ps, "PE")
        legs_ok = c_exit is not None and p_exit is not None
        if not naked:
            wc_exit = get_open_price(opt_idx, nxt, xpry, wc, "CE")
            wp_exit = get_open_price(opt_idx, nxt, xpry, wp, "PE")
            legs_ok = legs_ok and wc_exit is not None and wp_exit is not None
        if not legs_ok:
            dropped_no_open += 1
            continue

        exit_debit = c_exit + p_exit          # cost to buy back the shorts
        exit_wing_credit = 0.0
        if not naked:
            exit_wing_credit = wc_exit + wp_exit  # we sell the wings back

        # PnL per share = premium kept on shorts - premium recovered on wings
        pnl_per_share = (credit - exit_debit) - (debit_wing - exit_wing_credit)
        gross = pnl_per_share * LOT_SIZE
        cost = n_legs * 2 * cost_per_fill
        net = gross - cost

        trades.append({
            "entry_date": d, "exit_date": nxt,
            "spot": round(s, 1), "call_strike": cs, "put_strike": ps,
            "net_credit_rs": round(net_credit * LOT_SIZE, 0),
            "pnl": net, "pnl_gross": gross, "cost": cost,
        })

    return trades, skipped, dropped_no_open


def summarize(trades, name):
    stats, df = compute_stats(trades, name)
    if df is None:
        return stats, None
    gross_total = df["pnl_gross"].sum()
    stats["gross_return_pct"] = round(gross_total / CAPITAL * 100, 2)
    stats["cost_drag_rs"] = round(df["cost"].sum(), 0)
    stats["worst_trade_rs"] = round(df["pnl"].min(), 0)
    stats["best_trade_rs"] = round(df["pnl"].max(), 0)
    return stats, df


def main():
    df = load_data()
    spot = build_spot_series(df)
    _fut = build_fut_ohlc(df)
    opt_idx, _ = build_option_index(df)
    days = trading_days(df)
    expiry_map = front_week_expiry_map(df)
    print(f"Spot series: {len(spot)} days, {spot.index.min().date()} -> {spot.index.max().date()}\n")

    variants = [
        # name, kwargs
        ("ATM straddle (naked)",        dict(otm_pct=0.000, wing_pct=None)),
        ("1.0% strangle (naked)",       dict(otm_pct=0.010, wing_pct=None)),
        ("1.5% strangle (naked)",       dict(otm_pct=0.015, wing_pct=None)),
        ("2.0% strangle (naked)",       dict(otm_pct=0.020, wing_pct=None)),
        ("1.0% condor (1% wings)",      dict(otm_pct=0.010, wing_pct=0.010)),
        ("1.5% condor (1% wings)",      dict(otm_pct=0.015, wing_pct=0.010)),
        ("1.5% strangle, skip expiry",  dict(otm_pct=0.015, wing_pct=None, skip_expiry_day=True)),
    ]

    all_stats = []
    tails = {}
    for name, kw in variants:
        trades, skipped, dropped = run_overnight_premium(
            opt_idx, spot, days, expiry_map, label=name, **kw)
        stats, tdf = summarize(trades, name)
        stats["skipped_entry"] = skipped
        stats["dropped_no_open"] = dropped
        all_stats.append(stats)
        if tdf is not None:
            tails[name] = tdf.nsmallest(5, "pnl")[
                ["entry_date", "exit_date", "spot", "call_strike", "put_strike", "pnl"]]

    cols = ["strategy", "trades", "win_rate_pct", "gross_return_pct", "total_return_pct",
            "cost_drag_rs", "max_drawdown_pct", "profit_factor", "avg_win_rs", "avg_loss_rs",
            "worst_trade_rs", "best_trade_rs", "dropped_no_open", "skipped_entry"]
    summary = pd.DataFrame(all_stats)
    for c in cols:
        if c not in summary.columns:
            summary[c] = np.nan
    summary = summary[cols]

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 40)
    print("=" * 130)
    print("OVERNIGHT SHORT-PREMIUM (non-directional): sell at close, buy back at next open")
    print("GROSS = zero cost (raw edge)   |   NET = realistic per-fill cost   |   1 lot, simple-additive on Rs 1,00,000")
    print("=" * 130)
    print(summary.to_string(index=False))

    print("\nWorst 5 overnight trades per variant (the gap tail that short premium is short):")
    for name, t in tails.items():
        print(f"\n--- {name} ---")
        print(t.to_string(index=False))

    out = OUT_DIR / "overnight_theta_summary.csv"
    summary.to_csv(out, index=False)
    print(f"\nSaved summary -> {out}")


if __name__ == "__main__":
    main()
