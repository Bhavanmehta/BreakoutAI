"""
Standalone research prototype: scan a small watchlist of US tickers for "unusual"
single-leg options activity using Polygon.io's free tier, and write flagged contracts
to data/us/options_flow_research.json.

WHY THIS EXISTS: a Discord alert bot (e.g. Bullflow) can post "$83,203 swept on META
$780C at 12:52 PM, Bullish" because it pays for a real-time, trade-by-trade tape feed
and does its own aggressor-side (buy vs sell) classification against the NBBO quote at
each print. That is not reproducible for free -- Polygon's free tier only gives
completed-day AGGREGATES per contract (volume, vwap, transaction count), not individual
timestamped prints. This script is honest about that gap: it flags contracts with
unusually large day-volume / notional / average-trade-size (a real, if blunter, "big
prints vs retail noise" signal), but it does NOT and CANNOT claim a bullish/bearish
sentiment or a precise sweep timestamp the way a paid tape-reading service can. Treat
this as a research/backtesting prototype, not a live-alert replacement.

WHAT IT SCANS: for each ticker, the contracts within OPTIONS_FLOW_MONEYNESS_PCT of the
previous close, expiring within OPTIONS_FLOW_MAX_DTE_DAYS, capped to
OPTIONS_FLOW_MAX_CONTRACTS_PER_TICKER nearest-the-money contracts (closest strikes to
spot, nearest expiration first) -- a deliberately small slice, since the free tier's
5 req/min limit makes a full chain scan impractical (see options_flow_providers.py).

Needs POLYGON_API_KEY (backend/.env, see .env.example) -- free signup at
https://polygon.io/dashboard/signup. US market only (BREAKOUTAI_MARKET=US).

Usage:
    python options_flow_scan.py                    # high-conviction names from breakouts.json, most recent completed trading day
    python options_flow_scan.py META AMZN NVDA      # just these tickers
    python options_flow_scan.py --date 2026-07-02   # a specific completed trading day
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

import settings
from options_flow_providers import (
    PolygonQuotaExhausted,
    daily_agg,
    list_near_money_contracts,
    prev_close,
    ticker_has_options,
)

_last_call_ts = 0.0


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


def _pace():
    """Blocks until at least POLYGON_MIN_REQUEST_GAP_SEC has passed since the last
    Polygon call, across this whole process -- keeps every caller under the free
    tier's 5 req/min limit without needing a token-bucket library."""
    global _last_call_ts
    elapsed = time.monotonic() - _last_call_ts
    wait = settings.POLYGON_MIN_REQUEST_GAP_SEC - elapsed
    if wait > 0:
        time.sleep(wait)
    _last_call_ts = time.monotonic()


def _most_recent_completed_trading_day() -> date:
    """Most recent weekday strictly before today (naive: doesn't know US market
    holidays). Good enough for a research prototype -- a holiday just yields an empty
    daily-agg for every contract that day, which the script already handles as 'no
    trade', not an error."""
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:   # Sat=5, Sun=6
        d -= timedelta(days=1)
    return d


