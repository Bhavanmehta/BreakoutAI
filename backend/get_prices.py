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
    df = yf.download(f"{symbol}{settings.TICKER_SUFFIX}", start=start, interval="1d",
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


def _tidy_one(sub: pd.DataFrame, symbol: str) -> pd.DataFrame | None:
    """Turn a single ticker's OHLCV frame (from a batch download) into our tidy
    schema. Returns None if it's empty after dropping non-trading rows."""
    sub = sub.dropna(how="all").reset_index()
    sub.columns = [c[0] if isinstance(c, tuple) else c for c in sub.columns]
    sub = sub.rename(columns={
        "Date": "date", "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Volume": "volume",
    })
    if "close" not in sub.columns or sub["close"].dropna().empty:
        return None
    sub = sub.dropna(subset=["close"])
    sub["date"] = pd.to_datetime(sub["date"]).dt.tz_localize(None).dt.normalize()
    sub["symbol"] = symbol
    keep = ["date", "open", "high", "low", "close", "volume", "symbol"]
    return sub[keep].sort_values("date").reset_index(drop=True)


def fetch_prices_yfinance_batch(symbols: list[str], years: int = settings.HISTORY_YEARS,
                                chunk_size: int = 100) -> dict[str, pd.DataFrame]:
    """Fetch many symbols in one shot per chunk. `yf.download` with a ticker list
    is ~30x faster and far fewer HTTP requests than looping one symbol at a time,
    which matters at whole-market scale (~2000 stocks) where per-symbol calls both
    drag and invite rate-limiting.

    Yahoo periodically rate-limits whole IP ranges (common on shared CI runners
    like GitHub Actions) independent of how much *we* have actually requested.
    When that happens, back off and retry the same chunk a few times before
    giving up on it — plowing straight into the next chunk only makes an
    existing block worse and can wipe out the whole run (seen 2026-07-16).

    Returns {symbol: tidy_df} for the symbols that came back with usable data;
    symbols that failed or returned nothing are simply absent from the dict.
    """
    import yfinance as yf
    start = (date.today() - timedelta(days=int(years * 365.25) + 5)).isoformat()
    out: dict[str, pd.DataFrame] = {}

    max_retries = 4
    backoff_base_sec = 20  # doubles each retry: 20s, 40s, 80s, 160s

    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i + chunk_size]
        tickers = [f"{s}{settings.TICKER_SUFFIX}" for s in chunk]

        data = None
        for attempt in range(max_retries + 1):
            try:
                data = yf.download(tickers, start=start, interval="1d", auto_adjust=True,
                                   group_by="ticker", progress=False, threads=True)
                break
            except Exception as e:
                is_rate_limit = (
                    type(e).__name__ == "YFRateLimitError"
                    or "Rate limited" in str(e)
                    or "Too Many Requests" in str(e)
                )
                if is_rate_limit and attempt < max_retries:
                    wait = backoff_base_sec * (2 ** attempt)
                    print(f"    [batch {i//chunk_size}] rate limited, backing off "
                          f"{wait}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait)
                    continue
                print(f"    [batch {i//chunk_size}] download failed: {e}")
                data = None
                break

        if data is None or len(data) == 0:
            continue

        for sym in chunk:
            tkr = f"{sym}{settings.TICKER_SUFFIX}"
            try:
                # For a single-ticker chunk yfinance omits the ticker column level.
                sub = data[tkr] if isinstance(data.columns, pd.MultiIndex) else data
            except (KeyError, TypeError):
                continue
            tidy = _tidy_one(sub.copy(), sym)
            if tidy is not None and len(tidy) > 0:
                out[sym] = tidy
        time.sleep(1.5)  # be polite between chunks (was 0.3s — too aggressive)

    return out


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
