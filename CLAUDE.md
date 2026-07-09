# BreakoutAI (Working Title)

## Vision

A high-performance, minimalist, and educational "Pre-Breakout Radar" — originally built for the
Indian equity market (NSE/BSE), now mirrored for **US equities** too (same engine, same site, a
market toggle). Helps retail traders identify stocks that are mathematically coiling for a move,
rather than just showing lagging indicators. Prioritizes actionable, educational insights over raw
data saturation.

## Design Philosophy

- **Minimalist & Intuitive** — "Is it bullish?" should be clear in 3 seconds.
- **Educational** — every technical setup should explain *why* it's significant, not just flag it.
- **Actionable** — move from "observing the market" to "identifying trade setups."

## Stack

- **Frontend**: two static HTML files + Tailwind (CDN) + vanilla JS, no build step —
  [combined_breakout_scanner_platform.html](combined_breakout_scanner_platform.html) (the main
  scanner/watchlist app) and [performance.html](performance.html) (the forward-only track-record
  ledger, see the Performance page bullet below).
- **Charting**: the per-stock detail chart now **defaults to the home-grown annotated chart**
  (EMA8/21 overlays + volume/RSI panes + resistance/support/VCP/breakout markers, `lightweight-charts`);
  a **TradingView toggle** (`chartModeTradingview`, `BSE:<TICKER>`) remains as an alternate view and
  as the automatic fallback for stocks with no per-stock OHLC yet — see `export_ohlc.py` and TODO #8.
- **Backend**: Python (pandas/numpy/duckdb/yfinance/jugaad-data) in `backend/`. Real, working.
  Every script is **market-aware** via one env var, `BREAKOUTAI_MARKET` (`IN` default, or `US`) —
  see `settings.py` and the Multi-market bullet below.
- **Live API layer**: two Vercel Python serverless functions in `api/` — `watchlist.py` (My
  Watchlist CRUD, Upstash Redis-backed) and `quotes.py` (live intraday price overlay, direct Yahoo
  v8 chart-meta calls, no yfinance/pandas dependency to keep cold starts light). Everything else is
  static JSON, no always-on server.
- **Hosting**: Vercel (serves the two HTML files + the `api/` functions) / GitHub Pages, via GitHub
  CI/CD. **Serving data lives on a separate orphan `data` branch, not `main`** — see the Data-branch
  serving bullet below; this is the single biggest architectural change since the app was JSON-only.

## IMPORTANT: repo location & how to run

- **Canonical repo (git remote + Vercel)**: `C:\Users\bhava\OneDrive\Documents\GitHub\BreakoutAI`
  → `github.com/Bhavanmehta/BreakoutAI`. **Do all work here** and commit/push from here.
- **Python**: `C:\Users\bhava\AppData\Local\Programs\Python\Python312\python.exe` (not on PATH as
  `python`). **Git**: `C:\Program Files\Git\cmd` (add to `$env:Path`).
- **Run the pipeline**: `cd backend; python run_scan.py` — regenerates `data/breakouts.json`,
  `predictions_log.jsonl`, `track_record.json`, `performance.json`, per-stock `data/ohlc/*.json`,
  and the git-ignored DuckDB, for **India** by default. For the **US mirror**, set the env var
  first: `BREAKOUTAI_MARKET=US python run_scan.py` — writes to `data/us/` instead (see Multi-market
  bullet). `data/` is real and present locally either way; it's just **not committed on `main`**
  anymore (gitignored — see Data-branch serving bullet), so `git status` after a local run should
  show it clean.
- **Preview the site**: from the repo root `python -m http.server 8000`, open
  `http://localhost:8000/combined_breakout_scanner_platform.html` (or `/performance.html`).
  `file://` won't work (fetch is blocked). Locally the frontend reads straight from disk (`data/` /
  `data/us/`); in production it fetches from the `data` branch on raw.githubusercontent.com (see
  `DATA_BASE` in both HTML files).
- **Ask AI dev server** (optional, only needed to test the chat panel locally): `cd backend; python
  chat_server.py` (needs `GROQ_API_KEY`, see the Ask AI bullet) — a local proxy so the key never
  sits in client-side JS.
