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
  → `github.com/Bhavanmehta/BreakoutAI`. **Do all work here** and commit/push from here.
- A second, non-git copy exists at `C:\Users\bhava\Projects\BreakoutAI` (the tool cwd). Don't
  confuse them; the OneDrive one is the source of truth. `.claude/settings.local.json` (perms,
  incl. PowerShell/Bash allowlist) lives in the Projects copy since that's what the session reads.
- **Python**: `C:\Users\bhava\AppData\Local\Programs\Python\Python312\python.exe` (not on PATH as
  `python`). **Git**: `C:\Program Files\Git\cmd` (add to `$env:Path`).
- **Run the pipeline**: `cd backend; python run_scan.py` (regenerates `data/breakouts.json`,
  `predictions_log.jsonl`, `track_record.json`, and the git-ignored DuckDB).
- **Preview the site**: from the repo root `python -m http.server 8000`, open
  `http://localhost:8000/combined_breakout_scanner_platform.html`. `file://` won't work (fetch is
  blocked); the frontend fetches `data/breakouts.json`.

## Current State

Frontend [combined_breakout_scanner_platform.html](combined_breakout_scanner_platform.html) is
**JSON-driven** — it fetches `data/breakouts.json` (no more hardcoded dataset). Single clean view
(the old 3-tab layout and the fake Fundamentals tab were removed). Watchlist builds itself from the
data and sorts by breakout readiness. Cards: verdict/readiness (+ trend & sentiment badges, detected
pattern, reliability caveat), historical precedents (worked/faded tags), resistance proximity, VCP,
entry guidance; an indicator strip (ADX + EMA values) sits above the chart. A "track record" banner
sits up top. Everything shown is real computed data.

## Backend (`backend/`) — the data "engine"

A Python pipeline now exists and produces real computed data (replacing the hand-typed
`dataset` for the fields it can derive from price/volume). See `backend/README.md` for details.

- **Files** (flat, named by what they do): `settings.py` (watchlist of 12 + all thresholds),
  `get_prices.py`, `adjust_for_splits.py`, `find_breakouts.py`, `patterns.py` (chart-pattern
  detection), `track.py` (forward track record), `run_scan.py` (the entry point).
- **Data sources**: `yfinance` (default; already split/bonus-adjusted, works in CI) with a
  `jugaad-data` + NSE-corporate-actions fallback path that we adjust ourselves
  (`adjust_for_splits.py`) — the latter is the whole-market-scalable route, tested but not the
  daily default.
- **Flow** (`backend/run_scan.py`): fetch adjusted prices → `find_breakouts.py` computes EMA stack
  (8/21/50/200), ADX, resistance/touches, VCP contraction, breakout detection + per-stock historical
  stats → store in local DuckDB (`data/market_research.duckdb`, git-ignored) → write `breakouts.json`.
- **Breakout definition** (grounded in Minervini Trend Template / Weinstein Stage 2 / Turtle, not
  invented): close above prior 50-day high, on ≥1.5× avg volume, **while in an uptrend** (above a
  *rising* 200 EMA and above the 50 EMA) and **within 25% of the 52-week high**. The trend + 52w
  gates are what stop false breakouts (bounces in a downtrend) from being counted — see the TODO note
  and `settings.REQUIRE_UPTREND`. "Follow-through" (did it work) = gained ≥5% within 10 trading days.
- **`data/breakouts.json`** is the committed serving file the frontend reads. Schema: top-level
  `generated_at`/`as_of_date`/`source`/`disclaimer`/`stocks[]`; each stock has `price`, `ema_stack`
  (each EMA: period/value/position/label), `adx`, `resistance`, `volatility`, `trend` (in_uptrend),
  `breakout` (today + sentiment), `readiness` (label/watch/score — powers the "breakout soon?" cue),
  `history` (past_breakouts, followthrough_rate, followthrough_label, avg_fwd_return_20d_pct,
  examples[] with per-event `worked` flag), and `entry` (trigger/entry/stop text).
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
- **Reliability caveat**: `readiness` now carries `reliability`/`reliable` — a "primed" stock with
  weak historical follow-through gets an amber caution so it never oversells.
- **Track record — the forward test** (`track.py`): logs every stock's daily call to
  `data/predictions_log.jsonl` (append-only, committed) and grades each on-watch *episode* once
  (dedup) by whether it gained +5% within 10 days → `data/track_record.json`. Seeded with a
  walk-forward simulation of recent history (source="simulated") so it isn't empty; live calls
  accumulate daily. The frontend shows a track-record banner.

### Still TODO (in rough order)
1. **Tune the follow-through target** — current "+5% within 10 days" is aggressive for slow
   large-caps, giving a low (~11%) track-record hit rate. Consider ~+3%/15d or per-stock scaling.
   (`FOLLOWTHROUGH_TARGET_PCT` / `FOLLOWTHROUGH_WINDOW` in `settings.py`.)
2. **Fundamentals** (P/E, ROE, mcap) — not produced yet; need a source before those fields return.
3. **Expand the universe** beyond the 12-stock watchlist toward full NSE/BSE via the `jugaad`
   bhavcopy + `adjust_for_splits.py` path.
4. **Chart migration**: TradingView widget → `lightweight-charts` v5 to draw pattern overlays
   (resistance line, handle pivot, breakout marker) directly on candles.
5. **Enable the GitHub Action** (Actions tab → "Daily breakout scan" → Run workflow) to confirm it
   works from GitHub's servers; yfinance can rate-limit cloud IPs (fallback = jugaad path).

When resuming, read `backend/README.md` and check `data/breakouts.json` + `data/track_record.json`
for current real output before assuming anything above is still pending.
