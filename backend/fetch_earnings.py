"""
Standalone: populate data/earnings.json with per-stock quarterly EPS (estimated vs.
actual), from yfinance via earnings.py.

Earnings are slow-changing reference data (a new quarter lands roughly every 3
months), so -- like sectors/holdings/fundamentals -- this is deliberately NOT part
of the daily price scan. Run it occasionally (and re-run periodically to pick up
newly-reported quarters -- unlike sectors/fundamentals this DOES change every
quarter, just not daily); it writes a cached data/earnings.json that run_scan.py
merges into breakouts.json when present.

Resumable: skips symbols already in earnings.json and saves incrementally, so a
mid-run network hiccup never loses progress -- just run it again. Symbols are
fetched in readiness order (from the latest breakouts.json) so the stocks that are
actually setting up get their earnings filled in first; pass a limit to stop early.

Usage:
    python fetch_earnings.py            # whole universe, readiness-prioritized
    python fetch_earnings.py 300        # just the first 300 (by readiness)
"""
from __future__ import annotations
import json
import sys
import time

import settings
from earnings import fetch_earnings
from universe import build_universe

EARNINGS_JSON = settings.DATA_DIR / "earnings.json"
_SCORE_RANK = {"high": 0, "medium": 1, "low": 2}
_MISS = {"source": None, "quarters": [], "next": None}


def _load() -> dict:
    if EARNINGS_JSON.exists():
        with open(EARNINGS_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(data: dict):
    payload = dict(sorted(data.items()))
    with open(EARNINGS_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))


def _prioritized_symbols() -> list[str]:
    """Symbols to fetch, in readiness order (interesting stocks first).

    Prefer the full symbol list from the latest breakouts.json — it already covers
    the whole scanned universe (~1,800) and carries readiness, so we avoid a fresh
    NSE bhavcopy call (which rate-limits). Falls back to build_universe() only if no
    scan output exists yet."""
    if settings.BREAKOUTS_JSON.exists():
        try:
            with open(settings.BREAKOUTS_JSON, encoding="utf-8") as f:
                stocks = json.load(f).get("stocks", [])
            if stocks:
                stocks.sort(key=lambda s: _SCORE_RANK.get(s["readiness"]["score"], 3))
                return [s["symbol"] for s in stocks]
        except Exception:
            pass
    return list(build_universe())


def run(limit: int | None = None):
    data = _load()
    symbols = _prioritized_symbols()
    todo = [s for s in symbols if s not in data]
    if limit is not None:
        todo = todo[:limit]
    print(f"earnings.json has {len(data)} already; fetching {len(todo)} more "
          f"(of {len(symbols)} in universe)...\n")

    ok = est_ok = fail = 0
    t0 = time.time()
    for i, sym in enumerate(todo, 1):
        e = fetch_earnings(sym)
        if e:
            data[sym] = e
            ok += 1
            est_ok += e["source"] == "estimate"
        else:
            # Cache the miss too, so a resume doesn't retry the same dead symbols forever.
            data[sym] = _MISS
            fail += 1
        if i % 50 == 0:
            _save(data)
            print(f"  {i:5d}/{len(todo)} | ok {ok} (with estimates {est_ok}) fail {fail} | "
                  f"{time.time()-t0:.0f}s")
        time.sleep(0.15)

    _save(data)
    print(f"\nDone. {ok} fetched ({est_ok} with analyst estimates, "
          f"{ok - est_ok} actual-only), {fail} without any data. "
          f"earnings.json now has {len(data)} stocks.")


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run(lim)
