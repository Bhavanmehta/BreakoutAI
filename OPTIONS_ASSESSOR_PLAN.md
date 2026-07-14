# Options Trade Assessor — Session Handoff & Plan

Personal-use options risk/reward assessment + backtesting for the BreakoutAI site.
Single user (site owner), trades Nifty weekly expiries intraday, buys CE/PE directionally.
This file is the single source of truth to resume work in a fresh session.

---

## Operating mode (STRICT — agreed with user)

- **Fable (orchestrator):** ideation, design direction, architecture, data contracts,
  implementation specs, and reviewing/critiquing output. Reads files/greps for review.
  **Writes zero implementation code** (HTML/CSS/JS/Python).
- **Trivial-patch exemption:** Fable may directly apply fixes of **≤ 3 lines** (typo-class
  bugs found in review). Anything larger is dispatched.
- **Sonnet (via Agent dispatch):** ALL implementation code, driven by a full written spec.
- **Verification commands** (running servers, curl checks, `node --check`, tests) are
  **also dispatched** to agents — Fable does not run them directly.
- Docs/specs/plans (like this file) are Fable's to write.

---

## Repo orientation

- Root: `c:\Users\bhava\OneDrive\Documents\GitHub\BreakoutAI` (git repo, win32, PowerShell primary)
- Pattern: single-file HTML pages (Tailwind CDN, dark theme) + `api/*.py` Vercel-style
  handlers (`BaseHTTPRequestHandler`, stdlib + `requests` only) + `scripts/dev_server.py`
  local router (run: `python scripts/dev_server.py 8010` → http://localhost:8010).
- Key files:
  - `options_assessor.html` — the assessor page (self-contained; uses `scripts/options_math.js`)
  - `api/options_chain.py` — Dhan option-chain proxy. Reusable plumbing:
    `_load_env_file()` (reads `backend/.env`), `_env()` (env names: `DHAN_Client_ID`,
    `DHAN_Access_TOKEN`), `ConfigError`, `ProviderError`, `SymbolNotFoundError`,
    `LiveDhanProvider` (headers/_post with 429 handling), `MockDhanProvider` fallback,
    `_resolve_symbol(symbol) -> (scrip_id, segment, lot)` via Dhan scrip-master CSV
    (`https://images.dhan.co/api-data/api-scrip-master.csv`, cached 24h in Upstash +
    warm-lambda memo, key `optchain:fnomap:v1`), `SYMBOL_MAP` for 5 indices
    (NIFTY=13, BANKNIFTY=25, FINNIFTY=27, MIDCPNIFTY=442, SENSEX=51; seg `IDX_I`),
    `INDEX_LOT_FALLBACK` = {NIFTY:75, BANKNIFTY:35, FINNIFTY:65, MIDCPNIFTY:140, SENSEX:20}.
  - `scripts/dev_server.py` — routes `/api/quotes`, `/api/index_ohlc`, `/api/options_chain`
    (actions: `expirylist`, `chain`, `symbols`).
  - `scripts/options_math.js` — Black-Scholes math used by the page (Python port kept in
    sync by hand inside `api/options_chain.py`).
  - `combined_breakout_scanner_platform.html` — main scanner (links to assessor; NO query
    params are passed anymore, link is bare by design).
- Env/creds: `backend/.env` — `DHAN_Client_ID`, `DHAN_Access_TOKEN`, `UPSTASH_REDIS_REST_URL`,
  `UPSTASH_REDIS_REST_TOKEN`, `WATCHLIST_SECRET`. Never expose creds to the browser; all
  Dhan calls go through the server proxy.

## Assessor page — current behavior (all verified working)

- Symbol picker fed by `action=symbols` (5 indices + ~210 F&O stocks from scrip-master);
  any F&O symbol resolves (not index-only).
- Expiry dropdown: nearest **5** expiries only (`EXPIRIES.slice(0, 5)`).
- Strike ladder: ATM **±5** window with ▲/▼ scroll rows (`windowRows`, `LADDER_OFF`,
  click handler on `[data-scroll]`); works for both live chain and synthetic BS fallback.
- Fetch flow: `loadExpiries()` → `fetchLiveChain()`; sets spot/IV/days/lot-size from the
  live chain; auto-picks the ATM leg for current CE/PE side via `pickLeg`.
- `pickLeg(strike, leg, prem)` now **re-defaults SL on every leg pick** to the ~20%
  premium-loss underlying level (`slForPremiumLoss`, first-order delta approx; live delta
  from `LIVE_CHAIN` when chain matches, BS delta fallback). SL stays user-editable.
- Live/DEMO source badge; Mock provider fallback only for outages, never for unknown symbols.
- Trades saved to localStorage; verdict + metrics grid + payoff SVG render on recompute.

---

## Current status (checked 2026-07-12) — READ THIS FIRST IN A FRESH SESSION

Section A below is **fully built (items 1–5), but not backtested for real yet**:
- ✅ Done (commit `ad240ac5f`): items 1–3 (`api/options_backtest.py` — SQLite cache,
  `download`/`status`/`bars`/`backtest`/`replay` actions, both strategies, cost model,
  stats, assessor-replay bucketing) and item 5 (routed into `scripts/dev_server.py`).
- ✅ Done (uncommitted, Sonnet-built + Fable-reviewed + Playwright-verified): item 4 —
  `options_backtest.html` (status/coverage table, download form with call estimate,
  backtest params + costs override fieldset, straddle-aware fields/columns, summary
  cards, SVG equity curve, daily-P&L calendar heatmap, sortable 500-cap trade tables,
  replay scorecard with `expiry_weekday`) + `#optionsBacktestLink` ("🧪 Backtest") in
  `combined_breakout_scanner_platform.html`'s nav. **Not committed yet.**
- ✅ **Real backtest numbers exist now** (run 2026-07-12, agent-executed, results below).
  `backend/backtest_cache.db` (gitignored, local-only) holds **19,200 bars**: NIFTY,
  WEEK expiry code 1, **ATM only**, CE+PE, 5-min, 2026-01-01 09:15 → 2026-07-10 15:25 IST.
  Note: the planned ATM±2 offsets were NOT downloaded (deviation — ATM sufficed for all
  runs below; pull offsets later if a strategy needs them).
- ✅ **Niggle C resolved**: `expiry_flag=WEEK&expiry_code=1` verified live = nearest
  weekly expiry. Sample bars sane (IST market hours, spot ~24k, strike = round 50 near
  spot, ATM weekly premiums ~82–108). Download: 7 chunks, 14 calls, 0 errors, 61.7s.

### Real results (NIFTY ATM, 2026-01-01 → 2026-07-10, defaults: 09:20 entry, 15:15 EOD, SL 20%, target 40%, 1 lot, post-cost)

| strategy | trades | win rate | expectancy | PF | max DD | net P&L | costs |
|---|---|---|---|---|---|---|---|
| long CE ATM | 128 | 28.1% | −736.46 | 0.372 | 96,002.81 | −94,266.45 | 8,246.75 |
| long PE ATM | 128 | 20.3% | −997.41 | 0.212 | 128,065.83 | −127,668.87 | 8,044.82 |
| short straddle (roll) | 2544 | 51.7% | +42.78 | 1.229 | 26,662.61 | +108,829.93 | 327,557.32 |

Assessor replay (128 trades/side, skipped_no_iv 0, expiry_weekday=3):

| side | tier | trades | win rate | expectancy | avg R:R | avg PoP |
|---|---|---|---|---|---|---|
| CE | Unfavorable | 79 | 31.7% | −725.66 | 0.094 | 0.401 |
| CE | Marginal | 49 | 22.5% | −753.86 | 4.039 | 0.852 |
| PE | Unfavorable | 64 | 14.1% | −1257.07 | 0.047 | 0.332 |
| PE | Marginal | 64 | 26.6% | −737.76 | 4.874 | 0.840 |

**Findings (the honest read):**
1. **Verdict tiers do NOT separate performance.** Zero trades ever scored above
   "Marginal" in 6 months, so "does Favorable outperform?" is unanswerable on this data.
   Within populated tiers the signal is contradictory: PE Marginal beats Unfavorable,
   CE inverts. Every tier, both sides, negative expectancy.
2. Long single-leg ATM intraday with these defaults is just a losing strategy in this
   regime (theta + costs). The straddle is net-positive but with a brutal cost drag
   (₹327.5k costs on ₹108.8k net) — cost-sensitive, not robust.
3. This is direct evidence Section B (market IV/Greeks, Black-76, better verdict model)
   is correctly sequenced next — the current BS verdict scheme isn't predictive enough
   to be worth threshold-tuning.

**Next up, in order:**
1. ~~Section A item 4 (UI + nav link)~~ ✅ done, **pending commit** (3 files).
2. ~~Real download + backtest/replay numbers~~ ✅ done (above).
3. ~~Section B1-B3 (Black-76 forward, market IV/Greeks, intraday horizon)~~ ✅ shipped,
   replay re-run still didn't separate tiers.
4. ~~Section B5 (calibrate theoretical reprice to real entry premium)~~ ✅ shipped —
   **made the inversion WORSE**, not better (see B5 results below). Favorable still 0
   trades on both sides across the full 6-month real dataset.
5. **ROOT CAUSE FOUND (session 2026-07-13, see B6 below): the verdict tier and the real
   P&L are scored against two different, disconnected trade definitions** — not a
   calibration problem, a wiring bug. Confirmed by code read of `action_replay()`/
   `assess()` in `api/options_backtest.py`, plus external research confirming our EOD/
   intrabar backtest *methodology* itself is sound (so don't rearchitect around tick
   data — fix the scoring wiring). **B7 is the concrete, not-yet-built fix — read B6/B7
   below, then dispatch to Sonnet.** Do not attempt further threshold/calibration tuning
   (no "B8: tune constants again") — the disconnect must be fixed first or any new
   calibration will just be re-tuning noise.

---

## TODO — pending work (in priority order)

### A. Backtesting feature (user picked: build BOTH in one pass) — item 4 (UI) is NEXT UP, dispatch to Sonnet

Research findings (verified 2026-07):

- **Dhan Expired Options API**: `POST https://api.dhan.co/v2/charts/rollingoption`
  (docs: https://dhanhq.co/docs/v2/expired-options-data/). Headers same as option-chain
  (`access-token`, `client-id`). Body (all required): `exchangeSegment` (e.g. `NSE_FNO`),
  `interval` (1|5|15|25|60 min), `securityId` (underlying scrip id — same resolution as
  option-chain), `instrument` (`OPTIDX`/`OPTSTK`), `expiryCode` (int), `expiryFlag`
  (`WEEK`|`MONTH`), `strike` (`ATM`, `ATM+1`, `ATM-1`, …), `drvOptionType` (`CALL`|`PUT`),
  `requiredData` (subset of open/high/low/close/iv/volume/strike/oi/spot),
  `fromDate`/`toDate` (toDate non-inclusive).
  Constraints: **max 30 days per call**; **5 years** history; **minute-level** storage;
  strikes up to **ATM±10 for index options near expiry, ATM±3 otherwise**.
  Response: `data.ce` / `data.pe` objects of parallel arrays (`open[]...timestamp[]` epoch).
- **Reference repo**: marketcalls/ExpiryFlow (MIT) — patterns to borrow: chunked downloads
  + rate limiting + duplicate detection; IST normalization at storage time; commission
  slabs stored as editable data; layering routers → services → data manager.
- Existing tools (AlgoTest etc.) backtest generic strategies; our differentiator is
  replaying the ASSESSOR's own verdict logic ("Assessor Replay").

Build spec (agreed):

1. **Data layer** — `api/options_backtest.py`:
   - Chunked (≤30-day) pulls from `rollingoption`, honoring rate limits (sleep ~3s between
     calls like option-chain), duplicate-safe upserts.
   - Local **SQLite** cache (stdlib; NOT DuckDB — avoid new deps). Suggested file:
     `backend/backtest_cache.db` (gitignored). Table `bars(symbol, expiry_flag,
     expiry_code, strike_off, opt_type, interval_min, ts INTEGER, open, high, low, close,
     volume, oi, iv, spot, PRIMARY KEY(symbol, expiry_flag, expiry_code, strike_off,
     opt_type, interval_min, ts))`. Store IST-normalized timestamps.
   - Actions (GET, mirroring options_chain conventions): `download` (params: symbol, from,
     to, interval, strikes e.g. `ATM-2..ATM+2`, both CE/PE), `status` (cache coverage),
     `bars` (raw explorer), `backtest` (run engine), `replay` (assessor replay).
   - Note: Vercel serverless has no persistent disk — this feature is **local-only**
     (dev_server). Guard: if running on Vercel, return a clear "local only" error.
2. **Engine — strategy lab** (same file or `api/backtest_engine.py`):
   - Strategy 1: **directional long CE/PE** — params: entry time (default 09:20), side,
     strike offset (default ATM), SL = 20% premium loss (param), target = premium %
     (param), EOD flat 15:15, lots + lot size.
   - Strategy 2: **short ATM straddle with re-strike roll** (ExpiryFlow-style): sell ATM
     CE+PE at open; when spot's ATM strike changes, close and re-enter; EOD flat.
   - **Cost model** (editable data, defaults): brokerage ₹20/order; STT 0.1% on sell
     premium; NSE options txn fee 0.03503% on premium; SEBI ₹10/crore; GST 18% on
     (brokerage + txn); stamp 0.003% on buy premium.
   - Outputs: trade log, daily P&L, equity curve, win rate, expectancy, profit factor,
     max drawdown, avg win/loss — all post-cost.
3. **Assessor Replay** (the differentiator):
   - At each entry snapshot, compute the assessor verdict inputs (premium, IV, days,
     SL/target distances → R:R, PoP) from historical bars; bucket trades by verdict tier;
     report per-tier win rate/expectancy → does "Good R:R" actually outperform?
   - Keep verdict math identical to the page's (`scripts/options_math.js` logic; Python
     port already exists in `api/options_chain.py`).
4. **UI** — `options_backtest.html` (match existing dark theme / Tailwind CDN / fonts of
   `options_assessor.html`): params panel (symbol, date range, strategy, costs), download
   progress, equity curve (SVG like payoff diagram — no new chart deps), calendar heatmap
   of daily P&L, sortable trade table, assessor-replay scorecard.
5. **Routing** — add `/api/options_backtest` to `scripts/dev_server.py`; link the new page
   from the platform nav. E2E test with live Dhan creds via dispatched agent.

### B. Model overhaul — DESIGN SPEC (Fable, 2026-07-12; grounded in replay findings)

**Why the current verdict fails (from the real replay run):**
- (a) "Favorable" never fires: the `thetaPctOfPrem < 0.08` gate is impossible for weekly
  ATM options (theta ≈ 15–25%/day of premium). Gate assumes multi-day holds; the
  strategy is intraday. Marginal tier already averages rr 4.0 / pop 0.85 — theta gate
  alone demotes everything.
- (b) PoP is computed as probTouch over FULL days-to-expiry, but trades exit EOD the
  same day → pop 0.84–0.85 vs realized win rate 22–27%. Wrong horizon dominates any
  model-choice error.
- (c) Plain BS on cash spot ignores carry basis (small vs (a)/(b), but free to fix).

**B1. Forward via put-call parity (replaces "fetch futures") — spec:**
NIFTY weekly futures don't exist (futures are monthly), so "matching-expiry futures
price" is unfetchable for weeklies. Instead imply the forward from the chain itself:
`F = K_atm + (C_atm − P_atm) · e^(rT)` using the ATM strike's CE/PE ltp already in the
chain response. Implement in `scripts/options_math.js` (`impliedForward()`) + Python
port in `api/options_chain.py`. All d1/d2 math switches to Black-76 form:
`d1 = [ln(F/K) + σ²T/2] / σ√T`, price discounted `e^(−rT)`. Fallback: if ATM CE/PE ltp
missing, `F = spot · e^(rT)`. No new API calls, no new deps.

**B2. Market IV + Greeks first, BS fallback — spec:**
Dhan chain already returns per-leg `iv, delta, theta, gamma, vega`. `assess()` gains
optional `marketGreeks` + `marketIv` inputs; when present they REPLACE computed values
(delta/theta/gamma/vega straight from chain; IV feeds scenario repricing). BS-computed
values only when absent (mock provider / stale leg). UI passes the picked leg's chain
greeks through. Keep the Python port in sync (replay must use the same logic).

**B3. Fix the verdict for intraday (the actual bug) — spec:**
- PoP horizon: probTouch with `tYears = min(intraday horizon, time to expiry)` where
  intraday horizon = time from now to 15:15 IST (fraction of a trading day), NOT days
  to expiry. Expose `horizonDays` input; assessor page defaults it to same-day.
- Theta gate: replace absolute `< 8%/day` with theta cost over the HOLDING window
  (≈ theta · horizon fraction) compared to reward — e.g. flag when projected theta
  spend > 25% of projected reward. Thresholds stay data-tunable.
- Scenario repricing (`markAtTarget/markAtStop`): time decay = horizon fraction, not
  "half time to expiry".
- Acceptance bar (agreed): re-run the replay scorecard after implementation — tiers
  must actually separate (Favorable > Marginal > Unfavorable on win rate/expectancy),
  and at least some trades must reach Favorable. If tiers still don't separate, the
  verdict scheme itself goes back to design.

**B4. Model stack — RECOMMENDATION (needs user sign-off before build):**
- **Binomial: NO.** Index options are European; binomial only adds early-exercise
  handling — pure cost, zero benefit here.
- **Heston: NO.** Stochastic-vol calibration needs a vol surface + fitting machinery for
  a single-leg intraday verdict — massive complexity, and (a)/(b) above show the errors
  are horizon/threshold bugs, not vol-model bugs. YAGNI.
- **YES: practitioner Black-76** = B1 (parity-implied forward) + B2 (market IV/Greeks)
  + B3 (intraday horizon). This IS the practitioner stack. Cheapest change that can
  move the acceptance bar; re-evaluate fancier models only if B1–B3 fail it.

Sequencing: B1+B2+B3 in one Sonnet dispatch (math JS + Python port + assessor UI wiring
+ self-test extensions), then replay re-run to score it.

### B5. Calibration attempt — RESULT: did not fix it (session 2026-07-13)

B1–B3 shipped (commit `380247bee`/`4db277dea` range — see git log). Replay re-run showed
tiers *still* not separating, so B4/B5 (calibrate theoretical BS reprice to the real
observed entry premium via ratio `k = premium/theo_at_entry`, clamped [0.2, 5.0]) was
dispatched and implemented in `assess()` (`api/options_backtest.py` + JS port).

**Post-B5 replay vs pre-B5 (NIFTY ATM, 2026-01-01→2026-07-10):**

| Side | Tier | trades | win_rate | expectancy/trade | avg_rr | avg_pop |
|---|---|---|---|---|---|---|
| CE | Unfavorable | 98 | 35.7% (was 33.3%) | −517 | 1.69 | 0.204 |
| CE | Marginal | 30 | **3.3%** (was 20.0%) | −1,454 | 1.43 | 0.446 |
| CE | Favorable | 0 | — | — | — | — |
| PE | Unfavorable | 93 | 24.7% (was 16.9%) | −877 | 1.67 | 0.209 |
| PE | Marginal | 35 | **8.6%** (was 25.5%) | −1,318 | 1.40 | 0.495 |
| PE | Favorable | 0 | — | — | — | — |

**Conclusion: B5 made the inversion WORSE, not better.** Favorable still never fires.
PE flipped from "roughly right direction" (Marginal beating Unfavorable pre-B5) to
inverted too. This is the signal that stopped further calibration attempts — **the bug
is not in the reward/risk ratio's calibration, it's structural.** Do not dispatch a "B6:
tune constants further" task; read the root cause below first.

### B6. ROOT CAUSE (diagnosed 2026-07-13, confirmed by code read, not yet fixed) — READ THIS BEFORE TOUCHING B AGAIN

Traced in `api/options_backtest.py`, function `action_replay()` (~L863) calling
`assess()` (~L240):

- **The real P&L and the verdict tier are scored against two DIFFERENT, disconnected
  definitions of "target/stop", and neither is ever checked against the other:**
  - Real P&L (`_run_long`, ~L684): simple **% of premium** SL/target (`sl_pct=0.20`,
    `target_pct=0.40`), checked against real intrabar **premium** low/high on cached
    historical bars (SL checked first if both hit same bar). This produces the actual
    `net_pnl` / win-loss that the tiers are graded against.
  - Verdict tier (`assess()`): `action_replay` separately back-solves a **delta-
    linearized underlying** SL/target from those same premium %'s
    (`sl_u = spot - premium*sl_pct/|delta|`, mirror for target), then computes its OWN
    theoretical `rr`/`pop`/`thetaCostPctOfReward` from a **Black-Scholes projection of
    what SHOULD happen** at those underlying levels (`prob_touch`, a BS barrier-touch
    formula) — this projection is never validated against what the real trade actually
    did. It's a live re-derivation of a "should this workout" score, structurally blind
    to the real path.
  - Net effect: **the tier a trade lands in is close to statistically independent of
    whether it actually won or lost.** That's exactly the non-monotonic win-rate/
    expectancy-by-tier result above, and no amount of calibrating the *ratio* (B5) can
    fix a *decoupled-signal* problem.
- **Second, independent bug: the "Favorable" gate is close to unsatisfiable by
  construction.** It requires `rr>=2 AND pop>=0.40 AND thetaCostPctOfReward<=0.25`
  simultaneously (see `assess()` ~L363-370). For a single directional option trade, `rr`
  and `pop` are naturally *inversely* related (a target close to spot has high touch-
  probability but small reward; a far target has the reverse) — demanding both be high
  at once is close to impossible regardless of real edge, which is consistent with
  Favorable being 0/all-trades across the entire 6-month real dataset on both sides.

**External research check (2026-07-13) — is EOD/theoretical-repricing backtesting even
the right category of approach, before assuming it's a code bug?** Findings (sourced):
- Daily/EOD backtesting has real, well-documented failure modes (stop-loss detection
  gaps that *overstate* wins, path-dependency bias from single-trade-at-a-time entry,
  flat-IV BS mispricing away from ATM vs the real volatility smile/skew) — but these
  bias results **optimistic**, not the pessimistic skew we're seeing, and we already do
  the mitigations (real intrabar high/low on real cached bars, real entry premium, a
  cost model). Sources:
  [tradealgo.com](https://www.tradealgo.com/trading-guides/options/options-backtesting-how-to-test-strategies-before-risking-real-capital),
  [edeltapro.com](https://www.edeltapro.com/blog/why-use-end-of-day-prices-for-options-backtesting),
  [greekslab.com](https://greekslab.com/blog/best-practices-for-backtesting-0dte-options-strategies),
  [ryanoconnellfinance.com](https://ryanoconnellfinance.com/volatility-smile-skew/).
- **Verdict: methodology category is fine (EOD/theoretical + real intrabar SL/target is
  standard practice). The bug is the two-model disconnect above, not "wrong kind of
  backtest."** Do not rebuild on tick data / rearchitect around path-dependent
  simulation — fix the scoring/verdict wiring instead.

**B7 — RECOMMENDED FIX (not yet built, needs a fresh Sonnet dispatch):**
1. **Score the verdict against the SAME rule that actually trades.** Feed `assess()`
   the real premium-based SL/target (or better: compute `rr`/`pop`/theta directly off
   the premium path, not a delta-backed-out underlying level) so the tier's inputs and
   the real P&L's inputs are the same trade definition. Remove the
   `sl_u`/`tgt_u` delta-linearization detour in `action_replay()`.
2. **Replace the 3-way AND-gate with thresholds fit to real outcomes, or a single
   composite score.** Either (a) empirically pick `rr`/`pop`/theta cut points so tiers
   are monotonic in realized win-rate/expectancy on a held-out slice of the real cached
   data (proper fit/validate split, not eyeballed constants), or (b) collapse to one
   expectancy-weighted score instead of three independently-gated conditions.
3. **Add a CI-style acceptance script** (like the parity-check script used for B1-B3)
   that re-runs the replay scorecard and FAILS if tiers aren't monotonic
   (Unfavorable ≤ Marginal ≤ Favorable on both win-rate and expectancy) — don't rely on
   eyeballing scorecard tables again; this exact bug slipped through B1-B5 because nobody
   asserted monotonicity automatically.
4. Keep everything else (intrabar OHLC exits, cost model, real cached bars) — those are
   not the problem, don't touch them.
5. Acceptance bar unchanged from B3: re-run replay, tiers must separate AND at least
   some trades must reach Favorable, on the real NIFTY ATM dataset already cached in
   `backend/backtest_cache.db` (no new download needed).

**Do NOT start Section D (option selling) until B7 lands** — D3's credit-side verdict
model would inherit the same disconnected-scoring bug if built on top of unfixed B math.

### C. Known open niggles

- `expiryCode` semantics for `rollingoption` need verification against the instrument
  list docs during implementation (WEEK/MONTH + code index — verify with one live call
  before bulk download).
- Rate limits for `rollingoption` are undocumented — implement conservative throttle +
  429 backoff (options_chain `_post` already raises a clear 429 error to copy).
- Mock provider spot anchors are synthetic (`MOCK_DEFAULT_SPOT`) — fine, UI-only.

---

## D. Option SELLING (writing), mostly hedged — DESIGN SPEC (Fable, drafted while
user stepped away; needs sign-off before dispatch)

**Context:** user has only bought CE/PE so far (that's everything above). Now adding
option-*writing* — mostly hedged (defined-risk spreads/condors), not naked. This is a
materially different risk shape from Section A/B (long options): margin-bound instead
of premium-bound, thin/capped-and-positive theta instead of theta-negative, and the
existing verdict math (rr/PoP/thetaGate tuned for a debit buyer) does not transfer —
it needs a parallel "credit" verdict path, not a retrofit of the long-side one.

**Why this is its own section, not a bolt-on to A/B:** the backtest engine already has
a short-straddle strategy (uncapped risk, no hedge legs) — real numbers exist (128
trades/side long, 2544 straddle rolls) at the top of this file. That straddle is
useful as a *reference/calibration point* for D but is NOT what "mostly hedged" means;
D must add capped-risk multi-leg structures the straddle work doesn't cover at all
(margin modeling, leg-pairing, combined-position stop logic).

### D1. Strategy set (defined-risk only — no naked writing)

- **Credit spreads**: bull put spread (sell higher-strike PE, buy lower-strike PE) and
  bear call spread (sell lower-strike CE, buy higher-strike CE). Params: short strike
  offset (e.g. ATM±2..±4, deltas usually more informative than fixed offsets — see D3),
  wing width (strike steps between short and long leg), entry time, EOD/expiry exit.
- **Iron condor** = bull put spread + bear call spread simultaneously (4 legs, defined
  risk both sides). The natural "mostly hedged, sell premium both ways" structure for
  weekly index expiries.
  Iron fly (ATM straddle sold + wings bought) as a variant — tighter, more premium,
  more directional gamma risk; worth modeling but flag it as higher-touch than condor.
- **Short straddle/strangle WITH protective wings** = re-express the existing
  short-straddle engine (already built) as an iron fly/condor by adding a long leg on
  each side at a configurable width. This is the cheapest path to "hedge the straddle
  that already has real numbers" — reuse strategy-2's roll/re-strike logic, just also
  track the two long legs' P&L and net the combined position's margin + payoff.
- Explicitly OUT of scope for D (skip unless user asks): ratio spreads, naked
  strangles, calendar/diagonal spreads (need cross-expiry bars — the `rollingoption`
  chunked-download model doesn't cleanly span two expiries at once), broken-wing
  variants (asymmetric risk, easy to get wrong on a first pass).

### D2. Margin modeling (the part long-option buying never needed)

Selling is capital/margin-bound, not premium-bound — sizing and even "is this trade
worth it" depend on margin, so this has to be modeled, not skipped:

- **Dhan Margin Calculator API**: `POST https://api.dhan.co/v2/margincalculator`
  (single order: span+exposure+var+brokerage+leverage+available balance) and
  `POST https://api.dhan.co/v2/margincalculator/multi` (multi-leg — this is the one D
  needs, since every D1 structure is ≥2 legs and margin benefits from the hedge/offset
  between legs; pricing the legs independently via the single-order endpoint would
  overstate margin and make hedged structures look worse than they are). Docs:
  https://dhanhq.co/docs/v2/funds/ ; per-request live margin figures are for *current*
  session pricing only (per the docs) — **not usable for historical backtesting**, only
  for a live/paper "would this fit my capital" check on the assessor page.
- **Backtest-time margin**: Dhan's live margin API can't price historical dates, so the
  backtest engine needs an approximate SPAN+exposure model for historical runs instead.
  Options (cheapest first): (a) flat/simplified NSE exposure-margin heuristic (% of
  contract notional, same for all dates — crude but consistent, good enough for
  relative strategy comparison); (b) scrape/hardcode NSE's published SPAN margin % by
  month if a clean historical series is findable; (c) skip margin in the backtest
  entirely and report ROI on a fixed assumed-capital basis, with margin only checked
  live via the multi-margin API before an actual trade. **Recommend (c) for the first
  pass** — cheapest, avoids inventing a margin-history model that can't be verified,
  and keeps the backtest engine's existing "post-cost P&L" contract unchanged; add
  live margin-fit checks (a)/(b) only if the user wants margin-aware backtest sizing
  later.
- Track this as a documented approximation in the backtest output (a `margin_model:
  "not_modeled_use_live_check"` field or similar), same honesty discipline as the
  existing "source": "live"|"mock" badge on the assessor — never let a backtest number
  imply margin-verified reality it doesn't have.

### D3. Verdict / risk model for CREDIT structures (parallel to Section B, not reuse)

The Section B verdict math (rr, PoP-via-probTouch, theta gate) is built for a debit
buyer maximizing a capped-cost/uncapped(ish)-reward trade. A credit spread inverts the
shape: max reward = credit received (capped, usually small), max loss = wing width −
credit (capped, usually larger) — so raw rr is *structurally* < 1 for nearly every
sane condor/spread, and a straight port of Section B's `rr>=2` gate would flag
everything "Unfavorable" by construction. D needs its own thresholds:

- **PoP for a credit trade = P(spot stays between short strikes at expiry/exit)**, i.e.
  `1 − probTouch(short strike)` on each side (or the standard `1 − |delta of short
  leg|` approximation, which Dhan's chain already returns as `delta` per leg — cheaper
  and the industry-standard quick read). This is a different formula from the existing
  `probTouch` used for the long side's barrier-touch PoP; both can share the same
  `_norm_cdf`/Black-76 machinery from Section B but the credit path needs its own
  `pop_credit()`-style function, not a call-site reuse of B's `prob_touch`.
  **Correction to earlier framing**: unhedged/naked economics text from a prior general
  discussion of "high win-rate, small edge per trade" selling strategies doesn't apply
  unmodified here since every D1 structure is capped-risk by construction — the
  capped-loss wing is what makes tail risk on a single bad week bounded, which is the
  whole point of "mostly hedged."
- **Credit/width ratio** (credit received ÷ max loss) replaces rr as the primary
  reward metric — this is the standard practitioner number for spread selection (e.g.
  "sell for at least 1/3 the wing width").
  **Verdict tiers (proposed, needs user sign-off + replay validation like B's
  acceptance bar):** Favorable: PoP ≥ 0.65 AND credit/width ≥ 0.33; Marginal: PoP ≥
  0.55 AND credit/width ≥ 0.20; else Unfavorable. Same discipline as Section B: these
  are starting thresholds, not commitments — re-run the replay scorecard on real D
  backtest data before trusting them, exactly like B3's acceptance bar (tiers must
  actually separate on win-rate/expectancy, re-tune or scrap if they don't).
- **Combined-position stop**: unlike a single-leg long trade, exit logic is on the
  *net* position P&L (credit collected − current cost to close the whole spread), not
  a per-leg SL. Reuse strategy-2's existing re-strike/roll machinery as the template for
  "when to defend/roll a threatened short strike" rather than designing this from
  scratch.

### D4. Data requirements (extends, doesn't replace, A's data layer)

- Needs a **wider strike range** than what's cached today: OPTIONS_ASSESSOR_PLAN's
  "Current status" notes ATM-only bars exist (19,200 bars, no ATM±2 offsets pulled
  yet). D's spreads/condors need short+long legs typically ATM±2..±6 — re-download
  with `strikes=ATM-6..ATM+6` (still within `api/options_backtest.py`'s existing
  `download` action and the documented ATM±10 index constraint) rather than a new
  endpoint.
- No new data source needed — same `rollingoption` cache, same table schema (already
  keyed by `strike_off`), same chunked-download/throttle code from Section A.

### D5. UI

- Extend `options_assessor.html` (not a new page) with a "structure" mode toggle:
  single-leg (existing) vs. spread vs. condor. When a multi-leg mode is picked, the
  strike ladder becomes a **2 or 4-strike picker** (short/long per side) instead of the
  single ATM±5 ladder; payoff SVG already exists for single-leg — needs a multi-leg
  payoff renderer (sum of each leg's payoff, still an SVG, no new chart deps, matches
  existing dark-theme/Tailwind conventions).
- Add a "Check margin" button that calls the live Dhan multi-margin endpoint (via a new
  thin `api/options_margin.py` proxy, same secrets-stay-server-side pattern as
  `api/options_chain.py`) — clearly labeled as *live, current-session-only* margin, not
  a backtest input (per D2).
- `options_backtest.html` gains a strategy dropdown entry per D1 structure, reusing the
  existing equity-curve/calendar-heatmap/trade-table components — no new viz needed,
  just new strategy options feeding the same UI.

### D6. Sequencing recommendation

1. Do **NOT** start D before **B7 lands** (see B6/B7 above — the root-cause fix for the
   disconnected verdict-tier/real-P&L scoring bug found 2026-07-13). This is a hard gate,
   already stated inline in B7: D3's credit-side PoP/verdict math would inherit the same
   disconnected-scoring bug if built on top of unfixed replay wiring. Building D before
   B7 lands means redoing D's verdict once B7's fix changes what "correctly scored"
   even means.
2. D1a (cheapest, highest-value first slice): add long-wing legs to the *existing*
   short-straddle/iron-fly engine (reuses strategy-2 code, smallest diff, produces a
   real "hedged premium selling" number fastest) — before building the full
   spread/condor strike-picker UI.
3. D1b: bull-put / bear-call credit spreads + iron condor as the general case (new
   leg-pairing logic in the backtest engine, D3's credit verdict functions).
4. D2's live-margin check (`api/options_margin.py` + assessor "Check margin" button) —
   independent of D1's backtest work, can happen in parallel since it's live/paper-only
   (per D2, decision (c): backtest does NOT model margin).
5. D5's UI (multi-leg picker + payoff renderer) last, once D1's engine + D3's verdict
   thresholds are validated by a real backtest/replay run (same acceptance-bar
   discipline as B3 and A's "real numbers before UI polish" ordering).
6. Every implementation step still goes through the operating mode above: Fable writes
   the dispatch spec, Sonnet builds + runs verification, Fable reviews against spec.

---

## How to resume in a fresh session

1. Read this file top-to-bottom once: "Current status" (top), then **Section B6
   (root-cause diagnosis)** and **Section B7 (the fix spec)** near the end — that's the
   live work item. Sections A–D above B6 are historical/completed/future context, not
   what's next. Do not restart or re-derive B1–B5; they're done and their results are
   recorded (Section B5 in particular — re-running that calibration again without B7 is
   wasted work, see "Next up" note in Current status).
2. Follow the operating mode above (Fable = spec/review only; ≤3-line exemption;
   dispatch all code AND verification to Sonnet agents).
3. Fable: read B6/B7, confirm you agree with the diagnosis (or challenge it — the repo
   is the source of truth, not this doc), then turn B7 into a full dispatch prompt for a
   Sonnet agent: fix the trade-definition wiring bug in `assess()`/`action_replay()` in
   `api/options_backtest.py` so the verdict tier and the real backtested P&L are scored
   against the *same* trade (not two disconnected definitions). Sonnet must re-run the
   real-data replay scorecard (NIFTY, ATM, both CE/PE, full available history — same
   command pattern used in prior sessions: `python scripts/dev_server.py 8010` then hit
   `/api/options_backtest` `download`/`backtest`/`replay` actions, or the existing
   `curl`/`node` harness scripts already in the repo — grep for `replay` in
   `api/options_backtest.py` for the exact action names) and report whether tiers now
   separate (Favorable > Marginal > Unfavorable in expectancy) as the acceptance bar.
4. Only after tier separation is real do further calibration/threshold tuning (a "B8" if
   needed) — not before.
5. Dev server for manual checks: `python scripts/dev_server.py 8010` (agent-run).
