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

## Roadmap — "Engine" Phase

The near-term goal is replacing the hardcoded `dataset` object with a real Python-computed
pipeline:

1. **Data pipeline**: Python backend computing Support/Resistance (K-Means/peak detection) and
   volatility contraction, replacing the hand-authored numbers in `dataset`.
2. **Schema**: define a `breakouts.json` structure to pass pattern-recognition markers from
   Python to the frontend (levels, pivots, pattern labels, confidence).
3. **Algorithm development**, roughly in this order:
   - Level 1: automated Support/Resistance detection.
   - Level 2: VCP (Volatility Contraction Pattern) math.
   - Level 3: pattern classification (Cup & Handle, Ascending Triangle, etc.).
4. **Full chart migration**: TradingView widget → `lightweight-charts`, to draw custom pattern
   overlays (e.g. the "handle" pivot) directly on the chart instead of just describing them in
   the sidebar.

When picking up roadmap work, check whether `breakouts.json` or a `backend/`/`scripts/` Python
tree exists yet before assuming step 1 is still pending — this file won't stay in sync with that
automatically.
