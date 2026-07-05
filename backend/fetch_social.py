"""
Standalone: populate data/social.json with per-stock "social buzz" -- how much a stock
is being talked about, and with what tone -- from two independent, free sources:

  reddit  -- mention count (buzz) across a fixed set of India-trading subreddits
             (settings.SOCIAL_SUBREDDITS) in the last SOCIAL_MENTIONS_TIME_FILTER
             window, plus sentiment.py's local score over the matched posts' titles/
             selftext. Needs a free Reddit "script" app: create one at
             https://www.reddit.com/prefs/apps, then set REDDIT_CLIENT_ID /
             REDDIT_CLIENT_SECRET / REDDIT_USER_AGENT (see .env.example).
  trends  -- a 0-100 Google Trends search-interest score via pytrends (unofficial,
             no API key). Not sentiment -- just "how much is this being searched"
             attention, as a platform-independent complement to Reddit's buzz.

The two run as independent phases with independent per-symbol `as_of` markers (a
symbol can have fresh Reddit data but stale/missing Trends data, or vice versa --
each source has its own budget and failure modes). Same resumable,
incremental-save, conviction-ordered pattern as fetch_news.py.

Missing Reddit credentials just skips that phase; pytrends needs no key so the
Trends phase always runs (unless the whole run is capped to 0 stocks). run_scan.py
merges data/social.json into each stock's `social` field when present.

Usage:
    python fetch_social.py              # today's stale names in conviction order, budget-capped
    python fetch_social.py 50            # cap to the first 50 (by conviction) this run
    python fetch_social.py --check       # 1 test call per source against RELIANCE
"""
from __future__ import annotations
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pytrends.exceptions import ResponseError
from pytrends.request import TrendReq

import sentiment
import settings
from social_providers import QuotaExhausted, fetch_reddit_mentions

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
    if settings.SOCIAL_JSON.exists():
        with open(settings.SOCIAL_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(data: dict):
    payload = dict(sorted(data.items()))
    with open(settings.SOCIAL_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))


def _prioritized_stocks() -> list[dict]:
    """[{symbol, name}, ...] from the latest scan, highest conviction first -- same
    priority order fetch_news.py uses (buzz matters most for names actually setting up)."""
    if not settings.BREAKOUTS_JSON.exists():
        return []
    with open(settings.BREAKOUTS_JSON, encoding="utf-8") as f:
        stocks = json.load(f).get("stocks", [])

    def conv(s):
        c = (s.get("readiness") or {}).get("conviction")
        return c if c is not None else -1

    stocks.sort(key=conv, reverse=True)
    return [{"symbol": s["symbol"], "name": s.get("name") or s["symbol"]} for s in stocks]


def _buzz_label(mentions: int) -> str:
    if mentions >= settings.SOCIAL_BUZZ_HIGH:
        return "High"
    if mentions >= settings.SOCIAL_BUZZ_LOW:
        return "Medium"
    return "Low"


def _reddit_result(raw: dict) -> dict:
    texts = [p["title"] for p in raw["posts"]] + [p["selftext"] for p in raw["posts"] if p["selftext"]]
    sample = [{"title": p["title"], "url": p["permalink"], "subreddit": p["subreddit"], "score": p["score"]}
              for p in sorted(raw["posts"], key=lambda p: p["score"], reverse=True)[:3]]
    return {
        "mentions": raw["mentions"],
        "buzz": _buzz_label(raw["mentions"]),
        "sentiment": sentiment.score_texts(texts),
        "sample": sample,
    }


def _trends_interest(pytrends: TrendReq, name: str) -> int | None:
    """Latest 0-100 India search-interest point for `name` over the trailing month,
    or None if pytrends returns nothing (thin/unlisted search terms often do)."""
    pytrends.build_payload([name], timeframe="today 1-m", geo="IN")
    df = pytrends.interest_over_time()
    if df is None or df.empty or name not in df.columns:
        return None
    return int(df[name].iloc[-1])


def _check(client_id, client_secret, user_agent):
    print("Smoke test -- one call per source against RELIANCE.\n")
    if client_id and client_secret:
        try:
            r = fetch_reddit_mentions("RELIANCE", "Reliance Industries Limited", client_id, client_secret,
                                       user_agent or "BreakoutAI/1.0", settings.SOCIAL_SUBREDDITS,
                                       settings.SOCIAL_MENTIONS_TIME_FILTER)
            print("Reddit:", "OK" if r is not None else "no data returned")
            print(" ", r)
        except QuotaExhausted as e:
            print("Reddit: quota/auth error --", e)
        except Exception as e:
            print("Reddit: FAILED --", repr(e))
    else:
        print("Reddit: REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET not set, skipped.")
    print()
    try:
        pytrends = TrendReq(hl="en-IN", tz=330)
        score = _trends_interest(pytrends, "Reliance Industries")
        print("Google Trends:", "OK" if score is not None else "no data returned")
        print("  interest:", score)
    except Exception as e:
        print("Google Trends: FAILED --", repr(e))


