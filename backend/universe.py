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
import time

import pandas as pd
import requests

import settings

# US universe (settings.MARKET == "US"): every US common stock with market cap >=
# $50M via finvizfinance's free screener (no key) -- confirmed live to return ~4,960
# names in one filtered call, carrying sector/industry/market cap/price/volume for
# free. This supersedes an earlier narrower version of this function that unioned
# just S&P 500 + Nasdaq 100 + Russell 2000 (~2,500 names) -- that undercounted the
# real tradeable market by excluding non-index mid/small-caps; a market-cap floor
# catches all three indices anyway (every member exceeds $50M) plus everything else
# above the same liquidity floor NSE's own MIN_TURNOVER gate is trying to achieve.
# Deliberately NOT literally "Any" (no cap floor): true nano-caps/shells are the US
# equivalent of the illiquid tail NSE's MIN_TURNOVER already excludes. finvizfinance
# scrapes finviz.com (no official API) -- same risk profile already accepted
# elsewhere in this codebase for Google News RSS.
_US_MARKET_CAP_FLOOR = "+Micro (over $50mln)"


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
    every US common stock above settings' market-cap floor via finvizfinance. `size`
    is accepted for signature parity with build_universe() but not applied -- this
    is already a bounded, liquidity-filtered size, unlike NSE's whole-market
    bhavcopy which genuinely needs a top-N cut."""
    try:
        from finvizfinance.screener.overview import Overview
    except ImportError:
        print("  [universe] finvizfinance not installed -- falling back.")
        return _us_fallback()

    # Transient drops (RemoteDisconnected mid-pagination) are common on the
    # finviz scrape; retry before falling back, because the fallback path can
    # only ever be as good as the *last* scan.
    all_rows = None
    for attempt in range(3):
        try:
            fo = Overview()
            fo.set_filter(filters_dict={"Market Cap.": _US_MARKET_CAP_FLOOR})
            all_rows = fo.screener_view(verbose=0)
            break
        except Exception as e:
            print(f"  [universe] finviz market-cap screen failed "
                  f"(attempt {attempt + 1}/3: {e}).")
            all_rows = None
            if attempt < 2:
                time.sleep(5 * (attempt + 1))

    if all_rows is None or not len(all_rows):
        return _us_fallback()

    # finviz added a letter-avatar (logo fallback) inside the ticker cell
    # (observed 2026-07): scraped text comes back with the first letter doubled
    # ("AAAPL", "BBAX" for BAX, "AA" for A). Under the bug EVERY row has
    # t[0] == t[1]; in a healthy scrape only ~2-4% of real tickers do (AAPL,
    # BBY, ...). So detect at table level and strip wholesale -- a per-row
    # repair would be ambiguous ("AA" = doubled Agilent or real Alcoa?).
    t = all_rows["Ticker"].astype(str).str.strip()
    doubled = (t.str.len() > 1) & (t.str[0] == t.str[1])
    if doubled.mean() > 0.9:
        print(f"  [universe] US: finviz letter-avatar scrape bug detected "
              f"({doubled.mean():.0%} of tickers first-letter-doubled) -- "
              f"stripping the duplicated letter.")
        all_rows = all_rows.copy()
        all_rows["Ticker"] = t.str[1:]

    # SPACs/blank-check shells (~6% of the market-cap-floor screen, confirmed via a
    # live check) trade near trust value and don't "breakout" in any meaningful sense
    # -- the US equivalent of excluding ETF/fund units on the India side (ISIN prefix
    # there; finviz's own Industry tag does the same job cleanly here).
    all_rows = all_rows[all_rows["Industry"] != "Shell Companies"]

    # Clinical-stage (pre-revenue) biotech: these move on binary trial readouts and
    # FDA decisions, not the base-and-breakout structure this scanner reads, so they
    # pollute US results with un-chartable gap risk. We can't directly see "has an
    # approved drug", but finviz gives profitability for free as a clean proxy: a
    # Biotechnology name with no positive P/E has no earnings => still pre-commercial.
    # This deliberately KEEPS profitable large-cap biotechs (Amgen/Gilead/Vertex/
    # Regeneron all carry a P/E), which do trend technically -- so it excludes
    # "clinical-stage biotech", not "all biotech".
    is_biotech = all_rows["Industry"] == "Biotechnology"
    if "P/E" in all_rows.columns:
        profitable = pd.to_numeric(all_rows["P/E"], errors="coerce") > 0
    else:
        profitable = pd.Series(False, index=all_rows.index)   # can't tell -> treat as pre-revenue
    clinical_biotech = is_biotech & ~profitable
    n_clinical = int(clinical_biotech.sum())
    all_rows = all_rows[~clinical_biotech]
    if n_clinical:
        print(f"  [universe] US: excluded {n_clinical} clinical-stage (unprofitable) biotech names.")

    all_rows = all_rows.drop_duplicates(subset="Ticker", keep="first")
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
    print(f"  [universe] US: {len(universe)} unique symbols, market cap {_US_MARKET_CAP_FLOOR}.")
    return universe


def _us_fallback() -> dict:
    prior = _universe_from_last_scan()
    if prior:
        # Guard against reusing a scan that was itself produced under the
        # finviz letter-avatar bug (see discover_us_universe): if nearly every
        # symbol has its first letter doubled, the prior file is corrupt.
        syms = [s for s in prior if len(s) > 1]
        doubled = sum(1 for s in syms if s[0] == s[1]) / len(syms) if syms else 0.0
        if doubled > 0.9:
            print(f"  [universe] US: prior breakouts.json looks corrupted "
                  f"({doubled:.0%} of symbols first-letter-doubled) -- refusing to reuse it.")
        else:
            print(f"  [universe] US discovery failed -- reusing {len(prior)} symbols from the last breakouts.json.")
            return prior
    else:
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
