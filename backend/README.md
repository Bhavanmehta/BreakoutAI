# BreakoutAI — Backend (data pipeline)

This folder is the "engine" that turns raw market data into the results file the
website reads. It runs on a schedule (see `.github/workflows/daily-scan.yml`), so
there is **no always-on server** — it wakes up, does its job, writes a file, and stops.

## The files, in the order they run

| File | What it does |
|------|--------------|
| **settings.py** | The knobs: universe size, years of history, price source, thresholds. Start here. |
| **universe.py** | Discovers the scan universe every run from NSE's daily bhavcopy (via jugaad-data), filtered to real equity shares (ISIN prefix `INE`, excludes ETFs/funds). `settings.UNIVERSE_SIZE = None` = whole market (~2,055); set an int for top-N by turnover, raise `MIN_TURNOVER` to drop the illiquid tail. One lightweight NSE request; falls back to `settings.FALLBACK_WATCHLIST` if it fails. |
| **get_prices.py** | Pulls ~3 years of daily prices. `fetch_prices_yfinance_batch()` fetches ~100 tickers per `yf.download` call — the whole market in ~70s (vs ~10min one-at-a-time). Default source `yfinance` (already split/bonus-adjusted); alternative `jugaad` (raw NSE) corrected by `adjust_for_splits.py`, per-symbol only. |
| **adjust_for_splits.py** | Fixes the fake overnight "crashes" caused by splits/bonuses, using NSE's official corporate-action list. Run it directly to self-test the ratio parser. |
| **find_breakouts.py** | The "brain": EMA stack (8/21/50/200), ADX, resistance, VCP, trend-filtered breakout detection, R-multiple follow-through scoring, sentiment/readiness (+ reliability caveat), entry/stop guidance. |
| **patterns.py** | Real chart-pattern detection via swing pivots: Ascending Triangle, Cup & Handle, Double Bottom (bullish), Head & Shoulders (bearish), fallbacks. Heuristic (validated as *not* predictive — decorative context only). |
| **analogs.py** | The historical-analog engine behind "The Read". For today's bar it finds the most geometrically-similar past bar on the same stock (z-scored EMA-stack geometry + coil + ADX + distance-to-52w-high/resistance) and reports what happened next (`fwd_5/10/20d`, `worked`). Reuses columns `find_breakouts.add_indicators()` already computes. Run directly for a self-test. |
| **track.py** | The forward track record: logs each day's calls to `../data/predictions_log.jsonl` and grades past on-watch episodes → `../data/track_record.json`. |
| **holdings_screener.py** | **Primary ownership source** — parses screener.in's Shareholding Pattern table (~12 quarters of Promoter/FII/DII/Public %) from a company page. Reliable + quarterly (NSE rate-limits and is annual-ish). Run directly for a self-test. |
| **holdings.py** | **Fallback** ownership source — parses NSE shareholding filings into promoter / FII / DII / MF / public % (aggregate rollup contexts from the SHP XBRL). `fetch_holdings(..., history_points=N)` parses the recent N filings into a `history` series. Run directly for a self-test. |
| **fetch_holdings.py** | Standalone + resumable: populates `../data/holdings.json` (snapshot + quarterly `history[]`) — screener first, NSE fallback. Prioritizes by readiness, saves incrementally, re-fetches anything not yet screener-sourced. `run_scan.py` merges the result in. **Not** part of the daily scan. |
| **sectors.py** | Fetches each stock's sector / industry from `yfinance.info`. Run directly for a self-test. |
| **fetch_sectors.py** | Standalone + resumable: populates `../data/sectors.json` (`{symbol: {sector, industry}}`) via `sectors.py`. Fast (~0.3s/stock, whole market ~10min), driven by the latest `breakouts.json` symbol list (no NSE call), caches misses. `run_scan.py` merges it into each stock's `sector`/`industry`. **Not** part of the daily scan. |
| **run_scan.py** | The one you run. Discovers the universe, does all of the above for every stock in it, merges `holdings.json` + `sectors.json` if present, stores data in a local DuckDB file for research, writes **`../data/breakouts.json`** (the file the website consumes), and updates the track record. |
| **analyze_reliability.py** | Standalone, run manually (not part of the daily pipeline): validates whether the per-stock reliability caveat is actually predictive, pooled across the whole universe. |

**Breakout rule** (grounded in Minervini/Weinstein/Turtle): close above prior 50-day high, on
≥1.5× avg volume, **while in an uptrend** (above a rising 200 EMA + above 50 EMA) and **within 25%
of the 52-week high**. "Worked" = hit +1R before -1R (stop), within 10 trading days — an R-multiple,
not a fixed %, so it scales per-stock. All tunable in `settings.py`.

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

- The scan universe (the whole NSE market, ~1,800 stocks that produce cards) is
  discovered fresh every run via `universe.py`'s one bhavcopy request; price history
  comes from `yfinance`, batch-fetched (works from GitHub's servers, comes
  pre-adjusted). The `jugaad` + `adjust.py` price path exists and is tested but
  isn't the daily default — see `settings.PRICE_SOURCE`.
- `data/breakouts.json` is written compact (~3.1 MB, ~300 KB gzipped). At whole-market
  scale that committed-daily file and the append-only `data/predictions_log.jsonl`
  grow git history unboundedly — see CLAUDE.md TODO #4 for the planned fix.
- `data/market_research.duckdb` is a local research artifact and is **git-ignored**
  (regenerate it any time by running the pipeline). `data/breakouts.json` **is**
  committed — that's the file the site serves.
- Fundamentals (P/E, ROE, market cap) are **not** produced here yet; this pipeline
  only derives what can be computed from price/volume (+ merged sector/holdings enrichment).
- **Sector** and **ownership `history`** are enrichment layers merged from `sectors.json`
  / `holdings.json`, populated by their standalone `fetch_*.py` scripts — not the daily
  scan. `universe.py` now also falls back to the previous `breakouts.json` symbol list
  (not just the 12-name static watchlist) when the NSE bhavcopy is unreachable, so a
  rate-limited discovery day can't shrink/overwrite the whole-market universe.
- `settings.MIN_HISTORY_BARS` skips recently-listed/demerged symbols (e.g. a fresh
  spin-off with a couple weeks of trading) that the wider universe can surface —
  there isn't enough history yet for a trustworthy 200-EMA trend or 52-week high.
