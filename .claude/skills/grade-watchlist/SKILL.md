---
name: grade-watchlist
description: Grade the personal "My Watchlist" picks (Upstash store) with the production +1R-before-−1R follow-through rule — never 1-day price moves — and report per-market results vs the backtested base rates. Use for "how did my picks do", the periodic watchlist checkback, or any watchlist-performance question.
---

# Grade the personal watchlist

The one rule that matters: **grade picks with the same ruler the backtests use** —
+1R before −1R within `settings.FOLLOWTHROUGH_WINDOW` (10) trading days. A 1-day
close-to-close % table is noise and has repeatedly tempted premature conclusions
(the 2026-07-06 Fri→Mon check was explicitly a placeholder until the real rule
could be applied). If the user asks for a quick daily look, give it, but label it
"not the grading rule" and say when the real grade lands.

## 1. Pull the picks

- Read `api/watchlist.py` (repo root `api/`) first for the current storage contract.
  As of 2026-07: one Upstash Redis hash, key `watchlist`, field `"{market}:{symbol}"`
  (`US:AAPL`, `IN:TCS`), value JSON `{symbol, market, date_added, entry_price}`.
- Simplest read path: POST `["HGETALL","watchlist"]` to `UPSTASH_REDIS_REST_URL`
  with the bearer token — creds in `backend/.env` (values may be quote-wrapped;
  `api/watchlist.py`'s `_env()` shows the tolerant parse).
- **Collapse duplicate re-adds**: same symbol stored twice = one pick. Keep the
  earliest entry with a genuine multi-day runway (precedent: BLUSPRING identical
  dupe, CUPID re-added same-day at what was effectively that day's close — the
  original ₹198.84 entry was the real pick).

## 2. Grade with the production rule

- Entry = stored `entry_price`. Win = price touches entry + 1R before it touches
  the stop, within 10 trading days. R = entry − stop, stop = that bar's
  `resistance × settings.STOP_LOSS_FRACTION`. Read `backend/settings.py` fresh —
  don't hardcode the constants.
- **Shortcut** (documented in `backend/analyze_hc_rolling_window.py`):
  `find_breakouts.add_indicators()` computes `followthrough` / `r_multiple`
  unconditionally for every bar, with entry = that bar's close. So: batch-fetch
  each symbol's history (`get_prices.fetch_prices_yfinance_batch`, remember
  `BREAKOUTAI_MARKET=US` for US names — run the two markets separately), run
  `add_indicators`, locate the bar matching `date_added`, read `followthrough`.
- **Only trust the column if the entry bar's close ≈ the stored `entry_price`**
  (within ~1%). If the pick was added intraday at a different price, grade
  manually from the stored entry with the same stop/target construction.
- **Lookahead completeness**: `followthrough` at bar *i* needs bars *i+1..i+10*
  to exist. If fewer than 10 bars have elapsed, the value is NOT final — report
  those picks as **in progress** (days elapsed, current price vs the +1R / −1R
  levels, whether either side has been touched so far).

## 3. Report honestly

Three-plus buckets, never just win/loss: **won** (+1R first), **lost** (stop
first), **expired unresolved** (10 days, neither side — expect MANY for US: with
the fixed ±6% band ~52% of US events resolve neither side, a known finding),
**in progress**.

Compare against the backtested base rates (re-check `HANDOFF.md`/memory for
current numbers; as of 2026-07: India Method-A 38.8%, E2 41.6%; US base 26.7%,
strong_breakout ~45%, high_conviction ~51%; 50% = breakeven at the rule's 1:1
reward:risk). With n in the tens, say so — quote the binomial noise (e.g. 38
picks → ±16pt 95% CI) rather than declaring an edge or a failure.

Render results as an artifact (load the dataviz skill first; keep the same
tiles + diverging-bar + sortable-table shape as the 2026-07-06 check for
continuity). Finish by updating the `watchlist-friday-monday-checkback` memory
(or its successor) with the date, the graded numbers, and the next check date.
