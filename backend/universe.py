"""
Universe discovery — which symbols get scanned.

Historically a hand-typed 12-stock watchlist. Now, when settings.USE_DYNAMIC_UNIVERSE
is on, the scan universe is discovered from NSE's own daily bhavcopy (every listed
equity's OHLCV + turnover for one trading day, via jugaad-data). By default
(settings.UNIVERSE_SIZE = None) it keeps the whole market (~2000 real company
equities); set UNIVERSE_SIZE to an int to keep only the top-N by turnover, and/or
raise settings.MIN_TURNOVER to drop the illiquid tail (thin names produce noisy,
effectively untradeable "breakouts").

This only touches NSE for ONE lightweight request (the bhavcopy). Actual price
history still comes from get_prices() (yfinance by default), so widening the
universe does NOT mean N times more load on NSE's live API — that API is what
adjust_for_splits.py hits per-symbol, and is the fragile, rate-limit-prone part.

Falls back to settings.FALLBACK_WATCHLIST if bhavcopy discovery fails for any
reason (network, NSE blocking, no trading day found in the lookback window) — the
pipeline should never hard-fail just because universe discovery had a bad day.
"""
from __future__ import annotations
from datetime import date, timedelta
import io
import json

import pandas as pd
import requests

import settings

# US universe (settings.MARKET == "US"): S&P 500 + Nasdaq 100 + Russell 2000 via
# finvizfinance's free screener (Index filter) -- no key, one call per index,
# each already carrying sector/industry/market cap/price/volume. Deliberately NOT
# the full NYSE+NASDAQ list (~5,000+ names): that would need its own whole-pool
# liquidity-ranking pass since there's no single pre-aggregated US turnover file
# the way NSE bhavcopy is one. The three indices combined land at a similar scale
# to India's whole-market universe (~2,500 vs ~1,800-2,000) while staying
# pre-filtered to real, liquid, listed common stock. finvizfinance scrapes
# finviz.com (no official API) -- same risk profile already accepted elsewhere in
# this codebase for Google News RSS.
_US_INDICES = ["S&P 500", "NASDAQ 100", "RUSSELL 2000"]


def _nasdaq_listed_symbols() -> set[str]:
    """Symbols on NASDAQ (vs. NYSE/AMEX/ARCA), for TradingView's exchange-prefixed
    chart symbol. Free, no key: nasdaqtrader.com's own daily symbol directory.
    Best-effort -- an empty set here just means every US symbol defaults to NYSE
    in find_breakouts.py, which is a display-only imprecision, not a correctness bug."""
    try:
        r = requests.get("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", timeout=15)
        r.raise_for_status()
        lines = r.text.splitlines()[1:-1]  # drop header + "File Creation Time:" footer
        return {line.split("|")[0].strip() for line in lines if line.strip()}
    except Exception:
        return set()


def discover_us_universe(size: int | None = -1) -> dict:
    """US parallel to build_universe(): {symbol: {"name","sector","exchange"}},
    sourced from S&P 500 + Nasdaq 100 + Russell 2000 via finvizfinance. `size` is
    accepted for signature parity with build_universe() but not applied -- the
    combined index universe is already a bounded, pre-filtered size, unlike NSE's
    whole-market bhavcopy which genuinely needs a top-N cut."""
    try:
        from finvizfinance.screener.overview import Overview
    except ImportError:
        print("  [universe] finvizfinance not installed -- falling back.")
        return _us_fallback()

    frames = []
    for idx in _US_INDICES:
        try:
            fo = Overview()
            fo.set_filter(filters_dict={"Index": idx})
            df = fo.screener_view(verbose=0)
            if df is not None and len(df):
                frames.append(df)
        except Exception as e:
            print(f"  [universe] finviz Index={idx!r} fetch failed ({e}) -- continuing with the rest.")

    if not frames:
        return _us_fallback()

    all_rows = pd.concat(frames, ignore_index=True).drop_duplicates(subset="Ticker", keep="first")
    nasdaq_symbols = _nasdaq_listed_symbols()

    universe = {}
    for _, row in all_rows.iterrows():
        symbol = str(row["Ticker"]).strip().upper()
        if not symbol:
            continue
        yf_symbol = symbol.replace(".", "-")  # yfinance wants BRK-B, finviz shows BRK.B
        sector = str(row.get("Sector") or "").strip()
        industry = str(row.get("Industry") or "").strip()
        label = f"{sector} · {industry}" if sector and industry else (sector or industry)
        universe[yf_symbol] = {
            "name": str(row.get("Company") or symbol).strip(),
            "sector": label,
            "exchange": "NASDAQ" if symbol in nasdaq_symbols else "NYSE",
        }

    if not universe:
        return _us_fallback()
    print(f"  [universe] US: {len(universe)} unique symbols across {', '.join(_US_INDICES)}.")
    return universe


def _us_fallback() -> dict:
    prior = _universe_from_last_scan()
    if prior:
        print(f"  [universe] US discovery failed -- reusing {len(prior)} symbols from the last breakouts.json.")
        return prior
    print("  [universe] US discovery failed and no prior scan -- falling back to the static watchlist.")
    return dict(settings.FALLBACK_WATCHLIST)

