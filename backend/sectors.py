"""
Sector / industry classification for a stock, from yfinance.

Why it matters for a breakout radar: breakouts cluster by sector — when money
rotates into (say) PSU banks or defence, many names in that group set up at once.
Knowing each stock's sector unlocks two things the app wants: a sector *filter* on
the watchlist, and a sector *breadth* radar ("how many names in this group are
primed / breaking out"), which is a leading tell for a rotation.

Source: yfinance's `.info` carries `sector` (broad group, e.g. "Financial Services")
and `industry` (the sub-classification, e.g. "Insurance Brokers"). It's reliable and
fast (~0.3s/stock) for NSE ".NS" tickers. This is slow-changing reference data, so —
like holdings — it's fetched by a standalone script (fetch_sectors.py) into a cached
data/sectors.json that run_scan.py merges in, NOT re-fetched during the daily price scan.
"""
from __future__ import annotations

# yfinance's broad `sector` labels are US-GICS-flavoured; keep them but present the
# `industry` as the finer sub-label so a card reads like "Financial Services · Insurance
# Brokers", matching the curated FALLBACK_WATCHLIST convention ("Financials · Private Bank").


def fetch_sector(symbol: str) -> dict | None:
    """Return {"sector": str|None, "industry": str|None} for one NSE symbol, or None
    if yfinance has no classification for it. Never raises — networking/parse errors
    just return None so the caller can move on."""
    try:
        import yfinance as yf
        info = yf.Ticker(f"{symbol}.NS").info or {}
    except Exception:
        return None
    sector = (info.get("sector") or "").strip() or None
    industry = (info.get("industry") or "").strip() or None
    if not sector and not industry:
        return None
    return {"sector": sector, "industry": industry}


def sector_label(sector: str | None, industry: str | None) -> str:
    """Compose the display string the frontend shows, e.g. "Financial Services · Insurance
    Brokers". Falls back gracefully when only one part is known."""
    parts = [p for p in (sector, industry) if p]
    return " · ".join(parts)


if __name__ == "__main__":
    import time
    for sym in ["RELIANCE", "TCS", "CGPOWER", "POLICYBZR", "SBIN"]:
        s = fetch_sector(sym)
        print(f"{sym:10s} -> {s}  | label: {sector_label(**s) if s else '—'}")
        time.sleep(0.3)
