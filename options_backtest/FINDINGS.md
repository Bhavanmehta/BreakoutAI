# NIFTY Credit-Spread Backtest — Findings Log

**Purpose:** Investigate whether Stratzy-marketplace-style "credit spread overnight/expiry"
algos (e.g. the "Zen Credit Spread" family) have a genuine, cost-adjusted edge on real NIFTY
data, by building an independent backtest engine and testing every reasonable variant of the
underlying idea (directional-bias signal → sell an OTM credit spread → hold overnight or to
expiry).

**Bottom line so far: out of 61 total strategy variants tested (13 hand-built + 48 in a
systematic Zen-mechanic sweep), only 2 are profitable — and both are the same *shape* of
strategy (selective entry, held to expiry/settlement, tight stop discipline). Every
"overnight hold" variant and every naive directional-bias credit spread lost money.**

---

## 1. Engine & methodology (so results are reproducible / comparable)

- **Repo:** `options_backtest/` in this repo.
- **Data:** NSE F&O EOD Bhavcopy, `data/nifty_fo_daily.csv` (futures + options daily OHLC,
  OI, settlement price), loaded via `scripts/backtest.py::load_data()`.
- **Universe:** NIFTY index options/futures, front-week expiry only.
- **Capital:** `CAPITAL = ₹1,00,000`, `LOT_SIZE = 75`.
- **Costs:** `ROUND_TRIP_COST = ₹300` flat per trade (2 entry legs + 2 exit legs, incl.
  slippage estimate) — a real, non-zero cost is charged on every trade, not just a
  frictionless mark-to-market.
- **Strike selection:** `STRIKE_STEP = 50`, `STRIKE_SEARCH_TOL = 150` points (searches for a
  liquid substitute strike if the exact target strike is illiquid/missing).
- **Two hold-type engines** in `backtest.py`:
  - `run_overnight()` — enter EOD, exit next day's open (or SL/target intraday), pure
    overnight gap exposure.
  - `run_expiry_hold()` — enter ~2 days pre-expiry, hold through EOD checks to settlement.
- **Fallback rule:** if the model can't find a liquid strike at the target OTM%, it skips the
  day rather than forcing a trade (`# fallback: allow zero-volume quote if nothing liquid
  found`, used only as last resort).
- **Stats computed per strategy** (`compute_stats()`): trades, win_rate_pct, total_pnl_rs,
  total_return_pct, cagr_pct, max_drawdown_pct, avg_win/avg_loss, profit_factor, sharpe_like,
  period_days.