# Equity-only, mainline capital-market series. Excludes SME (SM), trade-to-trade
# (BE), government securities/bonds (GS/GB), and other non-equity series the
# bhavcopy also carries.
_EQUITY_SERIES = "EQ"
_EQUITY_SEGMENT = "CM"

# The "EQ" series also carries ETFs and mutual-fund units traded like stocks (e.g.
# LIQUIDBEES, gold/nifty ETFs) -- these aren't companies and don't "breakout" in
# the sense this app means. NSE ISINs distinguish them cleanly: equity shares of
# companies start with INE; fund/ETF/scheme units start with INF (or other non-INE
# prefixes). Filtering on this is more reliable than name-pattern matching.
_EQUITY_ISIN_PREFIX = "INE"


def _fetch_latest_bhavcopy() -> pd.DataFrame | None:
    """Walk backward from today to find the most recent trading day's bhavcopy
    (today/weekends/holidays won't have one)."""
    from jugaad_data.nse import bhavcopy_raw
    for delta in range(settings.UNIVERSE_LOOKBACK_DAYS):
        d = date.today() - timedelta(days=delta)
        try:
            raw = bhavcopy_raw(d)
            df = pd.read_csv(io.StringIO(raw))
            if len(df):
                return df
        except Exception:
            continue
    return None


def _universe_from_last_scan() -> dict | None:
    """Reconstruct the universe from the previous breakouts.json (symbol -> name/
    sector/industry). This is the *preferred* fallback when the NSE bhavcopy is
    unreachable/rate-limited: it preserves the whole ~1,800-name market from the last
    good run instead of collapsing to the hand-picked 12, so a bad NSE day never
    silently shrinks (and overwrites) the served universe. Returns None if there's no
    usable prior scan."""
    if not settings.BREAKOUTS_JSON.exists():
        return None
    try:
        with open(settings.BREAKOUTS_JSON, encoding="utf-8") as f:
            stocks = json.load(f).get("stocks", [])
    except Exception:
        return None
    universe = {}
    for s in stocks:
        sym = s.get("symbol")
        if sym:
            universe[sym] = {"name": s.get("name", sym),
                             "sector": s.get("sector", ""),
                             "industry": s.get("industry", "")}
    return universe or None


def build_universe(size: int | None = -1) -> dict:
    """Return {symbol: {"name": ..., "sector": ...}} ranked by turnover, or the
    static fallback watchlist if discovery fails. `size` = None means whole market;
    the default sentinel (-1) means "use settings.UNIVERSE_SIZE" (which is itself
    None for whole-market by default)."""
    if not settings.USE_DYNAMIC_UNIVERSE:
        return dict(settings.FALLBACK_WATCHLIST)

    if settings.MARKET == "US":
        return discover_us_universe(size)

    if size == -1:
        size = settings.UNIVERSE_SIZE
    df = _fetch_latest_bhavcopy()
    if df is None or "SctySrs" not in df.columns:
        prior = _universe_from_last_scan()
        if prior:
            print(f"  [universe] bhavcopy fetch failed -- reusing {len(prior)} symbols "
                  f"from the last breakouts.json.")
            return prior
        print("  [universe] bhavcopy fetch failed and no prior scan -- falling back to the static watchlist.")
        return dict(settings.FALLBACK_WATCHLIST)

    eq = df[(df["SctySrs"] == _EQUITY_SERIES) & (df["Sgmt"] == _EQUITY_SEGMENT)].copy()
    eq = eq[eq["ISIN"].astype(str).str.startswith(_EQUITY_ISIN_PREFIX)]
    eq = eq.dropna(subset=["TtlTrfVal", "TckrSymb"])
    eq = eq[eq["TtlTrfVal"] >= settings.MIN_TURNOVER]
    eq = eq.sort_values("TtlTrfVal", ascending=False)
    if size is not None:
        eq = eq.head(size)

    universe = {}
    for _, row in eq.iterrows():
        symbol = str(row["TckrSymb"]).strip()
        if not symbol:
            continue
        # Reuse the curated name/sector for the original watchlist names; fall
        # back to the bhavcopy's own instrument name (sector isn't in the
        # bhavcopy, and we don't have a fundamentals source yet -- see TODO #3).
        curated = settings.FALLBACK_WATCHLIST.get(symbol)
        if curated:
            universe[symbol] = curated
        else:
            name = str(row.get("FinInstrmNm", symbol)).strip() or symbol
            universe[symbol] = {"name": name, "sector": ""}

    if not universe:
        prior = _universe_from_last_scan()
        if prior:
            print(f"  [universe] bhavcopy had no usable equity rows -- reusing {len(prior)} "
                  f"symbols from the last breakouts.json.")
            return prior
        print("  [universe] bhavcopy had no usable equity rows -- falling back to the static watchlist.")
        return dict(settings.FALLBACK_WATCHLIST)
    return universe
