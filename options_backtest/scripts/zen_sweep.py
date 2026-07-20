#!/usr/bin/env python3
"""
Zen Credit Spread Overnight -- exhaustive sweep, run in parallel across worker processes.

Zen's own public description (community.stratzy.in/t/algo-spotlight-3-zen-credit-spread-overnight/275):
  "You close positions at 3:30 PM ... Zen Credit Spread Overnight is built to capture
  exactly that window ... It identifies a directional bias before market close and sets
  up a hedged credit spread position that stays live overnight."

We don't have Zen's proprietary signal or intraday tick data -- only real EOD NSE bhavcopy
(Open/High/Low/Close/OI per contract per day). So this sweep tests EVERY reasonable
EOD-computable "directional bias" signal we can build from that data, crossed with 4
strike-width configs, all using the *exact same* overnight mechanic as the validated
backtest.py engine (entry = today's ClsPric, exit = next trading day's OpnPric, 1 lot /
Rs 1,00,000 capital, Rs 300/round-trip cost) -- so results are directly comparable to the
original 13-strategy run.

Signals tested (12):
  momentum1/3/5/10   - N-day price momentum (pure trend continuation)
  mean_rev           - fade yesterday's >0.6% move (contrarian)
  vol_expand         - momentum, but only when realized vol is expanding (breakout regime)
  vol_contract       - momentum, but only when realized vol is contracting (calm regime)
  gap_pos            - where today's futures closed within today's own H-L range
                        (closest EOD proxy to "directional bias formed going into the 3:30
                        close", since it only uses today's own candle)
  skew               - PE vs CE premium richness at 1% OTM (options-market fear/greed)
  oi_bias  [NEW]      - front-month futures price change x OI change quadrant
                        (classic "smart money" long-build-up / short-build-up read)
  pcr      [NEW]      - front-week Put/Call OI ratio, contrarian sentiment fade
  always_bull         - non-directional control (always sells a bear put spread)

Widths tested (4): Tight / Medium / Wide / VeryWide short-strike OTM% and hedge width,
spanning the same range already used in the original 13-strategy backtest.

12 signals x 4 widths = 48 variants, run across a multiprocessing.Pool so they execute
concurrently instead of one at a time.
"""
import multiprocessing as mp
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest as bt  # reuse the validated load_data/build_*/run_overnight/compute_stats

OUT_DIR = bt.OUT_DIR

# --------------------------------------------------------------------------------------
# Two new EOD-only signals not present in backtest.py
# --------------------------------------------------------------------------------------
def build_fut_oi(df: pd.DataFrame) -> pd.Series:
    """Front-month futures open interest per day."""
    fut = df[df.FinInstrmTp == "IDF"].copy()
    fut = fut.sort_values(["TradDt", "XpryDt"])
    front = fut.groupby("TradDt").first()
    return front["OpnIntrst"].sort_index()


def build_pcr(df: pd.DataFrame, expiry_map: dict) -> pd.Series:
    """Front-week Put OI / Call OI ratio per day (restricted to the currently-tradable
    front-week expiry chain, not stale far-month OI)."""
    opt = df[df.FinInstrmTp == "IDO"].copy()
    opt = opt.dropna(subset=["OptnTp"])
    opt["front_xpry"] = opt["TradDt"].map(expiry_map)
    opt = opt[opt["XpryDt"] == opt["front_xpry"]]
    g = opt.groupby(["TradDt", "OptnTp"])["OpnIntrst"].sum().unstack(fill_value=0)
    pe = g["PE"] if "PE" in g.columns else pd.Series(dtype=float)
    ce = g["CE"] if "CE" in g.columns else pd.Series(dtype=float)
    pcr = pe / ce.replace(0, np.nan)
    return pcr.sort_index()


def sig_oi_bias(spot: pd.Series, fut_oi: pd.Series, d):
    """Price x OI quadrant: price up + OI up -> long build-up (bull); price down + OI up
    -> short build-up (bear). OI falling (covering/unwinding) -> no clean directional read."""
    hist_s, hist_oi = spot.loc[:d], fut_oi.loc[:d]
    if len(hist_s) < 2 or len(hist_oi) < 2:
        return "skip"
    dpx = hist_s.iloc[-1] - hist_s.iloc[-2]
    doi = hist_oi.iloc[-1] - hist_oi.iloc[-2]
    if dpx > 0 and doi > 0:
        return "bull"
    if dpx < 0 and doi > 0:
        return "bear"
    return "skip"


def sig_pcr(pcr: pd.Series, d, hi=1.3, lo=0.7):
    """Crowded put positioning (PCR high) -> contrarian bounce; crowded call positioning
    (PCR low) -> contrarian fade. Pure sentiment-extreme heuristic."""
    if d not in pcr.index or pd.isna(pcr.loc[d]):
        return "skip"
    v = pcr.loc[d]
    if v > hi:
        return "bull"
    if v < lo:
        return "bear"
    return "skip"


# --------------------------------------------------------------------------------------
# Grid definition
# --------------------------------------------------------------------------------------
SIGNAL_NAMES = [
    "momentum1", "momentum3", "momentum5", "momentum10",
    "mean_rev", "vol_expand", "vol_contract", "gap_pos", "skew",
    "oi_bias", "pcr", "always_bull",
]

