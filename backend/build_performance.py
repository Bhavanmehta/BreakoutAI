"""
data/performance.json -- the live, forward-only ledger behind performance.html:
every suggestion the site actually published (breakout-today / relative-strength /
the US high-conviction tiers), recorded on the day it was made and tracked for
the next PERF_TRACK_BARS trading days (~2 weeks), graded by the same production
+1R-before-stop rule as everything else. NOTHING is backfilled from before the
site started making these calls (the conviction era: 2026-07-03 IN, 2026-07-02 US)
-- this is a diary of real calls, not a retrospective replay.

How it stays honest:
  - Only identity is persisted per episode (symbol, name, date, signals,
    conviction-at-call). Entry/stop/target/closes/status are re-derived from the
    current price history on every refresh, so a retroactive yfinance split
    adjustment can never strand a stale entry price against adjusted closes.
  - A symbol re-flagged with the same signal within FOLLOWTHROUGH_WINDOW trading
    days of an existing episode is the same move, not a new suggestion --
    identical to the cooldown-dedup rule the backtests and the live tier badges
    (find_breakouts._last_is_fresh_fire) already use. Necessary because the
    plain "breaking out now" badge is NOT fresh-fire-gated in production and can
    repeat on consecutive days of one continuous move.
  - A call whose risk isn't well-defined under the site's own rule (entry at or
    below the stop -- rare, deep-below-resistance names) is skipped entirely,
    same as track.py's grading does, rather than shown ungradeable.

What it also derives (top-level "analytics" block, recomputed every write):
  - expectancy   -- mean R per resolved call (won = +1R, lost = -1R by the grade
    rule) plus the live win rate and mean held-to-window return.
  - benchmark    -- each call's window return vs just holding the index over the
    same dates (RS_BENCHMARK): mean alpha and the share of calls that beat it.
    Null when the benchmark fetch fails (offline); expectancy still computed.
  - hindsight    -- does the site's OWN conviction score stratify LIVE follow-
    through? Realized hit-rate by conviction bucket + by signal. Diagnostic only:
    NOT fed back into scoring (score.py stays on its validated-features footing;
    the live sample is far too small to recalibrate against).

Entry points:
  - run_scan.py calls update_from_scan(feat_by_symbol, summaries, as_of, benchmark)
    after each scan: appends today's suggestions + refreshes all open outcomes,
    passing the benchmark frame it already fetched so alpha is computed for free.
  - Standalone `python build_performance.py` refreshes outcomes from the DuckDB
    the last scan wrote (no new suggestions), re-fetching the benchmark itself for
    the analytics block (best-effort -- the benchmark block is null if offline).
  - `python build_performance.py --seed` additionally reconstructs the
    launch-era episodes from the committed breakouts.json snapshots in git
    history -- exactly what the site displayed on those days (latest committed
    version per as_of_date, conviction-era only), plus the working-tree file.

    BREAKOUTAI_MARKET=US python build_performance.py --seed   # US ledger
"""
from __future__ import annotations
import bisect
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import settings

# Backtested whole-market reference hit rates shown next to the live numbers
# (documented in HANDOFF.md / the multi-method-breakout-comparison memory).
# None = never backtested on that market; the page shows "--" rather than
# borrowing the other market's number.
REFERENCE_RATES = {
    "IN": {"breakout": 0.388, "relative_strength": 0.416},
    "US": {"breakout": 0.267, "relative_strength": None,
           "high_conviction": 0.511, "strong_breakout": 0.453},
}

# Badge display priority (frontend shows them in this order).
SIGNAL_ORDER = ["high_conviction", "strong_breakout", "breakout", "relative_strength"]

# Hindsight calibration: conviction buckets whose LIVE follow-through we check against
# the score the site actually assigned. Diagnostic only -- never fed back into scoring.
# A bucket's realized hit-rate is only trusted (shown as a number) once it has at least
# HINDSIGHT_MIN_N resolved calls; below that the sample is noise.
CONVICTION_BUCKETS = [(50, 59), (60, 69), (70, 79), (80, 100)]
HINDSIGHT_MIN_N = 5


