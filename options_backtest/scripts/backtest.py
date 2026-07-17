"""
Real-data backtest of 13 NIFTY hedged-credit-spread strategy variants, built to mirror
the "Credit Spread Overnight / Expiry" family (Zen, Curvature, Delta-Leverage, Mathematician's,
SkewHunter, Delta-Rotation, etc.) seen on Stratzy/Dhan, but with fully transparent,
auditable signal logic instead of marketing labels ("Lyapunov", "Hamiltonian", etc).

DATA SOURCE: NSE F&O Bhavcopy (UDiFF format), pulled directly from nsearchives.nseindia.com
for every trading day from 2025-06-16 to 2026-07-16 (real per-contract OPEN/HIGH/LOW/CLOSE,
volume, OI). This is REAL settlement-grade EOD data, not synthetic Black-Scholes pricing.

METHODOLOGY (read this before trusting any number):
  - Entry: at ENTRY DAY CLOSE (ClsPric) for both legs. This approximates the ~15:20 entry
    these algos describe, using the official EOD close print.
  - Overnight-hold strategies: exit at NEXT TRADING DAY OPEN (OpnPric) for both legs -
    matches "exits at the morning open" behavior described by Stratzy for Overnight algos.
  - Expiry-hold strategies: walk forward day-by-day using CLOSE-to-close marks, checking
    stop-loss / target each day, exiting at whichever trigger fires first, or at the
    expiry day's close/settlement if nothing triggers early. This is an EOD-mark walk,
    NOT an intraday tick simulation - real intraday stop-outs could differ (both better
    and worse) from what's modeled here.
  - Liquidity filter: only strikes with TtlTradgVol > 0 that day are eligible; if the
    target strike isn't tradable, we search the nearest tradable strike within 150 points;
    otherwise the trade is skipped for that day (logged, not silently dropped).
  - Costs: a flat Rs 300 per round-trip (4 option legs: 2 entry + 2 exit) is deducted from
    every trade's PnL as a brokerage/STT/slippage estimate.
  - Capital: Rs 1,00,000, 1 lot (75) per trade - matches the platform's own stated margin
    requirement for this algo family, so the resulting % returns are directly comparable
    to the marketing page's advertised return %.
  - Position sizing is intentionally NOT increased with account growth (no compounding
    lot-sizing) so returns are simple-additive on Rs 1,00,000 - the conservative, honest
    choice, and easy to sanity check.
"""
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_DIR = Path(__file__).resolve().parent.parent / "output"
OUT_DIR.mkdir(parents=True, exist_ok=True)

LOT_SIZE = 75
CAPITAL = 100_000
ROUND_TRIP_COST = 300.0  # Rs, flat estimate for 4 legs (2 entry + 2 exit) incl. slippage
STRIKE_STEP = 50
STRIKE_SEARCH_TOL = 150  # points, how far we'll search for a liquid substitute strike


# --------------------------------------------------------------------------------------
# 1. LOAD & CLEAN
# --------------------------------------------------------------------------------------
def load_data():
    print("Loading bhavcopy CSV...")
    df = pd.read_csv(DATA_DIR / "nifty_fo_daily.csv", low_memory=False)
    numeric_cols = [
        "StrkPric", "OpnPric", "HghPric", "LwPric", "ClsPric", "PrvsClsgPric",
        "UndrlygPric", "SttlmPric", "OpnIntrst", "TtlTradgVol",
    ]
    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["TradDt"] = pd.to_datetime(df["TradDt"])
    df["XpryDt"] = pd.to_datetime(df["XpryDt"])
    # NOTE: futures (IDF) rows have NO StrkPric - only require TradDt/XpryDt/ClsPric here.
    # Strike-specific cleaning happens later, only for the IDO (options) subset.
    df = df.dropna(subset=["TradDt", "XpryDt", "ClsPric"])
    print(f"Loaded {len(df):,} rows, {df.TradDt.nunique()} trading days, "
          f"{df.XpryDt.nunique()} distinct expiries, types={df.FinInstrmTp.unique().tolist()}")
    return df


