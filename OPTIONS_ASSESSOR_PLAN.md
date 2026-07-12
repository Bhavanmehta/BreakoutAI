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

## TODO — pending work (in priority order)

### A. Backtesting feature (user picked: build BOTH in one pass) — NEXT UP, dispatch to Sonnet

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

### B. Model overhaul (after backtest ships)

6. **Black-76 on futures forward**: backend fetches matching-expiry futures price from
   Dhan (NSE_FNO) alongside the chain; scenario/payoff math prices off the forward, not
   spot (indices especially — carry basis matters for weeklies).
7. **Use Dhan's own IV + per-leg delta everywhere** (chain already returns them) instead
   of recomputing from spot — assessor Greeks/PoP should trust market IV.
8. **Evaluate model stack**: binomial / practitioner-BS / (optionally) Heston as verdict
   layers; present a recommendation to the user before implementing.

### C. Known open niggles

- `expiryCode` semantics for `rollingoption` need verification against the instrument
  list docs during implementation (WEEK/MONTH + code index — verify with one live call
  before bulk download).
- Rate limits for `rollingoption` are undocumented — implement conservative throttle +
  429 backoff (options_chain `_post` already raises a clear 429 error to copy).
- Mock provider spot anchors are synthetic (`MOCK_DEFAULT_SPOT`) — fine, UI-only.

---

## How to resume in a fresh session

1. Read this file.
2. Follow the operating mode above (Fable = spec/review only; ≤3-line exemption;
   dispatch all code AND verification to Sonnet agents).
3. Start with section A: author the dispatch prompt from the build spec (items 1–5),
   dispatch to Sonnet, review output against spec, iterate.
4. Dev server for manual checks: `python scripts/dev_server.py 8010` (agent-run).