# --------------------------------------------------------------------------- #
# Extraction: which stocks in a scan's output count as "suggestions"
# --------------------------------------------------------------------------- #
def _suggestions_from_stocks(stocks: list[dict]) -> list[dict]:
    """The published calls in one day's summaries/breakouts.json: any stock
    flagged 'breaking out today' and/or carrying a readiness signal. Same schema
    for live summaries and committed snapshots."""
    out = []
    for s in stocks:
        r = s.get("readiness") or {}
        sig = []
        if r.get("signal") in ("high_conviction", "strong_breakout", "relative_strength"):
            sig.append(r["signal"])
        if (s.get("breakout") or {}).get("today") and "breakout" not in sig:
            sig.append("breakout")
        if sig:
            sig.sort(key=SIGNAL_ORDER.index)
            out.append({"symbol": s["symbol"], "name": s.get("name") or s["symbol"],
                        "signals": sig, "conviction": r.get("conviction")})
    return out


# --------------------------------------------------------------------------- #
# Ledger upsert with the cooldown-dedup rule
# --------------------------------------------------------------------------- #
def _bars_between(feat: pd.DataFrame | None, d1: str, d2: str) -> int:
    """Trading days between two ISO dates on this symbol's own calendar; falls
    back to a calendar-day * 5/7 estimate if the frame doesn't cover both."""
    if feat is not None:
        dates = pd.to_datetime(feat["date"]).dt.strftime("%Y-%m-%d").tolist()
        if d1 in dates and d2 in dates:
            return abs(dates.index(d2) - dates.index(d1))
    delta = abs((datetime.fromisoformat(d2) - datetime.fromisoformat(d1)).days)
    return round(delta * 5 / 7)


def _upsert(episodes: list[dict], candidates: list[dict], as_of: str,
            feat_by_symbol: dict[str, pd.DataFrame]) -> int:
    """Add one day's suggestions to the ledger. Per signal: skip if the same
    symbol already has an episode with that signal within FOLLOWTHROUGH_WINDOW
    trading days (same continuous move). Same-day re-runs merge new signals
    into the existing episode instead of duplicating it."""
    W = settings.FOLLOWTHROUGH_WINDOW
    by_symbol: dict[str, list[dict]] = {}
    for e in episodes:
        by_symbol.setdefault(e["symbol"], []).append(e)

    added = 0
    for c in candidates:
        prior = by_symbol.get(c["symbol"], [])
        feat = feat_by_symbol.get(c["symbol"])
        fresh = [s for s in c["signals"]
                 if not any(s in e["signals"] and e["date"] != as_of
                            and _bars_between(feat, e["date"], as_of) <= W
                            for e in prior)]
        if not fresh:
            continue
        same_day = next((e for e in prior if e["date"] == as_of), None)
        if same_day is not None:
            merged = sorted(set(same_day["signals"]) | set(fresh), key=SIGNAL_ORDER.index)
            if merged != same_day["signals"]:
                same_day["signals"] = merged
                added += 1
            continue
        ep = {"symbol": c["symbol"], "name": c["name"], "date": as_of,
              "signals": fresh, "conviction": c["conviction"],
              "entry": None, "stop": None, "target": None,
              "closes": [], "dates": [], "status": "open", "resolved_in": None}
        episodes.append(ep)
        by_symbol.setdefault(c["symbol"], []).append(ep)
        added += 1
    return added


# --------------------------------------------------------------------------- #
# Outcome refresh (re-derived from price history every run)
# --------------------------------------------------------------------------- #
def _grade(highs, lows, i: int, stop: float, target: float, n: int, window: int):
    """Order-aware forward scan, mirroring add_indicators' followthrough loop
    (stop checked before target within each bar). Returns (status, resolved_in)."""
    for j in range(i + 1, min(i + window, n - 1) + 1):
        if lows[j] <= stop:
            return "lost", j - i
        if highs[j] >= target:
            return "won", j - i
    if n - 1 - i >= window:
        return "expired", None
    return "open", None


