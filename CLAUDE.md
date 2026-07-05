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

- **Frontend**: single static HTML file + Tailwind (CDN) + vanilla JS. No build step.
- **Charting**: TradingView widget (`BSE:<TICKER>`). Migrating to `lightweight-charts` later for
  custom pattern overlays — see TODO.
- **Backend**: Python (pandas/numpy/duckdb/yfinance/jugaad-data) in `backend/`. Real, working.
- **Hosting**: Vercel / GitHub Pages, via GitHub CI/CD.

## IMPORTANT: repo location & how to run

- **Canonical repo (git remote + Vercel)**: `C:\Users\bhava\OneDrive\Documents\GitHub\BreakoutAI`
  → `github.com/Bhavanmehta/BreakoutAI`. **Do all work here** and commit/push from here. (A stale,
  non-git duplicate used to exist at `C:\Users\bhava\Projects\BreakoutAI` — it's been deleted; if
  it reappears, it's not the source of truth.)
- **Python**: `C:\Users\bhava\AppData\Local\Programs\Python\Python312\python.exe` (not on PATH as
  `python`). **Git**: `C:\Program Files\Git\cmd` (add to `$env:Path`).
- **Run the pipeline**: `cd backend; python run_scan.py` (regenerates `data/breakouts.json`,
  `predictions_log.jsonl`, `track_record.json`, and the git-ignored DuckDB).
- **Preview the site**: from the repo root `python -m http.server 8000`, open
  `http://localhost:8000/combined_breakout_scanner_platform.html`. `file://` won't work (fetch is
  blocked); the frontend fetches `data/breakouts.json`.

## Current State

Frontend [combined_breakout_scanner_platform.html](combined_breakout_scanner_platform.html) is
**JSON-driven** — it fetches `data/breakouts.json` (no more hardcoded dataset). Everything shown is
real computed data. The universe is the **whole NSE market** (~1,800 stocks that produce cards,
discovered daily — was a hand-typed 12) — see the Backend section.

**Layout (redesigned 2026-07 from the annotated-screenshot feedback):** a top **filter/sort bar**
(search · sector dropdown · sort [readiness/proximity/day-move/ADX/A→Z] · "Primed only" toggle),
then a two-column split: **left** = a **Sector Radar** (which sectors have the most primed/breaking
names, click to filter) + a **vertical watchlist** where each row is a compact *overview*
(symbol · Δ% · name · price · distance-to-resistance · sentiment), capped at `MAX_RESULTS`=80 and
click-to-drill; **right** = the selected stock's detail — a slim header (name · sector · price · Δ,
**no ADX here** — de-duplicated), then **The Read** (readiness + the historical-analog hero, see
`analogs.py`), the indicator strip (ADX + EMAs, each with a plain-English **ⓘ tooltip**) above the
TradingView chart, then Ownership (snapshot bars + a **tabbed FII-default over-time chart**),
Historical Precedents, Resistance, VCP, and entry guidance. The ⓘ tooltips are a single body-level
`#floatTip` positioned by `initTooltips()` (portal-style, viewport-clamped) so no card's
`overflow-hidden` can clip them. All vanilla JS: `applyFilters()`/`currentVisible()`
drive the list, `renderSectorRadar()`, `renderWatchlist()`, `renderAnalog()`, `renderHoldings()`
(quarterly `history` if present, else the annual promoter trend). The old horizontal chip strip and
the (already-hidden) track-record banner are gone.

## Backend (`backend/`) — the data "engine"

A Python pipeline now exists and produces real computed data (replacing the hand-typed
`dataset` for the fields it can derive from price/volume). See `backend/README.md` for details.

