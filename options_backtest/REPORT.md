# NIFTY Hedged Credit-Spread Strategy Family — Real-Data Backtest Report

**Goal:** Independently test whether a Stratzy-style "credit spread overnight/expiry" algo
family (the 13 marketplace algos: Zen Credit Spread Overnight, Curvature, Delta-Leverage,
Mathematician's, SkewHunter, SkewHunter TSL, Fixed RR 1:3, Vacuum GRID, Damper, Convex,
Settle-Down, Delta-Rotation, Delta-Shift) is actually worth trading, and — since their
underlying signal/entry logic isn't disclosed — build and backtest 13 **transparent,
auditable** variants of the same *style* of trade (short-strike vertical credit spread on
NIFTY, either closed at next-day open or held to expiry) on real NSE data, so the
mechanics and numbers can be fully sanity-checked instead of trusted on faith.

## TL;DR

- **11 of 13 variants lose money.** Every "overnight hold" variant is net negative over
  the test window, several catastrophically (-45% to -71% "return" on capital, with
  40-75% max drawdown).
- **Only 2 of 13 are profitable**, and both are **expiry-hold, not overnight-hold**:
  `Momentum-Expiry-HoldToSettle` (+24.9% total, 91.1% win rate, 56 trades) and
  `MeanReversion-Expiry` (+17.3% total, 77.8% win rate, 18 trades).
  Both use position-managed exits (SL/target checked daily), not a blind hold-to-close.
- **The "always sell a put spread with no signal" baseline (`ThetaHarvest-Baseline-Expiry`)
  loses money** (-8.0%, -25.6% max DD) — proving the edge in the two winners is not
  "theta always wins", it's the entry-timing signal + the expiry-hold risk management.
- **This strongly corroborates the earlier finding from Stratzy's own public Telegram
  history**: the *overnight* variants of this algo family are structurally exposed to
  gap risk and lose on a realistic sample, while the *expiry-hold* variants with active
  SL/target management are where whatever edge exists actually shows up.
- Sample sizes are still modest (mid-teens to ~200 trades over ~13 months) — **not**
  large enough to fully rule out variance, especially for the 18-trade
  `MeanReversion-Expiry` result. Treat this as directional evidence, not proof.

## Data & Methodology

- **Source:** NSE F&O Bhavcopy (UDiFF format), downloaded directly from
  `nsearchives.nseindia.com` for every trading day in the sample window — real,
  settlement-grade per-contract OHLC, volume and open interest (not synthetic
  Black-Scholes pricing). ~13 months of NIFTY options + futures data,
  `options_backtest/data/nifty_fo_daily.csv` (~87MB, gitignored — regenerate with
  `python options_backtest/scripts/fetch_bhavcopy.py`; see `data/fetch_log.txt` for the
  per-day fetch audit trail).
- **Universe:** NIFTY index options (`IDO`) and front-month NIFTY futures (`IDF`), used
  as the underlying spot-price proxy (front-month future close each day).
- **Entry:** at entry-day close (`ClsPric`) for both legs — approximates the ~15:20
  entry these platforms describe.
- **Overnight-hold strategies:** exit at next trading day's open (`OpnPric`) for both
  legs — matches the platform's stated "exits at the morning open" overnight behavior.
- **Expiry-hold strategies:** entered `entry_offset_days` (2) trading days before expiry,
  then walked forward day-by-day on close-to-close marks, checking stop-loss and target
  each day and exiting on whichever triggers first, or at expiry settlement if neither
  triggers. This is an EOD-mark walk, **not** an intraday tick simulation — real
  intraday stop-outs could differ (better or worse) from what's modeled here.
- **Liquidity filter:** only strikes with `TtlTradgVol > 0` on the day are eligible;
  if the target strike isn't tradable, search for the nearest tradable strike within
  150 points; otherwise skip the trade for that cycle (logged, not silently dropped).
- **Costs:** flat Rs 300 per round trip (4 legs: 2 entry + 2 exit) deducted from every
  trade as a brokerage/STT/slippage estimate.
- **Capital & sizing:** Rs 1,00,000, 1 lot (75) per trade, no compounding — matches the
  platform's own stated margin requirement for this algo family, so % returns are
  directly comparable to the marketplace's advertised return %.
- **Signals tested** (see `scripts/backtest.py` for exact code): 3-day momentum, 10-day
  momentum, 1-day mean-reversion fade, vol-expansion breakout, vol-contraction/calm
  regime, futures gap-position-in-range, PE/CE price-skew fade, and a no-signal
  "always sell a bullish put spread" baseline.

## Full Results (sorted by total return)