def refresh_outcomes(episodes: list[dict], feat_by_symbol: dict[str, pd.DataFrame]) -> None:
    """Recompute every episode's entry/stop/target/closes/status from the current
    history. Episodes whose symbol/date isn't in today's frames (delisting, data
    gap) keep their last stored values."""
    W = settings.FOLLOWTHROUGH_WINDOW
    drop: list[dict] = []
    for ep in episodes:
        feat = feat_by_symbol.get(ep["symbol"])
        if feat is None:
            continue
        df = feat[feat["close"].notna()]
        n = len(df)
        dates = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d").tolist()
        if ep["date"] not in dates:
            continue
        i = dates.index(ep["date"])
        closes = df["close"].to_numpy(dtype=float)
        res = df["resistance"].to_numpy(dtype=float)[i]
        entry = closes[i]
        atr_i = (df["atr_short"].to_numpy(dtype=float)[i]
                 if "atr_short" in df.columns else None)
        stop = settings.stop_from(res, atr_i)
        if stop is None or entry - stop <= 0:
            drop.append(ep)   # ungradeable under the site's own rule -- see docstring
            continue
        target = entry + (entry - stop)
        status, resolved_in = _grade(df["high"].to_numpy(dtype=float),
                                     df["low"].to_numpy(dtype=float),
                                     i, stop, target, n, W)
        ep.update({
            "entry": round(float(entry), 2),
            "stop": round(float(stop), 2),
            "target": round(float(target), 2),
            "closes": [round(float(c), 2)
                       for c in closes[i + 1: i + 1 + settings.PERF_TRACK_BARS]],
            "dates": dates[i + 1: i + 1 + settings.PERF_TRACK_BARS],
            "status": status,
            "resolved_in": resolved_in,
        })
    for ep in drop:
        episodes.remove(ep)


# --------------------------------------------------------------------------- #
# Analytics: expectancy, benchmark comparison, hindsight calibration
# --------------------------------------------------------------------------- #
def _benchmark_asof(benchmark: pd.DataFrame | None):
    """Build an as-of lookup over the benchmark index: the last bm_close on or
    before a given ISO date (so a call's entry/window dates always resolve to a
    real prior index level even on a market holiday). Returns callable or None."""
    if benchmark is None or len(benchmark) == 0:
        return None
    b = benchmark.dropna(subset=["bm_close"]).copy()
    b["d"] = pd.to_datetime(b["date"]).dt.strftime("%Y-%m-%d")
    b = b.sort_values("d")
    dates = b["d"].tolist()
    closes = b["bm_close"].to_numpy(dtype=float).tolist()
    if not dates:
        return None

    def lookup(d: str) -> float | None:
        i = bisect.bisect_right(dates, d) - 1
        return closes[i] if i >= 0 else None

    return lookup


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _compute_analytics(episodes: list[dict], benchmark: pd.DataFrame | None) -> dict:
    """Derive the top-level analytics block and annotate each eligible episode with
    its window return / benchmark return / alpha. Recomputed on every write so it
    always reflects the current re-derived outcomes."""
    bm = _benchmark_asof(benchmark)

    # --- Per-episode window return + alpha vs the index (annotate in place) ---
    call_rets, bm_rets, alphas = [], [], []
    for ep in episodes:
        for k in ("ret", "bm_ret", "alpha"):   # clear stale values before recompute
            ep.pop(k, None)
        closes, entry, dates = ep.get("closes") or [], ep.get("entry"), ep.get("dates") or []
        if not closes or not entry:
            continue
        ret = (closes[-1] / entry - 1) * 100.0
        ep["ret"] = round(ret, 2)
        if bm is not None and dates:
            b0, b1 = bm(ep["date"]), bm(dates[-1])
            if b0 and b1:
                bm_ret = (b1 / b0 - 1) * 100.0
                ep["bm_ret"] = round(bm_ret, 2)
                ep["alpha"] = round(ret - bm_ret, 2)
                call_rets.append(ret); bm_rets.append(bm_ret); alphas.append(ret - bm_ret)

    # --- Expectancy over resolved calls (won = +1R, lost = -1R by the grade rule) ---
    resolved = [e for e in episodes if e["status"] in ("won", "lost")]
    won = sum(1 for e in resolved if e["status"] == "won")
    lost = len(resolved) - won
    r_multiples = [1.0 if e["status"] == "won" else -1.0 for e in resolved]
    wins_r = [r for r in r_multiples if r > 0]
    losses_r = [r for r in r_multiples if r < 0]
    window_rets = [e["ret"] for e in episodes if "ret" in e]
    expectancy = {
        "won": won, "lost": lost,
        "win_rate": round(won / len(resolved), 3) if resolved else None,
        "expectancy_r": round(_mean(r_multiples), 3) if r_multiples else None,
        "avg_win_r": round(_mean(wins_r), 2) if wins_r else None,
        "avg_loss_r": round(_mean(losses_r), 2) if losses_r else None,
        "mean_window_return_pct": round(_mean(window_rets), 2) if window_rets else None,
    }

    # --- Benchmark: did the calls beat just holding the index? ---
    benchmark_block = None
    if bm is not None and alphas:
        benchmark_block = {
            "ticker": settings.RS_BENCHMARK, "label": settings.RS_BENCHMARK_LABEL,
            "n": len(alphas),
            "mean_call_return_pct": round(_mean(call_rets), 2),
            "mean_bm_return_pct": round(_mean(bm_rets), 2),
            "mean_alpha_pct": round(_mean(alphas), 2),
            "beat_rate": round(sum(1 for a in alphas if a > 0) / len(alphas), 3),
        }

    # --- Hindsight: does our own conviction score stratify LIVE follow-through? ---
    def _tally(items: list[dict]) -> tuple[int, int, float | None]:
        w = sum(1 for e in items if e["status"] == "won")
        l = sum(1 for e in items if e["status"] == "lost")
        return w, l, (round(w / (w + l), 3) if (w + l) >= HINDSIGHT_MIN_N else None)

    buckets, trend = [], []
    for lo, hi in CONVICTION_BUCKETS:
        members = [e for e in episodes
                   if e.get("conviction") is not None and lo <= e["conviction"] <= hi]
        w, l, hr = _tally(members)
        buckets.append({"lo": lo, "hi": hi, "n": len(members), "won": w, "lost": l, "hit_rate": hr})
        if hr is not None:
            trend.append(hr)
    monotonic = (all(trend[i] <= trend[i + 1] for i in range(len(trend) - 1))
                 if len(trend) >= 2 else None)

    by_signal = {}
    for sig in SIGNAL_ORDER:
        members = [e for e in episodes if sig in e["signals"]]
        if members:
            w, l, hr = _tally(members)
            by_signal[sig] = {"n": len(members), "won": w, "lost": l, "hit_rate": hr}

    return {
        "resolved_n": len(resolved),
        "expectancy": expectancy,
        "benchmark": benchmark_block,
        "hindsight": {
            "conviction_buckets": buckets,
            "by_signal": by_signal,
            "monotonic": monotonic,
            "note": (f"Live forward record only. A bucket's hit-rate appears once it has "
                     f">={HINDSIGHT_MIN_N} resolved calls. Diagnostic check on whether our "
                     "conviction score predicts follow-through -- not (yet) fed back into it."),
        },
    }