- **Claude Skills**: `.claude/skills/` has five repo-specific skills (`backtest-method`,
  `grade-watchlist`, `ship-signal`, `verify-frontend`, `wrap-session`) that codify this project's
  recurring workflows — invoke them by name rather than re-deriving the same steps from scratch.

## Current State

The site now covers **two markets** (India NSE/BSE + US, toggled on the same page,
`combined_breakout_scanner_platform.html`) plus a companion **performance page**
([performance.html](performance.html)). Everything shown is real computed data, JSON-driven —
no hardcoded dataset anywhere.

**Layout:** a top **filter/sort bar** (search · sector dropdown · sort [**breakout conviction**
(default) / readiness / proximity / day-move / ADX / A→Z] · "Primed only" toggle · a collapsible
**⚙ Fundamentals** filter panel — Market Cap/P/E/Revenue Growth/Profit Growth/ROE/D-E), then a
two-column split: **left** = a **Sector Radar** (click to filter) + a **vertical watchlist**, each
row showing symbol · Δ% · name · price · distance-to-resistance · sentiment · the big **conviction
number** · a one-click **★ quick-track** star (adds to My Watchlist without opening the detail
pane) · an amber **★ High conviction** pill for US `high_conviction`-tier stocks; capped at
`MAX_RESULTS`=80, click-to-drill. **Right** = the selected stock's detail — a slim header (name ·
sector · price · Δ · the conviction score), **The Read** (readiness + a de-emphasized, muted
one-day historical-analog reference — see `analogs.py` and `score.py`), then a single chart card
whose header carries the indicator strip (ADX + EMAs, ⓘ tooltips) and an **Annotated | TradingView**
toggle — defaulting to the annotated `lightweight-charts` view (EMA8/21, volume/RSI panes,
resistance/support/VCP/breakout overlays), Ownership (tabbed FII-default over-time chart), Historical Precedents,
Resistance/Support (now swing-pivot-clustered zones, see `levels.py`), VCP, entry guidance, and
fundamentals. A floating **Ask AI** chat panel (Groq-backed, tool-calling, can query/compare any
stock in the universe or run read-only SQL) sits alongside. **My Watchlist** is a separate personal
tab — add/remove any stock (Upstash-backed via `api/watchlist.py`), graded later by
`grade-watchlist`. The frontend's default sort/rank is the single 0–100 **breakout conviction**
score (`readiness.conviction`, see `score.py`), not raw readiness label.

## Backend (`backend/`) — the data "engine"

A Python pipeline produces all computed data. See `backend/README.md` for details.

- **Files** (flat, named by what they do): `settings.py` (all thresholds + universe size +
  per-market calibration), `universe.py` (discovers the scan universe, market-aware),
  `get_prices.py`, `adjust_for_splits.py`, `find_breakouts.py` (core indicators + readiness +
  conviction wiring), `methods.py` (research breakout-definition alternatives A-H, not all
  shipped), `score.py` (the conviction-score brain), `levels.py` (swing-pivot support/resistance),
  `patterns.py` (chart-pattern detection, decorative — see TODO #5), `analogs.py` (historical-analog
  engine behind "The Read"), `track.py` (forward track record), `build_performance.py` (the
  performance-page ledger), `holdings_screener.py` (primary quarterly ownership source,
  screener.in) + `holdings.py` (NSE-XBRL fallback) + `fetch_holdings.py`, `sectors.py` +
  `fetch_sectors.py` (sector/industry), `fundamentals.py` + `fetch_fundamentals.py` (market
  cap/P-E/growth/ROE/D-E), `earnings.py` + `fetch_earnings.py` (EPS estimate-vs-actual),
  `fetch_news.py` + `news_providers.py` + `event_classifier.py` + `sentiment.py` (news +
  sentiment), `fetch_social.py` + `social_providers.py` (Reddit/Trends buzz), `market_mood.py`
  (fear/greed gauge), `export_ohlc.py` (per-stock chart JSON), `ask_ai.py` + `chat_server.py` (Ask
  AI assistant), `run_scan.py` (the entry point), `analyze_reliability.py` /
  `analyze_hc_rolling_window.py` (standalone validation scripts).
