# Selection-Method Analysis — Cross-Market Findings & Gate Recommendations

Source: `scratch/analyze_by_selection.py` run against `scratch/data/us_breakouts.json`-derived
cached event tables (`events_in.parquet`, `events_us.parquet`), full output in
`scratch/selection_analysis.log`.

- IN: 92,137 events, 1,812 stocks, baseline hit rate 41.1%
- US: 200,824 events, 4,158 stocks, baseline hit rate 36.0%

This directly answers "how the stock was actually picked/filtered from the universe"
(method that fired, combo stacking, cofire confirmation, and the raw selection
features used at scan time: RS ratio, vol contraction, distance from 52w high, ADX,
volume surge, base depth, analog match, pattern type) — not just downstream
display fields (price, score, stop %).

## 1. Method / combo hit rate (vs baseline)

Consistent across BOTH markets (large n, same direction — trust these):

| Method | IN Δ (n) | US Δ (n) | Verdict |
|---|---|---|---|
| `A_donchian_minervini` | -2.2pp (9,555) | -5.2pp (19,892) | **Net negative in both markets, large n → cut or heavily downweight** |
| `M_shakeout_rebreak` | -4.6pp (4,180) | -4.7pp (9,934) | **Net negative in both markets → cut or downweight** |
| `G_pre_breakout_composite` | +3.0pp (1,694) | +2.8pp (5,449) | Reliable edge, keep |
| `G2_pre_breakout_retuned` | +2.7pp (2,180) | +5.3pp (4,369) | Reliable edge, keep — strongest in US |
| `E_relative_strength` / `E2_...uptrend` | ~0 to +0.6pp (large n) | +2.3–2.5pp (large n) | Neutral-to-positive core method, fine as a base filter but not a strong edge alone |
| `HC_tier1_high_conviction` / `L2_hc_deep_base` | +4.5–4.7pp (n~350) | +7.6–7.8pp (n~730) | Strong edge, but n modest — good candidate for a "high conviction" gate tier |
| `F_episodic_pivot` | -5.7pp (1,543) | -4.5pp (2,350) | **Net negative in both markets → cut** |