# --------------------------------------------------------------------------- #
# Load / write
# --------------------------------------------------------------------------- #
def _load_episodes() -> list[dict]:
    if not settings.PERF_JSON.exists():
        return []
    with open(settings.PERF_JSON, encoding="utf-8") as f:
        return json.load(f).get("episodes", [])


def _write(episodes: list[dict], as_of: str | None, analytics: dict | None = None) -> None:
    episodes.sort(key=lambda e: e["symbol"])
    episodes.sort(key=lambda e: e["date"], reverse=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of_date": as_of,
        "market": settings.MARKET,
        "live_since": min((e["date"] for e in episodes), default=None),
        "grade_window": settings.FOLLOWTHROUGH_WINDOW,
        "track_bars": settings.PERF_TRACK_BARS,
        "stop_loss_fraction": settings.STOP_LOSS_FRACTION,   # legacy/fallback; see stop_model
        "stop_model": settings.STOP_MODEL_DESC,
        "reference_rates": REFERENCE_RATES.get(settings.MARKET, {}),
        "disclaimer": ("Educational content only, not investment advice. A live, "
                       "forward-only record of the site's own published calls -- nothing "
                       "backfilled. Each call is graded by whether price hit +1R before "
                       f"the stop within {settings.FOLLOWTHROUGH_WINDOW} trading days."),
        "analytics": analytics,
        "episodes": episodes,
    }
    with open(settings.PERF_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))


