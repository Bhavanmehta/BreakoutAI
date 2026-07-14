# OPTIONS_PLATFORM_PLAN — Unified Options Platform (Buy-side + Sell-side)

**Status:** PLAN ONLY — implementation deliverable for Sonnet/Opus. No code in this document.
**Sources of truth:** `scratchpad/options_audit.md` (full codebase audit, per-file REUSABLE/REPLACE table + env-key inventory) and `scratchpad/ai_trader_research.md` (external repo research: `aaryansinha16/AI-trader`). Read both before starting any phase.
**Predecessor docs:** `OPTIONS_ASSESSOR_PLAN.md` (buy-side; Sections A–C shipped, Section D unshipped), `backend/OPTIONS_FLOW_TRADIER_NOTES.md`.

---

## 1. Verified current state (do not re-audit; confirmed by agent audit)

### Buy-side (assessor, bolted onto breakout scanner)
- Chain: `options_assessor.html` (UI) → `scripts/options_math.js` (client-side BS/Black-76) → `api/options_chain.py` (Dhan proxy; live chain has full OI/vol/IV/Greeks, mock is thinner) → `api/options_backtest.py` (local-only, 1132 lines, SQLite cache table `bars` with per-bar OI+IV).
- **Known bug B6 (confirmed unfixed):** `action_replay()` scores a delta-linearized *theoretical* trade, never the real intrabar path `_run_long()` traded — the "Favorable" verdict never fires across 6 months of real data.
- Section D (buy-side spreads/condors) is spec-only, explicitly gated behind B7.

### Sell-side (`dhan_ironcondor/`)
- `dashboard.py`: Streamlit, read-only, multi-book (condor + butterfly).
- Strike selection is **delta-target (0.20Δ)**, wings fixed ±200 pts. Credit/max-loss/breakeven math is correct; **margin is a flat guessed constant** (`MARGIN_PER_LOT_PAPER`) — no real margin-based R:R.
- Live feed: Dhan **WebSocket**, FUT-based; IV backed out locally via bisection (not vendor-supplied).
- Execution is **real** (`DhanBroker.place_order`) but gated behind `MODE="paper"` default, with do-not-go-live warnings until lot size/margin verified. State machine, rollback, crash-safe order persistence: solid — **reuse, do not rewrite**.

### Data layer
- Options history: **only** NIFTY-ATM-5min (`backend/backtest_cache.db`, 19,200 rows, ~Jan–Jul 2026). No other symbol/strike/index.
- Equity side: real feature-engineered ML dataset (`ohlcv_features`, 1,817 India + 4,478 US symbols, 3 yr).
- US options-flow "unusual activity" files are **single-day snapshots** — history does not accumulate.
- Tradier client fully built but **never wired** into `options_flow_scan.py`.

### Cross-cutting debt
- **Three duplicate Black-76/BS implementations** (JS client, buy-side Python, sell-side bisection path).
- **Two Dhan env conventions:** buy-side `DHAN_Client_ID` vs sell-side `DHAN_CLIENT_ID`; sell-side has no `.env.example`.

### External research (`AI-trader` repo) — idea-mine only, DO NOT port code
Repo is mostly aspirational scaffolding: models never trained on real data, tests reference an unavailable TrueData API, tiny `paper_trades/trades_test.jsonl`. Adopt **concepts only**:
1. **Option-chain feature set** (`features/option_chain_features.py`): PCR-near/PCR-far, OI-gradient, OI-concentration.
2. **Regime taxonomy** (`strategy/regime_detector.py`): `REGIME_STRATEGIES` mapping — TRENDING_BULL / TRENDING_BEAR / HIGH_VOLATILITY / LOW_VOLATILITY / GAMMA_PINNING — plus OI-interpretation states (LONG_BUILD_UP, SHORT_BUILD_UP, SHORT_COVERING, LONG_UNWINDING).
3. **Exit-agent concept** (DQN/RL exit) — concept only; deferred to Phase 5 (no training data yet).