def select_high_conviction_tickers() -> list[str]:
    """The universe the options scan SHOULD pull flow for: this repo's own
    high-conviction breakout candidates (breakouts.json), not a fixed mega-cap list.
    Two FREE pre-gates run here, before any options API call, using data breakouts.json
    already carries:

      1. conviction >= OPTIONS_FLOW_MIN_CONVICTION  -- only names we have a thesis on
      2. price * avg-daily-volume >= OPTIONS_FLOW_MIN_TURNOVER_USD  -- an illiquid stock
         almost never has liquid (or any) listed options, so spending a rate-limited
         Polygon call to discover that is pure waste. This equity-side proxy filters
         those out for free.

    Survivors are sorted highest-conviction first and capped to OPTIONS_FLOW_MAX_TICKERS
    (the rate-limit budget). Returns bare ticker symbols."""
    if not settings.BREAKOUTS_JSON.exists():
        print(f"  breakouts.json not found at {settings.BREAKOUTS_JSON} -- run run_scan.py first, "
              f"or pass explicit tickers.")
        return []
    with open(settings.BREAKOUTS_JSON, encoding="utf-8") as f:
        stocks = (json.load(f) or {}).get("stocks", [])

    picked = []
    for s in stocks:
        sym = s.get("symbol")
        if not sym:
            continue
        conviction = (s.get("readiness") or {}).get("conviction") or 0
        if conviction < settings.OPTIONS_FLOW_MIN_CONVICTION:
            continue
        price = s.get("price") or 0
        avg_vol = (s.get("volume") or {}).get("avg") or 0
        turnover = price * avg_vol
        if turnover < settings.OPTIONS_FLOW_MIN_TURNOVER_USD:
            continue
        picked.append((conviction, turnover, sym))

    picked.sort(key=lambda t: (t[0], t[1]), reverse=True)
    selected = [sym for _, _, sym in picked[:settings.OPTIONS_FLOW_MAX_TICKERS]]
    print(f"  universe: {len(selected)} high-conviction names "
          f"(conviction>={settings.OPTIONS_FLOW_MIN_CONVICTION}, "
          f"turnover>=${settings.OPTIONS_FLOW_MIN_TURNOVER_USD/1e6:.0f}M/day, "
          f"cap {settings.OPTIONS_FLOW_MAX_TICKERS}) out of {len(stocks)} scanned")
    return selected


def _load_has_options_cache() -> dict:
    """Persistent {ticker: {'has_options': bool, 'checked': 'YYYY-MM-DD'}} map. Whether
    a name has ANY listed options basically never changes, so a fresh 'no' lets us skip
    the reference call entirely on later runs."""
    path = settings.OPTIONS_FLOW_HAS_OPTIONS_CACHE
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_has_options_cache(cache: dict) -> None:
    path = settings.OPTIONS_FLOW_HAS_OPTIONS_CACHE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)


def _has_options_cache_fresh(entry: dict, today: date) -> bool:
    """True if a cache entry is present and younger than the TTL -- only then do we
    trust its 'has_options' verdict without re-checking."""
    checked = entry.get("checked")
    if not checked:
        return False
    try:
        age = (today - datetime.strptime(checked, "%Y-%m-%d").date()).days
    except ValueError:
        return False
    return 0 <= age < settings.OPTIONS_FLOW_HAS_OPTIONS_TTL_DAYS


def _select_contracts(contracts: list[dict], spot: float, max_n: int) -> list[dict]:
    """Nearest-expiration-first, then nearest-strike-to-spot, capped to max_n -- the
    contracts most likely to actually carry flow, given we can only afford a handful
    of daily-agg calls per ticker on the free tier."""
    def key(c):
        return (c.get("expiration_date", "9999-99-99"), abs(c.get("strike_price", 0) - spot))
    return sorted(contracts, key=key)[:max_n]