| Strategy | Trades | Win % | Total PnL (Rs) | Return % | Max DD % | Profit Factor | Hold |
|---|---:|---:|---:|---:|---:|---:|---|
| Momentum-Expiry-HoldToSettle | 56 | 91.1 | 24,874 | +24.87 | -7.35 | 2.35 | expiry |
| MeanReversion-Expiry | 18 | 77.8 | 17,246 | +17.25 | -3.13 | 5.12 | expiry |
| Conservative-Wide-Expiry | 30 | 23.3 | -2,314 | -2.31 | -3.36 | 0.60 | expiry |
| ThetaHarvest-Baseline-Expiry | 57 | 78.9 | -8,002 | -8.00 | -25.64 | 0.83 | expiry |
| MeanReversion-Overnight | 84 | 32.1 | -14,764 | -14.76 | -23.27 | 0.60 | overnight |
| GapPosition-Overnight | 174 | 27.6 | -29,692 | -29.69 | -35.09 | 0.52 | overnight |
| LowVol-Tight-Overnight | 109 | 27.5 | -38,711 | -38.71 | -40.81 | 0.25 | overnight |
| VolExpansion-Overnight | 84 | 28.6 | -39,698 | -39.70 | -39.86 | 0.26 | overnight |
| TrendFollow10d-Overnight | 202 | 30.2 | -40,976 | -40.98 | -41.29 | 0.50 | overnight |
| Aggressive-Tight-Overnight | 202 | 38.6 | -45,656 | -45.66 | -47.94 | 0.54 | overnight |
| SkewFade-Overnight | 190 | 33.7 | -54,660 | -54.66 | -56.67 | 0.40 | overnight |
| Momentum-Overnight-Wide | 202 | 15.3 | -66,982 | -66.98 | -67.06 | 0.22 | overnight |
| Momentum-Overnight-Tight | 202 | 34.2 | -71,152 | -71.15 | -74.66 | 0.38 | overnight |

Full per-trade logs for every strategy: `options_backtest/output/trades_<name>.csv`.
Machine-readable summary: `options_backtest/output/strategy_summary.csv`.

## What this means for "should I trade this algo family"

1. **Don't run the overnight variants blind.** All 6 overnight-hold designs tested here
   lose money, several severely, because a single adverse gap wipes out many days of
   collected premium (avg loss per losing trade is consistently 1.3-2x the avg win — the
   classic "picking up nickels in front of a steamroller" credit-spread failure mode).
   This matches what the earlier Telegram-history review already suggested about
   Stratzy's own overnight algos being survivorship/selection-biased "top performer of
   the day" picks, not a real edge.
2. **The expiry-hold style has real signal**, but only when paired with active
   SL/target management (not blind hold-to-settle) and a **timing signal** (momentum or
   mean-reversion), not zero signal. `ThetaHarvest-Baseline-Expiry` (no signal, always
   sell) still loses money and has the worst drawdown of the profitable-hold-type group,
   which is the key evidence that "sell premium, collect theta" alone isn't the edge —
   entry timing is.
3. **`Momentum-Expiry-HoldToSettle` is the standout**: 91.1% win rate, profit factor
   2.35, max drawdown only -7.35% over 56 trades across ~13 months. This is the closest
   analog in this test to what a disciplined trader should actually run:
   - Enter a directional credit spread 2 trading days before weekly expiry, direction
     set by 3-day price momentum.
   - Manage the position daily against a stop-loss (1.5x credit received) and a take
     -profit (60% of max profit), don't just hold blindly to settlement.
   - This is "moderate stoploss, aggressive target" in spirit — SL is a hard multiple of
     credit, target is taken early (60%) rather than greedily held to max, which is what
     keeps drawdown small.
4. **Caveats before trusting this as tradeable:**
   - EOD-mark backtest, not tick-level — real fills/slippage on the SL/target triggers
     within a day could differ.
   - 56 and 18 trades respectively are still a modest sample; `MeanReversion-Expiry`'s
     18-trade, 77.8%-win-rate result in particular should be treated as a hint, not a
     conclusion, until it's run over a longer/rolled-forward window.
   - No parameter search/optimization was done — the SL multiple (1.5x), target
     fraction (0.6), OTM width, and entry-offset (2 days) were picked once from
     reasonable priors, not tuned to these results. That's a deliberate choice to avoid
     overfitting, but it also means there may be a materially better (or worse)
     parameterization nearby that wasn't explored.
   - Flat Rs 300/round-trip cost is an estimate; real costs depend on broker and are
     higher for less liquid strikes forced into the 150-point liquidity-fallback search.

## Recommendation

- **Do not subscribe to / run the overnight-hold algos in this family** (which appears
  to include most of the marketplace's 13 listed variants) based on this evidence.
- **The expiry-hold, momentum-timed, actively-managed design is worth developing
  further** as the basis of a personal strategy — it is the only variant here that
  matches the user's stated goal of "moderate stoploss and aggressive targets" while
  showing a real (if not yet fully proven) edge on real data.
- Next steps to de-risk before committing real capital: (a) extend the data window as
  more history becomes available, (b) walk-forward / out-of-sample validate rather than
  single-window backtest, (c) paper-trade the exact `Momentum-Expiry-HoldToSettle` rules
  for a few live expiry cycles before sizing up.

## Reproducing this backtest

```bash
cd options_backtest/scripts
python fetch_bhavcopy.py     # re-downloads the NSE bhavcopy CSVs -> ../data/nifty_fo_daily.csv
python backtest.py           # runs all 13 strategies -> ../output/*.csv
```