- **Files** (flat, named by what they do): `settings.py` (all thresholds + universe size),
  `universe.py` (discovers the scan universe), `get_prices.py`, `adjust_for_splits.py`,
  `find_breakouts.py`, `patterns.py` (chart-pattern detection), `analogs.py` (historical-analog
  engine behind "The Read"), `track.py` (forward track record), `holdings_screener.py` (primary
  quarterly ownership source, screener.in) + `holdings.py` (NSE-XBRL fallback) + `fetch_holdings.py`,
  `sectors.py` + `fetch_sectors.py` (sector/industry), `run_scan.py` (the entry point),
  `analyze_reliability.py` (standalone validation script).
- **Universe** (`universe.py`): the scan list is discovered fresh every run, not hand-typed. One
  bhavcopy request via `jugaad-data` (`jugaad_data.nse.bhavcopy_raw`) returns every listed NSE
  equity's turnover for the latest trading day; filtered to real equity shares (ISIN prefix `INE`
  — excludes ETFs/fund units like LIQUIDBEES, which share the `EQ` series but aren't companies) and
  ranked by turnover. `settings.UNIVERSE_SIZE = None` means **whole market** (~2,055 equities;
  ~1,800 pass the history-length gate and produce cards); set it to an int to keep only the top-N,
  and/or raise `settings.MIN_TURNOVER` (default 0) to drop the illiquid tail. If bhavcopy discovery
  fails (NSE rate-limits aggressively), it now falls back to the **previous `breakouts.json`'s symbol
  list** (`_universe_from_last_scan()` — preserves the whole ~1,800-name market) and only then to
  `settings.FALLBACK_WATCHLIST` (the hand-picked 12) — so a bad NSE day can't silently shrink and
  overwrite the served universe down to 12. This is the *only* jugaad/NSE-scraping touchpoint in the
  daily flow — price history comes from `yfinance`.
- **Data sources**: `yfinance` (default; already split/bonus-adjusted, works in CI). At whole-market
  scale we **batch-fetch** — `get_prices.fetch_prices_yfinance_batch()` pulls ~100 tickers per
  `yf.download` call (~30× faster and far fewer requests than one-at-a-time; whole market fetches in
  ~70s vs. ~10min looping). `run_scan._fetch_all()` uses the batch path for yfinance and falls back
  to a per-symbol loop for the `jugaad` price source (which has no batch API). Single-symbol
  `get_prices()` and the `jugaad-data` + NSE-corporate-actions adjustment path (`adjust_for_splits.py`)
  still exist — tested but not the daily default (see `settings.PRICE_SOURCE`).
- **`settings.MIN_HISTORY_BARS`**: recently-listed/demerged symbols (e.g. a spin-off with 2 weeks of
  trading) can now surface from the wider universe. `ema()` never returns NaN regardless of history
  length, so a naive dropna doesn't catch them — `build_summary()` explicitly requires
  `MIN_HISTORY_BARS` (needs the 200-EMA "rising" check window and a genuine 52-week high) before
  producing a card, and skips the stock otherwise ("not enough history").
- **Flow** (`backend/run_scan.py`): fetch adjusted prices → `find_breakouts.py` computes EMA stack
  (8/21/50/200), ADX, resistance/touches, VCP contraction, breakout detection + per-stock historical
  stats → store in local DuckDB (`data/market_research.duckdb`, git-ignored) → write `breakouts.json`.
- **Breakout definition** (grounded in Minervini Trend Template / Weinstein Stage 2 / Turtle, not
  invented): close above prior 50-day high, on ≥1.5× avg volume, **while in an uptrend** (above a
  *rising* 200 EMA and above the 50 EMA) and **within 25% of the 52-week high**. The trend + 52w
  gates are what stop false breakouts (bounces in a downtrend) from being counted — see
  `settings.REQUIRE_UPTREND`. "Follow-through" (did it work) = price hit **+1R before -1R (stop)**
  within 10 trading days, where R = entry − stop and stop = resistance × `STOP_LOSS_FRACTION`
  (~6% risk, same stop the entry guidance shows) — an R-multiple, not a fixed %, so it scales
  per-stock instead of grading low-vol large-caps as failures and high-beta names as wins by
  construction; it's also order-aware (a drop through the stop that later recovers no longer
  counts as "worked"). See `add_indicators()` in `find_breakouts.py` and `settings.py`.
