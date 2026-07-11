# Accuracy v2 — Triple-Barrier + Meta-Label Engine (Backtest Spec)

_Handoff spec for backtesting (Opus). Goal: replace the flat ±6% band with an
institutional-grade exit/entry/filter stack and quantify each layer's contribution.
Primary metric is **expectancy in R (net)**, NOT hit rate. A 40% hit rate at 2:1 payoff
beats a 55% hit rate at 1:1. Pre-register parameters below; log every run; no silent
re-tuning._

---

## 0. Diagnosis being tested (why stops are getting hit today)

| # | Hypothesis | Evidence so far |
|---|---|---|
| D1 | Flat ±6% band mismatches per-name volatility → noise stop-outs in high-ATR names, dead trades in low-ATR names | 51.7% of US events resolve neither barrier in 10 days; ATR-regrade lifted US base ~26.7%→~41.8% and flattened across vol buckets |
| D2 | Entry at signal close = buying extension; healthy pivot retests tag the stop | Untested — Layer E isolates it |
| D3 | Stop distance is arbitrary (entry−6%), not at thesis invalidation (below pivot) | Untested — Layer X isolates it |
| D4 | No execution-time regime gate; breakouts fire identically in chop | Regime-bucketed reliability not yet published |
| D5 | We take every qualifier instead of the cross-sectional best | Ranked list exists but threshold-based |

---

## 1. Architecture (6 layers; backtest each as an ablation)

**Layer S — Signal (unchanged).** Existing scanner output is the candidate stream.
Do not modify candidate generation; v2 is about entry, exits, and selection.

**Layer E — Entry protocol.** Test three modes per candidate:
- E0 (baseline/current): fill at signal close.
- E1: fill at next-bar open (this is also the honest-grading baseline).
- E2 (retest limit): limit order at the breakout pivot; live for 5 bars; cancel if
  untouched. Extension veto: if signal close is already > 1.0·ATR14 above pivot,
  E2-only (never chase).
- Record fill rate for E2 — missed winners are a real cost; count them.

**Layer X — Exit barriers (triple-barrier).** For entry price P, pivot V, ATR14 A:
- Profit barrier: P + 2.0·A  (sweep 1.5 / 2.0 / 2.5)
- Stop barrier: **max(V − 0.5·A, P − 1.5·A)** — structure-based with noise buffer,
  capped so risk never exceeds 1.5·A. (Sweep buffer 0.25/0.5/0.75.)
- Time barrier: 15 bars (sweep 10/15/20). At expiry, exit at close and record the
  *actual* return (not a scratch) — time-barrier exits are a return stream, not a loss.
- All fills next-bar-open after barrier touch; gaps fill at the gapped price (no
  fantasy fills at the barrier level).

**Layer R — Regime gate.** Trade/publish full size only when ALL of:
- Index (NIFTY / SPY per market) above its 50DMA;
- Breadth: >40% of scan universe above 50DMA (sweep 30/40/50);
- Vol regime: 20d realized index vol below its 1-year 80th percentile.
Otherwise mark candidates "hostile regime" (still logged, so we can verify the gate
earns its keep out-of-sample rather than just deleting history).

**Layer M — Meta-label model (the core upgrade).**
- Label: for every historical candidate (and every ledger row), y = 1 if profit barrier
  hit first, 0 otherwise, under the Layer X scheme.
- Features (setup quality, all known at signal time — audit for leakage):
  1. Extension at entry, in ATRs above pivot
  2. Base length (bars) and base depth (%)
  3. Volatility contraction ratio (last-third base range / first-third) — VCP proxy
  4. Breakout-day volume multiple vs 20d avg, and up/down volume ratio over base
  5. Closing range of breakout bar (close − low)/(high − low)
  6. RS percentile vs universe, 63d; sector RS percentile
  7. ADX(14); distance from 52w high; days since 52w high
  8. **Prior failed breakouts on this same base (count)** — institutions weight this hard
  9. Days to next earnings (and the GATE_EARNINGS_VETO_DAYS flag)
  10. Regime features: mood composite, breadth %, index vol percentile
  11. Liquidity: median daily turnover; gap % on breakout day