- **Backtest window:** ~354–395 calendar days per strategy (varies slightly by how far back
  each strategy's signal needs lookback data), ~87 distinct weekly expiries covered.
- **Scripts:**
  - `scripts/backtest.py` — the validated core engine + the original 13 hand-designed
    strategies.
  - `scripts/zen_sweep.py` — reuses `backtest.py`'s engine (`import backtest as bt`) and runs
    a full grid of 12 directional-bias signals × 4 spread widths = 48 variants in parallel
    (multiprocessing, one worker per width bucket) to systematically probe the "Zen"
    mechanic's parameter space instead of guessing at one config.
- **Output:**
  - `output/strategy_summary.csv` + `output/trades_<strategy>.csv` — the 13 hand-built
    strategies.
  - `output/zen_sweep_summary.csv` + `output/trades_Zen-<signal>-<width>.csv` — the 48-variant
    sweep.

---

## 2. Round 1 — 13 hand-built strategy variants (`strategy_summary.csv`)

Sorted best → worst by `total_return_pct`:

| # | Strategy | Hold type | Trades | Win % | Total Return % | CAGR % | Max DD % | Profit Factor | Sharpe-like | Notes |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 🟢 | **Momentum-Expiry-HoldToSettle** | expiry | 56 | 91.1 | **+24.87** | +23.5 | -7.35 | 2.35 | 1.78 | Momentum-timed spread entered 2 days pre-expiry, held to settlement |
| 2 🟢 | **MeanReversion-Expiry** | expiry | 18 | 77.8 | **+17.25** | +22.2 | -3.13 | 5.12 | 2.69 | Contrarian entry 2 days pre-expiry, held with EOD SL/target checks |
| 3 | Conservative-Wide-Expiry | expiry | 30 | 23.3 | -2.31 | -2.4 | -3.36 | 0.60 | -0.96 | Wide 2.5% OTM strikes, only in low-vol regime, tighter SL |
| 4 | ThetaHarvest-Baseline-Expiry | expiry | 57 | 78.9 | -8.00 | -7.5 | -25.64 | 0.83 | -0.36 | **Non-directional benchmark** — always sells a put spread, no signal at all |
| 5 | MeanReversion-Overnight | overnight | 84 | 32.1 | -14.76 | -14.0 | -23.27 | 0.60 | -1.17 | Fades yesterday's >0.6% move |
| 6 | GapPosition-Overnight | overnight | 174 | 27.6 | -29.69 | -27.8 | -35.09 | 0.52 | -2.26 | Direction from futures' close-within-range |
| 7 | LowVol-Tight-Overnight | overnight | 109 | 27.5 | -38.71 | -38.6 | -40.81 | 0.25 | -4.44 | Only trades in calm/contracting-vol regime |
| 8 | VolExpansion-Overnight | overnight | 84 | 28.6 | -39.70 | -40.7 | -39.86 | 0.26 | -2.90 | Only trades in vol-breakout regime |
| 9 | TrendFollow10d-Overnight | overnight | 202 | 30.2 | -40.98 | -39.7 | -41.29 | 0.50 | -2.72 | 10-day trend filter, overnight hold |
| 10 | Aggressive-Tight-Overnight | overnight | 202 | 38.6 | -45.66 | -43.4 | -47.94 | 0.54 | -2.61 | ~0.6% OTM strikes → high premium, high gap risk |
| 11 | SkewFade-Overnight | overnight | 190 | 33.7 | -54.66 | -51.9 | -56.67 | 0.40 | -3.28 | Direction from PE/CE price skew at 1% OTM |
| 12 | Momentum-Overnight-Wide | overnight | 202 | 15.3 | -66.98 | -64.5 | -67.06 | 0.22 | -3.33 | Same signal as #1 but wider OTM |
| 13 | Momentum-Overnight-Tight | overnight | 202 | 34.2 | -71.15 | -68.7 | -74.66 | 0.38 | -3.48 | 3-day momentum, tight 1.0% OTM, held to next open |

**Key pattern: hold type dominates everything.** All 11 overnight-hold strategies lost money
(-14.8% to -71.2%). Both profitable strategies are expiry-hold with a *selective* (not
always-on) entry signal. Note strategy #4 (`ThetaHarvest-Baseline-Expiry`) proves expiry-hold
alone isn't sufficient — an unconditional "always sell a put spread" with no directional
signal still lost -8.00% and had the worst max drawdown of the expiry group (-25.64%), so the
*selective/timed entry* in #1 and #2 is doing real work, not just the hold-to-settlement
structure.

---

## 3. Round 2 — Zen-mechanic systematic sweep (`zen_sweep_summary.csv`)

Directly targets the marketplace "Zen Credit Spread Overnight/Expiry" claim: a directional-bias
signal computed before close, used to sell an OTM credit spread, held overnight. Instead of
testing one config, swept **12 signals × 4 widths = 48 variants** in parallel.

**Signals tested:** `pcr` (put/call OI ratio), `oi_bias` (OI-based bias), `mean_rev`,
`vol_contract`, `vol_expand`, `gap_pos`, `momentum1`, `momentum3`, `momentum5`, `momentum10`,
`skew`, `always_bull` (unconditional benchmark).
**Widths tested:** Tight / Medium / Wide / VeryWide OTM offsets.

### Result: 0 / 48 profitable.

Best overall — `Zen-pcr-Tight`: 77 trades, 42.9% win rate, **-3.40%** total return, -8.42% max
DD, profit factor 0.85. Still a net loser, just the least bad.

**Best width per signal** (Tight wins for 10 of 12 signals — wider spreads collect more
premium but bleed faster into overnight gap risk):

| Signal | Best width | Return % | Win % | Profit Factor |
|---|---|---:|---:|---:|
| pcr | Tight | -3.40 | 42.9 | 0.85 |
| oi_bias | Tight | -8.52 | 40.8 | 0.74 |
| mean_rev | Wide | -14.76 | 32.1 | 0.60 |
| vol_contract | Tight | -19.15 | 41.3 | 0.57 |
| vol_expand | Tight | -29.35 | 31.0 | 0.47 |
| gap_pos | Tight | -30.26 | 38.5 | 0.58 |
| momentum1 | Tight | -30.81 | 40.8 | 0.63 |
| momentum5 | Medium | -34.87 | 34.2 | 0.60 |
| momentum10 | Tight | -45.42 | 35.6 | 0.54 |
| momentum3 | Tight | -45.66 | 38.6 | 0.54 |
| skew | Tight | -67.55 | 37.4 | 0.38 |
| always_bull | VeryWide | -72.75 | 18.1 | 0.23 |

**Read on this:**
- **Options-flow signals (PCR, OI-bias) are the least-bad** of the 12 — the closest thing to a
  genuine informational edge (real positioning data) — yet still can't clear round-trip costs
  + overnight theta/gap risk on this dataset.
- **Pure price-action proxies (momentum-N, skew, always_bull) are the worst**, several losing
  45–99% of capital. Naive "sell the side matching last N-day return sign" gets destroyed by
  occasional large adverse moves — textbook unhedged short-premium tail risk.
- **Width never rescues a losing signal.** Tight caps loss-per-trade and wins on 10/12
  signals; the extra premium from Wide/VeryWide never compensates for tail losses.
- **All 48 variants are overnight-style** (per the marketplace claim's mechanic) — this
  reinforces Round 1's finding that overnight hold is structurally the losing shape here,
  independent of which signal drives direction.

---

## 4. Combined takeaways across all 61 variants

1. **2/61 variants are profitable overall (~3%)**, both expiry-hold with selective entries:
   `Momentum-Expiry-HoldToSettle` (+24.87%) and `MeanReversion-Expiry` (+17.25%).
2. **Every single overnight-hold variant lost money** — 11/13 from Round 1, 48/48 from Round 2
   — across a wide range of signal types (momentum, mean-reversion, gap, skew, vol
   regime, OI/PCR flow, and unconditional baselines). This is the strongest, most consistent
   signal in the whole exercise: overnight gap risk on NIFTY weekly-expiry OTM credit spreads
   structurally eats the premium collected, regardless of directional signal quality.
3. **No naive "always sell X" baseline works** — `ThetaHarvest-Baseline-Expiry` (-8.00%) and
   `Zen-always_bull-*` (-72.75% to -98.73%) both confirm you need a real, selective signal —
   raw theta harvesting isn't free money here.
4. **Marketplace-style "Zen Credit Spread" mechanic (directional bias + OTM spread + overnight
   hold), as tested across its full reasonable parameter space, has no profitable
   configuration** on this dataset with realistic costs. This directly challenges any
   marketplace-advertised return claims for that algo family unless they use materially
   different data, costs, timing, or risk management than what's modeled here.
5. **What *did* work shares 3 traits**: (a) hold to expiry/settlement rather than overnight,
   (b) a selective/timed entry rather than always-on, (c) tight stop-loss discipline
   (`Momentum-Expiry-HoldToSettle` max DD only -7.35%; `MeanReversion-Expiry` only -3.13%).

---

## 5. Open questions / natural next steps for further backtesting

- [x] The two winners have very small sample sizes (56 and 18 trades over ~1 year) — test
  statistical robustness: bootstrap/resample trades, or extend the data window if more
  Bhavcopy history is available, to see if the edge holds out-of-sample.
  **Done in Round 3 (§7b)** — both pass i.i.d. bootstrap (96–99% of resampled draws
  profitable). Extending the data window is still open.
- [x] Both winners use a 2-day-pre-expiry entry — sweep the entry offset (1/2/3/4 days
  pre-expiry) to see if 2 days is actually optimal or a lucky pick.
  **Done in Round 3 (§7a)** — `MeanReversion-Expiry` is robust (offsets 2/3 both strong),
  `Momentum-Expiry-HoldToSettle` looks fragile (offset 2 is an isolated spike between two
  losing offsets) — flagged for further scrutiny, not disqualified yet.
- [x] Try combining `Momentum-Expiry-HoldToSettle`'s selective/timed entry logic with the
  options-flow signals (`pcr`, `oi_bias`) instead of price momentum — flow signals were the
  best performers in the overnight sweep, might translate better to expiry-hold too.
  **Done in Round 3 (§7c)** — 6/8 flow×expiry-hold combos profitable, best is
  `FlowExpiry-pcr-Medium` (+14.22%, PF 123x on 21 trades) but not yet robustness-tested.
- [x] **New from Round 3:** robustness-test the flow×expiry-hold winners the same way the
  original 2 winners were (offset sweep + bootstrap) — `FlowExpiry-pcr-Medium`'s PF of 123x
  on 21 trades is a red flag for overfitting until checked.
  **Done in Round 4 (§8)** — the 123x PF is confirmed an offset-2 artifact (PF 1.14–6.69 at
  the other three offsets) though the *sign* of the return holds at all 4. `pcr-Wide` is the
  most robust flow winner (positive at every offset, bootstrap P5 +5.6%); both `oi_bias`
  variants flip negative at offset 3 and are the most fragile of the six.
- [ ] Extend the Bhavcopy history window (currently ~1 year / 268 trading days) if more data
  is available, to get more expiry cycles into the two winners' and flow winners' samples.
- [x] Stress-test the 2 winners around known high-vol events (budget day, election result
  days, big global selloffs) — check if their small max-DD is luck (no bad event in-sample)
  or genuine risk control (SL logic actually fired).
  **Done in Round 5 (§9a)** — split verdict: `MeanReversion-Expiry`'s SL demonstrably fired
  and capped a loss at -3.7% (real discipline), but `FlowExpiry-pcr-Wide` saw **zero** adverse
  moves ≥1% in any holding window — its pristine -0.2% DD is untested luck, not proven risk
  control. The stress test is the only check that exposed this; offset-sweep + bootstrap
  couldn't (they all resample the same shock-free trade set).
- [ ] Try a middle-ground hold type: enter overnight but with a hard SL/target check +
  early-exit-before-expiry-gap-risk, instead of pure "hold to next open."
- [x] Sensitivity-test `ROUND_TRIP_COST` (₹300 flat estimate) — rerun at ₹150 and ₹500 to see
  how fragile the two winners are to cost assumptions.
  **Done in Round 5 (§9b)** — `MeanReversion-Expiry` is cost-robust (still +9.15% at ₹750);
  `FlowExpiry-pcr-Wide` is cost-fragile (+11.46% → +2.01% at ₹750, PF collapses to 1.36) because
  its per-trade edge is thin and a fixed cost eats proportionally more of it.
- [ ] Cross-check position sizing — engine currently sizes to 1 lot fixed; test with
  Kelly-ish or vol-scaled sizing on just the 2 winning strategies.

---

## 7. Round 3 — robustness testing of the 2 winners + options-flow signals × expiry-hold

Follow-up on the two highest-value open items from §5. Engine untouched — reuses the same
`run_expiry_hold` / `compute_stats` code paths validated in Rounds 1–2. Script:
`scripts/round3_followups.py`. Outputs: `round3_offset_sweep.csv`, `round3_bootstrap.csv`,
`round3_flow_expiry_summary.csv`.

### 7a. Entry-offset sweep (1/2/3/4 trading days pre-expiry)

| Strategy | Offset (days) | Trades | Win % | Total Return % | Max DD % | Profit Factor |
|---|---:|---:|---:|---:|---:|---:|
| Momentum-Expiry-HoldToSettle | 1 | 56 | 62.5 | -16.68 | -28.79 | 0.53 |
| Momentum-Expiry-HoldToSettle | **2 (original)** | 56 | 91.1 | **+24.87** | -7.35 | 2.35 |
| Momentum-Expiry-HoldToSettle | 3 | 51 | 80.4 | -16.65 | -26.18 | 0.72 |
| Momentum-Expiry-HoldToSettle | 4 | 39 | 82.1 | +7.43 | -16.16 | 1.18 |
| MeanReversion-Expiry | 1 | 27 | 44.4 | -11.84 | -12.74 | 0.49 |
| MeanReversion-Expiry | **2 (original)** | 18 | 77.8 | **+17.25** | -3.13 | 5.12 |
| MeanReversion-Expiry | 3 | 21 | 76.2 | +17.14 | -2.68 | 5.09 |
| MeanReversion-Expiry | 4 | 18 | 72.2 | +0.36 | -9.09 | 1.02 |

**Read on this:**
- **Not a knife-edge pick, but not a broad plateau either — and the two winners diverge here.**
  `MeanReversion-Expiry` is genuinely robust: offsets 2 and 3 are nearly identical (+17.25% /
  +17.14%, PF ~5.1x both), so there's a real 2–3 day window, not one lucky offset.
  `Momentum-Expiry-HoldToSettle` is the opposite story — offset 2 is an isolated spike
  (+24.87%) surrounded by two losing offsets (1 and 3, both ~-16.6%) and a much weaker offset
  4 (+7.43%). That win/lose/lose/weak-win pattern looks more like offset=2 landing on a
  favorable slice of only 56 trades than a structural "2 days pre-expiry is better" effect.
- **Net effect:** `MeanReversion-Expiry`'s edge is now more trustworthy than `Momentum-Expiry-
  HoldToSettle`'s. Treat the latter's headline +24.87% with real skepticism until it survives
  more stress tests — this offset sweep is the first crack in what looked like the strongest
  result in the whole study.

### 7b. Bootstrap resample (5,000 draws, i.i.d. resampling of realized trade PnLs, offset=2 original config)

| Strategy | Trades | Mean Return % | Median % | P5 % | P95 % | % of draws profitable |
|---|---:|---:|---:|---:|---:|---:|
| Momentum-Expiry-HoldToSettle | 56 | 24.93 | 25.52 | 1.54 | 45.80 | 96.0 |
| MeanReversion-Expiry | 18 | 17.13 | 17.35 | 4.79 | 28.24 | 98.7 |

**Read on this:**
- **Given the trade set each strategy actually produced, the sign of the return is not
  fragile** — resampling the same 56/18 trades with replacement keeps both strategies
  profitable in ~96–99% of draws, and even the 5th-percentile outcome stays positive for both
  (+1.5% / +4.8%).
- **This is a narrower claim than "the strategy is robust."** Bootstrap resampling only
  measures sensitivity to *which* subset/order of the *observed* trades you happen to hold —
  it says nothing about whether the observed trades themselves are a lucky draw from the
  underlying process (that's what §7a's offset sweep, and the still-open high-vol-event
  stress test, are checking). Read 7a + 7b together: `MeanReversion-Expiry` passes both
  checks; `Momentum-Expiry-HoldToSettle`'s trade set is internally consistent (7b) but was
  generated by a config (offset=2) that looks fragile (7a).

### 7c. Options-flow signals (`pcr`, `oi_bias`) × expiry-hold engine

First test of this combination — Round 2 only ran `pcr`/`oi_bias` with overnight hold (where
they were the least-bad signals at -3.40% / -8.52%). Same 4 widths as the Zen sweep,
`sl_mult=1.5`, `target_frac=0.6`, `entry_offset_days=2` (matching the 2 winners' config).

| Strategy | Trades | Win % | Total Return % | Max DD % | Profit Factor |
|---|---:|---:|---:|---:|---:|
| **FlowExpiry-pcr-Medium** | 21 | 95.2 | **+14.22** | -0.11 | 123.35 |
| FlowExpiry-pcr-Wide | 21 | 71.4 | +11.46 | -0.23 | 25.84 |
| FlowExpiry-oi_bias-Medium | 23 | 87.0 | +8.42 | -6.00 | 1.76 |
| FlowExpiry-pcr-VeryWide | 21 | 38.1 | +5.44 | -1.29 | 4.13 |
| FlowExpiry-pcr-Tight | 21 | 90.5 | +4.92 | -7.16 | 1.50 |
| FlowExpiry-oi_bias-Wide | 23 | 73.9 | +3.19 | -7.56 | 1.25 |
| FlowExpiry-oi_bias-VeryWide | 23 | 52.2 | -1.50 | -7.58 | 0.87 |
| FlowExpiry-oi_bias-Tight | 23 | 82.6 | -6.14 | -20.35 | 0.75 |

**Read on this:**
- **6/8 profitable — the best hit rate of any signal/hold-type combination tested across all 3
  rounds** (vs. 2/13 in Round 1 and 0/48 in Round 2 overnight sweep). Moving `pcr`/`oi_bias`
  from overnight to expiry-hold flipped them from the least-bad losers to mostly-winners,
  reinforcing that hold-type (not signal choice) is the dominant lever, and adding a 3rd
  independent data point that expiry-hold + a genuine informational signal (not just price
  momentum) works.
- **`FlowExpiry-pcr-Medium`'s numbers are too good to take at face value:** PF 123x and max DD
  of -0.11% on only 21 trades means essentially every losing trade was capped near-zero —
  plausible given the SL/target mechanic, but this small a sample with this extreme a profit
  factor is a textbook case for the offset-sweep + bootstrap treatment from §7a/7b before
  trusting it over the two original winners.
- **`pcr` clearly beats `oi_bias` at every width**, echoing Round 2 where `pcr` was also the
  single best overnight signal — some real consistency in this dataset that put/call OI
  positioning carries more signal than price×OI build-up quadrants.
- **3 candidate winning combinations now exist**, all sharing the "expiry-hold + selective
  entry" shape: `Momentum-Expiry-HoldToSettle`, `MeanReversion-Expiry`, `FlowExpiry-pcr-
  Medium` (+ 4 more marginally-profitable flow variants). None of the flow variants have
  cleared the §7a/7b robustness bar yet — that's the natural next step if continuing.

---

## 8. Round 4 — robustness testing of the 6 flow × expiry-hold winners

Puts the same two checks used on the original 2 winners (§7a offset sweep + §7b bootstrap) on
the 6 profitable `pcr`/`oi_bias` × expiry-hold combos from §7c — the natural next step flagged
at the end of §7c, and a direct interrogation of `FlowExpiry-pcr-Medium`'s suspiciously extreme
PF 123x / 21-trade headline. Engine untouched; reuses `round3_followups.py`'s `offset_sweep()`
and `bootstrap_return()` verbatim. Script: `scripts/round4_flow_robustness.py`. Outputs:
`round4_flow_offset_sweep.csv`, `round4_flow_bootstrap.csv`.

### 8a. Entry-offset sweep (1/2/3/4 trading days pre-expiry) on the 6 flow winners

| Strategy | Offset (days) | Trades | Win % | Total Return % | Max DD % | Profit Factor |
|---|---:|---:|---:|---:|---:|---:|
| FlowExpiry-pcr-Medium | 1 | 24 | 58.3 | +1.00 | -5.91 | 1.14 |
| FlowExpiry-pcr-Medium | **2 (original)** | 21 | 95.2 | **+14.22** | -0.11 | 123.35 |
| FlowExpiry-pcr-Medium | 3 | 18 | 94.4 | +10.22 | -1.92 | 6.09 |
| FlowExpiry-pcr-Medium | 4 | 14 | 92.9 | +13.82 | -2.33 | 6.69 |
| FlowExpiry-pcr-Wide | 1 | 24 | 50.0 | +5.74 | -2.02 | 3.71 |
| FlowExpiry-pcr-Wide | **2 (original)** | 21 | 71.4 | **+11.46** | -0.23 | 25.84 |
| FlowExpiry-pcr-Wide | 3 | 18 | 77.8 | +7.06 | -1.33 | 5.87 |
| FlowExpiry-pcr-Wide | 4 | 14 | 92.9 | +12.00 | -0.97 | 12.98 |
| FlowExpiry-pcr-VeryWide | 1 | 24 | 20.8 | +1.48 | -2.77 | 1.45 |
| FlowExpiry-pcr-VeryWide | **2 (original)** | 21 | 38.1 | **+5.44** | -1.29 | 4.13 |
| FlowExpiry-pcr-VeryWide | 3 | 18 | 55.6 | +2.82 | -0.57 | 3.97 |
| FlowExpiry-pcr-VeryWide | 4 | 14 | 71.4 | +5.53 | -0.15 | 19.21 |
| FlowExpiry-pcr-Tight | 1 | 24 | 66.7 | +1.89 | -7.89 | 1.22 |
| FlowExpiry-pcr-Tight | **2 (original)** | 21 | 90.5 | **+4.92** | -7.16 | 1.50 |
| FlowExpiry-pcr-Tight | 3 | 18 | 88.9 | +8.17 | -6.47 | 2.20 |
| FlowExpiry-pcr-Tight | 4 | 14 | 85.7 | +11.86 | -3.50 | 2.84 |
| FlowExpiry-oi_bias-Medium | 1 | 16 | 56.2 | +6.52 | -0.93 | 6.04 |
| FlowExpiry-oi_bias-Medium | **2 (original)** | 23 | 87.0 | **+8.42** | -6.00 | 1.76 |
| FlowExpiry-oi_bias-Medium | 3 | 16 | 81.2 | **-13.54** | -17.82 | 0.53 |
| FlowExpiry-oi_bias-Medium | 4 | 21 | 81.0 | +5.15 | -11.25 | 1.21 |
| FlowExpiry-oi_bias-Wide | 1 | 16 | 50.0 | +3.56 | -1.42 | 3.09 |
| FlowExpiry-oi_bias-Wide | **2 (original)** | 23 | 73.9 | **+3.19** | -7.56 | 1.25 |
| FlowExpiry-oi_bias-Wide | 3 | 16 | 75.0 | **-11.21** | -16.96 | 0.58 |
| FlowExpiry-oi_bias-Wide | 4 | 21 | 81.0 | +2.82 | -9.10 | 1.13 |

**Read on this:**
- **`FlowExpiry-pcr-Medium`'s 123x PF is confirmed a lucky-sample artifact, exactly as flagged
  in §7c — but its *direction* survives.** The PF collapses from 123.35 (offset 2) to 6.09 /
  6.69 at offsets 3/4 and 1.14 at offset 1; that ~20× drop off a single offset is the signature
  of a favorable-sample spike, not a stable property. What *does* hold is the sign: it stays
  positive at all four offsets (+1.00 / +14.22 / +10.22 / +13.82). So this is the mirror image
  of `Momentum-Expiry-HoldToSettle` in §7a — Momentum flipped to two ~-16% losing offsets,
  whereas pcr-Medium never goes negative. Trust the *direction*, discount the *magnitude*: the
  headline +14.22% / 123x overstates a real but smaller edge.
- **`FlowExpiry-pcr-Wide` is the most robust of the six** — positive at every offset
  (+5.74 / +11.46 / +7.06 / +12.00), profit factor never below 3.7, and max DD ≤ -2% throughout.
  This is the flow winner that behaves most like `MeanReversion-Expiry` did in §7a (a genuine
  plateau, not a knife-edge). `pcr-VeryWide` is also sign-stable across all offsets but small
  (~2–5%); `pcr-Tight` is sign-stable too and actually *improves* at later offsets
  (+11.86% at offset 4) but carries the worst drawdowns of the pcr family (-6% to -8%).
- **Both `oi_bias` variants fail the sweep** — they flip hard negative at offset 3
  (-13.54% / -11.21%, with -17% / -17% drawdowns). An edge that only exists at 3 of 4 entry
  timings and blows up at the fourth isn't a robust edge. This sharpens §7c's "`pcr` clearly
  beats `oi_bias`" into something stronger: on this data `oi_bias` × expiry-hold has no
  timing-robust edge at all, while every `pcr` width does.

### 8b. Bootstrap resample (5,000 draws, i.i.d. resampling of realized trade PnLs, offset=2 original config)

| Strategy | Trades | Mean Return % | Median % | P5 % | P95 % | % of draws profitable |
|---|---:|---:|---:|---:|---:|---:|
| FlowExpiry-pcr-Medium | 21 | 14.25 | 14.10 | 8.69 | 20.44 | 100.0 |
| FlowExpiry-pcr-Wide | 21 | 11.49 | 11.24 | 5.57 | 18.15 | 100.0 |
| FlowExpiry-pcr-VeryWide | 21 | 5.42 | 5.24 | 0.92 | 10.47 | 98.1 |
| FlowExpiry-pcr-Tight | 21 | 5.06 | 5.94 | -11.03 | 17.53 | 73.6 |
| FlowExpiry-oi_bias-Medium | 23 | 8.48 | 9.22 | -8.23 | 22.91 | 81.6 |
| FlowExpiry-oi_bias-Wide | 23 | 3.13 | 3.93 | -15.32 | 18.47 | 64.1 |

**Read on this:**
- **The bootstrap and the offset sweep disagree about `pcr-Medium`, and the disagreement is the
  point — not a contradiction.** Bootstrap says pcr-Medium is 100% profitable with a +8.69% P5
  (rock-solid); the offset sweep (8a) says its PF is wildly unstable. Both are correct because
  they test different things: the bootstrap resamples a *fixed* 21-trade set (the offset-2
  trades), so it only measures whether *that sample's* sign is noise-fragile — it cannot see
  that *choosing offset 2 is itself what produced those 21 favorable trades*. The offset sweep
  is the check for design-choice sensitivity; the bootstrap is the check for within-sample
  noise. A strategy can pass one and fail the other, and reading only the bootstrap would have
  wrongly stamped pcr-Medium's 123x as robust. **Both checks are needed together** — this is the
  key methodological lesson of Round 4.
- **The bootstrap tails cleanly separate the six.** `pcr-Wide` (P5 +5.57, 100% profitable) and
  `pcr-Medium` (P5 +8.69, 100%) have entirely-positive resample distributions; `pcr-VeryWide`
  is positive but thin (P5 +0.92). The bottom three all have negative P5s and materially worse
  hit rates — `oi_bias-Wide` is the weakest by far (P5 -15.32, only 64% of draws profitable),
  consistent with it also failing the offset sweep hardest.

### 8c. Verdict — which flow winners are genuinely robust

Combining both checks (survives the offset sweep with the sign intact **and** has a
positive-to-thin bootstrap tail), best → worst:

| Rank | Strategy | Offset sweep | Bootstrap | Verdict |
|---|---|---|---|---|
| 1 | **FlowExpiry-pcr-Wide** | positive all 4 offsets, PF ≥ 3.7, DD ≤ -2% | P5 +5.6%, 100% | **Genuinely robust** — the trustworthy flow winner; behaves like `MeanReversion-Expiry` |
| 2 | FlowExpiry-pcr-Medium | positive all 4, but PF 1.1→123→6 (offset-2 spike) | P5 +8.7%, 100% | **Real but overstated** — trust the sign, not the +14.22%/123x magnitude |
| 3 | FlowExpiry-pcr-VeryWide | positive all 4, small (~2–5%) | P5 +0.9%, 98% | Stable but thin — genuine, low-magnitude edge |
| 4 | FlowExpiry-pcr-Tight | positive all 4 but deep DDs (-6 to -8%) | P5 -11.0%, 74% | Middling — sign-stable but wide loss tail |
| 5 | FlowExpiry-oi_bias-Medium | **flips -13.5% at offset 3** | P5 -8.2%, 82% | Fragile — no timing-robust edge |
| 6 | FlowExpiry-oi_bias-Wide | **flips -11.2% at offset 3** | P5 -15.3%, 64% | Most fragile — fails both checks |

**Net takeaways for the study:**
- **The count of study-wide "genuinely robust" strategies is now 2, not the 3 it looked like
  after §7c.** `MeanReversion-Expiry` (§7a/7b) and `FlowExpiry-pcr-Wide` (§8) both clear both
  checks. `FlowExpiry-pcr-Medium` — the strategy that *looked* like the best flow winner — turns
  out to be a real edge dressed up in a lucky offset; `Momentum-Expiry-HoldToSettle` (§7a) is
  still the weakest of the "winners" on robustness. So the promotion candidate out of Round 4 is
  `pcr-Wide`, not `pcr-Medium`.
- **`pcr` decisively beats `oi_bias` on robustness, not just on raw return.** All four `pcr`
  widths keep a positive sign across every entry offset; both `oi_bias` widths collapse at
  offset 3. Round 2 already found `pcr` the least-bad overnight signal — three independent
  rounds now point the same way, which is about as much cross-validation as this single-year
  dataset can give. If any flow strategy graduates to paper-trading, it should be a `pcr`
  variant (Wide the default, Medium as a higher-variance sibling), never `oi_bias`.
- **Still not cleared:** none of these have faced the high-vol-event stress test or an extended
  data window (both still open in §5). The single-year sample means even `pcr-Wide`'s "robust"
  rests on ~21 trades / ~14 expiry cycles — the offset sweep and bootstrap rule out the two
  cheapest failure modes (timing-luck and within-sample noise), not regime dependence.

---

## 9. Round 5 — deployability checks before live execution

Goal shifted from "is there an edge" to "which one would I actually trade over the next few
weeks, and what should I realistically expect." Runs the two most decision-relevant open items
from §5 (high-vol-event stress test + cost sensitivity) on the 2 genuinely-robust winners
(`MeanReversion-Expiry`, `FlowExpiry-pcr-Wide`), plus a near-term horizon bootstrap. Engine
untouched. Script: `scripts/round5_deployability.py`. Outputs: `round5_stress_summary.csv`,
`round5_cost_sweep.csv`, `round5_horizon_bootstrap.csv`, `round5_stress_<strategy>.csv`.

**In-sample context — the shocks that existed to be tested against:** the sample is NOT
event-free. Biggest single-day spot moves: **+3.92%** (2026-04-08), -3.03% (03-19), -2.70%
(03-23), +2.69% (02-03), and 6 more ≥2%. Counts: 53 days ≥1.0%, 19 days ≥1.5%, 10 days ≥2.0%.
So a strategy that never took a hit did so by *dodging*, not by *there being nothing to dodge*.

### 9a. High-vol-event stress test (worst adverse move during each trade's hold)

"Adverse" = a move against the position (down-day for a bull put-spread, up-day for a bear
call-spread), measured only over each trade's actual exposure window (after entry through exit).

| Strategy | Trades | Stop-losses fired | Trades hit by adverse move ≥1% | ≥1.5% | Worst single trade | Worst-trade exit |
|---|---:|---:|---:|---:|---:|---|
| MeanReversion-Expiry | 18 | 1 | 4 | 2 | **-3.70%** | stop_loss |
| FlowExpiry-pcr-Wide | 21 | 0 | **0** | **0** | -0.23% | target |

**Read on this — this is the round that separates the two winners, and it reverses part of the
Round 4 read:**
- **`MeanReversion-Expiry`'s risk control is real and demonstrated.** Its worst trade
  (2026-05-08 → 05-11) took a +1.51% adverse move, the stop-loss fired, and the loss was capped
  at -3.70%. That is the SL mechanic doing exactly its job on a live-like adverse event — not
  luck. Caveat: the very worst days in the sample (+3.92%, -3.03%) landed *outside* its holding
  windows, so the extreme 3–4% gap tail is still only lightly sampled (only 2 trades ever saw an
  adverse move ≥1.5%).
- **`FlowExpiry-pcr-Wide`'s spotless -0.2% max-DD is untested luck, not proven discipline.**
  Zero of its 21 trades saw *any* adverse move ≥1%; its stop-loss has **never once fired** (20
  targets, 1 expiry). The pristine track record exists because no shock ever landed inside a
  pcr-Wide holding window — its defense against a real adverse gap is completely unobserved. This
  is the single most important finding of the whole exercise for a *deployment* decision, and it
  is invisible to every other check: §4's bootstrap and §8's offset sweep both resample/re-time
  the *same* shock-free 21-trade set, so they all inherit the same blind spot and all look
  flawless. Only the stress test, which asks "what shocks did you actually survive," catches it.
- **Net:** on the risk axis, `MeanReversion-Expiry` (SL proven to fire and cap) now ranks
  *above* `FlowExpiry-pcr-Wide` (SL never tested) — the opposite of what their headline
  win-rate/DD numbers suggest. Pretty stats from a strategy that dodged every bullet are worth
  less than scarred stats from one that took a hit and survived.

### 9b. Cost sensitivity (`ROUND_TRIP_COST` swept ₹150 / 300 / 500 / 750)

The SL/target triggers evaluate on pre-cost pnl, so the trade *set* is identical across costs —
only realized pnl shifts. This cleanly isolates how much of each edge is cushion vs. cost-exposed.

| Strategy | ₹150 | ₹300 (base) | ₹500 | ₹750 |
|---|---:|---:|---:|---:|
| MeanReversion-Expiry (total return %) | +19.95 | +17.25 | +13.65 | **+9.15** |
| MeanReversion-Expiry (profit factor) | 6.41 | 5.12 | 3.58 | 2.26 |
| FlowExpiry-pcr-Wide (total return %) | +14.61 | +11.46 | +7.26 | **+2.01** |
| FlowExpiry-pcr-Wide (profit factor) | 186.5 | 25.8 | 4.35 | **1.36** |

**Read on this:**
- **`MeanReversion-Expiry` is cost-robust** — still solidly profitable (+9.15%, PF 2.26) even at
  ₹750/round-trip, double the ₹300 base estimate. Its edge has real cushion.
- **`FlowExpiry-pcr-Wide` is cost-fragile** — collapses from +11.46% to +2.01% (PF 1.36, DD
  blowing out to -5%) at ₹750. Its many small target-wins mean a fixed per-trade cost eats
  proportionally more of a thin edge (21 trades × ₹750 = 15.75% drag against a ~17.7% gross
  edge). ₹300 is optimistic for 4-leg weekly-option slippage; at a realistic ₹500 it's still
  +7.26%, but the margin of safety is much thinner than MeanReversion's.

### 9c. Near-term horizon bootstrap — what "the next few weeks" realistically looks like

"Next few weeks" of a weekly-expiry strategy is ~3–4 trades. Bootstrapping a *fixed 3/4-trade
horizon* (5,000 draws) shows the near-term outcome band, not the full-year headline — the honest
number for the actual question.

| Strategy | Horizon | P5 % | Median % | P95 % | % profitable | P(lose >5%) |
|---|---:|---:|---:|---:|---:|---:|
| MeanReversion-Expiry | 3 trades | -2.40 | +3.12 | +7.45 | 86.1 | 0.7 |
| MeanReversion-Expiry | 4 trades | -2.32 | +3.97 | +9.14 | 87.3 | 1.1 |
| FlowExpiry-pcr-Wide | 3 trades | -0.08 | +1.43 | +4.37 | 92.4 | 0.0 |
| FlowExpiry-pcr-Wide | 4 trades | +0.03 | +1.90 | +5.25 | 95.6 | 0.0 |

**Read on this:**
- **Realistic expectation, not the annual figure:** over the next ~4 weekly trades,
  `MeanReversion-Expiry` centers on **≈ +4%** (P5 -2.3%, P95 +9.1%), `FlowExpiry-pcr-Wide` on
  **≈ +2%** (tighter, P5 ≈ 0%). "Good returns in a few weeks" here means low-single-digit
  percent with a real chance of a small loss — *not* the +17%/+11% annual totals, which
  accumulate over ~14–21 trades across a whole year.
- **pcr-Wide's "0.0% chance of losing >5%" is conditional on the next few weeks looking like the
  benign past — and §9a is the reason not to bank on that.** The bootstrap draws from a trade set
  with no adverse-shock trade in it, so it *cannot* price in a gap it never saw. MeanReversion's
  band is more trustworthy precisely because its sample *contains* the -3.7% stop-out, so the
  -2.3% P5 reflects a real loss having happened.

### 9d. Verdict — the strategy to actually run, and the honest caveats

**Recommendation: `MeanReversion-Expiry` is the better live candidate**, despite `FlowExpiry-
pcr-Wide`'s higher win rate and cleaner drawdown — because the three deployment-relevant axes all
favor it:
1. **Proven risk control** (§9a): its stop-loss has actually fired and capped a loss at -3.7%;
   pcr-Wide's has never been tested by a single adverse move.
2. **Cost robustness** (§9b): profitable even at ₹750/trip; pcr-Wide nearly breaks even there.
3. **Trustworthy near-term band** (§9c): its bootstrap tail contains a real stop-out, and its
   median 4-trade return (+4%) is higher than pcr-Wide's (+2%).

`FlowExpiry-pcr-Wide` is a legitimate second/diversifying line, but only at low cost (≤₹500) and
with the clear-eyed understanding that its perfection is *unproven under stress*.

**Caveats that apply to trading either one for real — do not skip these:**
- **Small sample, one regime.** These rest on 18 / 21 trades over a single ~13-month window
  (2025-06 → 2026-07). No amount of resampling manufactures out-of-sample data; the edge could be
  specific to this year's vol regime.
- **EOD-mark stop-loss, not intraday.** `backtest.py` walks close-to-close and fires the SL on
  EOD marks. A real overnight/expiry-week gap can jump *through* the stop before you can act, so
  the -3.7% worst trade is a floor *under EOD assumptions*; the true hard floor is the wing cap
  (`max_loss = wing_points − credit`). Size positions to survive the wing-cap loss, not the
  backtested -3.7%.
- **"Next few weeks" is 3–4 trades — variance dominates.** A +4% median with a -2.3% P5 means a
  losing month is entirely normal even if the edge is real. This is not a get-rich-quick horizon;
  it's a small, positive-expectancy grind that needs many trades to show up.
- **Still not done (§5):** middle-ground hold type, extended data window, and position-sizing
  tests remain open. Nothing here has been paper-traded forward — the honest next step before
  real capital is a few weeks of forward paper trades against live prints, not another backtest.

---

## 10. Round 6 — non-directional iron condors (`iron_condor.py`, `iron_condor_validate.py`)

**Why:** the whole single-sided book tops out near 3–4%/month with 1–2 trades/month and marginal
robustness. An iron condor is non-directional — sell a put spread *below* spot **and** a call
spread *above* on the same front-week expiry, collect both credits, profit if the index stays
range-bound. It also fixes the frequency complaint: we open one every weekly expiry (~1/week),
not only when a signal fires. We reuse `backtest.py`'s exact primitives (`build_spread` twice,
`price_leg`, settlement, `compute_stats`) so it's measured on the same footing; only the two-sided
construction and the manage-at-X% exit are new. Cost is doubled to **₹600/round-trip** (8 legs).

### 10a. Sweep (12 configs, `iron_condor_summary.csv`)

| Config | Trades | Win% | Total ret% | CAGR% | MaxDD% | PF | Exit mix |
|---|---|---|---|---|---|---|---|
| IC-1pct-manage50 | 57 | 89.5 | **+57.5** | 52.7 | -14.6 | 1.94 | target 42 / expiry 9 / SL 6 |
| IC-1.5pct-manage50-sl1.5 | 57 | 68.4 | +37.4 | 34.5 | -15.0 | 2.04 | target 50 / SL 6 / expiry 1 |
| IC-1.5pct-hold2expiry | 57 | 75.4 | +34.2 | 31.5 | -24.4 | 1.64 | target 31 / expiry 20 / SL 6 |
| IC-1.5pct-manage75 | 57 | 75.4 | +31.0 | 28.6 | -25.8 | 1.58 | target 49 / SL 6 / expiry 2 |
| IC-1.5pct-manage50 | 57 | 68.4 | +18.9 | 17.5 | -28.7 | 1.35 | target 50 / SL 6 / expiry 1 |
| IC-1.5pct-manage35 | 57 | 63.2 | +18.3 | 16.9 | -26.8 | 1.36 | target 51 / SL 5 / expiry 1 |
| IC-2pct-manage50 | 57 | 35.1 | +13.2 | 12.2 | -13.9 | 1.42 | target 52 / SL 3 / expiry 2 |
| IC-1.5pct-manage50-4d | 40 | 82.5 | +1.8 | 1.6 | -18.9 | 1.03 | target 33 / SL 7 |
| IC-1.5pct-manage50-3d | 55 | 78.2 | -7.4 | -6.9 | -27.8 | 0.91 | target 45 / SL 8 / expiry 2 |
| IC-1.5pct-manage50-lowvol | 30 | 60.0 | -23.1 | -23.6 | -30.1 | 0.51 | target 25 / SL 5 |
| IC-2.5pct-manage50 | 57 | 29.8 | -37.4 | -35.4 | -38.5 | 0.43 | target 54 / SL 3 |

Coherent patterns (these read like real structure, not noise):
- **Tighter shorts win, wider shorts lose.** 1% OTM → +57%; 2.5% OTM → -37%. Wide condors collect
  tiny credit but still eat a full wing when breached — bad risk/reward. Opposite of the naive
  "wider = safer" intuition.
- **Earlier entry hurts** (3d/4d worse than 2d) despite higher win rates — more days = more gamma /
  more chances to be breached while holding through adverse moves.
- **The low-vol regime filter hurt again** (-23%), consistent with Rounds 3–5: "only trade when
  calm" cuts the sample without improving edge.
- **A tight stop (1.5× credit) cut MaxDD roughly in half** (-28.7% → -15.0%) — the one clearly
  useful knob.

### 10b. Robustness checks (`iron_condor_validate.py`) — these change the verdict

**Cost sensitivity** (total return % at 1× / 1.5× / 2× the ₹600 assumption):

| Config | ₹600 | ₹900 | ₹1200 |
|---|---|---|---|
| IC-1pct-manage50 | +57.5 | +40.4 | +23.3 |
| IC-1.5pct-manage50-sl1.5 | +37.4 | +20.3 | **+3.2** |
| IC-1.5pct-hold2expiry | +34.2 | +17.1 | **-0.0** |

→ Every config bleeds ~17 points of return per ₹300 of extra cost. These trade a lot of premium
near expiry; if real slippage on weekly NIFTY condors is worse than the flat estimate, two of the
three headline configs go to ~zero. **The edge is thin relative to transaction costs.**

**Out-of-sample split** (chronological H1 vs H2 of the ~13-month sample) — **the killer finding:**

| Config | H1 (Jun→Dec 2025) | H2 (Dec 2025→Jul 2026) |
|---|---|---|
| IC-1pct-manage50 | +2.7% (win 89%) | +54.8% (win 90%) |
| IC-1.5pct-manage50-sl1.5 | **-8.9%** (win 54%) | +46.3% (win 83%) |
| IC-1.5pct-hold2expiry | **-11.4%** (win 68%) | +45.6% (win 83%) |

→ **Essentially all the profit is in the second half.** Two of three configs *lost money* in H1.
This is not a stable edge — it's one range-bound stretch (H2, almost entirely 2026 data) carrying
the whole result. Textbook regime dependence / "one lucky window."

**Tail** (the high win rate hides fat left tails):

| Config | Worst trade | Worst 5 sum | Avg win | Avg loss | Win:loss size |
|---|---|---|---|---|---|
| IC-1pct-manage50 | -14.9% cap | **-57.6% cap** | ₹2,331 | ₹-10,232 | 1 : 4.4 |
| IC-1.5pct-manage50-sl1.5 | -12.7% cap | -32.2% cap | ₹1,884 | ₹-2,003 | 1 : 1.1 |
| IC-1.5pct-hold2expiry | -16.6% cap | -48.4% cap | ₹2,038 | ₹-3,818 | 1 : 1.9 |

→ The flashy +57% config loses ~4.4× its average win on each loser; its **worst 5 trades sum to
-58% of capital.** An 89% win rate + a tail like that = the classic premium-selling trap: it looks
flawless until a handful of breaches cluster. Only the **sl1.5** config has a sane ~1:1 win/loss
size, because the 1.5× stop caps the losers — that's loss control doing the work, not the entry.

### 10c. Verdict

The iron condor **does not deliver a robust "better than 3–4%/month" edge**, and it would be
dishonest to present the +57% number as one. What it actually delivers:
- ✅ **Frequency** — ~1 trade/week, non-directional (real improvement over 1–2/month).
- ❌ **Regime-dependent** — the entire backtest edge sits in H2; it was flat-to-losing in H1.
- ❌ **Cost-fragile** — thin margin over transaction costs; dies at 2× the cost assumption.
- ❌ **Fat left tail** — high win rate masks losses 2–4× the size of wins; the un-stopped configs
  can give back half of capital in their worst 5 trades.

The one durable lesson is risk management, not the structure: the **1.5× stop (`sl1.5`) is the only
variant with a healthy loss profile** and it halves drawdown — loss control matters more than the
entry. For this to become real capital, it needs multi-year / multi-regime data, a realistic
(measured, not assumed) cost model, and explicit tail budgeting — the same honest next step as
Round 5: forward paper trades against live prints, not another in-sample backtest.

---

## 6. File map

```
options_backtest/
├── scripts/
│   ├── backtest.py          # core engine + 13 hand-built strategies (Round 1)
│   ├── zen_sweep.py         # 12-signal x 4-width parallel sweep (Round 2)
│   ├── round3_followups.py # offset sweep + bootstrap + flow x expiry-hold (Round 3)
│   ├── round4_flow_robustness.py # offset sweep + bootstrap on the 6 flow winners (Round 4)
│   ├── round5_deployability.py # stress test + cost sweep + horizon bootstrap (Round 5)
│   ├── iron_condor.py       # non-directional condor engine + 12-config sweep (Round 6)
│   ├── iron_condor_validate.py # cost / OOS-split / tail checks on top condors (Round 6)
│   └── fetch_bhavcopy.py    # NSE F&O EOD data downloader
├── data/
│   └── nifty_fo_daily.csv   # NIFTY futures+options daily bhavcopy (source data)
└── output/
    ├── strategy_summary.csv         # Round 1 results table (13 rows)
    ├── trades_<strategy>.csv        # Round 1 per-trade logs (13 files)
    ├── zen_sweep_summary.csv        # Round 2 results table (48 rows)
    ├── trades_Zen-<signal>-<width>.csv  # Round 2 per-trade logs (48 files)
    ├── round3_offset_sweep.csv      # Round 3: entry-offset sweep on the 2 winners
    ├── round3_bootstrap.csv         # Round 3: bootstrap resample stats on the 2 winners
    ├── round3_flow_expiry_summary.csv  # Round 3: pcr/oi_bias x expiry-hold results (8 rows)
    ├── round4_flow_offset_sweep.csv # Round 4: offset sweep on the 6 flow winners (24 rows)
    ├── round4_flow_bootstrap.csv    # Round 4: bootstrap on the 6 flow winners (6 rows)
    ├── round5_stress_summary.csv    # Round 5: high-vol-event stress test on the 2 winners
    ├── round5_stress_<strategy>.csv # Round 5: per-trade adverse-move log for each winner
    ├── round5_cost_sweep.csv        # Round 5: ROUND_TRIP_COST sensitivity (150/300/500/750)
    ├── round5_horizon_bootstrap.csv # Round 5: 3/4-trade near-term outcome band
    ├── iron_condor_summary.csv      # Round 6: 12-config condor sweep results
    └── ic_trades_<config>.csv       # Round 6: per-config condor trade logs
```