- **`data/breakouts.json`** is the committed serving file the frontend reads. Written **compact**
  (no indent) since it's a machine-read artifact regenerated daily at ~1,800 stocks — ~3.1 MB on
  disk, but ~300 KB gzipped over the wire (Vercel/GH Pages gzip automatically), so a single fetch is
  fine and we deliberately did *not* shard it into per-stock files. Schema: top-level
  `generated_at`/`as_of_date`/`source`/`disclaimer`/`market_mood` (the market-wide gauge, see the
  Market Mood Index bullet below — NOT per-stock)/`stocks[]`; each stock has `price`, `ema_stack`
  (each EMA: period/value/position/label), `adx`, `resistance`, `volatility`, `trend` (in_uptrend),
  `breakout` (today + sentiment), `readiness` (label/watch/score — powers the "breakout soon?" cue),
  `history` (past_breakouts, followthrough_rate, followthrough_label, avg_fwd_return_20d_pct,
  examples[] with per-event `worked` flag), and `entry` (trigger/entry/stop text). Enrichment fields
  merged in from the standalone fetch scripts: `sector`/`industry` (from `sectors.json`), `analog`
  (from `analogs.py`), `holdings` (from `holdings.json`, incl. a quarterly `history[]` series
  once re-scraped), `news` (from `news.json`, see the News + sentiment bullet below), `social`
  (from `social.json`), and `earnings` (from `earnings.json`, see the Earnings bullet below).
- **Frontend is now wired to `breakouts.json`** (no more hardcoded dataset). Watchlist builds itself
  and sorts by readiness; cards: verdict/readiness + trend badge, historical precedents (with
  worked/faded tags), resistance proximity, VCP, entry guidance; indicator strip (ADX + EMA values)
  sits above the TradingView chart. Chart uses `BSE:` symbols (license-friendly; NSE hits a
  "only available on TradingView" wall in the free widget). Open via a local server, not file://.
- **Zero-backend flow**: `.github/workflows/daily-scan.yml` runs the pipeline after market close
  (10:30 UTC, Mon–Fri) and commits the refreshed JSON — no always-on server. Manually triggerable
  from the Actions tab.
- **Corporate-action adjustment**: raw NSE prices aren't split/bonus-adjusted (causes fake ~50%
  cliffs, e.g. Reliance's Oct-2024 1:1 bonus). `adjust_for_splits.py` parses the event ratio from
  NSE's text and back-adjusts; `python backend/adjust_for_splits.py` self-tests the parser.
- **Pattern detection** (`patterns.py`): real geometry via swing pivots → Ascending Triangle,
  Cup & Handle, Double Bottom (bullish), Head & Shoulders (bearish), with fallbacks. Heuristic;
  each stock's `pattern` field carries name/confidence/direction/description. Cup&Handle triggers
  a bit loosely — tighten if needed.
- **Historical-analog engine** (`analogs.py` → each stock's `analog` field): the evidence behind
  "The Read". For today's bar it builds a scale-free feature vector (EMA-stack geometry, coil
  ratio, ADX, distance-to-52w-high, distance-to-resistance) from columns `add_indicators()` already
  computes, **z-scores each feature across the stock's own history**, and finds the nearest past bar
  (Euclidean) that has a full forward runway and isn't in the recent overlapping window. Returns its
  date, a 0–1 `similarity`, and the actual `fwd_5/10/20d_pct` + `worked` — "today most resembles
  {date} ({sim}%); it then moved +X% in 20d". Rejects matches beyond a z-distance threshold (shows
  nothing rather than a bad precedent). Cheap/vectorized; whole-market runtime barely moves. NB: the
  single-symbol `get_prices()` path keeps today's still-forming NaN-close bar, so the engine queries
  from the last *complete* bar, not blindly `iloc[-1]`.
