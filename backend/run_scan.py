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
from get_prices import get_prices
from find_breakouts import add_indicators, build_summary


def run():
    started = time.time()
    con = duckdb.connect(str(settings.DUCKDB_PATH))

    all_prices = []
    all_features = []
    summaries = []

    print(f"Scanning {len(settings.WATCHLIST)} stocks (source: {settings.PRICE_SOURCE})...\n")
    for symbol, meta in settings.WATCHLIST.items():
        t0 = time.time()
        prices = get_prices(symbol)
        if prices is None or len(prices) == 0:
            print(f"  {symbol:10s} -> NO DATA (skipped)")
            continue

        feat = add_indicators(prices)
        summary = build_summary(feat, symbol, meta)
        if summary is None:
            print(f"  {symbol:10s} -> not enough history (skipped)")
            continue

        all_prices.append(prices)
        all_features.append(feat.assign(symbol=symbol))
        summaries.append(summary)

        b = "BREAKOUT" if summary["breakout"]["today"] else summary["breakout"]["sentiment"]
        print(f"  {symbol:10s} -> {len(prices):4d} bars | Rs {summary['price']:>9,.2f} "
              f"| {summary['adx']['label']:9s} | {b:8s} | {time.time()-t0:4.1f}s")

    if not summaries:
        print("\nNo stocks produced results — aborting without overwriting output.")
        return

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
    with open(settings.BREAKOUTS_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nWrote {settings.BREAKOUTS_JSON.relative_to(settings.REPO_DIR)} "
          f"({len(summaries)} stocks) in {time.time()-started:.1f}s total.")
    print(f"DuckDB research file: {settings.DUCKDB_PATH.relative_to(settings.REPO_DIR)}")


if __name__ == "__main__":
    run()