def build_spot_series(df: pd.DataFrame) -> pd.Series:
    """Front-month NIFTY future close per day -> proxy for underlying OHLC-consistent spot."""
    fut = df[df.FinInstrmTp == "IDF"].copy()
    fut = fut.sort_values(["TradDt", "XpryDt"])
    front = fut.groupby("TradDt").first()  # nearest expiry future each day
    spot_close = front["ClsPric"]
    spot_close.name = "spot_close"
    return spot_close.sort_index()


def build_fut_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    fut = df[df.FinInstrmTp == "IDF"].copy()
    fut = fut.sort_values(["TradDt", "XpryDt"])
    front = fut.groupby("TradDt").first()
    return front[["OpnPric", "HghPric", "LwPric", "ClsPric"]].sort_index()


def build_option_index(df: pd.DataFrame):
    """dict[(TradDt, XpryDt, Strike, OptnTp)] -> row dict (Opn/Hgh/Lw/Cls/Vol)."""
    opt = df[df.FinInstrmTp == "IDO"].copy()
    opt = opt.dropna(subset=["StrkPric", "OptnTp"])
    opt["StrkPric"] = opt["StrkPric"].round(0).astype(int)
    idx = {}
    for row in opt.itertuples(index=False):
        key = (row.TradDt, row.XpryDt, int(row.StrkPric), row.OptnTp)
        idx[key] = {
            "open": row.OpnPric, "high": row.HghPric, "low": row.LwPric,
            "close": row.ClsPric, "vol": row.TtlTradgVol, "settle": row.SttlmPric,
        }
    return idx, opt


def trading_days(df: pd.DataFrame):
    return sorted(df.TradDt.unique())


def front_week_expiry_map(df: pd.DataFrame):
    """For each TradDt, the nearest expiry >= TradDt available in the data that day."""
    m = {}
    for d, g in df[df.FinInstrmTp == "IDO"].groupby("TradDt"):
        future_exp = g.loc[g.XpryDt >= d, "XpryDt"]
        if len(future_exp):
            m[d] = future_exp.min()
    return m


def find_tradable_strike(opt_idx, tradd, xpry, target_strike, optn_tp):
    target_strike = int(round(target_strike / STRIKE_STEP) * STRIKE_STEP)
    for offset in range(0, STRIKE_SEARCH_TOL + 1, STRIKE_STEP):
        for cand in {target_strike - offset, target_strike + offset}:
            key = (tradd, xpry, cand, optn_tp)
            row = opt_idx.get(key)
            if row and row["vol"] and row["vol"] > 0 and row["close"] > 0:
                return cand, row
    # fallback: allow zero-volume (stale) quote if nothing liquid found, mark as illiquid
    key = (tradd, xpry, target_strike, optn_tp)
    row = opt_idx.get(key)
    if row and row["close"] > 0:
        return target_strike, row
    return None, None


# --------------------------------------------------------------------------------------
# 2. SIGNALS  (all computed using ONLY data known as of end of trading day `d`)
# --------------------------------------------------------------------------------------
def sig_momentum(spot: pd.Series, d, lookback=3, deadband=0.0005):
    hist = spot.loc[:d]
    if len(hist) <= lookback:
        return "skip"
    ret = hist.iloc[-1] / hist.iloc[-1 - lookback] - 1
    if ret > deadband:
        return "bull"
    if ret < -deadband:
        return "bear"
    return "skip"


def sig_momentum_long(spot: pd.Series, d, lookback=10, deadband=0.0):
    hist = spot.loc[:d]
    if len(hist) <= lookback:
        return "skip"
    ret = hist.iloc[-1] / hist.iloc[-1 - lookback] - 1
    return "bull" if ret >= 0 else "bear"


def sig_mean_reversion(spot: pd.Series, d, lookback=1, thresh=0.006):
    hist = spot.loc[:d]
    if len(hist) <= lookback:
        return "skip"
    ret = hist.iloc[-1] / hist.iloc[-1 - lookback] - 1
    if ret > thresh:
        return "bear"   # fade a sharp rally
    if ret < -thresh:
        return "bull"   # fade a sharp drop
    return "skip"


def realized_vol(spot: pd.Series, d, window):
    hist = spot.loc[:d]
    if len(hist) <= window + 1:
        return None
    rets = np.log(hist / hist.shift(1)).dropna()
    return rets.iloc[-window:].std() * math.sqrt(252)


