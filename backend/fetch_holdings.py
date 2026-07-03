"""
Standalone: populate data/holdings.json with per-stock ownership (promoter / FII /
DII / mutual-fund / public %), from NSE via holdings.py.

This is quarterly-slow data (NSE only publishes shareholding each quarter, and per
stock it's ~2 requests + a 0.5MB XBRL, ~1s each — the whole market is ~30 min). So
it is deliberately NOT part of the daily price scan: run it occasionally, it writes a
cached data/holdings.json that run_scan.py merges into breakouts.json when present.

Resumable: it skips symbols already in holdings.json and saves incrementally, so a
mid-run rate-limit/network hiccup never loses progress — just run it again. Symbols
are fetched in readiness order (from the latest breakouts.json) so the stocks that are
actually setting up get holdings first; pass a limit to stop early.

Usage:
    python fetch_holdings.py            # whole universe, readiness-prioritized
    python fetch_holdings.py 500        # just the first 500 (by readiness)
"""
from __future__ import annotations
import json
import sys
import time

import settings
from holdings import make_session, fetch_holdings
from holdings_screener import make_session as make_screener_session, fetch_holdings_screener
from universe import build_universe

HOLDINGS_JSON = settings.DATA_DIR / "holdings.json"
_SCORE_RANK = {"high": 0, "medium": 1, "low": 2}


def _load() -> dict:
    if HOLDINGS_JSON.exists():
        with open(HOLDINGS_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(data: dict):
    payload = dict(sorted(data.items()))
    with open(HOLDINGS_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))


def _prioritized_symbols() -> list[str]:
    """Universe symbols ordered by breakout readiness (from the latest scan) so the
    interesting stocks get holdings first; falls back to plain universe order."""
    symbols = list(build_universe())
    if settings.BREAKOUTS_JSON.exists():
        try:
            with open(settings.BREAKOUTS_JSON, encoding="utf-8") as f:
                stocks = json.load(f).get("stocks", [])
            rank = {s["symbol"]: _SCORE_RANK.get(s["readiness"]["score"], 3) for s in stocks}
            symbols.sort(key=lambda s: rank.get(s, 4))
        except Exception:
            pass
    return symbols


def run(limit: int | None = None):
    data = _load()
    symbols = _prioritized_symbols()
    # Re-fetch symbols we've never seen, older snapshot-only entries, and anything not
    # yet on the better quarterly screener source — so a re-scrape upgrades them in
    # place. Entries already sourced from screener are skipped (resumable).
    def _needs(sym):
        d = data.get(sym)
        return d is None or "history" not in d or d.get("source") != "screener"
    todo = [s for s in symbols if _needs(s)]
    if limit is not None:
        todo = todo[:limit]
    upgrades = sum(1 for s in todo if s in data)
    print(f"holdings.json has {len(data)} already; fetching {len(todo)} "
          f"({upgrades} upgrades + {len(todo)-upgrades} new) of {len(symbols)} in universe...\n")

    # Primary source: screener.in (quarterly Promoter/FII/DII/Public, reliable). Fall
    # back to NSE's XBRL path (annual-ish, rate-limits) only when screener has nothing.
    scr = make_screener_session()
    nse = make_session()
    ok = fail = 0
    t0 = time.time()
    for i, sym in enumerate(todo, 1):
        h = fetch_holdings_screener(sym, scr)
        if not h:
            h = fetch_holdings(sym, nse, prime=False, history_points=8)
        if h:
            data[sym] = h
            ok += 1
        else:
            fail += 1
        if i % 25 == 0:
            _save(data)
            print(f"  {i:5d}/{len(todo)} | ok {ok} fail {fail} | {time.time()-t0:.0f}s")
        time.sleep(0.6)  # be polite to screener

    _save(data)
    print(f"\nDone. {ok} fetched, {fail} failed. holdings.json now has {len(data)} stocks.")


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run(lim)
