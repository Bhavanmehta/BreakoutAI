"""
Standalone: populate data/news.json with per-stock news headlines + (where available) a
news-sentiment score, from Marketaux (primary -- has entity sentiment, ~100 free
req/day) and NewsData.io (secondary -- headlines only, ~12h-delayed, 200 free
credits/day). See news_providers.py's docstring for why these two and not Finnhub
(its free tier is US-only for company news/sentiment).

Unlike holdings/sectors/fundamentals (slow-changing reference data fetched once and kept
forever), news is time-sensitive: this refreshes daily rather than skip-if-present -- a
symbol only gets re-fetched if its cached entry isn't from today (IST). Still resumable
within a day via the same incremental-save pattern as fetch_holdings.py/fetch_sectors.py,
so a mid-run quota/network hiccup just needs a re-run.

Budget split: Marketaux is spent on the highest-conviction names first (where a news
signal is most actionable); NewsData.io then continues down the SAME conviction-ordered
list, skipping whatever Marketaux already refreshed today, so the two providers extend
coverage rather than duplicating it.

Needs MARKETAUX_API_KEY and/or NEWSDATA_API_KEY -- either exported, or in backend/.env
(gitignored, see .env.example). Missing a key just skips that provider (the other still
runs); missing both means this is a no-op. run_scan.py merges data/news.json into each
stock's `news` field when present, same optional pattern as holdings/sectors/fundamentals.

Usage:
    python fetch_news.py              # today's stale names in conviction order, budget-capped
    python fetch_news.py 50            # cap to the first 50 (by conviction) this run
    python fetch_news.py --check       # 1 test call per configured provider (RELIANCE),
                                        # prints what came back -- run this after adding keys
"""
from __future__ import annotations
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import settings
from news_providers import QuotaExhausted, fetch_marketaux, fetch_newsdata

IST = timezone(timedelta(hours=5, minutes=30))


def _load_env_file():
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def _today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _load() -> dict:
    if settings.NEWS_JSON.exists():
        with open(settings.NEWS_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(data: dict):
    payload = dict(sorted(data.items()))
    with open(settings.NEWS_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))


def _prioritized_stocks() -> list[dict]:
    """[{symbol, name}, ...] from the latest scan, highest conviction first -- news
    matters most for the names actually setting up. Empty if no scan has run yet (news
    is meaningless without a conviction order to prioritize by)."""
    if not settings.BREAKOUTS_JSON.exists():
        return []
    with open(settings.BREAKOUTS_JSON, encoding="utf-8") as f:
        stocks = json.load(f).get("stocks", [])

    def conv(s):
        c = (s.get("readiness") or {}).get("conviction")
        return c if c is not None else -1

    stocks.sort(key=conv, reverse=True)
    return [{"symbol": s["symbol"], "name": s.get("name") or s["symbol"]} for s in stocks]


def _check(marketaux_key: str | None, newsdata_key: str | None):
    print("Smoke test -- one call per configured provider against RELIANCE.\n")
    if marketaux_key:
        try:
            r = fetch_marketaux("RELIANCE", "Reliance Industries Limited", marketaux_key)
            print("Marketaux:", "OK" if r else "no data returned")
            print(" ", r)
        except QuotaExhausted as e:
            print("Marketaux: quota/auth error --", e)
        except Exception as e:
            print("Marketaux: FAILED --", repr(e))
    else:
        print("Marketaux: MARKETAUX_API_KEY not set, skipped.")
    print()
    if newsdata_key:
        try:
            r = fetch_newsdata("RELIANCE", "Reliance Industries Limited", newsdata_key)
            print("NewsData.io:", "OK" if r else "no data returned")
            print(" ", r)
        except QuotaExhausted as e:
            print("NewsData.io: quota/auth error --", e)
        except Exception as e:
            print("NewsData.io: FAILED --", repr(e))
    else:
        print("NewsData.io: NEWSDATA_API_KEY not set, skipped.")