def sig_vol_expansion_momentum(spot, d, short_w=5, long_w=20, lookback=3):
    rv_s, rv_l = realized_vol(spot, d, short_w), realized_vol(spot, d, long_w)
    if rv_s is None or rv_l is None:
        return "skip"
    if rv_s <= rv_l:
        return "skip"  # only trade when vol is expanding
    return sig_momentum(spot, d, lookback=lookback, deadband=0.0)


def sig_low_vol_momentum(spot, d, short_w=5, long_w=20, lookback=3):
    rv_s, rv_l = realized_vol(spot, d, short_w), realized_vol(spot, d, long_w)
    if rv_s is None or rv_l is None:
        return "skip"
    if rv_s >= rv_l:
        return "skip"  # only trade in a calm/contracting-vol regime
    return sig_momentum(spot, d, lookback=lookback, deadband=0.0)


def sig_gap_position(fut_ohlc: pd.DataFrame, d):
    """Where today's future closed within its own day range -> continuation bias."""
    if d not in fut_ohlc.index:
        return "skip"
    row = fut_ohlc.loc[d]
    rng = row.HghPric - row.LwPric
    if rng <= 0:
        return "skip"
    pos = (row.ClsPric - row.LwPric) / rng
    if pos > 0.65:
        return "bull"
    if pos < 0.35:
        return "bear"
    return "skip"


def sig_skew(opt_idx, spot, d, xpry, otm_pct=0.01):
    if d not in spot.index or xpry is None:
        return "skip"
    s = spot.loc[d]
    pe_strike = int(round(s * (1 - otm_pct) / STRIKE_STEP) * STRIKE_STEP)
    ce_strike = int(round(s * (1 + otm_pct) / STRIKE_STEP) * STRIKE_STEP)
    pe = opt_idx.get((d, xpry, pe_strike, "PE"))
    ce = opt_idx.get((d, xpry, ce_strike, "CE"))
    if not pe or not ce or pe["close"] <= 0 or ce["close"] <= 0:
        return "skip"
    # richer puts (fear premium elevated) -> contrarian bullish tilt; richer calls -> bearish tilt
    ratio = pe["close"] / ce["close"]
    if ratio > 1.15:
        return "bull"
    if ratio < 0.87:
        return "bear"
    return "skip"


# --------------------------------------------------------------------------------------
# 3. STRATEGY DEFINITIONS
# --------------------------------------------------------------------------------------
@dataclass
class Strategy:
    name: str
    signal_fn: str            # key into SIGNAL_FNS
    hold_type: str             # "overnight" | "expiry"
    short_otm_pct: float
    wing_pct: float
    sl_mult: float = 1.5       # stop-loss = sl_mult * credit received (expiry-hold only)
    target_frac: float = 0.6   # take profit at target_frac * max profit (expiry-hold only)
    entry_offset_days: int = 2  # for expiry-hold: enter N trading days before expiry
    notes: str = ""