WIDTHS = [
    ("Tight", 0.006, 0.005),
    ("Medium", 0.010, 0.007),
    ("Wide", 0.015, 0.010),
    ("VeryWide", 0.020, 0.012),
]


def make_strategies():
    strategies = []
    for sig_name in SIGNAL_NAMES:
        for width_name, otm, wing in WIDTHS:
            strategies.append(bt.Strategy(
                name=f"Zen-{sig_name}-{width_name}",
                signal_fn=sig_name,
                hold_type="overnight",
                short_otm_pct=otm,
                wing_pct=wing,
                notes=f"Zen-mechanic sweep: signal={sig_name}, width={width_name}",
            ))
    return strategies


# --------------------------------------------------------------------------------------
# Worker process: load the CSV + build all indices ONCE per process, then reuse across
# every strategy task routed to that worker.
# --------------------------------------------------------------------------------------
_WORKER_STATE = {}


def _init_worker():
    print(f"[worker {mp.current_process().name}] loading data...", flush=True)
    df = bt.load_data()
    spot = bt.build_spot_series(df)
    fut_ohlc = bt.build_fut_ohlc(df)
    opt_idx, _ = bt.build_option_index(df)
    days = bt.trading_days(df)
    expiry_map = bt.front_week_expiry_map(df)
    fut_oi = build_fut_oi(df)
    pcr = build_pcr(df, expiry_map)

    # register the 2 new signals + 2 extra momentum lookbacks into backtest.py's own
    # SIGNAL_FNS dict (module-local to this worker process) so run_overnight() -- which
    # is untouched -- can find them by key exactly like the original 8.
    bt.SIGNAL_FNS["momentum1"] = lambda spot, fut_ohlc, opt_idx, d, xpry: bt.sig_momentum(spot, d, lookback=1)
    bt.SIGNAL_FNS["momentum5"] = lambda spot, fut_ohlc, opt_idx, d, xpry: bt.sig_momentum(spot, d, lookback=5)
    bt.SIGNAL_FNS["oi_bias"] = lambda spot, fut_ohlc, opt_idx, d, xpry: sig_oi_bias(spot, fut_oi, d)
    bt.SIGNAL_FNS["pcr"] = lambda spot, fut_ohlc, opt_idx, d, xpry: sig_pcr(pcr, d)

    _WORKER_STATE.update(spot=spot, fut_ohlc=fut_ohlc, opt_idx=opt_idx, days=days, expiry_map=expiry_map)
    print(f"[worker {mp.current_process().name}] ready.", flush=True)


def _run_one(strat: "bt.Strategy"):
    st = _WORKER_STATE
    trades = bt.run_overnight(st["spot"], st["fut_ohlc"], st["opt_idx"], st["days"], st["expiry_map"], strat)
    stats, trade_df = bt.compute_stats(trades, strat.name)
    _, sig_name, width_name = strat.name.split("-")
    stats["signal"] = sig_name
    stats["width"] = width_name
    stats["notes"] = strat.notes
    if trade_df is not None:
        trade_df.to_csv(OUT_DIR / f"trades_{strat.name}.csv", index=False)
    return stats


# --------------------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------------------
def main():
    strategies = make_strategies()
    n_workers = min(mp.cpu_count(), 8, len(strategies))
    print(f"Running {len(strategies)} Zen-mechanic variants ({len(SIGNAL_NAMES)} signals "
          f"x {len(WIDTHS)} widths) across {n_workers} parallel worker processes...\n")

    t0 = time.time()
    with mp.Pool(processes=n_workers, initializer=_init_worker) as pool:
        results = pool.map(_run_one, strategies)
    elapsed = time.time() - t0

    summary = pd.DataFrame(results)
    summary = summary.sort_values("total_return_pct", ascending=False)
    summary.to_csv(OUT_DIR / "zen_sweep_summary.csv", index=False)

    print("=== FULL SWEEP (sorted by total return) ===")
    cols = ["strategy", "signal", "width", "trades", "win_rate_pct", "total_return_pct",
            "max_drawdown_pct", "profit_factor", "sharpe_like"]
    print(summary[cols].to_string(index=False))

    n_profitable = (summary["total_return_pct"] > 0).sum()
    print(f"\n{len(strategies)} variants run in {elapsed:.1f}s on {n_workers} workers.")
    print(f"Profitable variants: {n_profitable} / {len(strategies)} "
          f"({n_profitable / len(strategies) * 100:.0f}%)")

    print("\n=== BEST WIDTH PER SIGNAL ===")
    best_per_signal = summary.sort_values("total_return_pct", ascending=False).groupby("signal").first()
    best_per_signal = best_per_signal.sort_values("total_return_pct", ascending=False)
    print(best_per_signal[["width", "trades", "win_rate_pct", "total_return_pct", "max_drawdown_pct"]].to_string())

    print(f"\nSaved full sweep -> {OUT_DIR / 'zen_sweep_summary.csv'}")
    print(f"Saved per-variant trade logs -> {OUT_DIR}/trades_Zen-*.csv")


if __name__ == "__main__":
    main()
