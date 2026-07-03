# BreakoutAI — Backend (data pipeline)

This folder is the "engine" that turns raw market data into the results file the
website reads. It runs on a schedule (see `.github/workflows/daily-scan.yml`), so
there is **no always-on server** — it wakes up, does its job, writes a file, and stops.

## The files, in the order they run

| File | What it does |
|------|--------------|
| **settings.py** | The knobs: watchlist, years of history, price source, thresholds. Start here. |
| **get_prices.py** | Pulls ~3 years of daily prices per stock. Default source `yfinance` (already split/bonus-adjusted, works anywhere); alternative `jugaad` (raw NSE, whole-market friendly) corrected by `adjust_for_splits.py`. |
| **adjust_for_splits.py** | Fixes the fake overnight "crashes" caused by splits/bonuses, using NSE's official corporate-action list. Run it directly to self-test the ratio parser. |
| **find_breakouts.py** | The "brain": EMA stack, ADX (trend strength), nearby resistance, volatility contraction (VCP), breakout detection, a bullish/neutral/bearish read, and plain-English entry/stop guidance. |
| **run_scan.py** | The one you run. Does all of the above for every watchlist stock, stores data in a local DuckDB file for research, and writes **`../data/breakouts.json`** — the single file the website consumes. |

## Run it locally

```bash
cd backend
pip install -r requirements.txt
python run_scan.py            # refresh everything -> data/breakouts.json
python adjust_for_splits.py   # optional: self-test the split/bonus parser
```

## Configure

Everything tunable is in **settings.py**: the watchlist, how many years of history,
which price source, and all the pattern thresholds.

## Notes / known limits

- The daily scan uses `yfinance` because it works from GitHub's servers and comes
  pre-adjusted. The `jugaad` + `adjust.py` path is the one that scales to the *whole*
  NSE universe; it's tested and ready for when we expand beyond the watchlist.
- `data/market_research.duckdb` is a local research artifact and is **git-ignored**
  (regenerate it any time by running the pipeline). `data/breakouts.json` **is**
  committed — that's the file the site serves.
- Fundamentals (P/E, ROE, market cap) are **not** produced here yet; this pipeline
  only derives what can be computed from price/volume.
