"""
Standalone: populate data/sectors.json with per-stock sector / industry, from
yfinance via sectors.py.

Sector is slow-changing reference data, so — like holdings — this is deliberately
NOT part of the daily price scan. Run it occasionally; it writes a cached
data/sectors.json that run_scan.py merges into breakouts.json when present.

Resumable: skips symbols already in sectors.json and saves incrementally, so a
mid-run network hiccup never loses progress — just run it again. Symbols are fetched
in readiness order (from the latest breakouts.json) so the stocks that are actually
setting up get labelled first; pass a limit to stop early.

Usage:
    python fetch_sectors.py            # whole universe, readiness-prioritized (~10 min)
    python fetch_sectors.py 300        # just the first 300 (by readiness)
"""
from __future__ import annotations
import json
import sys
import time

import settings
from sectors import fetch_sector
from universe import build_universe

SECTORS_JSON = settings.DATA_DIR / "sectors.json"
_SCORE_RANK = {"high": 0, "medium": 1, "low": 2}


def _load() -> dict:
    if SECTORS_JSON.exists():
        with open(SECTORS_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(data: dict):
    payload = dict(sorted(data.items()))
    with open(SECTORS_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))


def _prioritized_symbols() -> list[str]:
    """Symbols to label, in readiness order (interesting stocks first).

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
    print(f"sectors.json has {len(data)} already; fetching {len(todo)} more "
          f"(of {len(symbols)} in universe)...\n")

    ok = fail = 0
    t0 = time.time()
    for i, sym in enumerate(todo, 1):
        s = fetch_sector(sym)
        if s:
            data[sym] = s
            ok += 1
        else:
            # Cache the miss too, so a resume doesn't retry the same dead symbols forever.
            data[sym] = {"sector": None, "industry": None}
            fail += 1
        if i % 50 == 0:
            _save(data)
            print(f"  {i:5d}/{len(todo)} | ok {ok} fail {fail} | {time.time()-t0:.0f}s")
        time.sleep(0.1)

    _save(data)
    print(f"\nDone. {ok} classified, {fail} without a sector. sectors.json now has {len(data)} stocks.")


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run(lim)