---

## 2. Target architecture (v1)

**v1 is a live-data scoring engine, not an ML system.** Options-strike ML is not trainable today — only the equity-selection half has real training data. v1 unifies feeds, math, and scoring; ML waits for accumulated history (Phase 5).

```
Dhan WS (India, FUT+chain)   Tradier REST (US chains)
            \                      /
        backend/options/feeds.py  (normalized chain snapshot model)
                     |
        backend/options/pricing.py  (SINGLE Black-76/BS + IV bisection + Greeks)
                     |
        backend/options/features.py (PCR-near/far, OI-gradient/concentration, IV context)
                     |
        backend/options/regime.py   (regime + OI-state classifier, rule-based v1)
                     |
        backend/options/scoring.py  (buy-verdict + sell-entry-gate, one engine)
        /                                   \
  assessor UI (buy)                 dhan_ironcondor dashboard (sell)
```

Both existing UIs stay; they become thin clients of the shared engine.

---

## 3. Phases & work packages

### Phase 0 — Hygiene & unification substrate (prerequisite for everything)
- **P0.1 Env unification.** Single convention `DHAN_CLIENT_ID` / `DHAN_ACCESS_TOKEN` everywhere; shim that reads legacy `DHAN_Client_ID` with a deprecation warning for one release. Add `.env.example` covering both sides (Dhan, `TRADIER_ACCESS_TOKEN`, `TRADIER_API_URL`), per the env-key inventory in the audit report.
- **P0.2 One pricing module.** Create `backend/options/pricing.py` as the single Black-76/BS + Greeks + IV-bisection implementation. Sell-side and buy-side Python import it; `scripts/options_math.js` stays for UI-side interactivity but gains a parity test (golden-vector JSON generated by the Python module, asserted in both runtimes).
- **Acceptance:** both apps boot with the new env names; golden-vector parity test passes (price/delta/IV agree within 1e-6 / 1e-4 respectively); grep shows no remaining `DHAN_Client_ID` reads outside the shim.

### Phase 1 — Unified data layer + history accumulation (highest-leverage, do early)
- **P1.1 Wire Tradier into `options_flow_scan.py`** (client already exists — wiring only) so US flow uses real chains.
- **P1.2 Normalized chain snapshot model** in `backend/options/feeds.py`: one dataclass/schema for Dhan (India) and Tradier (US) chains — strike, expiry, bid/ask, OI, volume, vendor-or-derived IV, timestamp, source.
- **P1.3 Accumulating history jobs.** Scheduled snapshot writer (extend the existing scheduler pattern the scanner uses) that appends chain snapshots to SQLite: India NIFTY (and later BANKNIFTY) via Dhan; US watchlist via Tradier. This converts the single-day US flow snapshots and the NIFTY-only cache into a growing dataset — the prerequisite for Phase 5 ML.
- **P1.4 Real margin.** Replace `MARGIN_PER_LOT_PAPER` guess with broker-queried margin where the Dhan API supports it; else a documented exchange-formula estimate with a `source: "estimated"` flag surfaced in the dashboard. R:R panels must label estimated vs. broker-confirmed margin.
- **Acceptance:** after 3 trading days, history DB shows ≥3 daily snapshot sets for both markets; US flow scan produces output from Tradier live data; condor R:R uses non-constant margin or clearly-flagged estimate.

### Phase 2 — Shared features, regime, and scoring engine
- **P2.1 `features.py`:** implement PCR-near/PCR-far, OI-gradient, OI-concentration (AI-trader concepts, our own code) over the normalized snapshot model; plus IV-rank/percentile from accumulated history (degrades gracefully while history is short).
- **P2.2 `regime.py`:** rule-based classifier emitting one of {TRENDING_BULL, TRENDING_BEAR, HIGH_VOLATILITY, LOW_VOLATILITY, GAMMA_PINNING} + OI state {LONG_BUILD_UP, SHORT_BUILD_UP, SHORT_COVERING, LONG_UNWINDING}. Thresholds in `config/settings.py`, not hard-coded.
- **P2.3 `scoring.py`:** one engine, two consumers:
  - Buy-side: assessor verdict gains regime/OI-state context (e.g., suppress long-premium verdicts in LOW_VOLATILITY/GAMMA_PINNING).
  - Sell-side: condor entry gate consumes the same regime signal (e.g., block entries in HIGH_VOLATILITY/TRENDING regimes; favor GAMMA_PINNING/LOW_VOLATILITY).
