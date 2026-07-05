"""
Fundamentals (market cap, P/E, growth, ROE, D/E) for a stock, from yfinance.

Why it matters for a breakout radar: a technical setup means something different on
a richly-valued, no-earnings small-cap vs. a cash-generative large-cap — these fields
let the watchlist be filtered by valuation/quality, not just by chart shape.

Source: yfinance's `.info` carries these directly for NSE ".NS" tickers. Like sectors
and holdings, this is slow-changing reference data, fetched by a standalone script
(fetch_fundamentals.py) into a cached data/fundamentals.json that run_scan.py merges
in, NOT re-fetched during the daily price scan.

Not included: ROCE isn't a yfinance field (it's an India-screener-style metric, not
standard Yahoo Finance data) — skipped rather than adding a second scrape source for
one field. Revisit if it's worth a screener.in-style scrape later (see holdings_screener.py
for that pattern).
"""
from __future__ import annotations

import settings


def fetch_fundamentals(symbol: str) -> dict | None:
    """Return market cap / P-E / growth / ROE / D-E for one NSE symbol via yfinance's
    .info, or None if nothing came back. Never raises — networking/parse errors just
    return None so the caller can move on.

    Unit notes (verified against known real-world figures for ICICIBANK/RELIANCE):
    - yfinance's marketCap is raw INR -> divide by 1e7 for Rupees Crore.
    - yfinance's debtToEquity is already *100 (e.g. 36.653 means a 0.37 ratio) -> /100.
    - revenueGrowth / earningsGrowth / returnOnEquity are fractions -> *100 for %.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(f"{symbol}{settings.TICKER_SUFFIX}").info or {}
    except Exception:
        return None
    if not info.get("marketCap"):
        return None
    return {
        "market_cap_cr": round(info["marketCap"] / 1e7, 1),
        "pe_ratio": info.get("trailingPE"),
        "revenue_growth_pct": (round(info["revenueGrowth"] * 100, 1)
                                if info.get("revenueGrowth") is not None else None),
        "profit_growth_pct": (round(info["earningsGrowth"] * 100, 1)
                               if info.get("earningsGrowth") is not None else None),
        "roe_pct": (round(info["returnOnEquity"] * 100, 1)
                    if info.get("returnOnEquity") is not None else None),
        "debt_to_equity": (round(info["debtToEquity"] / 100, 2)
                            if info.get("debtToEquity") is not None else None),
    }


if __name__ == "__main__":
    import time
    for sym in ["RELIANCE", "TCS", "ICICIBANK", "APOLLO", "CUPID", "NKIND", "3MINDIA"]:
        f = fetch_fundamentals(sym)
        print(f"{sym:10s} -> {f}")
        time.sleep(0.3)