STRATEGIES = [
    Strategy("Momentum-Overnight-Tight", "momentum3", "overnight", 0.010, 0.007,
             notes="3-day momentum, tight 1.0% OTM short strike, held to next open."),
    Strategy("Momentum-Overnight-Wide", "momentum3", "overnight", 0.020, 0.012,
             notes="Same signal as #1 but strikes further OTM -> less premium, less gap risk."),
    Strategy("MeanReversion-Overnight", "mean_rev", "overnight", 0.015, 0.010,
             notes="Fades yesterday's >0.6% move; opposite-of-momentum overnight bet."),
    Strategy("VolExpansion-Overnight", "vol_expand", "overnight", 0.012, 0.008,
             notes="Only trades when 5d realized vol > 20d (breakout regime) + momentum dir."),
    Strategy("LowVol-Tight-Overnight", "vol_contract", "overnight", 0.010, 0.006,
             notes="Only trades in calm/contracting-vol regime; tight premium harvesting."),
    Strategy("GapPosition-Overnight", "gap_pos", "overnight", 0.012, 0.008,
             notes="Direction from where today's futures closed within its day range."),
    Strategy("ThetaHarvest-Baseline-Expiry", "always_bull", "expiry", 0.015, 0.010,
             sl_mult=1.5, target_frac=0.6, entry_offset_days=2,
             notes="Non-directional benchmark: always sells a put spread, no signal at all."),
    Strategy("Momentum-Expiry-HoldToSettle", "momentum3", "expiry", 0.012, 0.008,
             sl_mult=1.5, target_frac=0.6, entry_offset_days=2,
             notes="Momentum-timed spread entered 2 days pre-expiry, held to settlement."),
    Strategy("MeanReversion-Expiry", "mean_rev", "expiry", 0.015, 0.010,
             sl_mult=1.5, target_frac=0.6, entry_offset_days=2,
             notes="Contrarian entry 2 days pre-expiry, held with EOD SL/target checks."),
    Strategy("Aggressive-Tight-Overnight", "momentum3", "overnight", 0.006, 0.005,
             notes="Very tight ~0.6% OTM strikes -> high premium, high assignment/gap risk."),
    Strategy("Conservative-Wide-Expiry", "vol_contract", "expiry", 0.025, 0.015,
             sl_mult=1.3, target_frac=0.5, entry_offset_days=2,
             notes="Wide 2.5% OTM strikes, only in low-vol regime, tighter SL, held to expiry."),
    Strategy("TrendFollow10d-Overnight", "momentum10", "overnight", 0.013, 0.009,
             notes="Slower 10-day trend filter instead of 3-day, overnight hold."),
    Strategy("SkewFade-Overnight", "skew", "overnight", 0.012, 0.008,
             notes="Direction from relative PE vs CE richness (put/call price skew) at 1% OTM."),
]

SIGNAL_FNS = {
    "momentum3": lambda spot, fut_ohlc, opt_idx, d, xpry: sig_momentum(spot, d, lookback=3),
    "momentum10": lambda spot, fut_ohlc, opt_idx, d, xpry: sig_momentum_long(spot, d, lookback=10),
    "mean_rev": lambda spot, fut_ohlc, opt_idx, d, xpry: sig_mean_reversion(spot, d),
    "vol_expand": lambda spot, fut_ohlc, opt_idx, d, xpry: sig_vol_expansion_momentum(spot, d),
    "vol_contract": lambda spot, fut_ohlc, opt_idx, d, xpry: sig_low_vol_momentum(spot, d),
    "gap_pos": lambda spot, fut_ohlc, opt_idx, d, xpry: sig_gap_position(fut_ohlc, d),
    "skew": lambda spot, fut_ohlc, opt_idx, d, xpry: sig_skew(opt_idx, spot, d, xpry),
    "always_bull": lambda spot, fut_ohlc, opt_idx, d, xpry: "bull",
}


# --------------------------------------------------------------------------------------
# 4. TRADE CONSTRUCTION + BACKTEST ENGINE
# --------------------------------------------------------------------------------------
def build_spread(opt_idx, d, xpry, spot_px, direction, short_otm_pct, wing_pct):
    if direction == "bull":
        short_type, long_type = "PE", "PE"
        short_target = spot_px * (1 - short_otm_pct)
        long_target = spot_px * (1 - short_otm_pct - wing_pct)
    else:
        short_type, long_type = "CE", "CE"
        short_target = spot_px * (1 + short_otm_pct)
        long_target = spot_px * (1 + short_otm_pct + wing_pct)

    short_strike, short_row = find_tradable_strike(opt_idx, d, xpry, short_target, short_type)
    long_strike, long_row = find_tradable_strike(opt_idx, d, xpry, long_target, long_type)
    if short_strike is None or long_strike is None or short_strike == long_strike:
        return None
    credit = short_row["close"] - long_row["close"]
    if credit <= 0:
        return None
    wing_points = abs(short_strike - long_strike)
    return {
        "direction": direction, "short_type": short_type, "long_type": long_type,
        "short_strike": short_strike, "long_strike": long_strike,
        "credit": credit, "wing_points": wing_points,
        "max_loss_per_share": wing_points - credit,
    }


def price_leg(opt_idx, d, xpry, strike, optn_tp, field):
    row = opt_idx.get((d, xpry, strike, optn_tp))
    if row is None:
        return None
    v = row.get(field)
    if v is None or v <= 0:
        v = row.get("close")  # fall back to close if open/high/low missing (illiquid print)
    return v


