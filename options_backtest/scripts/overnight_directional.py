"""
overnight_directional.py -- reverse-engineering "Zen Credit Spread Overnight" (Stratzy).

What the marketing page tells us about that algo:
  - Tags: Nifty, Hedged, DIRECTIONAL ; "Works in Directional Markets" ; Trade Type = Credit Spread
  - ~3 trades/week ; Success ratio 57.47% ; Avg profit/trade +5.43% ; Avg loss/trade -4.36%
  - Risk:Reward 1:1.25 ; Max DD -25.39% ; Win days 62.82% ; live track ~Feb 2026 -> Jul 2026

So it is NOT a non-directional condor. It is a DIRECTIONAL vertical credit spread: pick a
side (bull-put spread if bullish / bear-call spread if bearish), buy a protective wing
(hedged, capped risk), hold overnight, and -- crucially -- the avg loss (-4.36%) is far
below a 1%-wing spread's ~13% max loss, which means they CUT losers early with a stop.

This script rebuilds exactly that using the repo's OWN directional signals + build_spread,
but with two fixes over the buggy run_overnight():
  1. STRICT exits: a leg must have a real traded print (no silent fall-back to close).
  2. We test exit @ next OPEN ("pure overnight") AND @ next CLOSE ("hold the session"),
     and an optional EOD stop-loss proxy (cut at sl_mult x credit using the next close).

Goal: see which entry signal reproduces Zen's shape (~3/wk, ~57% win, positive expectancy)
in our data, so we can reason about how they actually pick entries.
"""
import numpy as np
import pandas as pd

from backtest import (
    load_data, build_spot_series, build_fut_ohlc, build_option_index,
    trading_days, front_week_expiry_map, build_spread, SIGNAL_FNS,
    LOT_SIZE, CAPITAL, OUT_DIR,
)

COST_PER_FILL = 30.0   # limit-style; 2-leg spread => 4 fills => Rs 120 round trip
N_FILLS = 4


def real_px(opt_idx, day, xpry, strike, tp, field):
    row = opt_idx.get((day, xpry, int(strike), tp))
    if row is None:
        return None
    v = row.get(field)
    return float(v) if (v is not None and v > 0) else None


def run_directional(spot, fut_ohlc, opt_idx, days, expiry_map, signal_fn, *,
                    otm_pct=0.010, wing_pct=0.010, exit_field="close",
                    sl_mult=None, cost_per_fill=COST_PER_FILL):
    """Directional credit spread from `signal_fn`, held to next `exit_field`.
    If sl_mult is set, a loss worse than sl_mult*credit (measured at exit price) is clamped
    to -sl_mult*credit (an optimistic EOD proxy for an intraday stop)."""
    trades, dropped = [], 0
    fn = SIGNAL_FNS[signal_fn]
    for i in range(len(days) - 1):
        d, nxt = days[i], days[i + 1]
        xpry = expiry_map.get(d)
        if xpry is None or (xpry - d).days > 8 or xpry == d or d not in spot.index:
            continue
        direction = fn(spot, fut_ohlc, opt_idx, d, xpry)
        if direction not in ("bull", "bear"):
            continue
        sp = build_spread(opt_idx, d, xpry, spot.loc[d], direction, otm_pct, wing_pct)
        if sp is None:
            continue
        se = real_px(opt_idx, nxt, xpry, sp["short_strike"], sp["short_type"], exit_field)
        le = real_px(opt_idx, nxt, xpry, sp["long_strike"], sp["long_type"], exit_field)
        if se is None or le is None:
            dropped += 1
            continue
        debit = se - le
        pnl_share = sp["credit"] - debit
        pnl_share = max(pnl_share, -sp["max_loss_per_share"])  # capped by the wing
        if sl_mult is not None:
            pnl_share = max(pnl_share, -sl_mult * sp["credit"])  # optimistic stop proxy
        gross = pnl_share * LOT_SIZE
        trades.append({"entry_date": d, "exit_date": nxt, "direction": direction,
                       "credit": sp["credit"], "max_loss": sp["max_loss_per_share"],
                       "pnl_gross": gross, "pnl": gross - N_FILLS * cost_per_fill})
    return pd.DataFrame(trades), dropped


def summarize(name, tdf, span_days):
    if len(tdf) == 0:
        return {"signal": name, "trades": 0}
    n = tdf["pnl"]
    wins, losses = n[n > 0], n[n <= 0]
    eq = CAPITAL + n.cumsum()
    dd = ((eq - eq.cummax()) / eq.cummax() * 100).min()
    exp = n.mean() / CAPITAL * 100
    return {
        "signal": name, "trades": len(tdf),
        "trades/wk": round(len(tdf) / (span_days / 7), 1),
        "win%": round(100 * len(wins) / len(n), 1),
        "avg_win%": round(wins.mean() / CAPITAL * 100, 2) if len(wins) else 0,
        "avg_loss%": round(losses.mean() / CAPITAL * 100, 2) if len(losses) else 0,
        "expectancy%/trade": round(exp, 2),
        "net_ret%": round(n.sum() / CAPITAL * 100, 1),
        "max_dd%": round(dd, 1),
        "%bull": round(100 * (tdf.direction == "bull").mean(), 0),
    }


def main():
    df = load_data()
    spot = build_spot_series(df)
    fut_ohlc = build_fut_ohlc(df)
    opt_idx, _ = build_option_index(df)
    days = trading_days(df)
    expiry_map = front_week_expiry_map(df)
    span = (spot.index.max() - spot.index.min()).days

    directional_signals = ["momentum3", "momentum10", "mean_rev", "vol_expand",
                           "vol_contract", "gap_pos", "skew", "always_bull"]

    print("=" * 150)
    print("ZEN TARGET PROFILE:  ~3 trades/wk | win 57.47% | avg win +5.43% | avg loss -4.36% | R:R 1:1.25 | maxDD -25.4%")
    print("Directional credit spread (1% short / 1% wing), STRICT exits, limit fills @ Rs30/fill.")
    print("=" * 150)

    for exit_field in ("open", "close"):
        for sl in (None, 1.5):
            rows = []
            for sig in directional_signals:
                tdf, _ = run_directional(spot, fut_ohlc, opt_idx, days, expiry_map, sig,
                                         exit_field=exit_field, sl_mult=sl)
                rows.append(summarize(sig, tdf, span))
            tag = f"exit@{exit_field}" + (f" + stop {sl}x credit" if sl else " + NO stop")
            print(f"\n----- {tag} -----")
            print(pd.DataFrame(rows).to_string(index=False))

    # Save the most Zen-like config's trade log for inspection
    best, _ = run_directional(spot, fut_ohlc, opt_idx, days, expiry_map, "momentum3",
                              exit_field="close", sl_mult=1.5)
    best.to_csv(OUT_DIR / "overnight_directional_momentum3.csv", index=False)
    print(f"\nSaved momentum3 trade log -> {OUT_DIR / 'overnight_directional_momentum3.csv'}")


if __name__ == "__main__":
    main()
