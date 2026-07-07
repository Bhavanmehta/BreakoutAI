---
name: ship-signal
description: Promotion discipline for putting any new live trigger, tier, or scoring change into production (find_breakouts.py / run_scan.py / score.py) after it backtests well. Use whenever a change alters what the site flags, badges, or how it ranks — the acceptance-test replay here has already caught one real shipped-would-have-been-wrong bug.
---

# Ship a signal to production

`/backtest-method` covers *researching* a method. This skill covers the step that
follows: wiring a validated signal into the live scan without silently changing
its statistics. Every step below exists because skipping it already caused (or
nearly caused) a real bug in this repo.

## 1. Backtest hygiene before any wiring

- Train/test split **by stock** (not by date), thresholds chosen on TRAIN only,
  **one** evaluation of finalists on TEST. A variant that dies on TEST is dead
  (precedent: the gap≥4% US tier looked great in-sample, 47.4% on TEST, excluded).
- Read `backend/settings.py` and `backend/methods.py` before inventing constants
  or trigger functions — most already exist.

## 2. The fresh-fire trap (the bug that actually happened)

Raw trigger columns (`is_breakout`, `is_breakout_c`, any new one) stay true on
consecutive days during one continuous move. The backtest counts only the first
day of each cluster (`_dedup_with_cooldown`, cooldown = `FOLLOWTHROUGH_WINDOW`).
A live anchor condition that reads the raw column therefore fires on days the
backtest never counted — and those extra days are *worse* days. Real magnitude:
US tier-1 inflated from backtested n=190/51.1% to live n=830/46.1% before the fix.

- Any live "is this firing today?" check must reimplement the same cooldown dedup.
  Pattern: `_last_is_fresh_fire()` in `backend/find_breakouts.py`. You cannot
  import it from `analyze_reliability.py` (circular import — it imports
  `add_indicators` from `find_breakouts`).

## 3. Acceptance test: replay through the REAL production code

Before shipping, replay the whole cached history through the actual imported
production helper (not a copy of the logic) and require n and hit rate to match
the backtest closely (precedent: tier 1 n=197/51.3% vs backtest 190/51.1%;
tier 2 exact). If they diverge, the production wiring is wrong — do not rationalize.
Also sanity-check the expected live cadence (e.g. "~6 high-conviction fires/month")
against a fresh `run_scan.py` + `grep -c` on the output JSON.

## 4. Cross-market regression

Shared code + market-gated features means an India change can leak into US and
vice versa. If the feature is for one market: run `build_summary()` on ~6 liquid
large-caps of the OTHER market and diff against the committed `breakouts.json` —
the bar is **byte-identical**. Market-specific thresholds live in `settings.py`
branched on `MARKET` (see the `HC_*` / score-calibration blocks), never inline.

## 5. Frontend + live verify

- `verdictExplainer` branch order matters: specific signals (e.g.
  `high_conviction`) must be checked BEFORE the generic `breakout.today` case,
  which they often coincide with. Check the detail header, watchlist-row pill,
  and both markets' rendering.
- Verify per the `verify-frontend` skill: real served page, Playwright, zero
  console errors.

## 6. Record it

Update the `multi-method-breakout-comparison` memory and `HANDOFF.md` with the
final numbers (backtest vs acceptance-replay vs first live day), and note the
expected cadence so a future session can spot drift. Commit on a branch, not
`main`, and only when the user asks.
