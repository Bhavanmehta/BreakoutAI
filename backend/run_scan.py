"""
The orchestrator. Run this to refresh everything:

    python run_scan.py

Steps, per stock in the watchlist:
    fetch adjusted prices  ->  compute indicators/breakouts  ->  store in DuckDB
Then it writes data/breakouts.json — the single file the website reads.

This is exactly what the daily GitHub Action runs.
"""
from __future__ import annotations
import json
import time
from datetime import datetime, timezone

import duckdb
import pandas as pd

import settings
from get_prices import get_prices, fetch_prices_yfinance_batch
from find_breakouts import add_indicators, build_summary
from track import update_and_evaluate
from universe import build_universe


def _fetch_all(symbols: list[str]) -> dict:
    """Fetch prices for the whole universe. At whole-market scale we batch through
    yfinance (one request per ~100 tickers); the jugaad source has no batch API, so
    it falls back to a per-symbol loop."""
    if settings.PRICE_SOURCE == "yfinance":
        t0 = time.time()
        prices = fetch_prices_yfinance_batch(symbols)
        print(f"  fetched {len(prices)}/{len(symbols)} symbols in {time.time()-t0:.1f}s (batched)\n")
        return prices
    return {s: p for s in symbols if (p := get_prices(s)) is not None and len(p) > 0}


def run():
    started = time.time()
    con = duckdb.connect(str(settings.DUCKDB_PATH))

    all_prices = []
    all_features = []
    feat_by_symbol = {}
    summaries = []

    watchlist = build_universe()
    print(f"Scanning {len(watchlist)} stocks (source: {settings.PRICE_SOURCE})...\n")
    prices_by_symbol = _fetch_all(list(watchlist.keys()))

    no_data = short_history = 0
    breakouts_today = []
    for symbol, meta in watchlist.items():
        prices = prices_by_symbol.get(symbol)
        if prices is None or len(prices) == 0:
            no_data += 1
            continue

        feat = add_indicators(prices)
        summary = build_summary(feat, symbol, meta)
        if summary is None:
            short_history += 1
            continue

        all_prices.append(prices)
        all_features.append(feat.assign(symbol=symbol))
        feat_by_symbol[symbol] = feat
        summaries.append(summary)
        if summary["breakout"]["today"]:
            breakouts_today.append(symbol)

    print(f"  {len(summaries)} produced cards | {no_data} no data | {short_history} too little history")
    print(f"  breaking out today ({len(breakouts_today)}): {', '.join(breakouts_today[:25]) or 'none'}"
          + (" ..." if len(breakouts_today) > 25 else ""))

    if not summaries:
        print("\nNo stocks produced results — aborting without overwriting output.")
        return

    # --- Merge cached ownership data (promoter/FII/DII/MF/public %) if present ---
    # holdings.json is produced separately by fetch_holdings.py (quarterly-slow NSE
    # data), so it's optional: stocks without an entry just carry holdings: null.
    holdings_path = settings.DATA_DIR / "holdings.json"
    if holdings_path.exists():
        with open(holdings_path, encoding="utf-8") as f:
            holdings = json.load(f)
        matched = 0
        for s in summaries:
            h = holdings.get(s["symbol"])
            s["holdings"] = h
            matched += 1 if h else 0
        print(f"  merged holdings for {matched}/{len(summaries)} stocks")
    else:
        for s in summaries:
            s["holdings"] = None

    # --- Merge cached sector/industry (from fetch_sectors.py) if present ---
    # Curated labels (FALLBACK_WATCHLIST) win; otherwise fill from sectors.json.
    # Also optional — stocks without an entry keep their (possibly blank) sector.
    sectors_path = settings.DATA_DIR / "sectors.json"
    if sectors_path.exists():
        with open(sectors_path, encoding="utf-8") as f:
            sectors = json.load(f)
        matched = 0
        for s in summaries:
            info = sectors.get(s["symbol"]) or {}
            # Prefer a curated non-empty sector; only fill from yfinance when blank.
            if not s.get("sector") and info.get("sector"):
                parts = [p for p in (info.get("sector"), info.get("industry")) if p]
                s["sector"] = " · ".join(parts)
            if not s.get("industry") and info.get("industry"):
                s["industry"] = info["industry"]
            matched += 1 if info.get("sector") else 0
        print(f"  merged sectors for {matched}/{len(summaries)} stocks")

    # --- Store into DuckDB (local research layer) ---
    prices_df = pd.concat(all_prices, ignore_index=True)
    features_df = pd.concat(all_features, ignore_index=True)
    con.execute("CREATE OR REPLACE TABLE ohlcv_daily AS SELECT * FROM prices_df")
    con.execute("CREATE OR REPLACE TABLE ohlcv_features AS SELECT * FROM features_df")
    con.close()

    # --- Write breakouts.json (serving layer the website reads) ---
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of_date": max(s["as_of"] for s in summaries),
        "source": settings.PRICE_SOURCE,
        "count": len(summaries),
        "disclaimer": ("Educational content only, not investment advice. Patterns can fail. "
                       "Always use a stop-loss and consult a SEBI-registered advisor before trading."),
        "stocks": summaries,
    }
    # Compact (no indent): this is a machine-read serving artifact regenerated daily
    # at ~2000 stocks, so we optimize committed/served size over hand-diff readability.
    with open(settings.BREAKOUTS_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    # --- Log today's calls & grade past ones (the forward track record) ---
    as_of = max(s["as_of"] for s in summaries)
    track = update_and_evaluate(feat_by_symbol, summaries, as_of)
    hr = f"{round(track['hit_rate']*100)}%" if track["hit_rate"] is not None else "n/a"
    print(f"\nWrote {settings.BREAKOUTS_JSON.relative_to(settings.REPO_DIR)} "
          f"({len(summaries)} stocks) in {time.time()-started:.1f}s total.")
    print(f"Track record: {track['actionable_evaluated']} actionable calls graded, "
          f"hit rate {hr} ({track['pending']} pending, {track['live_calls_logged']} live logged).")
    print(f"DuckDB research file: {settings.DUCKDB_PATH.relative_to(settings.REPO_DIR)}")


if __name__ == "__main__":
    run()
