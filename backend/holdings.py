"""
Ownership / shareholding data from NSE — who holds the stock (promoter, FII, DII,
mutual funds, public) and how the promoter's stake has trended.

Why it matters for a breakout radar: institutional and promoter *accumulation* is
exactly what precedes a Weinstein Stage-2 / Minervini breakout. A technical coil plus
rising FII/DII/promoter holding is a stronger, confirmed setup than the chart alone;
a falling promoter stake under a "breakout" is a caution flag.

Two NSE sources, both authoritative:
  1. corporate-share-holdings-master  -> the list of quarterly/annual filings, each
     with an `xbrl` link and the headline promoter% / public% (annual, ~10yr back).
  2. the linked SHP XBRL filing        -> the full category split (promoter / FII /
     DII / MF / public) for that filing. We read the aggregate rollup contexts
     (InstitutionsForeign = FII, InstitutionsDomestic = DII) so we never have to sum
     sub-categories by hand.

IMPORTANT: per-stock *daily* FII/DII flow is NOT public in India — only these
quarterly holdings + threshold-crossing bulk/block deals. So "over time" here means
quarter-over-quarter / year-over-year holding %, not a daily flow line.

This is quarterly-slow data: fetch rarely (see fetch_holdings.py), cache to
data/holdings.json, and let run_scan merge it in. Not part of the daily price scan.
"""
from __future__ import annotations
import re
import time

import requests

MASTER_URL = ("https://www.nseindia.com/api/corporate-share-holdings-master"
              "?index=equities&symbol={sym}")

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE",
}

# The one fact we read from the XBRL, per category context.
_PCT_FACT = re.compile(
    r'<in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares\s+'
    r'contextRef="([^"]+)"[^>]*>([^<]+)<')

# Aggregate rollup contexts -> our buckets. `_ContextI` is the summary context for
# each category (vs `_ContextNN` = individual named holders).
_BUCKETS = {
    "promoter": "ShareholdingOfPromoterAndPromoterGroup",
    "fii":      "InstitutionsForeign",       # aggregate foreign institutional
    "dii":      "InstitutionsDomestic",      # aggregate domestic institutional
    "mf":       "MutualFundsOrUTI",
    "public":   "PublicShareholding",
}


def make_session() -> requests.Session:
    """A session primed with NSE cookies so the API doesn't reject us."""
    s = requests.Session()
    s.headers.update(_HEADERS)
    s.get("https://www.nseindia.com", timeout=15)
    return s


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_shp_xbrl(txt: str) -> dict:
    """Extract the category split (promoter / FII / DII / MF / public %) from one SHP
    XBRL filing. Values in the file are fractions (0.5 = 50%); we return whole percent."""
    by_ctx = {}
    for ctx, val in _PCT_FACT.findall(txt):
        if ctx.endswith("_ContextI"):
            v = _num(val)
            if v is not None:
                by_ctx[ctx[:-len("_ContextI")]] = v * 100
    out = {}
    for bucket, ctx_name in _BUCKETS.items():
        v = by_ctx.get(ctx_name)
        out[bucket] = round(v, 2) if v is not None else None
    return out


def _parse_date(d: str):
    """SHP filing dates look like '31-MAR-2026'. Return a sortable key; unknown
    formats sort to the end."""
    from datetime import datetime
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(d.upper(), fmt.upper())
        except (ValueError, AttributeError):
            continue
    return datetime.min


def fetch_holdings(symbol: str, session: requests.Session | None = None,
                   prime: bool = True, history_points: int = 0) -> dict | None:
    """Fetch the shareholding split + promoter trend for one symbol.

    Returns None if nothing usable comes back. `prime=False` skips the per-symbol
    get-quotes warm-up call (a small speed-up when looping many symbols).

    history_points > 0 additionally parses that many of the most recent XBRL filings
    into a `history` time series [{date, promoter, fii, dii, mf, public}, ...] (newest
    first) — the "who's accumulating over time" data. Each point is one extra XBRL
    fetch (~0.5MB), so this multiplies per-stock cost; it's meant for the offline
    fetch_holdings.py re-scrape, not the daily scan. history_points == 0 keeps the
    original single-latest-filing behaviour."""
    s = session or make_session()
    try:
        if prime:
            s.get(f"https://www.nseindia.com/get-quotes/equity?symbol={symbol}", timeout=15)
        recs = s.get(MASTER_URL.format(sym=symbol), timeout=20).json()
    except Exception:
        return None
    if not isinstance(recs, list) or not recs:
        return None

    dated = [r for r in recs if r.get("date")]
    # Promoter trend: annual promoter% (integer-rounded at source), newest first.
    trend = [{"date": r["date"], "promoter": _num(r.get("pr_and_prgrp"))}
             for r in dated if r.get("pr_and_prgrp") not in (None, "")]
    trend.sort(key=lambda x: _parse_date(x["date"]), reverse=True)

    with_xbrl = sorted([r for r in dated if r.get("xbrl")],
                       key=lambda r: _parse_date(r["date"]), reverse=True)

    # Parse the latest filing (snapshot) and, if asked, the recent N (history series).
    n_to_parse = max(1, history_points) if with_xbrl else 0
    parsed = []  # [(date, split_dict)]
    for r in with_xbrl[:n_to_parse]:
        try:
            txt = s.get(r["xbrl"], timeout=30).text
            sp = parse_shp_xbrl(txt)
        except Exception:
            sp = {}
        if any(v is not None for v in sp.values()):
            parsed.append((r["date"], sp))
        if history_points:
            time.sleep(0.3)  # be polite between the extra XBRL fetches

    split, as_of = ({}, None)
    if parsed:
        as_of, split = parsed[0]

    history = None
    if history_points and parsed:
        history = [{"date": d, "promoter": sp.get("promoter"), "fii": sp.get("fii"),
                    "dii": sp.get("dii"), "mf": sp.get("mf"), "public": sp.get("public")}
                   for d, sp in parsed]

    if not split and not trend:
        return None
    out = {
        "as_of": as_of or (trend[0]["date"] if trend else None),
        "promoter": split.get("promoter"),
        "fii": split.get("fii"),
        "dii": split.get("dii"),
        "mf": split.get("mf"),
        "public": split.get("public"),
        "promoter_trend": trend[:8],  # ~8 years of annual snapshots
    }
    if history is not None:
        out["history"] = history
    return out


if __name__ == "__main__":
    # Quick manual check across different ownership structures, incl. the quarterly
    # history series (history_points>1). NSE rate-limits aggressively — if this times
    # out, wait and retry; the resumable fetch_holdings.py handles that gracefully.
    s = make_session()
    for sym in ["RELIANCE", "TCS", "SBIN", "HDFCBANK"]:
        h = fetch_holdings(sym, s, history_points=6)
        if h is None:
            print(f"{sym:10s} -> None")
        else:
            snap = {k: h[k] for k in ('as_of', 'promoter', 'fii', 'dii', 'mf', 'public')}
            hist = h.get("history") or []
            print(f"{sym:10s} -> {snap}")
            print(f"{'':10s}    history ({len(hist)}): " +
                  ", ".join(f"{p['date']}:P{p['promoter']}/F{p['fii']}/D{p['dii']}" for p in hist))
        time.sleep(0.6)
