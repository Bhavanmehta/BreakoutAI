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
| **signals.py** | The "why" / rationale layer. `build_rationale(rec)` turns one enriched record into a plain-language explanation: a two-column confirming/risk signal set, an edge-vs-risk **RSS** roll-up (`{edge, risk, net, confidence}`), a one-line **make-or-break** trigger, and advisory **gates** (liquidity / volume-confirm / uptrend / earnings-veto — thresholds from `settings.py`). Stored as `rec["rationale"]`. **Transparency only — never the ranker**; the conviction score stays purely technical. Runs *inside* `run_scan.py` after the enrichment merges; run directly to backfill an existing `breakouts.json`. |
| **track.py** | The forward track record: logs each day's calls to `../data/predictions_log.jsonl` and grades past on-watch episodes → `../data/track_record.json`. |
| **build_performance.py** | The live, forward-only ledger behind `performance.html` → `../data/performance.json`. Records every suggestion the site actually published (breakout-today / relative-strength / US high-conviction tiers) on the day it was made, tracks it for `PERF_TRACK_BARS` days, and grades it by the same +1R-before-stop rule. Only identity is persisted per episode; entry/stop/closes/status are re-derived from current price history on every refresh (so a retroactive split adjustment can't strand a stale entry). Also derives a top-level `analytics` block each write: **expectancy** (mean R, win rate), **benchmark** (per-call window return vs holding the index — mean alpha + beat-rate; null offline), and **hindsight** (does the site's own conviction score stratify live follow-through? — diagnostic only, never fed back into scoring). `run_scan.py` calls `update_from_scan()` after each scan (passing the benchmark frame it already fetched); run standalone to refresh outcomes only, or with `--seed` to reconstruct launch-era episodes from committed `breakouts.json` git snapshots. |
| **holdings_screener.py** | **Primary ownership source** — parses screener.in's Shareholding Pattern table (~12 quarters of Promoter/FII/DII/Public %) from a company page. Reliable + quarterly (NSE rate-limits and is annual-ish). Run directly for a self-test. |
| **holdings.py** | **Fallback** ownership source — parses NSE shareholding filings into promoter / FII / DII / MF / public % (aggregate rollup contexts from the SHP XBRL). `fetch_holdings(..., history_points=N)` parses the recent N filings into a `history` series. Run directly for a self-test. |
| **fetch_holdings.py** | Standalone + resumable: populates `../data/holdings.json` (snapshot + quarterly `history[]`) — screener first, NSE fallback. Prioritizes by readiness, saves incrementally, re-fetches anything not yet screener-sourced. `run_scan.py` merges the result in. **Not** part of the daily scan. |
| **sectors.py** | Fetches each stock's sector / industry from `yfinance.info`. Run directly for a self-test. |
| **fetch_sectors.py** | Standalone + resumable: populates `../data/sectors.json` (`{symbol: {sector, industry}}`) via `sectors.py`. Fast (~0.3s/stock, whole market ~10min), driven by the latest `breakouts.json` symbol list (no NSE call), caches misses. `run_scan.py` merges it into each stock's `sector`/`industry`. **Not** part of the daily scan. |
| **fetch_delivery.py** | Standalone: populates `../data/delivery.json` with per-stock NSE delivery-% (latest + trailing average, from the *full* bhavcopy — one whole-market CSV per trading day, ~30 requests total for the lookback window, not per-symbol). High delivery-% means buyers are holding overnight, not day-trading — a cheap "realness" confirm on a breakout. Never a ranker input; only surfaces in the rationale layer (`signals.py`). If NSE is unreachable and too few days come back, leaves any existing `delivery.json` untouched. **IN-only** (no free US equivalent) — `run_scan.py` carries `delivery: null` on a US run or if the file is absent, and the signal token never fires. **Not** part of the daily scan. |
| **market_mood.py** | Runs *inside* `run_scan.py` (not standalone). Computes the market-wide 0–100 Mood Index (`breakouts.json`'s top-level `market_mood`, not per-stock): Nifty vs its 20-day SMA, India VIX, NSE's daily aggregate FII/DII flow (z-scored against a persisted rolling history in `../data/fii_dii_history.json`), and whole-universe advance/decline breadth. Any component that fails to fetch drops out and the rest reweight. |
| **run_scan.py** | The one you run. Discovers the universe, does all of the above for every stock in it, merges `holdings.json` + `sectors.json` + `delivery.json` if present, computes the Market Mood Index, stores data in a local DuckDB file for research, writes **`../data/breakouts.json`** (the file the website consumes), and updates the track record + the live performance ledger (`build_performance.py`). |
| **sentiment.py** | Local VADER + a finance lexicon scores headline/post text (no provider sentiment is trusted). Used by `fetch_news.py` and `fetch_social.py`. |
| **event_classifier.py** | Layered on top of `sentiment.py`: ~19 ordered keyword categories (order win, SEBI penalty, earnings beat/miss, rating up/downgrade, buyback, promoter pledge, ...) each with a small signed bias, blended into the VADER score — weighted toward the event when VADER reads near-neutral. |
| **news_providers.py** / **fetch_news.py** | Standalone + resumable: populates `../data/news.json` from four providers in priority order — GNews, Marketaux, NewsData.io (all budget-capped, need API keys), then Google News RSS (no key, no quota, reaches the long tail the others miss). **Not** part of the daily scan's core, but is invoked separately in the GitHub Action. |
| **social_providers.py** / **fetch_social.py** | Standalone + resumable: populates `../data/social.json` — Reddit mention count + sentiment (needs a free Reddit app key, not yet configured) and Google Trends search-interest (no key). **Not** part of the daily scan. |
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
