"""
Data sourcing: daily prices + corporate-action events.

Two price sources are supported:
  * yfinance  -> prices come already adjusted for splits/bonuses. Works from any
                 network (including GitHub's servers). This is the default.
  * jugaad    -> raw NSE prices (whole-market friendly) that we adjust ourselves
                 using the corporate-action list from NSE. See adjust_for_splits.py.

Every price fetcher returns a tidy DataFrame with columns:
    date (datetime), open, high, low, close, volume, symbol
sorted oldest -> newest.
"""
from __future__ import annotations
from datetime import date, timedelta
import time
import pandas as pd

import settings


# ---------------------------------------------------------------------------
# yfinance (adjusted, CI-friendly) — default source
# ---------------------------------------------------------------------------
def fetch_prices_yfinance(symbol: str, years: int = settings.HISTORY_YEARS) -> pd.DataFrame | None:
    import yfinance as yf
    start = (date.today() - timedelta(days=int(years * 365.25) + 5)).isoformat()
    df = yf.download(f"{symbol}.NS", start=start, interval="1d",
                     auto_adjust=True, progress=False)
    if df is None or len(df) == 0:
        return None
    df = df.reset_index()
    # yfinance sometimes returns MultiIndex columns for a single ticker
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.rename(columns={
        "Date": "date", "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Volume": "volume",
    })
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    df["symbol"] = symbol
    keep = ["date", "open", "high", "low", "close", "volume", "symbol"]
    return df[keep].sort_values("date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# jugaad-data (raw NSE) + our own adjustment — the full-market path
# ---------------------------------------------------------------------------
def fetch_prices_jugaad(symbol: str, years: int = settings.HISTORY_YEARS) -> pd.DataFrame | None:
    from jugaad_data.nse import stock_df
    from adjust_for_splits import fetch_corporate_actions, apply_adjustments

    from_date = date.today() - timedelta(days=int(years * 365.25) + 5)
    raw = stock_df(symbol=symbol, from_date=from_date, to_date=date.today(), series="EQ")
    if raw is None or len(raw) == 0:
        return None
    raw = raw.rename(columns={
        "DATE": "date", "OPEN": "open", "HIGH": "high",
        "LOW": "low", "CLOSE": "close", "VOLUME": "volume",
    })
    raw["date"] = pd.to_datetime(raw["date"]).dt.normalize()
    raw = raw[["date", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)

    # Correct the fake price cliffs caused by splits/bonuses.
    events = fetch_corporate_actions(symbol, from_date, date.today())
    raw = apply_adjustments(raw, events)

    raw["symbol"] = symbol
    return raw[["date", "open", "high", "low", "close", "volume", "symbol"]]


# ---------------------------------------------------------------------------
# Unified entry point with graceful fallback
# ---------------------------------------------------------------------------
def get_prices(symbol: str, years: int = settings.HISTORY_YEARS,
               source: str = settings.PRICE_SOURCE) -> pd.DataFrame | None:
    """Fetch adjusted daily OHLCV for one symbol. Falls back to the other
    source if the primary one returns nothing."""
    primary, fallback = (fetch_prices_yfinance, fetch_prices_jugaad) \
        if source == "yfinance" else (fetch_prices_jugaad, fetch_prices_yfinance)
    for fn in (primary, fallback):
        try:
            df = fn(symbol, years)
            if df is not None and len(df) > 0:
                return df
        except Exception as e:
            print(f"    [{symbol}] {fn.__name__} failed: {e}")
        time.sleep(0.4)  # be polite between attempts
    return None
