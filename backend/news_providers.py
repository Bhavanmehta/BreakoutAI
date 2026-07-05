"""
Thin HTTP clients for the free news providers fetch_news.py uses.

GNews (https://gnews.io) -- 100 requests/day free, undelayed, spent first on the
most-primed/highest-conviction names only (see fetch_news.py's budget split).

Marketaux (https://www.marketaux.com) -- ~100 requests/day on the free plan, continues
coverage down the same conviction-ordered list after GNews.

NewsData.io (https://newsdata.io) -- articles are ~12h delayed, but a much bigger
daily budget (200 credits/day), so it extends plain headline coverage furthest down
the conviction list, where the delay matters least.

Google News RSS (news.google.com/rss/search) -- the 4th and last phase, run only for
whatever's still uncovered after the three budgeted APIs above. No key, no published
quota, so it's the one source that can realistically extend coverage into the long
tail of small/micro-caps the others often return nothing for (confirmed live: names
like DJ Mediaprint or Onmobile that GNews/Marketaux/NewsData all missed still turn up
30+ Google News results). Self-imposed pacing (NEWS_RSS_MIN_REQUEST_GAP_SEC) and a
self-imposed daily cap (NEWS_RSS_DAILY_BUDGET) keep it polite since there's no
official rate limit to respect. NOTE: Google's RSS feed terms restrict it to
"personal, non-commercial use" in a personal feed reader -- fine for this project's
current free/educational use, but worth re-checking if BreakoutAI is ever monetized.

None of the three carry usable sentiment on the free tier for our purposes (Marketaux's
per-article entity-level sentiment_score only appears when it can tag a specific matched
entity, which -- combined with the recency filter below -- meant real coverage was
often just 1-2 stocks out of a whole day's batch). Sentiment is instead computed
locally from headline text by sentiment.py, uniformly across all three providers --
see fetch_news.py.

Finnhub is deliberately not used here: its free tier is US-only for company news/
sentiment (international coverage needs a paid Premium plan), so despite a generous
60 calls/min rate limit it can't cover NSE/BSE names for free.

All three raise QuotaExhausted on a quota/auth-type response (401/402/403/429) so the
caller can stop spending that provider's budget for the rest of the run rather than
treating it as a one-off miss; any other non-200 response or empty result just returns
None (skip this symbol, keep going -- a single bad/uncovered stock shouldn't abort the run).
"""
from __future__ import annotations
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

import settings


def _sort_recent_first(headlines: list[dict]) -> list[dict]:
    """Newest-first by published_at; undated items sink to the bottom. A safety net so
    the cached order is always sensible even if a provider ignores our sort param."""
    def key(h):
        ts = h.get("published_at") or ""
        return ts  # ISO-8601 strings sort chronologically as text
    return sorted(headlines, key=key, reverse=True)

_TIMEOUT = 10


class QuotaExhausted(Exception):
    """Raised when a provider signals its quota is exhausted (or the key is bad) --
    distinct from an ordinary miss, so the caller stops calling that provider for the
    rest of this run instead of burning through remaining symbols on the same error."""