def run(limit: int | None = None):
    _load_env_file()
    marketaux_key = os.environ.get("MARKETAUX_API_KEY")
    newsdata_key = os.environ.get("NEWSDATA_API_KEY")

    if not marketaux_key and not newsdata_key:
        print("Neither MARKETAUX_API_KEY nor NEWSDATA_API_KEY is set -- nothing to do.\n"
              "Add one or both to backend/.env (see .env.example) or export them.")
        return

    data = _load()
    today = _today()
    stocks = _prioritized_stocks()
    if not stocks:
        print("No breakouts.json yet -- run run_scan.py first so there's a conviction "
              "order to prioritize news fetches by.")
        return
    if limit is not None:
        stocks = stocks[:limit]

    stale = [s for s in stocks if (data.get(s["symbol"]) or {}).get("as_of") != today]
    print(f"news.json has {len(data)} symbols; {len(stale)} of {len(stocks)} in today's "
          f"conviction order are stale (not refreshed today: {today}).\n")

    updated_today = {sym for sym, d in data.items() if d.get("as_of") == today}

    # --- Phase 1: Marketaux (sentiment + headlines) on the top of the stale list ---
    mkt_ok = mkt_fail = 0
    if marketaux_key:
        budget = settings.NEWS_MARKETAUX_DAILY_BUDGET
        t0 = time.time()
        for s in stale:
            if mkt_ok + mkt_fail >= budget:
                print(f"  Marketaux: budget of {budget} reached for today, stopping this provider.")
                break
            try:
                result = fetch_marketaux(s["symbol"], s["name"], marketaux_key)
            except QuotaExhausted as e:
                print(f"  Marketaux: quota/auth error ({e}) -- stopping this provider for today.")
                break
            if result:
                result["as_of"] = today
                data[s["symbol"]] = result
                updated_today.add(s["symbol"])
                mkt_ok += 1
            else:
                mkt_fail += 1
            if (mkt_ok + mkt_fail) % 20 == 0:
                _save(data)
                print(f"  Marketaux {mkt_ok+mkt_fail:4d}/{min(budget, len(stale))} | "
                      f"ok {mkt_ok} fail {mkt_fail} | {time.time()-t0:.0f}s")
            time.sleep(1.0)
        _save(data)
        print(f"  Marketaux done: {mkt_ok} updated, {mkt_fail} empty/failed.\n")
    else:
        print("  Marketaux: MARKETAUX_API_KEY not set, skipped.\n")

    # --- Phase 2: NewsData.io (headline-only) continues down the SAME priority list, ---
    # skipping anything Marketaux already refreshed today so the two providers extend
    # coverage rather than duplicating it.
    nd_ok = nd_fail = 0
    if newsdata_key:
        budget = settings.NEWS_NEWSDATA_DAILY_BUDGET
        remaining = [s for s in stale if s["symbol"] not in updated_today]
        t0 = time.time()
        for s in remaining:
            if nd_ok + nd_fail >= budget:
                print(f"  NewsData.io: budget of {budget} reached for today, stopping this provider.")
                break
            try:
                result = fetch_newsdata(s["symbol"], s["name"], newsdata_key)
            except QuotaExhausted as e:
                print(f"  NewsData.io: quota/auth error ({e}) -- stopping this provider for today.")
                break
            if result:
                result["as_of"] = today
                data[s["symbol"]] = result
                nd_ok += 1
            else:
                nd_fail += 1
            if (nd_ok + nd_fail) % 20 == 0:
                _save(data)
                print(f"  NewsData.io {nd_ok+nd_fail:4d}/{min(budget, len(remaining))} | "
                      f"ok {nd_ok} fail {nd_fail} | {time.time()-t0:.0f}s")
            time.sleep(0.3)
        _save(data)
        print(f"  NewsData.io done: {nd_ok} updated, {nd_fail} empty/failed.\n")
    else:
        print("  NewsData.io: NEWSDATA_API_KEY not set, skipped.\n")

    print(f"news.json now has {len(data)} symbols "
          f"({mkt_ok} via Marketaux, {nd_ok} via NewsData.io today).")


if __name__ == "__main__":
    _load_env_file()
    if "--check" in sys.argv:
        _check(os.environ.get("MARKETAUX_API_KEY"), os.environ.get("NEWSDATA_API_KEY"))
    else:
        lim = next((int(a) for a in sys.argv[1:] if a.isdigit()), None)
        run(lim)
