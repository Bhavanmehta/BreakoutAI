"""
overnight_gap_hardening.py -- stress-test the `gap_pos` directional overnight credit spread.

The earlier reverse-engineer (overnight_directional.py) showed gap_pos looked best BUT used an
optimistic stop: it just CLAMPED the loss at the next *close* to -sl_mult*credit. That can never
whipsaw -- a night that closes green is always booked green, even if it dipped through the stop
intraday. A real intraday stop turns some would-be winners into stopped-out losers.

Here we harden it. We now have full per-leg OHLC on the exit session, so we model the stop from the
spread's worst intraday mark:
    worst_debit  = short_leg HIGH - long_leg LOW      (both legs at their worst simultaneously)
    worst_pnl    = credit - worst_debit
If worst_pnl <= -sl_mult*credit the stop is HIT during the day -> booked at the stop, minus slippage,
regardless of where it closed. This is deliberately conservative (the two legs rarely peak together),
so it is a pessimistic bound on the realistic result.

Stop models compared:
  none          : hold to next close, no stop (wing is the only protection)
  close         : OLD optimistic clamp at the close (no whipsaw) -- the +54.6% baseline
  intraday      : realistic -- stop triggers on short_HIGH - long_LOW, booked at stop + slippage
  intraday_conf : same, but only honour the stop if the FUTURES also made an adverse intraday move
                  >= conf_thresh (filters spurious single-print option spikes on illiquid legs)

Everything is reported on FULL history and on a chronological out-of-sample split (H1 / H2):
a real edge should survive both halves, not just the full-sample average.
"""
import numpy as np
import pandas as pd

from backtest import (
    load_data, build_spot_series, build_fut_ohlc, build_option_index,
    trading_days, front_week_expiry_map, build_spread, SIGNAL_FNS,
    LOT_SIZE, CAPITAL, OUT_DIR,
)

COST_PER_FILL = 30.0
N_FILLS = 4


def fld(row, field, fb=None):
    """Leg price for a field, falling back to close on missing/illiquid prints."""
    v = row.get(field)
    if v is None or v <= 0:
        v = row.get("close")
    return float(v) if (v is not None and v > 0) else fb


def adverse_move(fut_ohlc, d, nxt, direction):
    """How far the future moved AGAINST the position intraday on the exit day (fraction)."""
    if d not in fut_ohlc.index or nxt not in fut_ohlc.index:
        return 0.0
    c = float(fut_ohlc.loc[d, "ClsPric"])
    if c <= 0:
        return 0.0
    if direction == "bull":                       # bull loses when market falls
        lo = float(fut_ohlc.loc[nxt, "LwPric"])
        return max(0.0, (c - lo) / c)
    hi = float(fut_ohlc.loc[nxt, "HghPric"])       # bear loses when market rises
    return max(0.0, (hi - c) / c)


