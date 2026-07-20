"""
overnight_breakout.py -- trend/breakout-CONFIRMED overnight credit spreads, run through the
exact same hardened harness as overnight_gap_hardening.py.

WHY: the hardening proved gap_pos losers *mean-revert* intraday, so a realistic stop whipsaws
(-91.6% vs the fake +54.6% optimistic-close number). The fix cannot be the stop -- it must be an
ENTRY whose losers *trend*, so that when the trade goes against us it keeps going and the stop
exits cleanly instead of getting chopped.

So we only sell the spread in the direction of a CONFIRMED move:
  donchN  : futures CLOSE breaks the prior N-day high (->bull, sell put spread, bet continuation)
            or breaks the prior N-day low (->bear, sell call spread). Classic Donchian breakout.
  atrK    : today's close-to-close move exceeds K * ATR14 (->bull up, ->bear down). Volatility-scaled.

Direction = trend CONTINUATION (sell premium in the direction of the break). Thesis: a failed
breakout reverses and trends against us -> the stop is now net-POSITIVE instead of a drag.

Everything else (spread construction, per-leg-OHLC intraday stop, costs, OOS H1/H2 split) is
imported unchanged from overnight_gap_hardening so the comparison is apples-to-apples.
"""
import numpy as np
import pandas as pd

from backtest import (
    load_data, build_spot_series, build_fut_ohlc, build_option_index,
    trading_days, front_week_expiry_map, OUT_DIR,
)
from overnight_gap_hardening import rows_for  # uses the hardened run_gap under the hood


# ----------------------------------------------------------------------------- features
def make_features(fut_ohlc):
    f = fut_ohlc.copy()
    f["prevC"] = f["ClsPric"].shift(1)
    tr = np.maximum(
        f["HghPric"] - f["LwPric"],
        np.maximum((f["HghPric"] - f["prevC"]).abs(), (f["LwPric"] - f["prevC"]).abs()),
    )
    f["atr14"] = tr.rolling(14).mean()
    for N in (5, 10, 20):
        f[f"hh{N}"] = f["HghPric"].rolling(N).max().shift(1)   # prior N-day high (excl today)
        f[f"ll{N}"] = f["LwPric"].rolling(N).min().shift(1)    # prior N-day low
    return f


# ----------------------------------------------------------------------------- signals
def donchian_signal(feat, N):
    hh, ll, cl = f"hh{N}", f"ll{N}", "ClsPric"

    def fn(spot, fut_ohlc, opt_idx, d, xpry):
        if d not in feat.index:
            return "skip"
        r = feat.loc[d]
        if pd.isna(r[hh]) or pd.isna(r[ll]):
            return "skip"
        c = r[cl]
        if c >= r[hh]:            # close breaks prior high -> upside breakout
            return "bull"
        if c <= r[ll]:            # close breaks prior low  -> downside breakdown
            return "bear"
        return "skip"

    return fn


def atr_break_signal(feat, k):
    def fn(spot, fut_ohlc, opt_idx, d, xpry):
        if d not in feat.index:
            return "skip"
        r = feat.loc[d]
        if pd.isna(r["atr14"]) or pd.isna(r["prevC"]) or r["atr14"] <= 0:
            return "skip"
        move = r["ClsPric"] - r["prevC"]
        if move >= k * r["atr14"]:
            return "bull"
        if move <= -k * r["atr14"]:
            return "bear"
        return "skip"

    return fn


NARROW = ["config", "split", "trades", "trd/wk", "win%", "avgW%", "avgL%",
          "exp%", "net%", "maxDD%", "stop%"]


def main():
    df = load_data()
    ctx = (
        build_spot_series(df),
        build_fut_ohlc(df),
        build_option_index(df)[0],
        trading_days(df),
        front_week_expiry_map(df),
    )
    mid = ctx[3][len(ctx[3]) // 2]
    feat = make_features(ctx[1])

    signals = {
        "donch5":  donchian_signal(feat, 5),
        "donch10": donchian_signal(feat, 10),
        "donch20": donchian_signal(feat, 20),
        "atr0.5":  atr_break_signal(feat, 0.5),
        "atr1.0":  atr_break_signal(feat, 1.0),
    }

    # per-signal: baseline (no stop) / optimistic close / realistic intraday sweep / futures-conf
    variants = [
        ("none",      dict(stop_model="none")),
        ("close",     dict(stop_model="close",         sl_mult=1.5)),
        ("intr-1.0",  dict(stop_model="intraday",      sl_mult=1.0)),
        ("intr-1.5",  dict(stop_model="intraday",      sl_mult=1.5)),
        ("intr-2.0",  dict(stop_model="intraday",      sl_mult=2.0)),
        ("conf-1.5",  dict(stop_model="intraday_conf", sl_mult=1.5)),
    ]

    allrows = []
    for sname, fn in signals.items():
        rows = []
        for vname, cfg in variants:
            rs = rows_for(f"{sname}/{vname}", ctx, mid,
                          signal_fn=fn, otm_pct=0.010, wing_pct=0.010, **cfg)
            for r in rs:
                r["signal"], r["variant"] = sname, vname
            rows += rs
        allrows += rows
        print(f"\n{'=' * 100}\n{sname}   breakout-confirmed overnight credit spread\n{'=' * 100}")
        sub = pd.DataFrame(rows)
        print(sub[[c for c in NARROW if c in sub.columns]].to_string(index=False))

    grid = pd.DataFrame(allrows)
    grid.to_csv(OUT_DIR / "breakout_grid.csv", index=False)

    # decisive one-liner per signal: does a REAL stop help vs no stop?  (FULL history)
    print(f"\n{'=' * 100}\nDOES A REAL STOP HELP?  FULL-history net%  (want intraday >= none for a trendy entry)\n{'=' * 100}")
    piv = grid[grid.split == "FULL"].pivot_table(index="signal", columns="variant",
                                                 values="net%", aggfunc="first")
    order = [v[0] for v in variants]
    print(piv[[c for c in order if c in piv.columns]].to_string())
    print("\nSaved full grid -> %s" % (OUT_DIR / "breakout_grid.csv"))


if __name__ == "__main__":
    main()