- Model: gradient-boosted trees (LightGBM/XGBoost) AND a plain logistic as sanity
  baseline. If GBT doesn't beat logistic out-of-sample, ship the logistic.
- Output: p̂ = P(win). Selection rule to test: (a) p̂ ≥ 0.45 cutoff, (b) top-K per day
  (K=5 IN / 5 US) by p̂ — institutions use cross-sectional top-K; test both.
- The published conviction score becomes a calibrated probability (this also feeds the
  Wilson/calibration work in SITE_CRITIQUE_AND_PLAN §2.2).

**Layer P — Sizing (for shadow portfolio / expectancy aggregation).**
Constant risk: each trade risks 0.5R where R = entry − stop. Portfolio expectancy is
then measured in R units, which makes IN and US directly comparable.

---

## 2. Evaluation protocol (non-negotiable)

1. **Walk-forward only.** Train on data ≤ T, embargo 10 bars, test forward; roll
   quarterly. No pooled random CV (leaks regime + overlapping barriers). Purge
   overlapping label windows (same stock, overlapping barrier spans) from train/test.
2. **Per-market separately** (IN, US). Never blend into one headline number.
3. **Costs:** entries/exits at next-bar open where specified; slippage haircut = 0.15%
   IN large-cap / 0.35% IN small-cap / 0.10% US liquid / 0.30% US thin (by turnover
   bucket); report gross AND net.
4. **Metrics reported per run:** expectancy (R, net) — primary; hit rate; avg win/avg
   loss; profit factor; time-barrier exit fraction; max drawdown of 0.5R-sized shadow
   book; alpha vs index over same holding windows; n per cell. No cell interpreted
   below n=50.
5. **Ablation table (the deliverable):** baseline (E0 + flat ±6%) vs +X vs +X+E vs
   +X+E+R vs +X+E+R+M — each layer must earn its keep in net expectancy or it doesn't
   ship. Also report per-regime (Greed/Neutral/Fear) rows.
6. **Anti-p-hacking:** parameter sweeps limited to the values pre-registered above;
   every run logged to a results table committed to the repo (including failures);
   final config chosen on train period only, then confirmed once on the untouched
   final test period. One confirmation shot — if it fails, back to the drawing board,
   not to the sweep.
7. **Success criteria to ship:** net expectancy ≥ +0.20R per trade with n ≥ 200 across
   walk-forward test windows in at least one market, stable sign across ≥70% of test
   windows, and meta-model calibration (predicted vs realized deciles) monotone.

## 3. Quick wins to ship regardless of backtest outcome
- Enforce the earnings veto at publish time (flag exists; make it binding).
- Extension veto: never publish "buy now" on names > 1 ATR above pivot; label "wait
  for retest" with the pivot price (D2 mitigation, zero model risk).
