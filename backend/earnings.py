"""
Quarterly EPS (estimated vs. actual) for a stock, from yfinance.

Two sources, in priority order, per stock -- NEVER blended within one stock's own
history (mixing conventions mid-series would paint a false-looking beat/miss from a
methodology switch, not a real one):

  1. yfinance's earnings calendar (`get_earnings_dates`) -- EPS Estimate + Reported
     EPS on the same analyst-comparable basis (often "adjusted", excluding one-off
     items), plus the next unreported quarter's estimate. Rich, but only ~40-50%
     of this project's actual watchlist has ANY analyst coverage (tested 2026-07-05
     against the top-30 conviction list -- large caps like RELIANCE/TCS had 24+
     quarters; plenty of the micro-caps this scanner surfaces, e.g. CUPID/NPST, had
     none at all).
  2. Fallback: the quarterly income statement's "Basic EPS" line -- unadjusted GAAP
     EPS, not always numerically identical to (1) for the same quarter, but tested
     available for ~100% of stocks (every listed company reports EPS whether or not
     analysts cover it). No forward estimate available from this source.

Slow-changing reference data like sectors/holdings/fundamentals -- fetched by a
standalone script (fetch_earnings.py) into a cached data/earnings.json that
run_scan.py merges in, NOT re-fetched during the daily price scan.
"""
from __future__ import annotations

MAX_QUARTERS = 8
# yfinance's earnings calendar can be STALE for a stock even when it returns rows --
# confirmed live (2026-07-05): SUVEN's get_earnings_dates() tops out at Feb 2020 (its
# most recent row!) while its quarterly_income_stmt has real data through Mar 2026.
# ~31% of stocks tested this way had a "most recent" earnings-calendar quarter more
# than a year old. A quarter ending more than STALE_DAYS ago is too old to be today's
# "recent earnings" -- treat the whole calendar as unusable and fall back.
STALE_DAYS = 200


def _fiscal_quarter_label(date) -> str:
    """Indian FY convention (Apr-Mar): a quarter ENDING in Jan-Mar is Q4 of the FY
    named after that March; Apr-Jun is Q1 of the *next* FY; and so on. E.g. a
    quarter ending 2025-09-30 -> 'Q2 FY26' (FY26 = Apr 2025-Mar 2026)."""
    m, y = date.month, date.year
    if m <= 3:
        return f"Q4 FY{y % 100:02d}"
    if m <= 6:
        return f"Q1 FY{(y + 1) % 100:02d}"
    if m <= 9:
        return f"Q2 FY{(y + 1) % 100:02d}"
    return f"Q3 FY{(y + 1) % 100:02d}"


def _from_earnings_dates(t) -> tuple[list[dict], dict | None]:
    import pandas as pd
    from datetime import datetime, timezone, timedelta
    try:
        ed = t.get_earnings_dates(limit=MAX_QUARTERS + 2)
    except Exception:
        return [], None
    if ed is None or ed.empty:
        return [], None
    ed = ed.sort_index()  # oldest first

    quarters, future = [], []
    for date, row in ed.iterrows():
        actual = row.get("Reported EPS")
        estimate = row.get("EPS Estimate")
        if pd.isna(actual):
            # An unreported quarter -- only useful as the "next" callout.
            if pd.notna(estimate):
                future.append((date, float(estimate)))
            continue
        quarters.append({
            "quarter": _fiscal_quarter_label(date),
            "actual": round(float(actual), 2),
            "estimate": round(float(estimate), 2) if pd.notna(estimate) else None,
        })
    if not quarters:
        return [], None
    # Staleness guard -- see STALE_DAYS docstring. The most recently REPORTED quarter
    # (not a future estimate row) has to be genuinely recent, or this whole source is
    # untrustworthy for "recent earnings" and we fall back to the income statement.
    last_reported = ed.dropna(subset=["Reported EPS"]).index.max()
    if (datetime.now(timezone.utc) - last_reported.tz_convert("UTC")).days > STALE_DAYS:
        return [], None
    next_q = None
    if future:
        d, est = min(future, key=lambda x: x[0])
        next_q = {"date": d.strftime("%Y-%m-%d"), "estimate": round(est, 2)}
    return quarters[-MAX_QUARTERS:], next_q


def _from_income_stmt(t) -> list[dict]:
    import pandas as pd
    try:
        q = t.quarterly_income_stmt
    except Exception:
        return []
    if q is None or "Basic EPS" not in q.index:
        return []
    row = q.loc["Basic EPS"].dropna()
    if row.empty:
        return []
    row = row.sort_index()  # oldest first
    return [{"quarter": _fiscal_quarter_label(date), "actual": round(float(val), 2), "estimate": None}
            for date, val in row.items()][-MAX_QUARTERS:]


def fetch_earnings(symbol: str) -> dict | None:
    """{"source": "estimate"|"actual_only", "quarters": [...], "next": {...}|None}
    for one NSE symbol, or None if nothing came back from either source. Never
    raises -- networking/parse errors just return None so the caller can move on."""
    try:
        import yfinance as yf
        t = yf.Ticker(f"{symbol}.NS")
    except Exception:
        return None

    quarters, next_q = _from_earnings_dates(t)
    source = "estimate"
    if not quarters:
        quarters = _from_income_stmt(t)
        source = "actual_only"
        next_q = None
    if not quarters:
        return None
    return {"source": source, "quarters": quarters, "next": next_q}


if __name__ == "__main__":
    import time
    for sym in ["RELIANCE", "TCS", "APOLLO", "CUPID", "DJML", "SUVEN"]:
        r = fetch_earnings(sym)
        print(f"{sym:10s} -> source={r['source'] if r else None} "
              f"quarters={len(r['quarters']) if r else 0} next={r['next'] if r else None}")
        time.sleep(0.3)
