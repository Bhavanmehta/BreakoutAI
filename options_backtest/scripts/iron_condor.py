"""
Iron-condor variant of the expiry-hold engine.

Motivation (see FINDINGS.md): the one-sided credit-spread strategies top out
around 3-4%/month with very few trades and marginal robustness. An iron condor
is non-directional: we sell BOTH a put spread (below spot) and a call spread
(above spot) on the same front-week expiry, collect both credits, and profit
when the index stays range-bound. We also test "manage winners early" -- close
the whole position once combined MTM profit reaches target_frac of the net
credit (the classic tastytrade 50% rule) -- vs. holding to settlement.

This file DELIBERATELY reuses the primitives in backtest.py (data loading,
strike selection, per-leg pricing, stats) so the condor is measured on exactly
the same footing as the single-sided book. Only the position construction and
the EOD management loop are new.

Run:  python scripts/iron_condor.py
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

import backtest as bt
from backtest import (
    LOT_SIZE, CAPITAL, ROUND_TRIP_COST,
    load_data, build_spot_series, build_fut_ohlc, build_option_index,
    trading_days, front_week_expiry_map,
    build_spread, price_leg, compute_stats, SIGNAL_FNS, OUT_DIR,
)

# An iron condor is 4 legs at entry + 4 at exit = 8 leg-crossings, i.e. double
# the single spread's round trip. backtest.ROUND_TRIP_COST covers 4 legs.
IC_ROUND_TRIP_COST = 2 * ROUND_TRIP_COST  # Rs, flat, incl. slippage


@dataclass
class ICStrategy:
    name: str
    short_otm_pct: float          # short strikes this far OTM on each side
    wing_pct: float               # long wings this far beyond the shorts
    entry_offset_days: int = 2    # enter N trading days before expiry
    target_frac: float = 0.50     # take profit at target_frac * net credit (manage winners)
    sl_mult: float = 2.0          # stop when combined loss >= sl_mult * net credit
    regime_filter: str = ""       # "" = always trade both sides; else key into SIGNAL_FNS,
                                  #   trade only when that signal is not "skip"
    notes: str = ""


def _settle_debit(opt_idx, dd, xpry, spread, fallback_debit):
    """Combined settlement debit for one spread (short - long) using settle price."""
    s_settle = opt_idx.get((dd, xpry, spread["short_strike"], spread["short_type"]), {}).get(
        "settle", None)
    l_settle = opt_idx.get((dd, xpry, spread["long_strike"], spread["long_type"]), {}).get(
        "settle", None)
    if s_settle is None or l_settle is None:
        return fallback_debit
    return s_settle - l_settle


def run_iron_condor(spot, fut_ohlc, opt_idx, df_days, expiry_map, strat: ICStrategy):
    trades = []
    seen_expiries = set()
    day_to_idx = {d: i for i, d in enumerate(df_days)}

    for i, d in enumerate(df_days):
        xpry = expiry_map.get(d)
        if xpry is None or xpry in seen_expiries:
            continue
        exp_idx = day_to_idx.get(xpry)
        if exp_idx is None:
            candidates = [k for k in day_to_idx if k <= xpry]
            if not candidates:
                continue
            exp_idx = day_to_idx[max(candidates)]
        entry_idx = exp_idx - strat.entry_offset_days
        if entry_idx < 0 or entry_idx >= len(df_days):
            continue
        entry_d = df_days[entry_idx]
        if expiry_map.get(entry_d) != xpry:
            continue
        seen_expiries.add(xpry)
        if entry_d not in spot.index:
            continue

        # optional regime gate: only sell premium when the filter is "on"
        if strat.regime_filter:
            sig = SIGNAL_FNS[strat.regime_filter](spot, fut_ohlc, opt_idx, entry_d, xpry)
            if sig == "skip":
                continue

        spot_px = spot.loc[entry_d]
        put_spread = build_spread(opt_idx, entry_d, xpry, spot_px, "bull",
                                  strat.short_otm_pct, strat.wing_pct)
        call_spread = build_spread(opt_idx, entry_d, xpry, spot_px, "bear",
                                   strat.short_otm_pct, strat.wing_pct)
        if put_spread is None or call_spread is None:
            continue

        net_credit = put_spread["credit"] + call_spread["credit"]
        if net_credit <= 0:
            continue
        # At expiry only one side can finish ITM (spot can't be below the short
        # put AND above the short call at once), so the structural max loss is
        # the wider wing minus the total credit collected.
        max_loss_per_share = max(put_spread["wing_points"],
                                 call_spread["wing_points"]) - net_credit
        target_amount = strat.target_frac * net_credit
        sl_amount = strat.sl_mult * net_credit

        exit_reason, exit_day, exit_debit = None, None, None
        for j in range(entry_idx + 1, exp_idx + 1):
            dd = df_days[j]
            ps = price_leg(opt_idx, dd, xpry, put_spread["short_strike"], put_spread["short_type"], "close")
            pl = price_leg(opt_idx, dd, xpry, put_spread["long_strike"], put_spread["long_type"], "close")
            cs = price_leg(opt_idx, dd, xpry, call_spread["short_strike"], call_spread["short_type"], "close")
            cl = price_leg(opt_idx, dd, xpry, call_spread["long_strike"], call_spread["long_type"], "close")
            if None in (ps, pl, cs, cl):
                continue
            put_debit = ps - pl
            call_debit = cs - cl
            cur_debit = put_debit + call_debit
            cur_pnl = net_credit - cur_debit
            is_last = (dd == xpry) or (j == exp_idx)

            if cur_pnl <= -sl_amount:
                exit_reason, exit_day, exit_debit = "stop_loss", dd, cur_debit
                break
            if cur_pnl >= target_amount:
                exit_reason, exit_day, exit_debit = "target", dd, cur_debit
                break
            if is_last:
                put_settle = _settle_debit(opt_idx, dd, xpry, put_spread, put_debit)
                call_settle = _settle_debit(opt_idx, dd, xpry, call_spread, call_debit)
                exit_debit = put_settle + call_settle
                exit_reason, exit_day = "expiry", dd

        if exit_reason is None or exit_debit is None:
            continue

        pnl_per_share = net_credit - exit_debit
        pnl_per_share = max(pnl_per_share, -max_loss_per_share)
        pnl = pnl_per_share * LOT_SIZE - IC_ROUND_TRIP_COST
        trades.append({
            "entry_date": entry_d, "exit_date": exit_day,
            "net_credit": round(net_credit, 2), "exit_debit": round(exit_debit, 2),
            "put_short": put_spread["short_strike"], "put_long": put_spread["long_strike"],
            "call_short": call_spread["short_strike"], "call_long": call_spread["long_strike"],
            "max_loss_per_share": round(max_loss_per_share, 2),
            "pnl": pnl, "exit_reason": exit_reason,
        })
    return trades


IC_STRATEGIES = [
    # --- manage winners at 50% of net credit (tastytrade rule) ---
    ICStrategy("IC-1.5pct-manage50", 0.015, 0.010, entry_offset_days=2,
               target_frac=0.50, sl_mult=2.0,
               notes="1.5% OTM shorts, 1.0% wings, both sides, exit at 50% of credit."),
    ICStrategy("IC-2pct-manage50", 0.020, 0.010, entry_offset_days=2,
               target_frac=0.50, sl_mult=2.0,
               notes="Wider 2% OTM shorts -> lower credit, lower touch probability."),
    ICStrategy("IC-1pct-manage50", 0.010, 0.008, entry_offset_days=2,
               target_frac=0.50, sl_mult=2.0,
               notes="Tight 1% OTM shorts -> fat credit, higher touch probability."),
    ICStrategy("IC-2.5pct-manage50", 0.025, 0.015, entry_offset_days=2,
               target_frac=0.50, sl_mult=2.0,
               notes="Very wide 2.5% OTM shorts, 1.5% wings, low touch prob."),
    # --- manage at other levels ---
    ICStrategy("IC-1.5pct-manage35", 0.015, 0.010, entry_offset_days=2,
               target_frac=0.35, sl_mult=2.0,
               notes="Same 1.5% IC but bank profit earlier at 35% of credit."),
    ICStrategy("IC-1.5pct-manage75", 0.015, 0.010, entry_offset_days=2,
               target_frac=0.75, sl_mult=2.0,
               notes="Greedier: hold winners until 75% of credit captured."),
    ICStrategy("IC-1.5pct-hold2expiry", 0.015, 0.010, entry_offset_days=2,
               target_frac=0.99, sl_mult=2.0,
               notes="No early management (target ~=100%); held to settlement for contrast."),
    # --- earlier entry (more theta days, more gamma risk) ---
    ICStrategy("IC-1.5pct-manage50-3d", 0.015, 0.010, entry_offset_days=3,
               target_frac=0.50, sl_mult=2.0,
               notes="Enter 3 days pre-expiry instead of 2."),
    ICStrategy("IC-1.5pct-manage50-4d", 0.015, 0.010, entry_offset_days=4,
               target_frac=0.50, sl_mult=2.0,
               notes="Enter 4 days pre-expiry."),
    # --- tighter stop ---
    ICStrategy("IC-1.5pct-manage50-sl1.5", 0.015, 0.010, entry_offset_days=2,
               target_frac=0.50, sl_mult=1.5,
               notes="Tighter stop at 1.5x credit."),
    # --- regime-gated: only sell in calm/contracting vol ---
    ICStrategy("IC-1.5pct-manage50-lowvol", 0.015, 0.010, entry_offset_days=2,
               target_frac=0.50, sl_mult=2.0, regime_filter="vol_contract",
               notes="Only opens the condor when 5d vol < 20d vol (calm regime)."),
]


def main():
    df = load_data()
    spot = build_spot_series(df)
    fut_ohlc = build_fut_ohlc(df)
    opt_idx, opt_df = build_option_index(df)
    days = trading_days(df)
    expiry_map = front_week_expiry_map(df)

    print(f"Spot series: {len(spot)} days, {spot.index.min()} -> {spot.index.max()}")
    print(f"Front-week expiries mapped for {len(expiry_map)} days")
    print(f"IC round-trip cost assumption: Rs {IC_ROUND_TRIP_COST} (8 legs)\n")

    all_stats = []
    all_logs = {}
    for strat in IC_STRATEGIES:
        trades = run_iron_condor(spot, fut_ohlc, opt_idx, days, expiry_map, strat)
        stats, tdf = compute_stats(trades, strat.name)
        if tdf is not None:
            all_logs[strat.name] = tdf
            # exit-reason mix helps interpret WHY a config performs as it does
            mix = tdf["exit_reason"].value_counts().to_dict()
            stats["exit_mix"] = ";".join(f"{k}={v}" for k, v in mix.items())
        stats["notes"] = strat.notes
        all_stats.append(stats)
        print(f"{strat.name:32s} trades={stats.get('trades'):>3} "
              f"win%={stats.get('win_rate_pct')} "
              f"ret%={stats.get('total_return_pct')} "
              f"cagr%={stats.get('cagr_pct')} "
              f"maxDD%={stats.get('max_drawdown_pct')} "
              f"PF={stats.get('profit_factor')}")

    summary = pd.DataFrame(all_stats)
    if "total_return_pct" in summary.columns:
        summary = summary.sort_values("total_return_pct", ascending=False)
    summary.to_csv(OUT_DIR / "iron_condor_summary.csv", index=False)
    print("\n=== IRON CONDOR SUMMARY (sorted by total return) ===")
    cols = [c for c in ["strategy", "trades", "win_rate_pct", "total_return_pct",
                        "cagr_pct", "max_drawdown_pct", "profit_factor", "sharpe_like",
                        "period_days", "exit_mix"] if c in summary.columns]
    print(summary[cols].to_string(index=False))

    for name, tdf in all_logs.items():
        tdf.to_csv(OUT_DIR / f"ic_trades_{name}.csv", index=False)
    print(f"\nSaved summary -> {OUT_DIR / 'iron_condor_summary.csv'}")
    print(f"Saved per-config trade logs -> {OUT_DIR}/ic_trades_*.csv")


if __name__ == "__main__":
    main()