- **P2.4 Surface in both UIs:** regime badge + top contributing features in assessor page and Streamlit dashboard.
- **Acceptance:** identical snapshot in ⇒ identical regime/score out across both UIs (shared-engine test); regime unit tests with hand-built fixture chains for each of the 5 regimes and 4 OI states.

### Phase 3 — Backtest integrity (fix before trusting any verdict)
- **P3.1 Fix B6:** `action_replay()` must replay the **same intrabar path** `_run_long()` trades, not a delta-linearized approximation. Score realized path P&L; keep the linearized figure only as a labeled diagnostic column.
- **P3.2 Regression evidence:** re-run the 6-month NIFTY dataset (19,200 rows); acceptance is that "Favorable" now fires at a plausible non-zero rate and a written before/after distribution note is committed alongside.
- **P3.3 Sell-side replay (new, small):** replay condor entry-gate decisions against the same cache to sanity-check Phase 2 gate thresholds (read-only analytics; no execution path changes).
- **Acceptance:** B6 regression test enshrining path-consistency; before/after note committed.

### Phase 4 — Buy-side spreads (unblocks old Section D / B7)
- With `pricing.py` (P0.2) and the scoring engine (P2.3) shared, implement Section D of `OPTIONS_ASSESSOR_PLAN.md` (verticals first; condors on buy-side UI reuse sell-side leg math). Keep the delta-target strike-selection approach (0.20Δ pattern) rather than fixed offsets — audit confirmed sell-side already does this correctly.
- **Acceptance:** vertical spread assessment end-to-end on live Dhan chain in the assessor UI; leg math identical to sell-side (shared module — assert by test, not by review).

### Phase 5 — DEFERRED: ML & exit agents (do not start in this cycle)
- Blocked on Phase 1 history accumulation (target: ≥3 months multi-strike snapshots before any training).
- When unblocked: options-strike model on accumulated chain features; exit-agent (AI-trader DQN *concept*) prototyped offline against replay only. **Never** wire ML output to `DhanBroker.place_order` without the existing `MODE="paper"` gate plus a new explicit per-strategy allowlist.

---

## 4. Guardrails (binding for the implementing agent)
1. **No live-execution changes** outside P1.4 margin queries. `MODE="paper"` default and existing do-not-go-live warnings stay until the user verifies lot size/margin.
2. **Do not rewrite** the sell-side state machine, rollback, or order-persistence code — audit rates it solid. Refactors limited to imports of the shared pricing module.
3. **Do not port AI-trader code.** Concepts only (§1). Its models/tests are not trustworthy.
4. **No new heavyweight deps.** Reuse existing stack (SQLite, existing scheduler, Streamlit, vanilla JS assessor).
5. Every phase lands with tests named above; a phase without its acceptance evidence is not done.

## 5. Suggested execution order & sizing
P0 (small, 1 session) → P1 (medium; P1.3 job must ship early so history accrues during the rest of the work) → P2 (medium-large) → P3 (small-medium, high value) → P4 (medium). P5 deferred.

## 6. Open questions for the user (answer before P1.4 / P4)
1. Dhan margin API availability on your account tier — determines P1.4 real-vs-estimate path.
2. Which US watchlist should the Tradier snapshot job track (breakout-scanner output vs. static list)?
3. BANKNIFTY: add to sell-side snapshot accumulation in P1.3 now, or NIFTY-only until storage/rate limits are validated?