- **Sector / industry** (`sectors.py` + `fetch_sectors.py` → `data/sectors.json`): per-stock sector
  + industry from `yfinance.Ticker(sym+'.NS').info` (~0.3s/stock, whole market ~10min; ~1,818/1,822
  classify). Standalone + resumable like holdings; driven by the latest `breakouts.json` symbol list
  so it needs **no NSE call**; caches misses. `run_scan.py` merges it into each stock's `sector`
  (curated `FALLBACK_WATCHLIST` labels win) + `industry`. Powers the frontend sector filter + Sector
  Radar (the "which groups are heating up" breadth view). The frontend derives the broad *group* by
  splitting the display sector on `" · "`.
- **Reliability caveat**: `readiness` now carries `reliability`/`reliable` — a "primed" stock with
  weak historical follow-through gets an amber caution so it never oversells.
- **Track record — the forward test** (`track.py`): logs every stock's daily call to
  `data/predictions_log.jsonl` (append-only, committed) and grades each on-watch *episode* once
  (dedup) by the same +1R-before-stop rule as above → `data/track_record.json`. Seeded with a
  walk-forward simulation of recent history (source="simulated") so it isn't empty; live calls
  accumulate daily. **The frontend track-record banner is currently HIDDEN** (`#trackBanner` has
  `hidden`, `renderTrackRecord()` call commented in `loadData`) — the forward sample is still tiny
  (27), mostly simulated, and the sub-breakeven headline number undersells the tool. Re-enable once
  live calls mature, ideally reframed around the measured readiness edge (see analyze_reliability).
- **Ownership / shareholding** (`fetch_holdings.py` → `data/holdings.json`): per-stock promoter / FII
  / DII / public % **plus a real quarterly time series** (`history[]`). **Primary source is now
  `holdings_screener.py` (screener.in)** — its company page renders a Shareholding Pattern table with
  ~12 quarters of Promoter/FII/DII/Public %; we parse it via the stable classification keys in each
  row's `Company.showShareholders('foreign_institutions', ...)` onclick. It's far more reliable than
  NSE (which rate-limits hard and only cleanly surfaces annual promoter %) and gives quarterly FII/DII
  directly. The **NSE XBRL path (`holdings.py`) is kept as a fallback** — it reads the
  `corporate-share-holdings-master` API + parses the SHP XBRL's aggregate rollup contexts
  (`InstitutionsForeign`=FII, `InstitutionsDomestic`=DII, `MutualFundsOrUTI`=MF); `history_points=N`
  parses the recent N XBRLs. `fetch_holdings.py` tries screener first, then NSE, and re-fetches any
  entry not yet screener-sourced. Note screener's top-level table has no MF split (it's under DIIs),
  so `mf` is null on screener rows. **Quarterly-slow, so it's decoupled from the daily scan**:
  `fetch_holdings.py` is a
  standalone, resumable populate script (prioritizes by readiness, saves incrementally); `run_scan.py`
  just merges `holdings.json` into each stock's `holdings` field if present (stocks without an entry
  carry `holdings: null`). **The redesigned card (2026-07)** shows the current snapshot bars **plus a
  tabbed who's-accumulating-over-time chart** — defaults to **FII**, click Promoter/DII/Public to
  switch; each is a mini bar chart with the % on top of every bar, quarter labels on the x-axis
  ("Jun'24"…"Mar'26"), and a "▲/▼ X% over N quarters" delta. Frontend `renderOwnTrend()`/`ownChartHTML()`;
  it normalizes either the screener `history[]` (multi-category) or an older NSE `promoter_trend`
  (promoter-only, degrades gracefully). NOTE: per-stock *daily* FII/DII flow is not public in India;
  only these quarterly holdings + threshold bulk/block deals exist. **`holdings.json` is being
  re-scraped from screener** (readiness-prioritized, resumable) — top ~few-hundred done, run
  `python fetch_holdings.py` to finish the whole market.
