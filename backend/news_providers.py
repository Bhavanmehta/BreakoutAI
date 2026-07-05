"""
Thin HTTP clients for the two free news/sentiment providers fetch_news.py uses.

Marketaux (https://www.marketaux.com) -- the primary source: entity-level sentiment
(-1..1) alongside headlines, but only ~100 requests/day on the free plan, so it's spent
on the highest-conviction names only (see fetch_news.py's budget split).

NewsData.io (https://newsdata.io) -- the secondary source: no sentiment on its free
tier and articles are ~12h delayed, but a much bigger daily budget (200 credits/day), so
it extends plain headline coverage further down the conviction list.

Finnhub is deliberately not used here: its free tier is US-only for company news/
sentiment (international coverage needs a paid Premium plan), so despite a generous
60 calls/min rate limit it can't cover NSE/BSE names for free.

Both raise QuotaExhausted on a quota/auth-type response (401/402/403/429) so the caller
can stop spending that provider's budget for the rest of the run rather than treating it
as a one-off miss; any other non-200 response or empty result just returns None (skip
this symbol, keep going -- a single bad/uncovered stock shouldn't abort the run).
"""
from __future__ import annotations
import re
from datetime import datetime, timedelta, timezone

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


def _core_name(name: str) -> str:
    """'GMR Airports Limited' -> 'gmrairports' -- strips common corporate suffixes so a
    provider's matched entity name can be fuzzy-matched against our own company name
    regardless of exact legal-suffix differences."""
    n = re.sub(r"\b(ltd\.?|limited|pvt\.?|private|inc\.?|corporation|corp\.?|company|co\.?)\b",
               "", name, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]", "", n.lower())


def _sentiment_label(score: float) -> str:
    if score >= settings.NEWS_SENTIMENT_BULLISH:
        return "Bullish"
    if score <= settings.NEWS_SENTIMENT_BEARISH:
        return "Bearish"
    return "Neutral"


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

    core = _core_name(name)
    matched_scores, headlines = [], []
    for a in articles[:10]:
        for e in (a.get("entities") or []):
            score = e.get("sentiment_score")
            if score is None:
                continue
            e_core = _core_name(e.get("name", ""))
            if e.get("symbol", "").upper() == symbol.upper() or (e_core and (e_core in core or core in e_core)):
                matched_scores.append(score)
        if len(headlines) < 5 and a.get("title"):
            headlines.append({
                "title": a["title"], "source": (a.get("source") or "").strip() or None,
                "url": a.get("url"), "published_at": a.get("published_at"),
                "provider": "marketaux",
            })

    if not headlines:
        return None

    sentiment = None
    if matched_scores:
        avg = round(sum(matched_scores) / len(matched_scores), 2)
        sentiment = {"score": avg, "label": _sentiment_label(avg)}

    return {"headlines": _sort_recent_first(headlines), "sentiment": sentiment}


def fetch_newsdata(symbol: str, name: str, api_key: str) -> dict | None:
    """Headline-only (no sentiment on the free tier), ~12h-delayed, India-filtered
    company news via a name search."""
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
    return {"headlines": _sort_recent_first(headlines), "sentiment": None}
