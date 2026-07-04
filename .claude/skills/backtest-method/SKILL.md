---
name: backtest-method
description: Backtest one or more breakout-detection method definitions (existing or new) against BreakoutAI's whole-market cached history via backend/analyze_reliability.py, with a smoke-test-first workflow and a plain-language results summary.
---

# Backtest a breakout-detection method

Use this whenever the ask is some form of "does this trigger/method actually work",
"try it with looser/tighter parameters", "test combos of methods", or "how accurate is
X compared to what we already have" — for the breakout-detection layer specifically
(`backend/methods.py`, `backend/find_breakouts.py`, `backend/analyze_reliability.py`).

This is retrospective/pooled backtesting, not live forward-testing — see
[[retrospective-over-forward-testing]] in memory: testing across years of cached
whole-market history is far more statistically powered than waiting on live daily
accumulation (~1 event/stock per ~10 trading days).

## 0. Ground yourself first

- Read `backend/settings.py` for existing thresholds (don't reinvent constants that
  already exist for a similar purpose).
- Read `backend/methods.py` to see which trigger functions already exist
  (`add_method_b_vcp`, `..._c_squeeze`, `..._d_trend_inception`, `..._d2_..._loose`,
  `..._e_relative_strength`, `..._f_episodic_pivot`, `add_all_methods`) before writing
  a new one — a new method is usually a new function in this file plus a registration
  in `analyze_reliability.py`'s `BASE_METHODS`/`COMBOS` dicts, not a new pipeline.
- Check memory's `multi-method-breakout-comparison` for what's already been tried and
  its results, so you don't re-run something already answered.

## 1. Smoke-test before any whole-market run

Whole-market runs batch-fetch ~2,000 tickers and take a couple of minutes — cheap
enough to run, but expensive enough that a logic bug should be caught first on a small
sample. Write a throwaway script (do not commit it) that runs the new/changed method
over ~10 liquid, well-known large-caps (e.g. TCS, RELIANCE, INFY, HDFCBANK, ITC,
TATASTEEL, SUNPHARMA, TITAN, LT, ICICIBANK) and prints fire counts per stock. Look
specifically for:
- **Event clustering** — a permissive trigger staying true for many consecutive days
  during one continuous move (not independent trials). If a method fires far more
  often per stock than a plausible number of distinct setups over the window, this is
  the likely cause — fix via `_dedup_with_cooldown()` (cooldown = `FOLLOWTHROUGH_WINDOW`
  from settings), applied before counting/grading.
- Obviously wrong values (NaN resistance/stop/target, negative prices, etc.).

Delete the throwaway script once it's served its purpose.

## 2. Run the whole-market backtest

```
cd backend
python analyze_reliability.py > ../scratch_reliability.log 2>&1
```

This grades every method/combo in `BASE_METHODS`/`COMBOS` against the same
+1R-before-stop-in-`FOLLOWTHROUGH_WINDOW`-days rule used for the production baseline
(Method A), so comparisons are apples-to-apples. It reports, per method: `n` (graded
events), `stocks` (distinct stocks that fired at least once), `events/stock`,
`hit_rate`, and a p-value vs. the Method A baseline — plus a Jaccard overlap matrix
(is this method catching different days than others, or redundant?), and
`print_examples()` output (concrete stock/date/price walkthroughs).

If testing a brand-new method or combo, add it to `BASE_METHODS`/`COMBOS` in
`analyze_reliability.py` first (mirror the existing entries' shape).

## 3. Interpret in plain language — always include these points

- **What `n` means and why methods differ wildly**: `n` is how many times that
  method's trigger fired (after cooldown dedup) across the whole market over the
  history window — a stricter/rarer method (e.g. a multi-condition trend-inception)
  will have a tiny `n` (dozens) next to a loose one (e.g. relative-strength new-high)
  firing thousands of times. Small `n` means read the hit rate as noise until it
  clears roughly `n>=20`, and treat anything under ~50 as "promising, not proven."
- **The 50% breakeven line**: the grading rule is a strict 1:1 reward:risk bet (target
  distance from entry equals stop distance), so 50% hit rate is breakeven *before any
  costs* — a 48.6% hit rate is not "almost a coin flip in our favor," it's slightly
  below breakeven. Always frame hit rates relative to this line, not just relative to
  the Method A baseline.
- **Statistical significance vs. the baseline** (the printed p-value) is a different
  question from "is this profitable" — a method can be significantly different from
  Method A and still be under 50%.
- **Combining methods is not automatically additive** — report actual measured combo
  hit rates (from `COMBOS`), not an estimate; a method with a good solo hit rate can
  wash back out when intersected with another (this happened with AE in the existing
  results — check memory before assuming a new combo will help).
- **Give live examples**: use/extend `print_examples()` to show 1-2 real stock/date
  walkthroughs per method being discussed — actual price, resistance, stop, target,
  and whether target-before-stop was hit within the window. Show target/stop as a %
  distance from entry, and label any same-day-N close as "for reference only" since
  the grading rule is path-dependent (intraday touch), not "the close N days later."

## 4. Record results

Append a new dated results block to the `multi-method-breakout-comparison` memory file
(don't overwrite prior runs — this is a running research log). If the session is also
ending, fold the summary into HANDOFF.md too (see the `wrap-session` skill).

## 5. Stay in research territory

Nothing here gets wired into `run_scan.py` or the served frontend until a method
clears both a meaningful sample size (`n` well past the noise threshold) *and* a hit
rate that's actually above the 50% breakeven line, ideally with significance vs. A.
Don't propose promoting a method to production based on a thin or sub-breakeven result.
