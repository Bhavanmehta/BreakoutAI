"""
Standalone: populate data/news.json with per-stock news headlines from GNews
(phase 1 -- 100 free req/day, undelayed, spent on the most-primed/highest-conviction
names first), Marketaux (phase 2, ~100 free req/day), NewsData.io (phase 3 --
~12h-delayed but a bigger 200 free credits/day budget, so it extends coverage furthest
down the list where freshness matters less), and Google News RSS (phase 4 -- no key, no
quota, covers whatever's still left after the three budgeted APIs). See
news_providers.py's docstring for why these four and not Finnhub (its free tier is
US-only for company news/sentiment).

Sentiment is NOT taken from any provider -- Marketaux's free-tier entity sentiment only
appears when it can tag a specific matched entity, which combined with the recency
filter below meant real coverage was often just one stock out of a whole day's batch.
Instead, once a stock's headlines are fetched (from whichever provider), sentiment.py
scores the headline text ourselves (VADER + a finance lexicon + event_classifier's
corporate-event bias), so every stock with ANY cached headline gets a sentiment label,
uniformly across all four providers.

Unlike holdings/sectors/fundamentals (slow-changing reference data fetched once and kept
forever), news is time-sensitive: this refreshes daily rather than skip-if-present -- a
symbol only gets re-fetched if its cached entry isn't from today (IST). Still resumable
within a day via the same incremental-save pattern as fetch_holdings.py/fetch_sectors.py,
so a mid-run quota/network hiccup just needs a re-run.

Budget split: GNews is spent on the highest-conviction names first (where a news
signal is most actionable, and its 100/day budget is deliberately reserved for those
names rather than the long tail); Marketaux, then NewsData.io, then Google News RSS
continue down the SAME conviction-ordered list, each skipping whatever an earlier
provider already refreshed today, so the four providers extend coverage rather than
duplicating it.

Needs at least one of MARKETAUX_API_KEY / NEWSDATA_API_KEY / GNEWS_API_KEY -- either
exported, or in backend/.env (gitignored, see .env.example). Missing a key just skips
that provider (the others still run); Google News RSS needs no key so it always runs.
run_scan.py merges data/news.json into each stock's `news` field when present, same
optional pattern as holdings/sectors/fundamentals.

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
import sentiment
from news_providers import QuotaExhausted, fetch_gnews, fetch_marketaux, fetch_newsdata, fetch_rss

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


def _check(marketaux_key: str | None, newsdata_key: str | None, gnews_key: str | None = None):
    print("Smoke test -- one call per configured provider against RELIANCE.\n")
    if gnews_key:
        try:
            r = fetch_gnews("RELIANCE", "Reliance Industries Limited", gnews_key)
            print("GNews:", "OK" if r else "no data returned")
            print(" ", r)
        except QuotaExhausted as e:
            print("GNews: quota/auth error --", e)
        except Exception as e:
            print("GNews: FAILED --", repr(e))
    else:
        print("GNews: GNEWS_API_KEY not set, skipped.")
    print()
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
    print()
    try:
        r = fetch_rss("RELIANCE", "Reliance Industries Limited")
        print("Google News RSS:", "OK" if r else "no data returned")
        print(" ", r)
    except QuotaExhausted as e:
        print("Google News RSS: blocked --", e)
    except Exception as e:
        print("Google News RSS: FAILED --", repr(e))
    print()
    print("Sentiment scorer (local, no key needed):")
    print(" ", sentiment.score_texts([
        "Company reports record profit, beats estimates",
        "Board approves buyback of shares",
    ]))


def _attach_sentiment(result: dict) -> dict:
    """Score this stock's cached headline titles ourselves (sentiment.py) regardless of
    which provider they came from -- see the module docstring for why we stopped
    relying on Marketaux's own entity-tagged sentiment."""
    result["sentiment"] = sentiment.score_texts([h["title"] for h in result["headlines"]])
    return result