- Publish regime-bucketed hit rates (transparency now, gate later once validated).
- One-failed-breakout memory: if the same base already failed once, require higher
  volume multiple on the retry (feature #8 as a hard rule until the model ships).

## 3b. Evidence addendum — 2026-07-09 code + ledger deep-dive (READ FIRST)

Full read of find_breakouts.py / score.py / build_performance.py / settings.py plus an
autopsy of all 604 live episodes (168 IN + 436 US). These findings sharpen — and in two
places correct — the assumptions above:

**A1. ALL 40 resolved live calls resolved on bar 1.** Every `resolved_in` across both
markets (14 IN, 26 US: winners AND losers) equals 1. Median barrier width is 6.6% of
entry; the US HC tier *requires* ATR ≥ 4.5%/day (`HC_ATR_MIN_PCT`), so the ±1R band is
~1–1.5 daily ATRs on selected names → the grade is decided by the *next single bar's
direction*. The live record is currently measuring 1-day noise, not 10-day
follow-through. The HC "energy" gate is a patch that makes the fixed band resolvable —
re-examine whether it survives ATR-scaled grading (it may be selecting FOR noise).

**A2. Chasing is NOT the main problem — CORRECTION to D2.** Extension above pivot at
entry across all 604 published calls: median +0.7%, p75 ~2%, p90 ~3%. Only 18–23%
enter >2% extended. The stop is also already pivot-anchored (`resistance × 0.94`,
find_breakouts.py:119), not entry-anchored — better than assumed. Keep the >1-ATR
extension veto, but it's a minor lever here.

**A3. The ruler contaminates the feature science — the deepest issue.** "Neither
barrier hit" is graded as FAILURE in the backtest (find_breakouts.py:124), and
resolution probability scales with ATR under a fixed %-of-price band. So every
validated feature correlated with volatility gets mechanically inflated (base depth —
US weight 0.70! — deep bases are volatile stocks) and anti-correlated features get
punished (vol contraction "measured negative"; score.py's own docstring admits it
*reverses entirely* under ATR-neutral grading). **Therefore: the backtest deliverable
must re-validate EVERY feature under the new ruler — including previously REJECTED
ones (vol contraction, market regime, method co-fires) and previously ACCEPTED ones
(base depth, trailing reliability). Current weights are suspect until re-measured.**
Note: regime was tested and rejected only as a crude SPX-vs-200dma binary (-0.5pt,
p=0.52); test breadth % and vol-percentile forms before concluding.

**A4. Symmetric 1R:1R grading is structurally negative-EV.** Expectancy at the
measured base rates: IN 0.388×(+1) + 0.612×(−1) = **−0.22R**; US 0.267 → **−0.47R**;
even HC's 51.1% → +0.02R ≈ zero before costs. Under this rule the site can only win by
hit-rate selection above ~55% — near-impossible at scale. The profit-barrier sweep
(1.5/2.0/2.5·ATR) plus a trailing-exit variant is therefore not an optimization, it's
existential. Optimize expectancy, never hit rate.

**A5. Entry price is fictional by one overnight gap.** Grading enters at the signal
day's own close (find_breakouts.py:118; build_performance.py:193), but the scan runs
after the close — no user can get that fill. Next-bar-open grading (E1) is mandatory
for honesty, not just rigor.

**A6. Same-bar double-touch is silently graded as a loss.** `_grade` checks
`low ≤ stop` before `high ≥ target` within each bar (build_performance.py:165-169;
same in find_breakouts.py:126-131). With A1's day-1 wide bars, one bar can span BOTH
barriers and the intrabar order is unknowable from daily data. Count these
double-touch bars, report their share, and exclude or sensitivity-test them.

**A7. Live vs reference metrics are apples-to-oranges.** Live `win_rate` counts only
won/lost and EXCLUDES `expired` (build_performance.py:269), while the backtest
reference rates (26.7%/38.8%) count neither-resolved as failures. performance.html
shows these side by side. Fix: one outcome taxonomy {target, stop, expired} everywhere,
report all three shares, and grade expired episodes by their actual window return
(closes are already stored).

**A8. Survivorship in the 3y replay.** The universe is discovered from TODAY's
bhavcopy/listings and replayed 3 years back — delisted losers are absent, inflating
base rates. Document it; mitigate if a point-in-time listing source is cheap.

**A9. Add to the meta-label feature list:** cross-sectional RS percentile rank vs the
whole scan universe (IBD-style; methods.py explicitly notes it was skipped as a
"bigger lift" — it's a two-pass DuckDB query, and it is THE classic institutional
momentum feature), plus breakout-bar quality (closing range, gap vs intraday portion).

## 4. What this buys us strategically
The ledger + this pipeline = a self-improving loop no competitor has: every published
call generates a new labeled training row, the meta-model retrains quarterly, and the
calibration page proves it publicly. Retail sites sell static rules; this is a live,
audited learning system. That is the "very good accuracy" endgame — not a magic
indicator, but a machine that converts our own track record into edge.