- **Multi-market** (`settings.py`): every script reads `BREAKOUTAI_MARKET` from the environment
  once at import (`IN` default or `US`) and branches nearly everything off it — `DATA_DIR`
  (`data/` vs `data/us/`), ticker suffix (`.NS` vs none), RS benchmark (Nifty vs S&P 500), VIX
  ticker, currency symbol/formatting, score calibration (see Conviction score bullet), and
  US-only `HC_ENABLED` high-conviction tiers. India's universe (`universe.py`) is discovered from
  the daily NSE bhavcopy (whole market, ~2,055 equities); the **US universe** was independently
  widened from an initial S&P500+Nasdaq100+Russell2000 seed to a full market-cap-floor screen
  (~4,668 symbols). Two separate GitHub Actions run the two markets on their own schedules — see
  the Data-branch serving bullet.
- **Universe** (`universe.py`, India): the scan list is discovered fresh every run, not hand-typed.
  One bhavcopy request via `jugaad-data` (`jugaad_data.nse.bhavcopy_raw`) returns every listed NSE
  equity's turnover for the latest trading day; filtered to real equity shares (ISIN prefix `INE`
  — excludes ETFs/fund units like LIQUIDBEES) and ranked by turnover. `settings.UNIVERSE_SIZE =
  None` means whole market; set it to an int to keep only the top-N. If bhavcopy discovery fails,
  it falls back to the **previous `breakouts.json`'s symbol list** and only then to
  `settings.FALLBACK_WATCHLIST` (hand-picked 12) — so a bad NSE day can't silently shrink the
  served universe. This is the *only* jugaad/NSE-scraping touchpoint in the daily flow.
- **Data sources**: `yfinance` (default; already split/bonus-adjusted, works in CI). At
  whole-market scale we **batch-fetch** — `get_prices.fetch_prices_yfinance_batch()` pulls ~100
  tickers per `yf.download` call. Single-symbol `get_prices()` and the `jugaad-data` +
  NSE-corporate-actions adjustment path (`adjust_for_splits.py`) still exist as a tested fallback,
  not the daily default (see `settings.PRICE_SOURCE`).
- **`settings.MIN_HISTORY_BARS`**: recently-listed/demerged symbols can surface from the wider
  universe. `build_summary()` explicitly requires `MIN_HISTORY_BARS` (needs the 200-EMA "rising"
  check window and a genuine 52-week high) before producing a card, skipping the stock otherwise.
- **Flow** (`backend/run_scan.py`): fetch adjusted prices → `find_breakouts.py` computes EMA stack
  (8/21/50/200), ADX, resistance/support zones (`levels.py`), VCP contraction, breakout detection +
  per-stock historical stats, plus the E/E2 relative-strength and (US-only) squeeze/high-conviction
  trigger columns from `methods.py` → conviction score (`score.py`) → store in local DuckDB
  (`data/market_research.duckdb`, git-ignored) → write `breakouts.json` + per-stock OHLC +
  `performance.json`.
- **Breakout definition** (grounded in Minervini Trend Template / Weinstein Stage 2 / Turtle, not
  invented): close above prior 50-day high, on ≥1.5× avg volume, **while in an uptrend** (above a
  *rising* 200 EMA and above the 50 EMA) and **within 25% of the 52-week high** — see
  `settings.REQUIRE_UPTREND`. "Follow-through" (did it work) = price hit **+1R before -1R (stop)**
  within 10 trading days, where R = entry − stop and stop = resistance × `STOP_LOSS_FRACTION`
  (~6% risk) — an R-multiple, not a fixed %, order-aware. See `add_indicators()` in
  `find_breakouts.py` and `settings.py`. **India's Method-A base hit rate is ~38.8%; the US mirror's
  is much lower, ~26.7%**, because the fixed ±6%-of-resistance band is implicitly tuned to Indian
  volatility (confirmed via an ATR-scaled regrade: US base rate becomes a volatility-neutral 41.8%
  under that alternative rule) — production still uses the fixed-band rule for both markets (see
  the Conviction score bullet for how scoring was recalibrated per-market instead of changing the
  rule itself).
