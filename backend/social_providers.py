"""
Thin HTTP client for Reddit -- the free "social buzz" source fetch_social.py uses.

Reddit (https://www.reddit.com) -- a registered "script" app (free, no billing;
create one at https://www.reddit.com/prefs/apps) grants an OAuth2 client-credentials
token, which searches a fixed set of India-focused trading subreddits
(settings.SOCIAL_SUBREDDITS) for symbol/company mentions in the last
SOCIAL_MENTIONS_TIME_FILTER window. Mention count is the "buzz" signal; the post
titles/selftext are scored for sentiment by sentiment.py (fetch_social.py does that --
this module only fetches raw posts) so social sentiment sits on the same scale as
news sentiment.

StockTwits is deliberately not used here: its coverage is almost entirely US-listed
tickers, so NSE/BSE names essentially don't appear on it (and as of this writing
they're not accepting new developer registrations anyway).

For the US market, ApeWisdom (apewisdom.io) is used INSTEAD of Reddit's own OAuth
API: it's a free, keyless, pre-aggregated Reddit mention-count tracker (WSB-centric
subreddit coverage), so the US social phase needs zero credentials, unlike India's
still-unresolved Reddit app requirement. It returns mention counts/rank/upvotes, not
post text, so there's no per-post sentiment to score for US buzz (mentions/rank only).

Raises QuotaExhausted on a 401/403/429 (bad credentials or rate-limited) so the caller
can stop for the rest of the run; any other non-200 response or empty result just
returns None (skip this symbol, keep going).
"""
from __future__ import annotations
import time

import requests

_TIMEOUT = 10
_APEWISDOM_BASE = "https://apewisdom.io/api/v1.0/filter"


class QuotaExhausted(Exception):
    """Raised when a provider signals bad credentials or a rate limit -- distinct
    from an ordinary miss, so the caller stops spending budget on the same error."""


def fetch_apewisdom_mentions(filter_name: str = "wallstreetbets") -> dict[str, dict] | None:
    """Whole-board snapshot in one call -> {TICKER: {mentions, rank, upvotes,
    mentions_24h_ago, rank_24h_ago}}, or None on failure. Free, no key. Paginates
    internally (the endpoint returns ~100 rows/page); stops once a page comes back
    short or empty. Call once per run and look symbols up locally rather than
    hitting the API per-symbol -- it's a fixed leaderboard, not a per-ticker query."""
    out: dict[str, dict] = {}
    page = 1
    while True:
        try:
            resp = requests.get(f"{_APEWISDOM_BASE}/{filter_name}/page/{page}",
                                 headers={"User-Agent": "Mozilla/5.0"}, timeout=_TIMEOUT)
        except requests.RequestException:
            break
        if resp.status_code == 429:
            raise QuotaExhausted("HTTP 429 from ApeWisdom (rate limited)")
        if resp.status_code != 200:
            break
        try:
            payload = resp.json() or {}
        except ValueError:
            break
        results = payload.get("results") or []
        for r in results:
            ticker = (r.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            out[ticker] = {
                "mentions": r.get("mentions", 0),
                "rank": r.get("rank"),
                "upvotes": r.get("upvotes", 0),
                "mentions_24h_ago": r.get("mentions_24h_ago", 0),
                "rank_24h_ago": r.get("rank_24h_ago"),
            }
        if len(results) < 100 or page >= (payload.get("pages") or 1):
            break
        page += 1
        time.sleep(0.3)
    return out or None

# Module-level so the OAuth token (valid ~1h) is reused across every symbol in a run
# instead of re-authenticating per request.
_token_cache = {"token": None, "expires_at": 0.0}


def _get_token(client_id: str, client_secret: str, user_agent: str) -> str | None:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]
    try:
        resp = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": user_agent},
            timeout=_TIMEOUT,
        )
    except requests.RequestException:
        return None
    if resp.status_code in (401, 403):
        raise QuotaExhausted(f"HTTP {resp.status_code} from Reddit's token endpoint (bad credentials)")
    if resp.status_code != 200:
        return None
    try:
        payload = resp.json() or {}
    except ValueError:
        return None
    token = payload.get("access_token")
    if not token:
        return None
    _token_cache["token"] = token
    # Refresh a minute early so we never fire a request on an already-expired token.
    _token_cache["expires_at"] = now + payload.get("expires_in", 3600) - 60
    return token


def fetch_reddit_mentions(symbol: str, name: str, client_id: str, client_secret: str,
                           user_agent: str, subreddits: list[str], time_filter: str = "week",
                           limit: int = 25) -> dict | None:
    """Posts mentioning this stock's symbol or name across `subreddits` in the last
    `time_filter` window -> {mentions, posts: [...]}, or None on any failure/no-key."""
    token = _get_token(client_id, client_secret, user_agent)
    if not token:
        return None

    first_word = (name.split()[0] if name else symbol).strip()
    query = symbol if first_word.lower() == symbol.lower() else f'{symbol} OR "{first_word}"'
    sr_path = "+".join(subreddits)

    try:
        resp = requests.get(
            f"https://oauth.reddit.com/r/{sr_path}/search",
            params={"q": query, "restrict_sr": 1, "sort": "new", "t": time_filter, "limit": limit},
            headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent},
            timeout=_TIMEOUT,
        )
    except requests.RequestException:
        return None

    if resp.status_code in (401, 403, 429):
        raise QuotaExhausted(f"HTTP {resp.status_code} from Reddit search (auth or rate limit)")
    if resp.status_code != 200:
        return None

    try:
        children = ((resp.json() or {}).get("data") or {}).get("children") or []
    except ValueError:
        return None

    posts = []
    for c in children:
        d = c.get("data") or {}
        if not d.get("title"):
            continue
        posts.append({
            "title": d["title"],
            "selftext": (d.get("selftext") or "")[:500],
            "subreddit": d.get("subreddit"),
            "score": d.get("score", 0),
            "num_comments": d.get("num_comments", 0),
            "permalink": ("https://reddit.com" + d["permalink"]) if d.get("permalink") else None,
            "created_utc": d.get("created_utc"),
        })

    return {"mentions": len(posts), "posts": posts}
