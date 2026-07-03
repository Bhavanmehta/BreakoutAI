# BreakoutAI — Session Handoff

_Last updated: 2026-07-03 (session 2). Read this + `CLAUDE.md` (durable project record) to resume.
When you start a fresh chat, point it here first._

## TL;DR of where things stand

This session added the **sector feature** and did a substantial **UI overhaul** driven by the
user's annotated-screenshot feedback, plus a **historical-analog engine** for "The Read" and
**quarterly ownership-history** support. The app still scans the whole NSE market (1,822 cards).

**Nothing is committed** — all changes are local/uncommitted in the working tree. Decide whether
to commit before/after the next session (user's setup: commit on a branch, not `main`).

## What we built/changed this session

1. **Sector layer** (`backend/sectors.py` + `fetch_sectors.py` → `data/sectors.json`): sector +
   industry per stock from `yfinance.info`. **Populated for all 1,822** (1,818 classified).
   `run_scan.py` merges it into each stock's `sector`/`industry`. `find_breakouts.build_summary`
   now emits `industry`.
2. **Historical-analog engine** (`backend/analogs.py` → each stock's `analog` field): finds the
   past bar most geometrically similar to today (z-scored EMA-stack geometry + coil + ADX +
   distance-to-52w/resistance) and reports what happened next. Wired into `build_summary`. 1,821/1,822
   have an analog. This is the evidence behind the new "The Read".
3. **Frontend overhaul** (`combined_breakout_scanner_platform.html`, full rewrite) — addresses all
   6 screenshot comments:
   - Top **filter/sort bar** (search · sector · sort · "Primed only") replaces the old watchlist strip.
   - **Vertical watchlist** on the left, each row an *overview* (symbol/Δ/name/price/dist-to-res/
     sentiment), click to drill in. Capped at 80.
   - **Sector Radar** panel (left) — which sectors have the most primed/breaking names; click to filter.
   - **ADX de-duplicated** — shown once (indicator strip), removed from the detail header.
   - **ⓘ tooltips** explaining ADX / EMA / VCP / Ownership in plain English.
   - **The Read** now leads with the analog hero ("today resembles {date}, +X% after").
   - **Ownership over time** — snapshot bars + a who's-accumulating trend (quarterly `history` when
     present, else the annual promoter trend). Verified in a headless (Playwright) render — no JS errors.
4. **Quarterly ownership history + Ownership UI rebuild** (screenshot round 2):
   - **New primary source: screener.in** (`backend/holdings_screener.py`) — its company page has a
     Shareholding Pattern table with ~12 quarters of Promoter/FII/DII/Public %. Solved the NSE
     rate-limit blocker (NSE kept 100%-blocking this IP; screener is reliable + quarterly). NSE XBRL
     path kept as fallback. `fetch_holdings.py` now tries screener first and re-fetches anything not
     yet screener-sourced. **Re-scrape running in the background** (readiness-prioritized, resumable) —
     top few-hundred done; run `python fetch_holdings.py` to finish the whole market.
   - **Ownership card redesigned** — snapshot bars + a **tabbed over-time chart defaulting to FII**
     (click Promoter/DII/Public), each a mini bar chart with the **% on top of every bar**, **quarter
     labels on the x-axis** ("Jun'24"…"Mar'26"), and a "▲/▼ X% over N quarters" delta. Degrades to the
     annual promoter trend for any not-yet-screener stock. (`renderOwnTrend`/`ownChartHTML`.)
   - **Fixed the ⓘ tooltip overflow** — the chart card's `overflow-hidden` was clipping the old
     CSS tooltip. Replaced with a single body-level `#floatTip` positioned by `initTooltips()`
     (portal-style, viewport-clamped, flips below when no room above).
5. **Universe hardening** (`backend/universe.py`): when the NSE bhavcopy fails, it now falls back to
   the previous `breakouts.json` symbol list (whole ~1,800 market) instead of collapsing to the
   12-name static watchlist — so a rate-limited discovery day can't shrink/overwrite the universe.
   This actually happened this session (bhavcopy was blocked) and the fallback carried the full 1,822.

## Key discussions / decisions (user chose the ambitious option on all three)
- **The Read** → build the **true historical-analog engine** (not just reframed copy). Done.
- **Ownership** → build **real quarterly FII/DII/promoter history** (parse multiple XBRLs). Code done;
  re-scrape pending NSE.
- **Sector** → **filter + Sector-Radar breadth overview** (both). Done.

## How to run (Windows)
- Python: `C:\Users\bhava\AppData\Local\Programs\Python\Python312\python.exe` (not on PATH).
  Git: add `C:\Program Files\Git\cmd` to `$env:Path`.
- Pipeline: `cd backend; python run_scan.py` → regenerates `data/breakouts.json`.
- Sectors: `cd backend; python fetch_sectors.py [limit]` → `data/sectors.json` (~10min full; done).
- Holdings (incl. quarterly history): `cd backend; python fetch_holdings.py [limit]` → `data/holdings.json`
  (**pending** — run when NSE isn't blocking; resumable, re-fetches snapshot-only entries to add history).
- Self-tests: `python analogs.py`, `python sectors.py`, `python holdings.py`.
- Preview: from repo root `python -m http.server 8000`, open
  `http://localhost:8000/combined_breakout_scanner_platform.html` (not `file://`).

## Pending / next
- **Finish `fetch_holdings.py`** (screener source) to cover the whole market — the background
  re-scrape had done the top few-hundred by readiness at session end; just re-run it to continue
  (resumable). Then re-run `run_scan.py` (or re-merge) so `breakouts.json` carries everyone's
  quarterly `history`.
- **Fundamentals** (P/E, ROE, mcap) — the one remaining part of old TODO #6; `yfinance.info` has them
  (same fetch-script pattern as sectors).
- **Enable the daily GitHub Action** (TODO #9) and confirm it survives yfinance rate-limits at
  whole-market scale from GitHub's IPs; also fixes the "Data as of" staleness.
- **Git/log growth** (TODO #4) — `breakouts.json` (~3.1MB/day) + `predictions_log.jsonl` still grow
  git unboundedly; move serving data off `main` / prune the log.
- **Search-relevance polish** (exact/prefix first) and a "sort search matches by relevance" pass.
- Retire the pattern badge fully (now labelled decorative); chart migration to `lightweight-charts`.

## New/changed files this session (all uncommitted)
- **New:** `backend/sectors.py`, `backend/fetch_sectors.py`, `backend/analogs.py`,
  `backend/holdings_screener.py`, `data/sectors.json`
- **Changed:** `backend/find_breakouts.py`, `backend/run_scan.py`, `backend/universe.py`,
  `backend/holdings.py`, `backend/fetch_holdings.py`, `backend/README.md`,
  `combined_breakout_scanner_platform.html`, `CLAUDE.md`, `data/breakouts.json`,
  `data/holdings.json`, `data/predictions_log.jsonl`, `data/track_record.json`, `HANDOFF.md`