def run(limit: int | None = None):
    _load_env_file()
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    user_agent = os.environ.get("REDDIT_USER_AGENT") or "BreakoutAI/1.0 (social buzz fetch)"

    data = _load()
    today = _today()
    stocks = _prioritized_stocks()
    if not stocks:
        print("No breakouts.json yet -- run run_scan.py first so there's a conviction "
              "order to prioritize by.")
        return
    if limit is not None:
        stocks = stocks[:limit]

    # --- Phase 1: Reddit mentions + sentiment ---
    rd_ok = rd_fail = 0
    if client_id and client_secret:
        stale = [s for s in stocks if ((data.get(s["symbol"]) or {}).get("reddit") or {}).get("as_of") != today]
        print(f"Reddit: {len(stale)} of {len(stocks)} in today's conviction order are stale.\n")
        budget = settings.SOCIAL_REDDIT_DAILY_BUDGET
        t0 = time.time()
        for s in stale:
            if rd_ok + rd_fail >= budget:
                print(f"  Reddit: budget of {budget} reached for today, stopping.")
                break
            try:
                raw = fetch_reddit_mentions(s["symbol"], s["name"], client_id, client_secret, user_agent,
                                             settings.SOCIAL_SUBREDDITS, settings.SOCIAL_MENTIONS_TIME_FILTER)
            except QuotaExhausted as e:
                print(f"  Reddit: quota/auth error ({e}) -- stopping for today.")
                break
            entry = data.setdefault(s["symbol"], {})
            if raw is not None:
                entry["reddit"] = {**_reddit_result(raw), "as_of": today}
                rd_ok += 1
            else:
                entry.setdefault("reddit", None)
                rd_fail += 1
            if (rd_ok + rd_fail) % 20 == 0:
                _save(data)
                print(f"  Reddit {rd_ok+rd_fail:4d}/{min(budget, len(stale))} | "
                      f"ok {rd_ok} fail {rd_fail} | {time.time()-t0:.0f}s")
            time.sleep(1.0)
        _save(data)
        print(f"  Reddit done: {rd_ok} updated, {rd_fail} empty/failed.\n")
    else:
        print("Reddit: REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET not set, skipped.\n")

    # --- Phase 2: Google Trends search interest ---
    tr_ok = tr_fail = 0
    stale = [s for s in stocks if ((data.get(s["symbol"]) or {}).get("trends") or {}).get("as_of") != today]
    print(f"Trends: {len(stale)} of {len(stocks)} in today's conviction order are stale.\n")
    budget = settings.TRENDS_DAILY_BUDGET
    pytrends = TrendReq(hl="en-IN", tz=330)
    t0 = time.time()
    for s in stale:
        if tr_ok + tr_fail >= budget:
            print(f"  Trends: budget of {budget} reached for today, stopping.")
            break
        try:
            score = _trends_interest(pytrends, s["name"])
        except ResponseError as e:
            print(f"  Trends: rate-limited/blocked ({e}) -- stopping for today.")
            break
        except Exception:
            score = None
        entry = data.setdefault(s["symbol"], {})
        if score is not None:
            entry["trends"] = {"interest": score, "as_of": today}
            tr_ok += 1
        else:
            entry.setdefault("trends", None)
            tr_fail += 1
        if (tr_ok + tr_fail) % 20 == 0:
            _save(data)
            print(f"  Trends {tr_ok+tr_fail:4d}/{min(budget, len(stale))} | "
                  f"ok {tr_ok} fail {tr_fail} | {time.time()-t0:.0f}s")
        time.sleep(settings.TRENDS_MIN_REQUEST_GAP_SEC)
    _save(data)
    print(f"  Trends done: {tr_ok} updated, {tr_fail} empty/failed.\n")

    print(f"social.json now has {len(data)} symbols ({rd_ok} Reddit, {tr_ok} Trends updated today).")


if __name__ == "__main__":
    _load_env_file()
    if "--check" in sys.argv:
        _check(os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET"),
               os.environ.get("REDDIT_USER_AGENT"))
    else:
        lim = next((int(a) for a in sys.argv[1:] if a.isdigit()), None)
        run(lim)