def run_gap(spot, fut_ohlc, opt_idx, days, expiry_map, *,
            signal_fn=None, otm_pct=0.010, wing_pct=0.010, sl_mult=1.5,
            stop_model="intraday", conf_thresh=0.004, stop_slip=0.25,
            tp_frac=None, cost_per_fill=COST_PER_FILL):
    """Directional spread held to next close, with a configurable stop model.

    signal_fn: fn(spot, fut_ohlc, opt_idx, d, xpry) -> 'bull'|'bear'|'skip'.
               Defaults to the gap_pos signal.
    tp_frac: if set, take profit when the spread can be bought back for <= tp_frac*credit
             (i.e. we captured (1-tp_frac)*credit). Assumes stop is checked first (pessimistic).
    """
    fn = signal_fn if signal_fn is not None else SIGNAL_FNS["gap_pos"]
    trades = []
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
        srow = opt_idx.get((nxt, xpry, sp["short_strike"], sp["short_type"]))
        lrow = opt_idx.get((nxt, xpry, sp["long_strike"], sp["long_type"]))
        if srow is None or lrow is None:
            continue
        sc, lc = fld(srow, "close"), fld(lrow, "close")
        if sc is None or lc is None:
            continue

        credit = sp["credit"]
        maxloss = sp["max_loss_per_share"]
        stop_level = -sl_mult * credit if sl_mult else None

        # exit-at-close pnl (capped by the wing)
        pnl_close = min(max(credit - (sc - lc), -maxloss), credit)

        # intraday extremes of the spread mark
        sh, ll = fld(srow, "high", sc), fld(lrow, "low", lc)
        sl_, lh = fld(srow, "low", sc), fld(lrow, "high", lc)
        pnl_worst = max(credit - (sh - ll), -maxloss)   # most adverse mark
        pnl_best = min(credit - (sl_ - lh), credit)      # most favourable mark
        has_intraday = (srow.get("high") or 0) > 0 and (lrow.get("low") or 0) > 0

        outcome = "close"
        if stop_model == "none" or stop_level is None:
            realized = pnl_close
        elif stop_model == "close":
            # optimistic: floor the CLOSE pnl at the stop, never whipsaw
            realized = max(pnl_close, stop_level)
            outcome = "stop" if pnl_close < stop_level else "close"
        else:  # intraday / intraday_conf -- realistic
            stop_hit = pnl_worst <= stop_level
            if stop_model == "intraday_conf" and stop_hit:
                if adverse_move(fut_ohlc, d, nxt, direction) < conf_thresh:
                    stop_hit = False  # unconfirmed by futures -> treat as illiquid print
            tp_level = (1 - tp_frac) * credit if tp_frac else None
            if stop_hit:
                realized = max(stop_level - stop_slip * credit, -maxloss)
                outcome = "stop"
            elif tp_level is not None and pnl_best >= tp_level:
                realized = tp_level
                outcome = "tp"
            else:
                realized = pnl_close

        gross = realized * LOT_SIZE
        trades.append({
            "entry_date": d, "exit_date": nxt, "direction": direction, "credit": credit,
            "outcome": outcome, "had_intraday": has_intraday,
            "pnl_gross": gross, "pnl": gross - N_FILLS * cost_per_fill,
        })
    return pd.DataFrame(trades)


def summarize(label, split, tdf):
    if len(tdf) == 0:
        return {"config": label, "split": split, "trades": 0}
    n = tdf["pnl"]
    wins, losses = n[n > 0], n[n <= 0]
    span = max((tdf.entry_date.max() - tdf.entry_date.min()).days, 1)
    eq = CAPITAL + n.cumsum()
    dd = ((eq - eq.cummax()) / eq.cummax() * 100).min()
    return {
        "config": label, "split": split, "trades": len(tdf),
        "trd/wk": round(len(tdf) / (span / 7), 1),
        "win%": round(100 * len(wins) / len(n), 1),
        "avgW%": round(wins.mean() / CAPITAL * 100, 2) if len(wins) else 0.0,
        "avgL%": round(losses.mean() / CAPITAL * 100, 2) if len(losses) else 0.0,
        "exp%": round(n.mean() / CAPITAL * 100, 3),
        "net%": round(n.sum() / CAPITAL * 100, 1),
        "maxDD%": round(dd, 1),
        "stop%": round(100 * (tdf.outcome == "stop").mean(), 0),
        "tp%": round(100 * (tdf.outcome == "tp").mean(), 0),
    }


def rows_for(label, ctx, mid, **cfg):
    tdf = run_gap(*ctx, **cfg)
    h1, h2 = tdf[tdf.entry_date < mid], tdf[tdf.entry_date >= mid]
    return [summarize(label, "FULL", tdf),
            summarize(label, "  H1", h1),
            summarize(label, "  H2", h2)]


_ALL = []