- **News + sentiment** (`fetch_news.py` → `data/news.json`, merged into each stock's `news` field):
  headlines from four providers run in this priority order, each skipping whatever an earlier one
  already refreshed today so they extend coverage rather than duplicate it — **GNews** (phase 1, 100
  free req/day, undelayed, spent on the highest-conviction names first), **Marketaux** (phase 2, ~100
  free req/day), **NewsData.io** (phase 3, ~12h-delayed but 200 free credits/day so it reaches
  furthest), and **Google News RSS** (phase 4, no key/quota — `news_providers.fetch_gnews` /
  `fetch_marketaux` / `fetch_newsdata` / `fetch_rss`). RSS is the one source that reaches small/
  micro-caps the three budgeted APIs return nothing for (confirmed live). NOTE: Google's RSS feed
  terms restrict it to personal/non-commercial use in a feed reader — fine for this project's
  current free/educational use, revisit if ever monetized. Finnhub is deliberately not used (US-only
  free tier). Sentiment is never taken from a provider — `sentiment.py` scores every cached headline
  itself (VADER + a hand-picked `FINANCE_LEXICON`) uniformly regardless of source, so coverage
  doesn't depend on a provider's own (fragile) entity-tagging. Layered on top:
  **`event_classifier.py`** — ~19 ordered keyword categories (order win, SEBI penalty, rating up/
  downgrade, earnings beat/miss, buyback, promoter pledge, management exit, ...) each with a small
  signed bias, blended with the VADER score (weighted toward VADER when it's already confident,
  toward the event when VADER reads near-neutral — that's exactly where a word-level scorer has
  nothing useful to say). `sentiment.score_texts()` returns `{score, label, event}`; `event` is
  whichever headline's classified event swung the blended score most, surfaced in the frontend next
  to the sentiment badge so "Bullish" isn't a black box. News is time-sensitive (unlike holdings/
  sectors/fundamentals) so it refreshes daily rather than skip-if-present. GitHub Action needs
  `GNEWS_API_KEY`/`MARKETAUX_API_KEY`/`NEWSDATA_API_KEY` as repo secrets (RSS always runs regardless).