def run(limit: int | None = None):
    _load_env_file()
    marketaux_key = os.environ.get("MARKETAUX_API_KEY")
    newsdata_key = os.environ.get("NEWSDATA_API_KEY")
    gnews_key = os.environ.get("GNEWS_API_KEY")
    # Google News RSS needs no key, so it's never the reason to bail out early --
    # only note when none of the budgeted APIs are configured (RSS-only coverage).
    if not marketaux_key and not newsdata_key and not gnews_key:
        print("None of MARKETAUX_API_KEY / NEWSDATA_API_KEY / GNEWS_API_KEY is set -- "
              "running with Google News RSS only (no key needed).\n"
              "Add one or more to backend/.env (see .env.example) for higher-quality/faster coverage.\n")

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

    # --- Phase 1: GNews on the top of the stale list (most primed/highest-conviction ---
    # names first -- its 100/day budget is deliberately reserved for these rather than
    # spent on the long tail).
    gn_ok = gn_fail = 0
    if gnews_key:
        budget = settings.NEWS_GNEWS_DAILY_BUDGET
        t0 = time.time()
        for s in stale:
            if gn_ok + gn_fail >= budget:
                print(f"  GNews: budget of {budget} reached for today, stopping this provider.")
                break
            try:
                result = fetch_gnews(s["symbol"], s["name"], gnews_key)
            except QuotaExhausted as e:
                print(f"  GNews: quota/auth error ({e}) -- stopping this provider for today.")
                break
            if result:
                result["as_of"] = today
                data[s["symbol"]] = _attach_sentiment(result)
                updated_today.add(s["symbol"])
                gn_ok += 1
            else:
                gn_fail += 1
            if (gn_ok + gn_fail) % 20 == 0:
                _save(data)
                print(f"  GNews {gn_ok+gn_fail:4d}/{min(budget, len(stale))} | "
                      f"ok {gn_ok} fail {gn_fail} | {time.time()-t0:.0f}s")
            time.sleep(1.2)  # GNews's free-plan burst limiter 429s well under 1s spacing (see news_providers.fetch_gnews)
        _save(data)
        print(f"  GNews done: {gn_ok} updated, {gn_fail} empty/failed.\n")
    else:
        print("  GNews: GNEWS_API_KEY not set, skipped.\n")

    # --- Phase 2: Marketaux continues down the SAME priority list, skipping anything ---
    # GNews already refreshed today so the providers extend coverage rather than
    # duplicating it.
    mkt_ok = mkt_fail = 0
    if marketaux_key:
        budget = settings.NEWS_MARKETAUX_DAILY_BUDGET
        remaining = [s for s in stale if s["symbol"] not in updated_today]
        t0 = time.time()
        for s in remaining:
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
                data[s["symbol"]] = _attach_sentiment(result)
                updated_today.add(s["symbol"])
                mkt_ok += 1
            else:
                mkt_fail += 1
            if (mkt_ok + mkt_fail) % 20 == 0:
                _save(data)
                print(f"  Marketaux {mkt_ok+mkt_fail:4d}/{min(budget, len(remaining))} | "
                      f"ok {mkt_ok} fail {mkt_fail} | {time.time()-t0:.0f}s")
            time.sleep(1.0)
        _save(data)
        print(f"  Marketaux done: {mkt_ok} updated, {mkt_fail} empty/failed.\n")
    else:
        print("  Marketaux: MARKETAUX_API_KEY not set, skipped.\n")

    # --- Phase 3: NewsData.io continues down the SAME priority list, skipping anything ---
    # GNews or Marketaux already refreshed today. Its ~12h delay matters least this far
    # down the list, and its bigger 200/day budget extends coverage furthest.
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
                data[s["symbol"]] = _attach_sentiment(result)
                updated_today.add(s["symbol"])
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

    # --- Phase 4: Google News RSS continues down the SAME priority list -- no key, no ---
    # published quota, so this is the one phase that can realistically reach the long
    # tail of small/micro-caps the three budgeted APIs above often return nothing for.
    rss_ok = rss_fail = 0
    budget = settings.NEWS_RSS_DAILY_BUDGET
    remaining = [s for s in stale if s["symbol"] not in updated_today]
    t0 = time.time()
    for s in remaining:
        if rss_ok + rss_fail >= budget:
            print(f"  Google News RSS: budget of {budget} reached for today, stopping this provider.")
            break
        try:
            result = fetch_rss(s["symbol"], s["name"])
        except QuotaExhausted as e:
            print(f"  Google News RSS: blocked ({e}) -- stopping this provider for today.")
            break
        if result:
            result["as_of"] = today
            data[s["symbol"]] = _attach_sentiment(result)
            updated_today.add(s["symbol"])
            rss_ok += 1
        else:
            rss_fail += 1
        if (rss_ok + rss_fail) % 20 == 0:
            _save(data)
            print(f"  Google News RSS {rss_ok+rss_fail:4d}/{min(budget, len(remaining))} | "
                  f"ok {rss_ok} fail {rss_fail} | {time.time()-t0:.0f}s")
        time.sleep(settings.NEWS_RSS_MIN_REQUEST_GAP_SEC)
    _save(data)
    print(f"  Google News RSS done: {rss_ok} updated, {rss_fail} empty/failed.\n")

    print(f"news.json now has {len(data)} symbols ({gn_ok} via GNews, {mkt_ok} via Marketaux, "
          f"{nd_ok} via NewsData.io, {rss_ok} via Google News RSS today).")


if __name__ == "__main__":
    _load_env_file()
    if "--check" in sys.argv:
        _check(os.environ.get("MARKETAUX_API_KEY"), os.environ.get("NEWSDATA_API_KEY"), os.environ.get("GNEWS_API_KEY"))
    else:
        lim = next((int(a) for a in sys.argv[1:] if a.isdigit()), None)
        run(lim)
