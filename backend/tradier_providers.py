"""
Thin HTTP client for Tradier's options-chain API -- the open-interest + greeks + spread
half of options_flow_scan.py's unusual-activity signal (Polygon's free tier supplies
volume/vwap but NOT open interest, which is the single best free "new positioning" proxy).

Why Tradier alongside Polygon (they're complementary, not redundant):
  - Open interest: Tradier gives it per contract; Polygon's free tier does not. day
    volume >> standing OI is the clearest "this is NEW positioning, not churn" signal.
  - Greeks + IV: Tradier returns delta/gamma/theta/vega + IV per contract (ORATS-backed).
  - Bid/ask: lets us skip untradeable wide markets and compute a real spread%.
  - Rate limit: ~120 req/min vs Polygon's 5, so we can pull a whole expiration's chain
    in one call instead of Polygon's rate-limited contract-by-contract daily-agg crawl.

Auth/environment (differs from Polygon -- hence a separate module):
  - Bearer token in the Authorization header (TRADIER_ACCESS_TOKEN), not an apiKey query
    param.
  - Base URL from TRADIER_API_URL. Default https://sandbox.tradier.com -- the free,
    paper-only sandbox. Sandbox data is ~15-min delayed and OI can occasionally read
    stale on a contract; that is fine for this research/backtest prototype (see
    options_flow_scan.py's docstring on what this can and can't honestly claim). Point
    TRADIER_API_URL at https://api.tradier.com only if you have a funded/live token.

Docs: https://documentation.tradier.com/brokerage-api/markets/get-options-chains
"""
from __future__ import annotations
import os
import time

import requests

_TIMEOUT = 15
# Same transient-network retry rationale as options_flow_providers._get: a dropped
# connection is infra noise, not a quota/auth problem -- retry a couple times cheaply
# rather than let one flaky handshake kill a multi-name scan. Small/local, not tunable.
_MAX_RETRIES = 3
_RETRY_BACKOFF_SEC = 2.0

_DEFAULT_BASE = "https://sandbox.tradier.com"


class TradierAuthError(Exception):
    """Raised on 401 (bad/expired token) -- distinct from an ordinary empty chain, so
    the caller can stop the run instead of hammering the same auth error per ticker."""


def _base_url() -> str:
    return (os.environ.get("TRADIER_API_URL") or _DEFAULT_BASE).rstrip("/")


def _token() -> str:
    tok = (os.environ.get("TRADIER_ACCESS_TOKEN") or "").strip()
    if not tok:
        raise TradierAuthError(
            "TRADIER_ACCESS_TOKEN not set -- add it to backend/.env (see .env.example). "
            "Free sandbox token: https://developer.tradier.com/ -> API Access -> Sandbox."
        )
    return tok


def _get(session: requests.Session, path: str, **params) -> dict:
    headers = {"Authorization": f"Bearer {_token()}", "Accept": "application/json"}
    url = f"{_base_url()}{path}"
    last_exc: requests.exceptions.RequestException | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = session.get(url, params=params, headers=headers, timeout=_TIMEOUT)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF_SEC * (attempt + 1))
                continue
            raise
        if resp.status_code == 401:
            raise TradierAuthError(f"{path} -> HTTP 401: {resp.text[:200]}")
        if resp.status_code >= 500 and attempt < _MAX_RETRIES:
            time.sleep(_RETRY_BACKOFF_SEC * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    raise last_exc or RuntimeError(f"{path}: exhausted retries with no response")


def _as_list(node) -> list:
    """Tradier collapses single-element arrays to a bare object and omits empties
    entirely (returns null). Normalize to a plain list so callers never special-case."""
    if node is None:
        return []
    return node if isinstance(node, list) else [node]


def get_expirations(session: requests.Session, symbol: str) -> list[str]:
    """Sorted list of available expiration dates (YYYY-MM-DD) for `symbol`. One call.
    Empty list if the underlying has no listed options."""
    data = _get(session, "/v1/markets/options/expirations", symbol=symbol)
    exps = ((data or {}).get("expirations") or {})
    return _as_list(exps.get("date"))


def get_chain(session: requests.Session, symbol: str, expiration: str,
              greeks: bool = True) -> list[dict]:
    """Full options chain for `symbol` at a single `expiration` (one call). Each dict
    carries volume, open_interest, bid, ask, strike, option_type, and (greeks=True) a
    nested `greeks` dict with delta/gamma/theta/vega + IV. Returns raw Tradier contract
    dicts -- flag logic lives in options_flow_scan.py, not here."""
    data = _get(
        session, "/v1/markets/options/chains",
        symbol=symbol, expiration=expiration,
        greeks="true" if greeks else "false",
    )
    options = ((data or {}).get("options") or {})
    return _as_list(options.get("option"))


def spread_pct(contract: dict) -> float | None:
    """(ask - bid) / mid * 100 for a contract, or None if the market is one-sided /
    missing (bid or ask is 0/absent) -- a one-sided market isn't tradeable, so callers
    treat None as 'fails the spread gate' rather than 'zero spread'."""
    bid = contract.get("bid") or 0
    ask = contract.get("ask") or 0
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    return (ask - bid) / mid * 100