Market-specific / inconsistent (don't generalize):
- `I_volume_profile`: +2.3pp IN (n=7,279) but only +0.5pp US (n=16,078) — weak, not a strong standalone signal.
- `C_squeeze`: -1.7pp both markets, modest n — mild negative, borderline cut.
- `AE_combo`, `AI_combo`, `AJ_combo`: negative in both, but these are combos involving `A_donchian_minervini`, which is already flagged as negative — the combo weakness is likely just inherited from A, not evidence combining hurts independently.

## 2. Does stacking signals (combos) help?

- IN: base methods weighted hit 41.0% vs combos 41.5% — combos roughly a wash (+0.5pp).
- US: base methods weighted hit 36.4% vs combos 32.5% — **combos are worse by -3.9pp**.

**Finding: stacking multiple detection methods does NOT reliably improve hit rate,
and actively hurts in the US market.** This is likely because combos are dominated
by `A_donchian_minervini`/`M_shakeout_rebreak` co-firing rather than genuine
independent confirmation. Do not treat "more methods fired" as a quality signal by
itself — check *which* methods.

## 3. Cofire confirmation (independent signals agreeing same day)

| Signal | IN (True vs False) | US (True vs False) | Verdict |
|---|---|---|---|
| `rs_cofire` | 41.0% vs 41.5% | 36.2% vs 35.5% | No consistent effect — not useful as a gate |
| `d_cofire` | 49.2% vs 41.0% (n=742, small) | 29.1% vs 36.1% (n=2,591, **negative**) | Inconsistent/contradictory across markets — do not use |
| `l_cofire` | 44.0% vs 40.7% (n=11,936) | 38.8% vs 35.7% (n=15,816) | **Consistent +3.1–3.3pp in both markets, decent n → keep as a positive confirmation gate** |

## 4. Selection features (raw scan-time inputs)

- **`rs_ratio`**: Inversely related to hit rate in IN (weak RS 46.5% → strong RS
  36.3%, monotonic) but flat/noisy in US. Counterintuitive — do not use high RS
  ratio as a positive gate; if anything it's a mild negative in IN.
- **`vol_contraction`**: Looser (higher) contraction quartile outperforms tighter
  in both markets (IN 39.4%→43.5%, US 35.0%→36.8%) — opposite of the common VCP
  assumption that "tighter = better." Do not gate on tightness alone.
- **`dist_from_52w_high`**: **Direction flips between markets** — IN rewards being
  close to the high (0-5% off = 42.4%, best), US rewards being further away
  (>15% off = 40.5%, best; 0-5% off = 34.3%, worst). Market-specific — do not use
  a single shared threshold across IN/US.
- **`adx`**: Flat/inconsistent in both markets — not predictive, drop from gating.
- **`vol_surge`**: **Consistent inverse relationship in both markets** — low
  volume-surge quartile beats high (IN 42.3%→39.9%, US 39.5%→32.3%, US drop is
  large). A big volume spike at breakout is *not* a positive selection signal in
  this data — worth flagging since it contradicts typical breakout heuristics.
- **`base_depth_pct`**: **Consistent and strong** — shallow bases outperform deep
  bases in both markets (IN 43.2%→37.7%, US 40.1%→30.4%, a -9.7pp spread in US).
  This is one of the cleanest, most reliable gating features found.
- **`analog_sim` / `analog_worked`**: Coverage is low (IN 4.9% of events, US 6.8%)
  but when present it's predictive and consistent: prior-analog-worked cases beat
  prior-analog-failed cases by +4.3pp (IN) and +6.5pp (US). Worth surfacing as a
  conviction booster when available, not a hard gate (too sparse to gate on).
- **Pattern type**: **Consistent across markets** — `Ascending Triangle` and
  `Tight Consolidation` are the two worst-performing labeled patterns in BOTH
  markets (IN 33.8%/33.3%, US 28.7%/31.1%), while `No clear pattern` and
  `Double Bottom` are at/above baseline in both. Explicit pattern labeling is
  not adding value on average — if anything, the two "classic-looking" tight
  patterns underperform generic/no-pattern breakouts.

## Recommended gate changes (ranked by confidence)

1. **Cut or heavily downweight `A_donchian_minervini`, `M_shakeout_rebreak`,
   `F_episodic_pivot`** as standalone qualifying methods — negative in both
   markets with large n. These currently contribute a lot of volume (9.5k/19.9k,
   4.2k/9.9k, 1.5k/2.3k events) for below-baseline results.
2. **Do not credit "combo fired" (multiple methods same day) as inherently
   higher quality** — remove any scoring boost tied purely to method count;
   it's a wash in IN and net negative in US, mostly because it's driven by the
   already-flagged negative methods co-firing.
3. **Keep/boost `G_pre_breakout_composite`, `G2_pre_breakout_retuned`, and the
   `HC_tier1_high_conviction`/`L2_hc_deep_base` tier** as premium signals —
   consistently +2.7 to +7.8pp in both markets.
4. **Add `l_cofire == True` as a positive confirmation gate/boost** (consistent
   +3pp both markets); do NOT use `d_cofire` or `rs_cofire` this way.
5. **Add `base_depth_pct` (shallow-base quartile) as a hard/soft gate** — the
   single cleanest raw feature, consistent direction, large effect size
   (especially in US).
6. **Flag `vol_surge`**: consider gating OUT the top quartile (biggest volume
   spike) rather than requiring one, since it correlates with worse outcomes in
   both markets — counter to the current implicit assumption.
7. **Drop `rs_ratio` and `adx` as gating criteria** — not predictive, or
   inversely predictive, in this data.
8. **Treat `dist_from_52w_high` as market-specific**, not a shared threshold —
   direction of the relationship flips between IN and US.
9. **Deprioritize `Ascending Triangle`/`Tight Consolidation` pattern labels** in
   scoring/prioritization; they underperform "no clear pattern" breakouts in
   both markets.
10. **Analog match is a good tie-breaker/conviction booster, not a gate** —
    predictive when present but only covers ~5-7% of events.

## Caveats

- These are unconditional/univariate splits on the retrospective DuckDB history
  (same event generator as `analyze_reliability.py`), not a multivariate
  model — features and methods interact, so treat this as prioritization
  guidance for what to test next in combination, not a finished ruleset.
- Small-n rows (`*` in the log, n<30, e.g. `HC_after_H_alert`, `SB_and_H`) are
  noise and excluded from all conclusions above.
- `d_cofire`/`AD_combo`/`AED_combo` etc. have small-to-moderate n and
  contradictory signs across markets — flagged as "do not use" rather than
  "confirmed negative," since the evidence is weaker than for the large-n
  findings.