def scan_ticker(session: requests.Session, ticker: str, day: date, api_key: str,
                cache: dict | None = None) -> list[dict]:
    # has_options gate: if a fresh cache entry says this name has no listed options,
    # skip it entirely -- zero API calls. Otherwise confirm once (one cheap ref call)
    # and record the verdict so future runs can skip it for free.
    if cache is not None:
        entry = cache.get(ticker, {})
        if _has_options_cache_fresh(entry, day) and not entry.get("has_options"):
            print(f"  {ticker}: no listed options (cached), skipping")
            return []
        if not _has_options_cache_fresh(entry, day):
            _pace()
            has_opts = ticker_has_options(session, ticker, api_key)
            cache[ticker] = {"has_options": has_opts, "checked": day.isoformat()}
            if not has_opts:
                print(f"  {ticker}: no listed options, skipping (cached for next run)")
                return []

    _pace()
    spot = prev_close(session, ticker, api_key)
    if spot is None:
        print(f"  {ticker}: no prev-close data, skipping")
        return []

    _pace()
    contracts = list_near_money_contracts(
        session, ticker, spot, api_key,
        moneyness_pct=settings.OPTIONS_FLOW_MONEYNESS_PCT,
        max_dte_days=settings.OPTIONS_FLOW_MAX_DTE_DAYS,
        as_of=day,
        limit=1000,
    )
    if not contracts:
        print(f"  {ticker}: no near-the-money contracts found in range, skipping")
        return []

    picked = _select_contracts(contracts, spot, settings.OPTIONS_FLOW_MAX_CONTRACTS_PER_TICKER)
    print(f"  {ticker}: spot~{spot:.2f}, scanning {len(picked)}/{len(contracts)} contracts for {day}")

    flagged = []
    for c in picked:
        _pace()
        agg = daily_agg(session, c["ticker"], day, api_key)
        if agg is None:
            continue
        volume = agg.get("v", 0)
        vwap = agg.get("vw", agg.get("c", 0))
        transactions = agg.get("n", 0) or 1
        notional = volume * vwap * 100
        avg_trade_size = volume / transactions if transactions else 0

        if volume < settings.OPTIONS_FLOW_MIN_VOLUME:
            continue
        reasons = []
        if notional >= settings.OPTIONS_FLOW_MIN_NOTIONAL:
            reasons.append("high_notional")
        if avg_trade_size >= settings.OPTIONS_FLOW_MIN_AVG_TRADE_SIZE:
            reasons.append("large_avg_trade_size")
        if not reasons:
            continue

        flagged.append({
            "ticker": ticker,
            "option_ticker": c["ticker"],
            "call_put": "Call" if c.get("contract_type") == "call" else "Put",
            "strike": c.get("strike_price"),
            "expiration": c.get("expiration_date"),
            "date": day.isoformat(),
            "volume": volume,
            "transactions": transactions,
            "vwap": round(vwap, 4) if vwap else vwap,
            "avg_trade_size": round(avg_trade_size, 1),
            "notional_est": round(notional, 2),
            "flag_reasons": reasons,
            # Deliberately no "sentiment"/"side" field -- see module docstring: that
            # needs tick-level NBBO comparison this free-tier daily-agg pull can't do.
        })
    return flagged


def run(tickers: list[str], day: date):
    api_key = os.environ.get("POLYGON_API_KEY", "").strip()
    if not api_key:
        sys.exit("POLYGON_API_KEY not set -- add it to backend/.env (see .env.example) "
                 "or export it. Free key: https://polygon.io/dashboard/signup")
    if settings.MARKET != "US":
        sys.exit("Options-flow scan is US-only. Re-run with BREAKOUTAI_MARKET=US.")

    session = requests.Session()
    cache = _load_has_options_cache()
    all_flagged = []
    t0 = time.time()
    try:
        for ticker in tickers:
            try:
                all_flagged.extend(scan_ticker(session, ticker, day, api_key, cache))
            except PolygonQuotaExhausted as e:
                print(f"  {ticker}: Polygon quota/auth error, stopping run early -- {e}")
                break
    finally:
        # Persist has_options verdicts even on early exit -- the cheap ref calls we
        # already spent shouldn't be thrown away just because the run stopped short.
        _save_has_options_cache(cache)

    settings.OPTIONS_FLOW_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = {"date": day.isoformat(), "generated_at": datetime.now().isoformat(),
               "flagged": all_flagged}
    with open(settings.OPTIONS_FLOW_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\nDone in {time.time()-t0:.0f}s. {len(all_flagged)} flagged contracts "
          f"written to {settings.OPTIONS_FLOW_JSON}")


if __name__ == "__main__":
    _load_env_file()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tickers", nargs="*",
                        help="Tickers to scan (default: high-conviction names from breakouts.json)")
    parser.add_argument("--date", dest="day", default=None,
                         help="Completed trading day, YYYY-MM-DD (default: most recent weekday)")
    args = parser.parse_args()

    if args.tickers:
        tickers = [t.upper() for t in args.tickers]
    else:
        tickers = select_high_conviction_tickers()
        if not tickers:
            sys.exit("No high-conviction tickers to scan (empty breakouts.json and no tickers "
                     "given). Run run_scan.py first, or pass tickers explicitly.")
    day = datetime.strptime(args.day, "%Y-%m-%d").date() if args.day else _most_recent_completed_trading_day()
    run(tickers, day)