- **Support / resistance — now real trader-standard zones** (`levels.py`, folded into
  `find_breakouts.py`): the mechanical rolling-N-day-high/low the breakout trigger uses is fine as
  a backtested signal but was a poor thing to *draw on a chart* (a single stale touch, sometimes a
  months-old irrelevant level). `levels.py` instead clusters swing pivots into horizontal zones (the
  3-point rule), ranks each by touch count weighted by volume + recency, and reports the nearest
  validated zone above/below price with a 0–1 strength — this is what the Resistance/Support card
  and the annotated chart's support line now show (replacing a naive single-level readout).
- **Conviction score — the single 0–100 ranking number** (`score.py`, `readiness.conviction`,
  frontend's **default sort**): `100*(0.55*imminence + 0.45*quality_norm)`, where quality =
  `0.60*shrunk_reliability + 0.25*base_depth + 0.15*method_confirmation` — **only
  backtest-validated features** (deliberately excludes ADX, volume-surge magnitude, named chart
  patterns, vol_contraction, and the one-day analog — all shown non-predictive or counterintuitive
  in `analyze_reliability.py`). **Bayesian shrinkage** (`reliability_estimate`,
  `(worked + 4*prior)/(total + 4)`) is the key idea behind the reliability caution text: a 0-of-1
  history reads as ~neutral, not a red flag, fixing the old "one bad breakout flashes red" problem;
  it requires ≥3 past events before making any negative claim. **Calibration is per-market**
  (`settings.SCORE_BASE_RATE`/`SCORE_W_REL`/`W_DEPTH`/`W_METHOD`): India uses its 0.39 base rate and
  0.60/0.25/0.15 weights (unchanged, verified bit-identical across the recalibration); the US mirror
  was independently backtested (60/40 train/test split, TEST tertile spread 14.4%→39.4%,
  p<1e-96) and re-weighted to 0.30/0.70/0.00 rel/depth/method (method-confirmation dropped —
  measured -12.2pt harmful on US data). The one-day historical analog is deliberately shown but
  visually de-emphasized (muted gray, not red/green) — it IS weakly predictive (36.7% vs 33.0%,
  p=0.011) but far weaker than the aggregate track record, so it no longer competes visually with
  the validated signal. See `analyze_reliability.py::test_score`/`test_analog_predictiveness`.
- **US-only high-conviction tiers** (`readiness.signal` = `"high_conviction"` / `"strong_breakout"`,
  gated by `settings.HC_ENABLED = MARKET == "US"`): a disciplined precision search (point-in-time
  trader features — closing range, cross-sectional RS percentile, breadth, base tightness/age, $
  liquidity, cross-method co-fires; train-only gate search, one-shot test evaluation) found
  **"squeeze-confirmed breakout"** (volatility-squeeze release + a recent Method-A breakout + ATR≥
  4.5% of price + not-chased + a liquidity floor) at **51.1% hit rate (52.0% held-out TEST)** —
  roughly double the US 26.7% base and past the 50% breakeven line for this project's strict 1:1
  reward:risk grading rule. A looser variant (`strong_breakout`, no squeeze/gap requirement) scored
  45.3%/46.2% TEST — real lift, kept as a second, lower-conviction (floor 80 vs 90) tier. **Any new
  live trigger built this way needs a fresh-fire/cooldown-dedup gate matching the backtest's**
  (`find_breakouts._last_is_fresh_fire()`) **or it silently multiplies how often it fires and
  dilutes the measured hit rate** — caught exactly this bug pre-ship (a naive port inflated tier 1
  from backtested n=190/51.1% to n=830/46.1%); always acceptance-replay through the real production
  code path before trusting a ported trigger. A follow-up rolling-window test found the edge decays
  over the following week if entry is delayed (46.2%→~32-40% by lag 3-5 for `high_conviction`), so
  the badge is deliberately NOT sticky/held for a week — see `analyze_hc_rolling_window.py`.
- **Breakout-method research framework** (`methods.py` + `analyze_reliability.py`, mostly
  research-only): implements alternative breakout/pre-breakout *definitions* B through H as trigger
  columns sharing the same cached indicator columns — VCP multi-leg contraction (B), volatility
  squeeze (C), trend-inception DI-cross (D/D2), relative strength vs benchmark (E/E2 — **E2 is the
  only one shipped to production**, folded into readiness as an independent `"relative_strength"`
  tier), episodic gap+volume pivot (F), and two comprehensive pre-breakout composites (G — Minervini
  Trend Template + CAN SLIM RS + VCP + institutional volume, 0-100 score; H — "Pressure Cooker"
  coiling score). All graded by the same +1R-before-stop rule via `_dedup_with_cooldown` (a fire
  cooldown so one continuous move isn't counted as many independent trials). Best-validated,
  non-shipped standalone finding: **G/G2 (a retuned version with base depth as a monotonic ramp
  instead of a band) hits 37.6%** on the US market, well above the 26.7% base but still below the
  50% breakeven line and below the already-shipped HC/SB tiers — kept as research, not wired into
  `run_scan.py`. A recurring, robustly-confirmed counterintuitive finding across this research:
  **volatility contraction alone is a weak-to-negative predictor**, in tension with the classic VCP
  thesis — **base depth is consistently the strongest real feature** in both markets. See the
  `multi-method-breakout-comparison` memory file for the full backtest history if extending this.
- **Ask AI** (`ask_ai.py` + `chat_server.py`, Groq-backed floating chat panel): a standard
  tool-calling model (not Groq's "compound" auto-tool system, which can't take custom tools) with
  four tools — `lookup_stock` (exact/fuzzy ticker or name resolution to full computed context),
  `search_stocks` (filtered/sorted slice of the whole universe), `run_sql` (read-only DuckDB SQL
  over every stock's flattened fields, for open-ended aggregate questions), and `web_search` (only
  invoked when genuinely needed, proxied to Groq's compound-mini so the scarce web-search budget
  isn't spent by default). `chat_server.py` is a local dev proxy so `GROQ_API_KEY` never reaches
  client-side JS; production needs the equivalent wired server-side (see the file's own docstring).
- **My Watchlist** (`api/watchlist.py`, a Vercel Python serverless function, Upstash Redis-backed):
  the personal add/track feature — one Redis hash keyed `"{market}:{symbol}"` (so the same ticker
  in two markets can't collide), gated by a single shared secret (`WATCHLIST_SECRET`) since there's
  only one real user. Name/current price are deliberately NOT stored — the frontend joins them from
  the already-loaded `breakouts.json` at render time. Graded periodically with the real +1R rule via
  the `grade-watchlist` Skill (never graded on raw 1-day price moves).
- **Live intraday overlay** (`api/quotes.py`, Vercel serverless): `GET /api/quotes?symbols=...` —
  price/prev-close/change% only (never readiness/ADX/resistance, which are anchored to the last
  *completed* daily close and would be conceptually wrong to recompute mid-session). Hits Yahoo's
  public v8 chart-meta endpoint directly (no yfinance/pandas — keeps the function's cold start
  light per Vercel's per-file bundling). Powers both the watchlist's few-minutes polling and the
  performance page's `● LIVE` overlay for still-open calls.
- **Performance page** (`performance.html` + `build_performance.py`, wired into `run_scan.py`): a
  forward-only ledger of every published call (breakout / relative-strength / US high-conviction
  tiers) from the conviction era onward — nothing backfilled. Each call tracked ~2 weeks, graded by
  the same +1R-before-stop rule, with the live intraday overlay for open calls. US high-conviction
  tiers only shown for the US ledger (India has none, `HC_ENABLED` gate).
- **`data/breakouts.json`** is the serving file the frontend reads. Written **compact** (no indent)
  since it's a machine-read artifact regenerated daily at whole-market scale — ~3.1 MB on disk,
  ~300 KB gzipped. Schema: top-level `generated_at`/`as_of_date`/`source`/`disclaimer`/`market_mood`
  (market-wide, NOT per-stock)/`stocks[]`; each stock has `price`, `ema_stack`, `adx`, `resistance`
  (now zone-based, see `levels.py`), `volatility`, `trend`, `breakout`, `readiness`
  (label/watch/score/**conviction**/signal — powers both the readiness cue and the default sort),
  `history`, and `entry`. Enrichment fields merged in from standalone fetch scripts: `sector`/
  `industry`, `analog`, `holdings`, `news`, `social`, `earnings`, `fundamentals`.
- **Data-branch serving architecture (2026-07-06) — the single biggest infra change since the
  JSON-driven rewrite.** Serving JSON no longer lives on `main` at all (`data/` is gitignored +
  untracked there). Instead, `.github/workflows/daily-scan.yml` (India, 10:30 UTC Mon–Fri) and
  `daily-scan-us.yml` (US, 21:30 UTC) each force-push a single fresh commit of the regenerated data
  to an **orphan `data` branch**. Both HTML files read a `DATA_BASE` constant: `data/` (or
  `data/us/`) on localhost/`file://`, else
  `https://raw.githubusercontent.com/Bhavanmehta/BreakoutAI/data/` (CORS-open, ~5min CDN cache) in
  production. The `api/` Vercel functions stay origin-relative — unaffected. This killed the
  unbounded git-history growth that whole-market daily commits to `main` used to cause (was TODO
  #4). **Windows gotcha**: `origin/main`'s pre-fix history still contains an old commit with
  `data/us/ohlc/CON.json` (a Windows-reserved device name) — checking out those specific old trees
  on Windows fails (`invalid path`); `main`'s *current* tree is data-less so a fresh clone is fine.
  `export_ohlc.py`/the frontend's `safeName()` now map reserved names (`CON` → `CON_.json`) so this
  can't recur.
- **Corporate-action adjustment** (India): raw NSE prices aren't split/bonus-adjusted.
  `adjust_for_splits.py` parses the event ratio from NSE's text and back-adjusts;
  `python backend/adjust_for_splits.py` self-tests the parser.
- **Pattern detection** (`patterns.py`): real geometry via swing pivots → Ascending Triangle, Cup &
  Handle, Double Bottom, Head & Shoulders, with fallbacks. **Confirmed non-predictive** by
  `analyze_reliability.py` (named patterns underperform "no clear pattern") — shown as decorative
  context only, not implying signal (see TODO #5).
- **Historical-analog engine** (`analogs.py` → each stock's `analog` field): z-scores a scale-free
  feature vector (EMA-stack geometry, coil ratio, ADX, distance-to-52w-high, distance-to-resistance)
  against the stock's own history and finds the nearest past bar with a full forward runway,
  returning date/similarity/actual forward return. Weakly predictive (see Conviction-score bullet)
  — shown de-emphasized, not as a bold verdict.
- **Sector / industry** (`sectors.py` + `fetch_sectors.py` → `sectors.json`): per-stock sector +
  industry from `yfinance.Ticker(...).info`. Powers the frontend sector filter + Sector Radar.
- **Fundamentals** (`fundamentals.py` + `fetch_fundamentals.py` → `fundamentals.json`): market cap,
  P/E, revenue/profit growth, ROE, D/E via yfinance `.info`. **ROCE is not a yfinance field at all**
  (India-screener-style metric) — deliberately skipped rather than adding a new scrape source for
  one field. **Currency/unit handling is market-aware**: `market_cap` is stored RAW in the stock's
  own native currency (no baked-in ₹-crore conversion), all scale/currency formatting pushed to the
  frontend keyed on `MARKET` — found and fixed a real bug where the US mirror initially inherited
  India's hardcoded ₹-crore division and showed US market caps mislabeled (e.g. Moderna as "₹3,165
  Cr"). Any future currency-denominated fundamentals field needs the same market-neutral-raw-value
  pattern, not a baked-in conversion constant.
- **Reliability caveat**: `readiness` carries `reliability`/`reliable` — a "primed" stock with weak
  historical follow-through gets an amber caution (now driven by the shrunk Bayesian estimate, see
  Conviction-score bullet, not a raw historical percentage).
- **Track record — the forward test** (`track.py`): logs every stock's daily call to
  `predictions_log.jsonl` (append-only) and grades each on-watch episode by the same +1R-before-stop
  rule → `track_record.json`. **The frontend's old track-record banner is retired in favor of the
  standalone Performance page** (see that bullet) — a cleaner, forward-only, per-call ledger.
- **Ownership / shareholding** (`fetch_holdings.py` → `holdings.json`): per-stock promoter/FII/DII/
  public % plus a real quarterly time series. Primary source `holdings_screener.py` (screener.in,
  ~12 quarters, no rate-limiting); NSE XBRL (`holdings.py`) kept as fallback. Card shows current
  snapshot bars plus a tabbed who's-accumulating-over-time chart (FII default).
- **News + sentiment** (`fetch_news.py` → `news.json`): four providers in priority order — GNews,
  Marketaux, NewsData.io, Google News RSS (no key/quota, reaches small/micro-caps the budgeted APIs
  miss). `sentiment.py` (VADER + a finance lexicon) scores every headline uniformly regardless of
  source; `event_classifier.py` layers ~19 keyword-categorized event types (order win, SEBI penalty,
  rating change, earnings beat/miss, etc.) blended with the VADER score. Refreshes daily.
- **Social buzz** (`fetch_social.py` → `social.json`): Reddit mention count + sentiment (needs a
  Reddit script-app key — not yet obtained, phase inactive) plus Google Trends search-interest
  (pytrends, no key). Same resumable, conviction-ordered populate pattern as fetch_news.
- **Market Mood Index** (`market_mood.py`, runs inside `run_scan.py`): a single market-wide 0–100
  fear/greed gauge — trend (index vs its 20-day SMA), VIX (inverted), FII/FPI net flow (India only,
  `HAS_FII_DII_FLOW`), and breadth (% of the scanned universe up today), any dropping out and the
  rest reweighting if a fetch fails.
- **Earnings** (`earnings.py` + `fetch_earnings.py` → `earnings.json`): quarterly EPS
  estimated-vs-actual (dumbbell chart). Two sources, never blended per stock: yfinance's earnings
  calendar (analyst-comparable, only ~40-50% coverage) falling back to the quarterly income
  statement's Basic EPS (~100% coverage, no forward estimate). Guards against `get_earnings_dates()`
  returning genuinely stale data (`STALE_DAYS`) — always verify a financial data source returns
  *current* data, not just non-empty data.
- **Reliability validation** (`analyze_reliability.py`, standalone, run manually): checks whether
  the "X% of past breakouts followed through" caveat is actually predictive, pooled across the whole
  universe (now well-powered, tens of thousands of graded events per market). Robust findings:
  **persistence is strongly significant** (trailing follow-through rate predicts the next breakout);
  **base depth is the most consistently strong feature in both markets**; **volatility
  contraction is weak-to-counterintuitive** (opposite the classic VCP thesis, confirmed repeatedly
  across both markets and multiple method variants — see the methods-research bullet); **ADX,
  volume-surge magnitude, and named chart patterns are not predictive**. This is the empirical basis
  for what's in/out of `score.py`'s conviction formula.

### Still TODO (in rough order)
1. ~~Tune the follow-through target~~ — done: R-multiple (+1R before -1R stop), order-aware,
   per-stock-scaled. `settings.FOLLOWTHROUGH_TARGET_PCT` no longer exists; tune via
   `settings.STOP_LOSS_FRACTION` / `FOLLOWTHROUGH_WINDOW` instead.
2. ~~Widen the universe~~ — done, whole-market for both India (~2,055 equities) and US (~4,668,
   independently widened past the original S&P500+Nasdaq100+Russell2000 seed).
3. ~~Frontend watchlist at scale~~ — done (conviction-ranked, search, "Primed only" toggle,
   fundamentals filter panel).
4. ~~Git / log growth at whole-market scale~~ — **done (2026-07-06)**: serving data moved off
   `main` entirely onto an orphan `data` branch, force-pushed fresh each run. See the Data-branch
   serving bullet. `predictions_log.jsonl` pruning is still informally handled by `track.py`
   rewriting the whole file each run rather than an explicit prune policy — revisit only if it
   becomes a real size problem.
5. **Retire or rework the pattern badge** — still open. `patterns.py`'s named-pattern badge remains
   confirmed non-predictive; still shown as decorative context, not folded into scoring. The
   validated features that *do* predict (persistence, base depth) are already in `score.py`.
6. ~~Fundamentals + sector~~ — **both done**: `sectors.py`/`fetch_sectors.py` (sector/industry
   filter + Sector Radar) and `fundamentals.py`/`fetch_fundamentals.py` (Market Cap/P-E/growth/
   ROE/D-E filter panel, ROCE deliberately excluded — not a yfinance field).
7. ~~Holdings layer~~ — **done**, real quarterly history via screener.in, tabbed over-time chart.
   Still display-only (not folded into scoring) — validating its predictiveness before doing so is
   the one remaining piece, same discipline already applied to everything in `score.py`.
8. **Chart migration — done.** The per-stock annotated overlay chart (`export_ohlc.py` +
   `lightweight-charts`, EMA8/21 + volume/RSI panes, with resistance/support zones, VCP pivots and
   breakout markers drawn directly on the primary candles) is now the **default** detail-pane chart
   (`chartMode = "annotated"`). The TradingView widget remains only as an opt-in toggle
   (`chartModeTradingview`) and as the automatic fallback for stocks that have no per-stock OHLC
   exported yet. Verified end-to-end via `_verify_frontend.py` (IN + US, zero console errors).
9. ~~Enable the GitHub Action~~ — **done**, and expanded to two workflows
   (`daily-scan.yml` India, `daily-scan-us.yml` US), both running on schedule and pushing to the
   `data` branch (see the Data-branch serving bullet) rather than committing to `main`.
10. **New, open**: fold G2 (the retuned pre-breakout composite, 37.6% US hit rate) or the
    validated `high_conviction`/`strong_breakout` sequential-confirmation combos into a future
    "early radar" panel, distinct from and additive to the existing conviction score — a product
    direction discussed but not yet built (see `multi-method-breakout-comparison` memory for the
    full backtest numbers if picking this up).
11. **New, open**: US grading currently uses the same fixed ±6%-of-resistance stop/target band as
    India even though it under-resolves for calm US large-caps (see the Breakout-definition
    bullet) — switching to an ATR-scaled band would raise the US base rate to a volatility-neutral
    ~42% but changes the displayed stop/history/track record sitewide; flagged as a live option,
    not decided.
12. ~~Card-UX cluster (competitor-ideas #1–#4)~~ — **done (Sprint 2)**: the "why" / rationale
    layer. `backend/signals.py::build_rationale(rec)` derives, per record, a two-column
    confirming/risk signal set, an edge-vs-risk **RSS** roll-up (`{edge, risk, net, confidence}`),
    a single **make-or-break** line, and advisory **gates** (liquidity / volume-confirm / uptrend /
    earnings-veto — thresholds in `settings.py`). Stored as `rec["rationale"]`. It is a
    **transparency layer only — never the ranker**; the conviction score in `score.py` stays purely
    technical (reliability + base depth + method). Wired into `run_scan.py` after the enrichment
    merges and backfilled onto both `breakouts.json` files. Frontend renders it in the detail pane
    via `renderRationale`/`renderMakeOrBreak`/`renderGates`; the block auto-hides when a record has
    no `rationale` (old JSON degrades to the classic read). Verified via `_verify_frontend.py`
    (IN + US, rich + sparse picks, zero new console errors).

When resuming, read `HANDOFF.md` first (session-by-session detail + what's committed vs. not), then
check `data/breakouts.json` / `data/us/breakouts.json` + `track_record.json` / `performance.json`
for current real output before assuming anything above is still pending.