def update_from_scan(feat_by_symbol: dict[str, pd.DataFrame],
                     summaries: list[dict], as_of: str,
                     benchmark: pd.DataFrame | None = None) -> tuple[int, int]:
    """run_scan.py's entry point. Returns (new_episodes, total_episodes). Pass the
    benchmark frame the scan already fetched so the analytics block can compare each
    call against the index (None is fine -- the benchmark sub-block is then null)."""
    episodes = _load_episodes()
    added = _upsert(episodes, _suggestions_from_stocks(summaries), as_of, feat_by_symbol)
    refresh_outcomes(episodes, feat_by_symbol)
    analytics = _compute_analytics(episodes, benchmark)
    _write(episodes, as_of, analytics)
    return added, len(episodes)


# --------------------------------------------------------------------------- #
# Standalone: refresh from DuckDB; --seed reconstructs launch-era calls from git
# --------------------------------------------------------------------------- #
def _features_from_duckdb() -> dict[str, pd.DataFrame]:
    import duckdb
    con = duckdb.connect(str(settings.DUCKDB_PATH), read_only=True)
    symbols = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM ohlcv_features").fetchall()]
    out: dict[str, pd.DataFrame] = {}
    q = ("SELECT date, high, low, close, resistance FROM ohlcv_features "
         "WHERE symbol = ? ORDER BY date")
    for sym in symbols:
        try:
            g = con.execute(q, [sym]).df()
        except Exception:      # one corrupted segment shouldn't abort the refresh
            continue
        if len(g):
            out[sym] = g
    con.close()
    return out


def _committed_snapshots() -> list[dict]:
    """The launch-era diary source: every committed conviction-era breakouts.json,
    one per distinct as_of_date (latest committed version wins -- it's what the
    site served for most of that day), plus the current working-tree file."""
    git = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"
    rel = settings.BREAKOUTS_JSON.relative_to(settings.REPO_DIR).as_posix()
    snapshots: dict[str, dict] = {}

    def consider(data: dict):
        as_of = data.get("as_of_date")
        stocks = data.get("stocks") or []
        # conviction era only -- the page starts the day the site started scoring
        if as_of and as_of not in snapshots and stocks \
                and "conviction" in (stocks[0].get("readiness") or {}):
            snapshots[as_of] = data

    if settings.BREAKOUTS_JSON.exists():
        with open(settings.BREAKOUTS_JSON, encoding="utf-8") as f:
            consider(json.load(f))
    shas = subprocess.run([git, "log", "--pretty=%H", "--", rel], cwd=settings.REPO_DIR,
                          capture_output=True, text=True, encoding="utf-8").stdout.split()
    for sha in shas:                       # newest first, so first-seen per as_of wins
        show = subprocess.run([git, "show", f"{sha}:{rel}"], cwd=settings.REPO_DIR,
                              capture_output=True, text=True, encoding="utf-8")
        if show.returncode != 0 or not show.stdout:
            continue
        try:
            consider(json.loads(show.stdout))
        except json.JSONDecodeError:
            continue
    return [snapshots[k] for k in sorted(snapshots)]   # oldest -> newest


def main():
    t0 = time.time()
    seed = "--seed" in sys.argv
    feat_by_symbol = _features_from_duckdb()
    episodes = _load_episodes()
    added = 0
    if seed:
        for snap in _committed_snapshots():
            added += _upsert(episodes, _suggestions_from_stocks(snap["stocks"]),
                             snap["as_of_date"], feat_by_symbol)
    refresh_outcomes(episodes, feat_by_symbol)
    as_of = None
    for feat in feat_by_symbol.values():
        d = str(pd.Timestamp(feat["date"].iloc[-1]).strftime("%Y-%m-%d"))
        as_of = d if as_of is None or d > as_of else as_of
    try:                                   # benchmark is best-effort: null block if the fetch fails
        from methods import fetch_benchmark
        benchmark = fetch_benchmark()
    except Exception as e:
        print(f"  benchmark fetch failed ({e}); analytics.benchmark will be null")
        benchmark = None
    analytics = _compute_analytics(episodes, benchmark)
    _write(episodes, as_of, analytics)
    print(f"{'Seeded ' + str(added) + ' episodes, ' if seed else ''}"
          f"{len(episodes)} total, refreshed against {len(feat_by_symbol)} symbols "
          f"in {time.time()-t0:.1f}s -> {settings.PERF_JSON.relative_to(settings.REPO_DIR)}")


if __name__ == "__main__":
    main()
