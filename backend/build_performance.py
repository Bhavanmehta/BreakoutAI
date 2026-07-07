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

Entry points:
  - run_scan.py calls update_from_scan(feat_by_symbol, summaries, as_of) after
    each scan: appends today's suggestions + refreshes all open outcomes.
  - Standalone `python build_performance.py` refreshes outcomes from the DuckDB
    the last scan wrote (no new suggestions).
  - `python build_performance.py --seed` additionally reconstructs the
    launch-era episodes from the committed breakouts.json snapshots in git
    history -- exactly what the site displayed on those days (latest committed
    version per as_of_date, conviction-era only), plus the working-tree file.

    BREAKOUTAI_MARKET=US python build_performance.py --seed   # US ledger
"""
from __future__ import annotations
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
        if not res or np.isnan(res) or entry - res * settings.STOP_LOSS_FRACTION <= 0:
            drop.append(ep)   # ungradeable under the site's own rule -- see docstring
            continue
        stop = res * settings.STOP_LOSS_FRACTION
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
# Load / write
# --------------------------------------------------------------------------- #
def _load_episodes() -> list[dict]:
    if not settings.PERF_JSON.exists():
        return []
    with open(settings.PERF_JSON, encoding="utf-8") as f:
        return json.load(f).get("episodes", [])


def _write(episodes: list[dict], as_of: str | None) -> None:
    episodes.sort(key=lambda e: e["symbol"])
    episodes.sort(key=lambda e: e["date"], reverse=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of_date": as_of,
        "market": settings.MARKET,
        "live_since": min((e["date"] for e in episodes), default=None),
        "grade_window": settings.FOLLOWTHROUGH_WINDOW,
        "track_bars": settings.PERF_TRACK_BARS,
        "stop_loss_fraction": settings.STOP_LOSS_FRACTION,
        "reference_rates": REFERENCE_RATES.get(settings.MARKET, {}),
        "disclaimer": ("Educational content only, not investment advice. A live, "
                       "forward-only record of the site's own published calls -- nothing "
                       "backfilled. Each call is graded by whether price hit +1R before "
                       f"the stop within {settings.FOLLOWTHROUGH_WINDOW} trading days."),
        "episodes": episodes,
    }
    with open(settings.PERF_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))


def update_from_scan(feat_by_symbol: dict[str, pd.DataFrame],
                     summaries: list[dict], as_of: str) -> tuple[int, int]:
    """run_scan.py's entry point. Returns (new_episodes, total_episodes)."""
    episodes = _load_episodes()
    added = _upsert(episodes, _suggestions_from_stocks(summaries), as_of, feat_by_symbol)
    refresh_outcomes(episodes, feat_by_symbol)
    _write(episodes, as_of)
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
    _write(episodes, as_of)
    print(f"{'Seeded ' + str(added) + ' episodes, ' if seed else ''}"
          f"{len(episodes)} total, refreshed against {len(feat_by_symbol)} symbols "
          f"in {time.time()-t0:.1f}s -> {settings.PERF_JSON.relative_to(settings.REPO_DIR)}")


if __name__ == "__main__":
    main()
