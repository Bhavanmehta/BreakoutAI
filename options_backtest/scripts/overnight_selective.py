"""
overnight_selective.py -- the promised upgrade to the HEDGED condor:
  * enter ONLY when premium is rich enough (net credit >= min fraction of the wing width)
    -> this naturally fires on high-IV nights and skips the cheap ones where cost wins;
  * hold to NEXT CLOSE (theta is realized during the session, not overnight);
  * skip the expiry session (pin risk);
  * model LIMIT-style execution at a lower per-fill cost (default Rs 30, vs Rs 75 market).

We sweep the richness threshold so you can see the trade-off: fewer, richer trades vs
the fixed cost drag. "cred/width" = net credit as a fraction of the max risk (wing width);
the higher it is, the more you are paid to take the risk.
"""
import numpy as np
import pandas as pd

from backtest import (
    load_data, build_spot_series, build_option_index,
    trading_days, front_week_expiry_map, find_tradable_strike,
    LOT_SIZE, CAPITAL, OUT_DIR,
)

N_FILLS = 8  # 4-leg condor round trip


def px(opt_idx, day, xpry, strike, tp, field):
    row = opt_idx.get((day, xpry, int(strike), tp))
    if row is None:
        return None
    v = row.get(field)
    return float(v) if (v is not None and v > 0) else None


def run(opt_idx, spot, days, expiry_map, *, otm_pct, wing_pct, min_cred_width,
        cost_per_fill=30.0, exit_field="close", skip_expiry=True):
    trades, dropped, seen = [], 0, 0
    for i in range(len(days) - 1):
        d, nxt = days[i], days[i + 1]
        if d not in expiry_map or d not in spot.index:
            continue
        xpry = expiry_map[d]
        if skip_expiry and pd.Timestamp(xpry) == pd.Timestamp(d):
            continue
        s = float(spot.loc[d])
        cs, c_row = find_tradable_strike(opt_idx, d, xpry, s * (1 + otm_pct), "CE")
        ps, p_row = find_tradable_strike(opt_idx, d, xpry, s * (1 - otm_pct), "PE")
        wc, wc_row = find_tradable_strike(opt_idx, d, xpry, s * (1 + otm_pct + wing_pct), "CE")
        wp, wp_row = find_tradable_strike(opt_idx, d, xpry, s * (1 - otm_pct - wing_pct), "PE")
        if None in (cs, ps, wc, wp) or wc == cs or wp == ps:
            continue
        net_credit = (c_row["close"] + p_row["close"]) - (wc_row["close"] + wp_row["close"])
        if net_credit <= 0:
            continue
        max_width = max(wc - cs, ps - wp)  # points of risk (wider side)
        cred_width = net_credit / max_width
        seen += 1
        if cred_width < min_cred_width:
            continue  # not rich enough -- skip this night

        ce = px(opt_idx, nxt, xpry, cs, "CE", exit_field)
        pe = px(opt_idx, nxt, xpry, ps, "PE", exit_field)
        wce = px(opt_idx, nxt, xpry, wc, "CE", exit_field)
        wpe = px(opt_idx, nxt, xpry, wp, "PE", exit_field)
        if None in (ce, pe, wce, wpe):
            dropped += 1
            continue
        pnl_share = (net_credit) - ((ce + pe) - (wce + wpe))
        gross = pnl_share * LOT_SIZE
        trades.append({"entry_date": d, "exit_date": nxt,
                       "cred_width": round(cred_width, 3),
                       "pnl_gross": gross, "pnl": gross - N_FILLS * cost_per_fill})
    return pd.DataFrame(trades), seen, dropped


def summarize(name, tdf, seen, span_days):
    if len(tdf) == 0:
        return {"variant": name, "trades": 0}
    n = tdf["pnl"]; g = tdf["pnl_gross"]
    eq = CAPITAL + n.cumsum()
    dd = ((eq - eq.cummax()) / eq.cummax() * 100).min()
    return {
        "variant": name, "trades": len(tdf),
        "trades/wk": round(len(tdf) / (span_days / 7), 1),
        "took%_of_nights": round(100 * len(tdf) / seen, 0) if seen else 0,
        "win%": round(100 * (n > 0).mean(), 1),
        "gross_ret%": round(g.sum() / CAPITAL * 100, 1),
        "net_ret%": round(n.sum() / CAPITAL * 100, 1),
        "max_dd%": round(dd, 1),
        "worst_night": round(g.min(), 0),
    }


def main():
    df = load_data()
    spot = build_spot_series(df)
    opt_idx, _ = build_option_index(df)
    days = trading_days(df)
    expiry_map = front_week_expiry_map(df)
    span = (spot.index.max() - spot.index.min()).days

    otm, wing = 0.010, 0.010  # 1% short / 1% wing condor (the best gross variant earlier)
    rows = []
    for thr in [0.00, 0.12, 0.15, 0.18, 0.20, 0.25, 0.30]:
        tdf, seen, dropped = run(opt_idx, spot, days, expiry_map,
                                 otm_pct=otm, wing_pct=wing, min_cred_width=thr)
        rows.append(summarize(f"cred/width >= {thr:.2f}", tdf, seen, span))

    out = pd.DataFrame(rows)
    pd.set_option("display.width", 220); pd.set_option("display.max_columns", 40)
    print("=" * 130)
    print(f"SELECTIVE hedged condor ({otm*100:.0f}% short / {wing*100:.0f}% wing), held to NEXT CLOSE, "
          f"LIMIT fills @ Rs30/fill (Rs240/condor)")
    print("Enter only on nights where net credit >= X of wing width (richer premium). Sweep X:")
    print("=" * 130)
    print(out.to_string(index=False))
    out.to_csv(OUT_DIR / "overnight_selective_summary.csv", index=False)
    print(f"\nSaved -> {OUT_DIR / 'overnight_selective_summary.csv'}")


if __name__ == "__main__":
    main()
