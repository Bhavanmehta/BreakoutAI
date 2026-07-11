"""
Thin HTTP client for Polygon.io's free ("Basic") tier -- backs options_flow_scan.py's
unusual-single-leg-options-activity research prototype.

Free-tier reality check (why this client is shaped the way it is):
  - Rate limit: 5 requests/minute. options_flow_scan.py's pacer (settings.
    POLYGON_MIN_REQUEST_GAP_SEC) enforces a safe gap between every call this module
    makes, so a whole ticker scan (contracts list + one daily-agg call per contract)
    takes a couple of minutes, not seconds.
  - Data is end-of-day / delayed on the free tier -- there is no live "alert stream"
    here. This client only ever asks for a specific already-completed trading day's
    daily aggregate per contract, so the delay is a non-issue: a finished day's bar
    doesn't change whether you fetch it 1 minute or 3 hours after the close.
  - Daily aggregates (volume, vwap, transaction count), not raw tick-by-tick trades.
    That means: no aggressor/side (buy vs sell) determination -- doing that honestly
    needs each trade compared against the NBBO quote at that instant, which is a much
    heavier tick-level pull the free tier's rate limit can't support across a watchlist.
    What IS honestly derivable from one daily-agg call per contract: total contracts
    traded that day, total notional (volume * vwap * 100), and average trade size
    (volume / transaction count) -- a real, if blunter, "big prints vs retail noise"
    signal. See options_flow_scan.py's docstring for the full "what this can/can't do"
    writeup before extending this.
  - Options reference/contracts metadata (strikes, expirations, contract type) is a
    separate, cheaper "reference" endpoint -- one call per ticker regardless of how
    many contracts it returns.

Docs: https://polygon.io/docs/rest/options (Polygon.io rebranded to Massive.com on
2025-10-30 -- api.massive.com is the new default base URL, but api.polygon.io "remains
supported for an extended period" per the migration notes and existing API keys work
identically on either host, so this client keeps pointing at api.polygon.io rather than
churn the URL for no functional gain; flip _BASE below if polygon.io is ever sunset).
"""
from __future__ import annotations
import time
from datetime import date as _date

import requests

_BASE = "https://api.polygon.io"
_TIMEOUT = 15
# Transient-network retry knobs: a dropped connection (RemoteDisconnected, read
# timeout, etc.) is not a quota/auth problem and not the caller's fault -- retrying
# a couple times with a short backoff is cheap insurance against one flaky TCP
# handshake killing an otherwise-working multi-minute scan. Deliberately small/local
# (not settings.-driven) since this is infra-noise handling, not a tunable strategy
# parameter.
_MAX_RETRIES = 3
_RETRY_BACKOFF_SEC = 2.0


class PolygonQuotaExhausted(Exception):
    """Raised on a 429 (rate limit) or 401/403 (bad/unentitled key) -- distinct from an
    ordinary empty result, so the caller can stop the run instead of burning through the
    rest of the watchlist on the same error."""


def _get(session: requests.Session, path: str, api_key: str, **params) -> dict:
    params["apiKey"] = api_key
    last_exc: requests.exceptions.RequestException | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = session.get(f"{_BASE}{path}", params=params, timeout=_TIMEOUT)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF_SEC * (attempt + 1))
                continue
            raise
        if resp.status_code in (401, 403, 429):
            raise PolygonQuotaExhausted(
                f"{path} -> HTTP {resp.status_code}: {resp.text[:200]}"
            )
        if resp.status_code >= 500 and attempt < _MAX_RETRIES:
            time.sleep(_RETRY_BACKOFF_SEC * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    # Unreachable in practice (loop above always returns or raises), but keeps
    # type-checkers happy and fails loudly instead of silently returning None.
    raise last_exc or RuntimeError(f"{path}: exhausted retries with no response")


def prev_close(session: requests.Session, ticker: str, api_key: str) -> float | None:
    """Previous completed trading day's close for the underlying -- used only as a
    cheap spot-price reference to pick near-the-money strikes, NOT for anything
    time-sensitive. One call."""
    data = _get(session, f"/v2/aggs/ticker/{ticker}/prev", api_key, adjusted="true")
    results = data.get("results") or []
    return results[0]["c"] if results else None


def list_near_money_contracts(
    session: requests.Session,
    underlying: str,
    spot: float,
    api_key: str,
    moneyness_pct: float,
    max_dte_days: int,
    as_of: _date,
    limit: int,
) -> list[dict]:
    """Reference-data lookup (one call): contracts for `underlying` expiring within
    the next `max_dte_days` days, strikes within `moneyness_pct`% of `spot` on either
    side. Returns raw Polygon contract dicts (ticker, strike_price, contract_type,
    expiration_date, ...) -- caller decides how many to actually pull daily-aggs for."""
    lo = spot * (1 - moneyness_pct / 100)
    hi = spot * (1 + moneyness_pct / 100)
    exp_gte = as_of.isoformat()
    exp_lte = (as_of.replace(day=1)  # placeholder, overwritten below via timedelta math
               )
    # avoid a dateutil dependency for a +N days calc
    from datetime import timedelta
    exp_lte = (as_of + timedelta(days=max_dte_days)).isoformat()

    data = _get(
        session, "/v3/reference/options/contracts", api_key,
        underlying_ticker=underlying,
        **{
            "strike_price.gte": round(lo, 2),
            "strike_price.lte": round(hi, 2),
            "expiration_date.gte": exp_gte,
            "expiration_date.lte": exp_lte,
        },
        limit=min(limit, 1000),
        order="asc",
        sort="expiration_date",
    )
    return data.get("results") or []


def ticker_has_options(session: requests.Session, underlying: str, api_key: str) -> bool:
    """Cheap yes/no: does `underlying` have ANY listed options at all? One reference
    call with limit=1 and NO strike/expiry filter -- deliberately distinct from
    list_near_money_contracts, which filters to a price/DTE window and can be
    legitimately empty for a perfectly optionable name. Caller should cache the result
    (has-options status effectively never changes)."""
    data = _get(
        session, "/v3/reference/options/contracts", api_key,
        underlying_ticker=underlying, limit=1,
    )
    return bool(data.get("results"))


def daily_agg(session: requests.Session, option_ticker: str, day: _date, api_key: str) -> dict | None:
    """One completed trading day's OHLCV + vwap + transaction count for a single
    option contract (one call). Returns None if the contract simply didn't trade
    that day (perfectly normal for illiquid strikes, not an error)."""
    iso = day.isoformat()
    data = _get(
        session, f"/v2/aggs/ticker/{option_ticker}/range/1/day/{iso}/{iso}",
        api_key, adjusted="true",
    )
    results = data.get("results") or []
    return results[0] if results else None
