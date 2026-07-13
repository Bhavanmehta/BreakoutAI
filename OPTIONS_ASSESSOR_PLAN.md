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
3. **Section B** (model overhaul — Black-76 on futures forward, Dhan's own IV/delta,
   model-stack evaluation). Fable to write the design/eval spec first; re-run the
   replay scorecard after each model change — tier separation is now the measurable
   acceptance bar.

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

### C. Known open niggles

- `expiryCode` semantics for `rollingoption` need verification against the instrument
  list docs during implementation (WEEK/MONTH + code index — verify with one live call
  before bulk download).
- Rate limits for `rollingoption` are undocumented — implement conservative throttle +
  429 backoff (options_chain `_post` already raises a clear 429 error to copy).
- Mock provider spot anchors are synthetic (`MOCK_DEFAULT_SPOT`) — fine, UI-only.

---

## How to resume in a fresh session

1. Read this file, especially "Current status" at the top — items 1–3 + 5 of Section A
   are already built; **item 4 (the UI page) is what's next**, not a restart of A.
2. Follow the operating mode above (Fable = spec/review only; ≤3-line exemption;
   dispatch all code AND verification to Sonnet agents).
3. Fable: turn Section A item 4's spec (below) into a full dispatch prompt for Sonnet —
   `options_backtest.html` (params panel, download progress, SVG equity curve, P&L
   calendar heatmap, sortable trade table, assessor-replay scorecard) wired to the
   already-built `/api/options_backtest` actions (`download`/`status`/`bars`/
   `backtest`/`replay` — see `api/options_backtest.py`), plus the nav link from
   `combined_breakout_scanner_platform.html`. Review Sonnet's output against the spec.
4. Once the UI exists, dispatch a second Sonnet pass: run a real (several-month, NIFTY,
   ATM±2, both strategies) download + backtest + replay — report real win-rate/
   expectancy/drawdown numbers back into this doc (not just "it runs").
5. Only after real numbers exist, move to Section B (model overhaul).
6. Dev server for manual checks: `python scripts/dev_server.py 8010` (agent-run).