- **Social buzz** (`fetch_social.py` → `data/social.json`, merged into each stock's `social` field):
  Reddit mention count + `sentiment.py` score across India-trading subreddits (needs a free Reddit
  script-app key — not yet obtained, so this phase is currently inactive) plus a Google Trends
  search-interest score (pytrends, unofficial, no key). StockTwits skipped (near-zero NSE/BSE
  coverage). Same conviction-ordered, resumable, incremental-save pattern as fetch_news.py.
- **Market Mood Index** (`market_mood.py`, runs *inside* `run_scan.py` — not a separate script,
  since it's cheap and time-sensitive unlike holdings/sectors/fundamentals): a single market-wide
  0–100 fear/greed gauge in `breakouts.json`'s top-level `market_mood` field (NOT per-stock — shown
  as a header badge, `renderMarketMood()`). Four equally-weighted components, any of which drops out
  and the rest reweight proportionally if it fails to fetch: **trend** (Nifty close vs its 20-day
  SMA — reuses the `benchmark` already fetched for Method E, no extra call), **vix** (India VIX,
  `^INDIAVIX` via yfinance, inverted), **fii_flow** (today's NSE-published market-*wide* aggregate
  FII/FPI net equity flow, `nseindia.com/api/fiidiiTradeReact` via a plain `requests.Session()` —
  no cookies/auth needed in practice; z-scored against its own trailing 21-day history persisted in
  `data/fii_dii_history.json`, capped to 90 days), **breadth** (% of the whole scanned universe that
  closed up today — classic advance/decline, reuses data the scan already computes rather than
  fetching separate NSE sector indices). NOTE: this is market-wide aggregate flow only — per-stock
  daily FII/DII is not public anywhere in India (see the Ownership bullet above); a genuinely
  different, coarser data point from the quarterly per-stock holdings.
- **Earnings** (`earnings.py` + `fetch_earnings.py` → `data/earnings.json`, merged into each
  stock's `earnings` field): quarterly EPS estimated-vs-actual, shown as a dumbbell/dot-plot card
  (one hue, two shades — dim "Estimated", bright "Actual" — left of "one similar-looking day",
  which was shrunk to half-width to make room). Two sources, never blended within one stock's
  history: (1) yfinance's earnings calendar (`get_earnings_dates`) — analyst-comparable EPS
  estimate/actual/surprise%, but only ~40-50% of this project's actual watchlist has ANY analyst
  coverage (tested against the top-30 conviction list — micro-caps like CUPID/NPST had none); (2)
  fallback to the quarterly income statement's "Basic EPS" line — unadjusted GAAP EPS, available
  for ~100% of stocks (every listed company reports EPS regardless of analyst coverage), no
  forward estimate. When a quarter/stock has no estimate, the actual EPS still gets plotted rather
  than leaving a gap — only the estimate dot is skipped. **Known gotcha**: `get_earnings_dates()`
  can return genuinely **stale** data for a stock even when it returns real-looking rows — SUVEN's
  freshest row there was from Feb 2020 (six years old) despite it reporting quarterly ever since;
  ~31% of stocks tested had a last-quarter-reported date over a year old. `earnings.py` guards
  against this (`STALE_DAYS`) by falling back to the income-statement source when the calendar's
  most recent reported quarter is too old to trust. Quarterly-slow reference data like holdings/
  sectors/fundamentals — fetched by the standalone script, not the daily scan; whole-market
  populate takes ~25-30 min (yfinance per-symbol calls), and a fraction of stocks land on
  `actual_only` from transient yfinance flakiness during a mass run even when richer estimate data
  is really available (re-fetching that single symbol later usually recovers it — not worth a
  special retry mechanism for what's a minor completeness gap, not a correctness bug).
- **Reliability validation** (`backend/analyze_reliability.py`, standalone — not part of
  `run_scan.py`, run manually; batch-fetches, ~2min on the whole market): checks whether the
  per-stock "X% of past breakouts followed through" caveat is actually predictive, pooled across the
  whole universe. Two tests: (1) persistence — does a stock's trailing follow-through rate predict
  its *next* breakout?, and (2) features — do ADX / vol_contraction / distance-from-52w-high /
  volume-surge / base depth / pattern type predict follow-through pooled across all stocks? **Now
  very well-powered** (17,695 graded events across the whole market, up from ~127/12 originally).
  Robust, consistent findings: **persistence is strongly significant** (p<0.001 — low-trailing-rate
  stocks hit 32%, high-trailing-rate hit 46%, so the UI's reliability caveat is well earned);
  **base depth** is robust (p<0.001, deeper bases follow through *more*, 41% vs 35% shallow);
  **vol_contraction** is significant but *counterintuitive* (less contraction slightly beats more —
  opposite the VCP thesis, but consistent across samples). Weak/unreliable: distance-from-52w-high
  is significant but its *direction flipped* between the 280-stock and whole-market runs. Not
  predictive: **ADX**, **volume-surge magnitude**, and **named chart patterns** — "No clear pattern"
  (41% hit rate) beat every named pattern including Cup & Handle (36%) and Tight Consolidation
  (21%), so `patterns.py`'s badge is decorative, not predictive; strong argument to stop letting it
  imply signal (see TODO).

### Still TODO (in rough order)
1. ~~Tune the follow-through target~~ — done: replaced the fixed "+5% within 10 days" with an
   R-multiple (+1R before -1R stop), order-aware, per-stock-scaled. See the breakout-definition
   bullet above. Track-record hit rate moved 11% → 18% and the per-stock spread compressed
   (was 0.0–0.75, now 0.0–0.36), consistent with the old target being volatility-biased rather
   than measuring setup quality. `settings.FOLLOWTHROUGH_TARGET_PCT` no longer exists; tune via
   `settings.STOP_LOSS_FRACTION` / `FOLLOWTHROUGH_WINDOW` instead.
2. ~~Widen the universe~~ — done, then taken all the way to **whole-market**: `universe.py` now
   discovers every NSE equity (~2,055) from the daily bhavcopy (`UNIVERSE_SIZE = None`), ~1,800
   produce cards; `get_prices` batch-fetches so the full run is ~95s. Confirmed strong, significant
   signal in `analyze_reliability.py` at 17,695-event scale (see the Reliability-validation bullet).
3. ~~Frontend watchlist at scale~~ — done: the chip strip shows the top 12 by readiness by default
   with a search box for the rest (`renderWatchlist`, `TOP_N`/`MAX_RESULTS`). Possible next polish:
   rank search matches by relevance (exact/prefix first) rather than readiness, and/or a "primed
   only" toggle.
4. **Git / log growth at whole-market scale** (new tech debt from #2): `data/breakouts.json` (~3.1MB,
   changes daily → ~0.75GB/yr of git history) and `data/predictions_log.jsonl` (append-only, now
   ~1,800 lines/day, and `track.py` rewrites the whole file each run) both grow unboundedly in a
   committed-daily-data model. Not urgent pre-beta, but the real fix is to stop committing serving
   data to `main` (build it as a deploy artifact / on an orphan `data` branch) and to prune the
   predictions log to unevaluated + recent-N rows.
5. **Retire or rework the pattern badge** — `analyze_reliability.py` shows named chart patterns don't
   predict follow-through (they slightly *under*-perform "no clear pattern"). The UI now labels it
   "Chart pattern" as decorative context (de-emphasized under The Read) rather than implying signal,
   but it's still shown. Fully retiring it, and folding the features that *do* predict (trailing-rate
   persistence, base depth) directly into `readiness`/`reliability` scoring, is still open. The new
   `analog` field is the first validated-style "evidence" surfaced in its place.
6. ~~Fundamentals + **sector**~~ — **sector done** (2026-07): `sectors.py` + `fetch_sectors.py` →
   `data/sectors.json`, merged into `breakouts.json` (`sector`/`industry`); powers the frontend
   sector filter + Sector Radar. See the Sector bullet. **Fundamentals (P/E, ROE, mcap) still not
   produced** — `yfinance` `.info` also carries these as a candidate source (same fetch script pattern).
7. ~~Holdings layer (FII/DII/MF/promoter)~~ — **built with real quarterly history**; the redesigned
   card shows ownership **over time** (tabbed FII-default chart, % on bars, quarter x-axis). Solved the
   NSE rate-limit blocker by switching the **primary source to screener.in** (`holdings_screener.py` —
   12 quarters of Promoter/FII/DII/Public, no rate-limiting), NSE XBRL kept as fallback. Remaining:
   (a) **finish the re-scrape** — `fetch_holdings.py` is repopulating `holdings.json` from screener,
   readiness-prioritized; run it to cover the whole market; (b) still display-only — validate
   predictiveness (`analyze_reliability.py`-style) before letting rising-holding influence scoring.
8. **Chart migration**: TradingView widget → `lightweight-charts` v5 to draw pattern overlays
   (resistance line, handle pivot, breakout marker) directly on candles.
9. **Enable the GitHub Action** (Actions tab → "Daily breakout scan" → Run workflow) to confirm it
   works from GitHub's servers at the new whole-market scale (was tested at 12); yfinance can
   rate-limit cloud IPs (fallback = jugaad path). Local whole-market run is ~95s, so runtime isn't
   the concern — only whether GitHub's IPs get throttled by Yahoo differently, and whether the daily
   commit size (#4) is acceptable.

When resuming, read `backend/README.md` and check `data/breakouts.json` + `data/track_record.json`
for current real output before assuming anything above is still pending.