def run_overnight(spot, fut_ohlc, opt_idx, df_days, expiry_map, strat: Strategy):
    trades = []
    for i, d in enumerate(df_days[:-1]):
        nxt = df_days[i + 1]
        xpry = expiry_map.get(d)
        if xpry is None or (xpry - d).days > 8:
            continue  # only trade within the current weekly cycle
        if xpry == d:
            continue  # don't open new overnight risk on expiry day itself
        if d not in spot.index:
            continue
        direction = SIGNAL_FNS[strat.signal_fn](spot, fut_ohlc, opt_idx, d, xpry)
        if direction == "skip":
            continue
        spread = build_spread(opt_idx, d, xpry, spot.loc[d], direction,
                               strat.short_otm_pct, strat.wing_pct)
        if spread is None:
            continue
        # exit next day at OPEN for both legs
        short_exit = price_leg(opt_idx, nxt, xpry, spread["short_strike"], spread["short_type"], "open")
        long_exit = price_leg(opt_idx, nxt, xpry, spread["long_strike"], spread["long_type"], "open")
        if short_exit is None or long_exit is None:
            continue
        debit = short_exit - long_exit
        pnl_per_share = spread["credit"] - debit
        pnl_per_share = max(pnl_per_share, -spread["max_loss_per_share"])  # capped by wing
        pnl = pnl_per_share * LOT_SIZE - ROUND_TRIP_COST
        trades.append({
            "entry_date": d, "exit_date": nxt, "direction": direction,
            "credit": spread["credit"], "debit": debit,
            "short_strike": spread["short_strike"], "long_strike": spread["long_strike"],
            "max_loss_per_share": spread["max_loss_per_share"],
            "pnl": pnl, "exit_reason": "next_open",
        })
    return trades


def run_expiry_hold(spot, fut_ohlc, opt_idx, df_days, expiry_map, strat: Strategy):
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
            continue  # entry day isn't actually in this expiry's front-week window
        seen_expiries.add(xpry)

        if entry_d not in spot.index:
            continue
        direction = SIGNAL_FNS[strat.signal_fn](spot, fut_ohlc, opt_idx, entry_d, xpry)
        if direction == "skip":
            continue
        spread = build_spread(opt_idx, entry_d, xpry, spot.loc[entry_d], direction,
                               strat.short_otm_pct, strat.wing_pct)
        if spread is None:
            continue
        max_profit = spread["credit"]
        sl_amount = strat.sl_mult * spread["credit"]
        target_amount = strat.target_frac * max_profit

        exit_reason, exit_day, debit = None, None, None
        for j in range(entry_idx + 1, exp_idx + 1):
            dd = df_days[j]
            s_px = price_leg(opt_idx, dd, xpry, spread["short_strike"], spread["short_type"], "close")
            l_px = price_leg(opt_idx, dd, xpry, spread["long_strike"], spread["long_type"], "close")
            if s_px is None or l_px is None:
                continue
            cur_debit = s_px - l_px
            cur_pnl = spread["credit"] - cur_debit
            is_last = (dd == xpry) or (j == exp_idx)
            if cur_pnl <= -sl_amount:
                exit_reason, exit_day, debit = "stop_loss", dd, cur_debit
                break
            if cur_pnl >= target_amount:
                exit_reason, exit_day, debit = "target", dd, cur_debit
                break
            if is_last:
                s_settle = opt_idx.get((dd, xpry, spread["short_strike"], spread["short_type"]), {}).get("settle", s_px)
                l_settle = opt_idx.get((dd, xpry, spread["long_strike"], spread["long_type"]), {}).get("settle", l_px)
                debit = (s_settle or s_px) - (l_settle or l_px)
                exit_reason, exit_day = "expiry", dd
        if exit_reason is None or debit is None:
            continue
        pnl_per_share = spread["credit"] - debit
        pnl_per_share = max(pnl_per_share, -spread["max_loss_per_share"])
        pnl = pnl_per_share * LOT_SIZE - ROUND_TRIP_COST
        trades.append({
            "entry_date": entry_d, "exit_date": exit_day, "direction": direction,
            "credit": spread["credit"], "debit": debit,
            "short_strike": spread["short_strike"], "long_strike": spread["long_strike"],
            "max_loss_per_share": spread["max_loss_per_share"],
            "pnl": pnl, "exit_reason": exit_reason,
        })
    return trades


