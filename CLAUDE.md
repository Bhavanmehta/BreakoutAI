# BreakoutAI (Working Title)

## Vision

A high-performance, minimalist, and educational "Pre-Breakout Radar" for the Indian equity
market (NSE/BSE). Helps retail traders identify stocks that are mathematically coiling for a
move, rather than just showing lagging indicators. Prioritizes actionable, educational insights
over raw data saturation.

## Design Philosophy

- **Minimalist & Intuitive** — "Is it bullish?" should be clear in 3 seconds.
- **Educational** — every technical setup should explain *why* it's significant, not just flag it.
- **Actionable** — move from "observing the market" to "identifying trade setups."

## Stack

- **Frontend**: HTML5, Tailwind CSS, vanilla JavaScript.
- **Charting**: currently the TradingView widget; migrating to `lightweight-charts` to enable
  custom pivot/marker drawing (needed for pattern overlays like VCP handles).
- **Backend (target)**: Python (Scipy, Numpy, Pandas) — not yet started, see Roadmap.
- **Hosting**: Vercel / GitHub Pages, via GitHub CI/CD.

## Current State

The whole app currently lives in one file: [combined_breakout_scanner_platform.html](combined_breakout_scanner_platform.html).
There is no backend and no build step — it's a static page with an inline `<script>` block.

- `dataset` object (~line 263) hardcodes three tickers (ETERNAL, TCS, RELIANCE) with all
  meta/technical/pattern/fundamental fields baked in. This is a placeholder for the future
  Python-generated data pipeline, not real-time data. Price/market cap/P/E were manually
  refreshed to real ballpark figures on 2026-07-03 via web search so the sidebar doesn't
  visibly contradict the live TradingView chart — they will drift out of date again since
  nothing here is fetched automatically. A "Sample Data" badge on the meta panel makes this
  explicit to users. Resistance/entry/stop-loss levels are illustrative, derived by applying
  the original mock percentages to the refreshed prices, not real S/R analysis — that's still
  Roadmap step 1.
- Three tabs share a sidebar+chart layout except Fundamentals, which is full-width:
  - **Technical Indicators** — ADX, Zero Lag Trend, EMA stack (10/20/50/200).
  - **Pre-Breakout Radar** (default tab) — AI pattern analysis card, resistance proximity
    physics, volatility contraction (VCP) panel.
  - **Fundamentals** — ROE/ROCE/Debt-to-Equity tiles + QoQ sales / YoY profit bar charts.
- Chart is rendered via the TradingView widget (`BSE:<TICKER>` symbols) inside `#tv-chart-frame`,
  reloaded on stock/tab switch.
- Data source: BSE symbols (not NSE) — NSE data is licensing-blocked; BSE is license-friendly.
  This was a deliberate switch to fix iframe `NOT_FOUND` errors.

### Completed Milestones

- Unified single-page layout with sidebar-to-chart workflow.
- Educational "Pattern Analysis" cards (bullish/bearish sentiment, entry triggers, pattern
  description, risk-management disclaimers).
- Pre-breakout indicators (ADX, Zero Lag Trend) alongside traditional MA stacks.
- CI/CD via GitHub → Vercel.

## Backend (`backend/`) — the data "engine"

A Python pipeline now exists and produces real computed data (replacing the hand-typed
`dataset` for the fields it can derive from price/volume). See `backend/README.md` for details.

- **Files** (flat, named by what they do): `settings.py` (watchlist + thresholds),
  `get_prices.py`, `adjust_for_splits.py`, `find_breakouts.py`, `run_scan.py` (the entry point).
- **Data sources**: `yfinance` (default; already split/bonus-adjusted, works in CI) with a
  `jugaad-data` + NSE-corporate-actions fallback path that we adjust ourselves
  (`adjust_for_splits.py`) — the latter is the whole-market-scalable route, tested but not the
  daily default.
- **Flow** (`backend/run_scan.py`): fetch adjusted prices → `find_breakouts.py` computes EMA stack,
  ADX, resistance/touches, VCP contraction, breakout detection + per-stock historical breakout stats
  → store in local DuckDB (`data/market_research.duckdb`, git-ignored) → write `data/breakouts.json`.
- **`data/breakouts.json`** is the committed serving file the frontend is meant to read. Schema:
  top-level `generated_at`/`as_of_date`/`source`/`disclaimer`/`stocks[]`; each stock has
  `price`, `ema_stack`, `adx`, `resistance`, `volatility`, `breakout` (today + sentiment),
  `history` (past breakouts, win rate, avg fwd return), and `entry` (trigger/entry/stop text).
- **Zero-backend flow**: `.github/workflows/daily-scan.yml` runs the pipeline after market close
  (10:30 UTC, Mon–Fri) and commits the refreshed JSON — no always-on server. Manually triggerable
  from the Actions tab.
- **Corporate-action adjustment**: raw NSE prices aren't split/bonus-adjusted (causes fake ~50%
  cliffs, e.g. Reliance's Oct-2024 1:1 bonus). `adjust_for_splits.py` parses the event ratio from
  NSE's text and back-adjusts; `python backend/adjust_for_splits.py` self-tests the parser.

### Still TODO (in rough order)
1. **Wire the frontend to `breakouts.json`** — the HTML still uses its inline hardcoded `dataset`;
   it should fetch the JSON instead. (Fundamentals like P/E, ROE, mcap are NOT yet produced by the
   backend — needs a separate source before those fields can go live.)
2. **Expand the universe** beyond the 6-stock watchlist toward full NSE/BSE, via the
   `jugaad` bhavcopy + `adjust.py` path.
3. **Pattern classification** (Cup & Handle, Ascending Triangle, etc.) — currently we detect
   generic breakouts + resistance/VCP, not named chart shapes yet.
4. **Chart migration**: TradingView widget → `lightweight-charts` v5 to draw pattern overlays
   (resistance line, handle pivot, breakout marker) directly on candles.

When picking up this work, read `backend/README.md` and check `data/breakouts.json` for the
current real output before assuming anything above is still pending — this file won't stay in sync
automatically.