def fetch_marketaux(symbol: str, name: str, api_key: str) -> dict | None:
    """One stock's recent India-market news + aggregate entity sentiment, via a
    company-name search. NSE ticker suffixes aren't reliably indexed in Marketaux's
    symbol universe, so a name search with a countries=in filter is more robust than
    guessing an exchange-suffixed symbol format (e.g. RELIANCE.NSE).

    `published_after` + `sort=published_desc` are essential: without them the free
    search returns whatever matches by relevance, which for thinly-covered small-caps
    is often years-old articles. We constrain to the last NEWS_MAX_AGE_DAYS and take
    the newest first so a stock either gets genuinely recent news or none."""
    published_after = (datetime.now(timezone.utc)
                       - timedelta(days=settings.NEWS_MAX_AGE_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        resp = requests.get(
            "https://api.marketaux.com/v1/news/all",
            params={
                "search": name, "countries": "in", "language": "en",
                "filter_entities": "true", "limit": 10, "api_token": api_key,
                "published_after": published_after, "sort": "published_desc",
            },
            timeout=_TIMEOUT,
        )
    except requests.RequestException:
        return None

    if resp.status_code in (401, 402, 429):
        raise QuotaExhausted(f"HTTP {resp.status_code} from Marketaux (quota or auth)")
    if resp.status_code != 200:
        return None

    try:
        articles = (resp.json() or {}).get("data") or []
    except ValueError:
        return None
    if not articles:
        return None

    headlines = []
    for a in articles[:10]:
        if len(headlines) >= 5:
            break
        if not a.get("title"):
            continue
        headlines.append({
            "title": a["title"], "source": (a.get("source") or "").strip() or None,
            "url": a.get("url"), "published_at": a.get("published_at"),
            "provider": "marketaux",
        })

    if not headlines:
        return None
    return {"headlines": _sort_recent_first(headlines)}


def fetch_newsdata(symbol: str, name: str, api_key: str) -> dict | None:
    """Headline-only, ~12h-delayed, India-filtered company news via a name search."""
    try:
        resp = requests.get(
            "https://newsdata.io/api/1/latest",
            params={"apikey": api_key, "q": name, "country": "in", "language": "en"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException:
        return None

    if resp.status_code in (401, 403, 429):
        raise QuotaExhausted(f"HTTP {resp.status_code} from NewsData.io (quota or auth)")
    if resp.status_code != 200:
        return None

    try:
        payload = resp.json() or {}
    except ValueError:
        return None
    if payload.get("status") != "success":
        return None
    articles = payload.get("results") or []
    if not articles:
        return None

    headlines = []
    for a in articles[:5]:
        if not a.get("title"):
            continue
        headlines.append({
            "title": a["title"], "source": a.get("source_id"),
            "url": a.get("link"), "published_at": a.get("pubDate"),
            "provider": "newsdata",
        })
    if not headlines:
        return None
    # NewsData's free "latest" feed is already recent; sort newest-first for consistency.
    return {"headlines": _sort_recent_first(headlines)}


def fetch_gnews(symbol: str, name: str, api_key: str) -> dict | None:
    """Headline-only, India-filtered company news via GNews's search endpoint (100 free
    requests/day) -- run first, on the most-primed/highest-conviction names, before
    Marketaux and NewsData.io extend coverage further down the list.

    GNews's free plan also enforces a short-burst rate limit (observed: 429s even with
    ~1 req/sec spacing, clean at ~1.2s -- see fetch_news.py's inter-call sleep) that is
    distinct from the daily quota and reports the SAME 429 status. A burst 429 says
    "too many requests ... in a short period of time" in its body; we retry that once
    after a short backoff and treat it as a soft miss (not QuotaExhausted) if it
    persists, so one rate-limit blip doesn't abort the whole day's GNews run. Any other
    429/401/403 is a real quota/auth failure."""
    def _do_request():
        return requests.get(
            "https://gnews.io/api/v4/search",
            params={"q": name, "country": "in", "lang": "en", "max": 5, "apikey": api_key},
            timeout=_TIMEOUT,
        )

    try:
        resp = _do_request()
    except requests.RequestException:
        return None

    if resp.status_code == 429 and "short period of time" in resp.text:
        time.sleep(2.0)
        try:
            resp = _do_request()
        except requests.RequestException:
            return None
        if resp.status_code == 429 and "short period of time" in resp.text:
            return None  # transient burst limit, not a real quota exhaustion -- skip this symbol

    if resp.status_code in (401, 403, 429):
        raise QuotaExhausted(f"HTTP {resp.status_code} from GNews (quota or auth)")
    if resp.status_code != 200:
        return None

    try:
        articles = (resp.json() or {}).get("articles") or []
    except ValueError:
        return None
    if not articles:
        return None

    headlines = []
    for a in articles[:5]:
        if not a.get("title"):
            continue
        headlines.append({
            "title": a["title"], "source": ((a.get("source") or {}).get("name")),
            "url": a.get("url"), "published_at": a.get("publishedAt"),
            "provider": "gnews",
        })
    if not headlines:
        return None
    return {"headlines": _sort_recent_first(headlines)}


_RSS_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
}


def fetch_rss(symbol: str, name: str) -> dict | None:
    """No-key, no-quota company news via Google News' RSS search -- the last-resort
    phase 4 that covers whatever the three budgeted APIs above couldn't (see module
    docstring). `when:{N}d` mirrors NEWS_MAX_AGE_DAYS so recency stays consistent
    across every provider.

    Each item's title comes as "Headline - Source Name"; a companion <source> tag
    carries the source cleanly, so we strip the redundant suffix off the title when
    it matches. The <link> is a news.google.com redirect (not the publisher's own
    URL) -- that's inherent to how Google News RSS works, but it still resolves to
    the real article when opened, which is all a headline card needs."""
    query = f"{name} when:{settings.NEWS_MAX_AGE_DAYS}d"
    try:
        resp = requests.get(
            "https://news.google.com/rss/search",
            params={"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
            headers=_RSS_HEADERS, timeout=_TIMEOUT,
        )
    except requests.RequestException:
        return None

    if resp.status_code in (403, 429):
        raise QuotaExhausted(f"HTTP {resp.status_code} from Google News RSS (blocked)")
    if resp.status_code != 200:
        return None

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return None
    items = root.findall(".//item")
    if not items:
        return None

    headlines = []
    for it in items[:5]:
        title = it.findtext("title")
        if not title:
            continue
        source = it.findtext("source")
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)]
        pub_raw = it.findtext("pubDate")
        try:
            published_at = parsedate_to_datetime(pub_raw).isoformat() if pub_raw else None
        except (TypeError, ValueError):
            published_at = None
        headlines.append({
            "title": title, "source": source, "url": it.findtext("link"),
            "published_at": published_at, "provider": "rss",
        })
    if not headlines:
        return None
    return {"headlines": _sort_recent_first(headlines)}
