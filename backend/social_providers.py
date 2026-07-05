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
tickers, so NSE/BSE names essentially don't appear on it.

Raises QuotaExhausted on a 401/403/429 (bad credentials or rate-limited) so the caller
can stop for the rest of the run; any other non-200 response or empty result just
returns None (skip this symbol, keep going).
"""
from __future__ import annotations
import time

import requests

_TIMEOUT = 10

# Module-level so the OAuth token (valid ~1h) is reused across every symbol in a run
# instead of re-authenticating per request.
_token_cache = {"token": None, "expires_at": 0.0}


class QuotaExhausted(Exception):
    """Raised when Reddit signals bad credentials or a rate limit -- distinct from an
    ordinary miss, so the caller stops spending budget on the same error."""


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