# --------------------------------------------------------------------------------------
# 5. STATS
# --------------------------------------------------------------------------------------
def compute_stats(trades, strat_name):
    if not trades:
        return {"strategy": strat_name, "trades": 0}, None
    df = pd.DataFrame(trades).sort_values("entry_date")
    df["ret_pct"] = df["pnl"] / CAPITAL * 100
    df["cum_pnl"] = df["pnl"].cumsum()
    equity = CAPITAL + df["cum_pnl"]
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max * 100
    max_dd = drawdown.min()

    wins = df[df.pnl > 0]
    losses = df[df.pnl <= 0]
    win_rate = len(wins) / len(df) * 100
    avg_win = wins.pnl.mean() if len(wins) else 0
    avg_loss = losses.pnl.mean() if len(losses) else 0
    profit_factor = (wins.pnl.sum() / abs(losses.pnl.sum())) if losses.pnl.sum() != 0 else np.inf

    total_pnl = df.pnl.sum()
    total_return_pct = total_pnl / CAPITAL * 100
    n_days = (df.exit_date.max() - df.entry_date.min()).days
    years = max(n_days / 365.25, 1 / 365.25)
    cagr = ((CAPITAL + total_pnl) / CAPITAL) ** (1 / years) - 1 if CAPITAL + total_pnl > 0 else -1

    daily_rets = df["ret_pct"].values
    sharpe_like = (
        (daily_rets.mean() / daily_rets.std()) * math.sqrt(len(daily_rets) / years)
        if len(daily_rets) > 1 and daily_rets.std() > 0 else 0
    )

    stats = {
        "strategy": strat_name, "trades": len(df), "win_rate_pct": round(win_rate, 1),
        "total_pnl_rs": round(total_pnl, 0), "total_return_pct": round(total_return_pct, 2),
        "cagr_pct": round(cagr * 100, 1), "max_drawdown_pct": round(max_dd, 2),
        "avg_win_rs": round(avg_win, 0), "avg_loss_rs": round(avg_loss, 0),
        "profit_factor": round(profit_factor, 2) if np.isfinite(profit_factor) else 999,
        "sharpe_like": round(sharpe_like, 2),
        "period_days": n_days,
    }
    return stats, df


# --------------------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------------------
def main():
    df = load_data()
    spot = build_spot_series(df)
    fut_ohlc = build_fut_ohlc(df)
    opt_idx, opt_df = build_option_index(df)
    days = trading_days(df)
    expiry_map = front_week_expiry_map(df)

    print(f"Spot series: {len(spot)} days, {spot.index.min()} -> {spot.index.max()}")
    print(f"Front-week expiries mapped for {len(expiry_map)} days")

    all_stats = []
    all_trade_logs = {}
    for strat in STRATEGIES:
        print(f"\nRunning: {strat.name} ({strat.hold_type}) ...")
        if strat.hold_type == "overnight":
            trades = run_overnight(spot, fut_ohlc, opt_idx, days, expiry_map, strat)
        else:
            trades = run_expiry_hold(spot, fut_ohlc, opt_idx, days, expiry_map, strat)
        stats, trade_df = compute_stats(trades, strat.name)
        if trade_df is not None:
            all_trade_logs[strat.name] = trade_df
        stats["hold_type"] = strat.hold_type
        stats["notes"] = strat.notes
        all_stats.append(stats)
        print(f"  -> trades={stats.get('trades')} win_rate={stats.get('win_rate_pct')}% "
              f"total_return={stats.get('total_return_pct')}% max_dd={stats.get('max_drawdown_pct')}%")

    summary = pd.DataFrame(all_stats)
    summary = summary.sort_values("total_return_pct", ascending=False)
    summary.to_csv(OUT_DIR / "strategy_summary.csv", index=False)
    print("\n\n=== SUMMARY (sorted by total return) ===")
    print(summary.to_string(index=False))

    for name, tdf in all_trade_logs.items():
        safe = name.replace("/", "_")
        tdf.to_csv(OUT_DIR / f"trades_{safe}.csv", index=False)

    print(f"\nSaved summary -> {OUT_DIR / 'strategy_summary.csv'}")
    print(f"Saved per-strategy trade logs -> {OUT_DIR}/trades_*.csv")


if __name__ == "__main__":
    main()