def show(title, rows):
    cols = ["config", "split", "trades", "trd/wk", "win%", "avgW%", "avgL%",
            "exp%", "net%", "maxDD%", "stop%", "tp%"]
    print(f"\n{'=' * 118}\n{title}\n{'=' * 118}")
    print(pd.DataFrame(rows)[cols].to_string(index=False))
    for r in rows:
        rr = dict(r)
        rr["section"] = title.split(")")[0]
        _ALL.append(rr)


def main():
    df = load_data()
    spot = build_spot_series(df)
    fut_ohlc = build_fut_ohlc(df)
    opt_idx, _ = build_option_index(df)
    days = trading_days(df)
    expiry_map = front_week_expiry_map(df)
    ctx = (spot, fut_ohlc, opt_idx, days, expiry_map)
    mid = days[len(days) // 2]

    print(f"gap_pos hardening | {len(days)} trading days | OOS split at {pd.Timestamp(mid).date()}")
    print("Target (Zen live): ~3/wk, win 57.5%, avgW +5.43%, avgL -4.36%, maxDD -25.4% (on ~3x leverage)")

    # A) Stop-model comparison at the base geometry (otm 1% / wing 1%, sl 1.5x)
    rows = []
    for model in ("none", "close", "intraday", "intraday_conf"):
        rows += rows_for(model, ctx, mid, otm_pct=0.010, sl_mult=1.5, stop_model=model)
    show("A) STOP MODEL  (otm 1% / wing 1%, sl 1.5x credit)  <- how much does a REAL stop cost?", rows)

    # B) Stop-multiplier sweep on the realistic intraday model
    rows = []
    for sl in (None, 1.0, 1.5, 2.0):
        lbl = "sl=none" if sl is None else f"sl={sl}x"
        rows += rows_for(lbl, ctx, mid, otm_pct=0.010, sl_mult=sl, stop_model="intraday")
    show("B) STOP MULTIPLIER  (realistic intraday stop, otm 1% / wing 1%)", rows)

    # C) Strike-distance sweep (realistic intraday stop, sl 1.5x)
    rows = []
    for otm in (0.005, 0.010, 0.015):
        rows += rows_for(f"otm={otm*100:.1f}%", ctx, mid, otm_pct=otm, sl_mult=1.5, stop_model="intraday")
    show("C) STRIKE DISTANCE  (realistic intraday stop, wing 1%, sl 1.5x)", rows)

    # D) Take-profit overlay on the realistic intraday config
    rows = []
    for tp in (None, 0.5, 0.3):
        lbl = "tp=off" if tp is None else f"tp@{int(tp*100)}%"
        rows += rows_for(lbl, ctx, mid, otm_pct=0.010, sl_mult=1.5, stop_model="intraday", tp_frac=tp)
    show("D) TAKE-PROFIT overlay  (realistic intraday stop, otm 1% / wing 1%, sl 1.5x)", rows)

    # E) intraday_conf variant param check + confirmed-stop robustness
    rows = []
    for ct in (0.003, 0.004, 0.006):
        rows += rows_for(f"conf>={ct*100:.1f}%", ctx, mid, otm_pct=0.010, sl_mult=1.5,
                         stop_model="intraday_conf", conf_thresh=ct)
    show("E) CONFIRMED STOP threshold  (futures must move >= X% adverse to honour the stop)", rows)

    # Save the trade log of the most realistic base config
    best = run_gap(*ctx, otm_pct=0.010, sl_mult=1.5, stop_model="intraday_conf", conf_thresh=0.004)
    best.to_csv(OUT_DIR / "overnight_gap_hardened.csv", index=False)
    print(f"\nSaved realistic (intraday_conf) trade log -> {OUT_DIR / 'overnight_gap_hardened.csv'}")

    pd.DataFrame(_ALL).to_csv(OUT_DIR / "gap_hardening_grid.csv", index=False)
    print(f"Saved full result grid -> {OUT_DIR / 'gap_hardening_grid.csv'}")


if __name__ == "__main__":
    main()
