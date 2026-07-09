# BreakoutAI — Session Handoff

_Last updated: 2026-07-06 (session 11, kill-list execution). Read this +
`CLAUDE.md` (durable project record) to resume._
_When you start a fresh chat, point it here first._

---

## ⏭️ NEXT SESSION — START HERE (2026-07-06)

**Plan of record:** `docs/IDEAS_ROADMAP.md` — work the **kill/fix list §2 in
strict order first**, then R1→R5. Do not reorder or jump to new features.

### ✅ Done so far
- **Kill-list #1 (R1) — Conviction honesty badge.** Live-record badge added to
  desktop + mobile scanner cards; joins the same live-record JSON
  `performance.html` reads, threshold `HINDSIGHT_MIN_N=5`
  ("unproven live — N graded" until a bucket has ≥5 resolved). _Commit `5c808a99`._
- **Kill-list #2 (R2) — 26k-px performance page.** Calls now render as collapsed
  rows, default filter "graded", `PAGE_SIZE=25` client-side load-more; summary /
  hindsight / by-signal tables untouched. _Commit `5c808a99`._
- **1a — Badge gated behind a flag (currently OFF).** Per decision to stay quiet
  during the initial phase, `convLiveBadge()` early-returns `null` when
  `const SHOW_CONV_LIVE_BADGE = false` (defined next to `HINDSIGHT_MIN_N` in
  `combined_breakout_scanner_platform.html`). All logic preserved and dormant;
  both consumers (desktop row ~L1785, mobile detail card ~L2100) already treat
  `null` as "render nothing". _Commit `15f556a0`._
  - **To re-enable:** flip that one line to `true`, re-run the Playwright
    screenshots (1440px + 390px, IN + US), commit. ~30-sec change.
  - **⚠️ Open tension:** roadmap still marks kill-list #1 as ✅ DONE, but with the
    badge OFF the naked-conviction credibility gap is technically visible again.
    Decide next session: (a) leave fully off, (b) show only the cautionary amber
    "unproven" badge and hide proven green/red, or (c) turn back on. If staying
    off, annotate the roadmap so the ✅ isn't misleading.

### 📋 To do — remaining kill/fix list (do these before R3+)
- [ ] **1c — Kill the mixed-signal copy.** One card, one verdict: "Breaking out
      now" and "On watch" must never co-render. Fix in the copy-generation step
      of the Action. _(This was queued as the next task.)_
- [ ] **#4 — Two mobile 404 assets + tap interception.** Fix z-index /
      pointer-events on the Ask-AI FAB and sticky header; fix the 404s. (Overlaps
      with R3.)
- [ ] **#5 — Reconcile backtest vs live inline.** Wherever a historical
      reliability % appears, append "live so far: X/Y" from the same JSON.

### 🗺️ Then the roadmap (in order)
- [ ] **R3 — Watchlist reliability pass** (absorbs #4): reproduce at 390px/1440px,
      fix intercepted taps + 404s, re-run tap script until zero retries.
- [ ] **R4 — Homepage live-record strip** (Trade-Ideas steal): slim strip under
      header from performance JSON, losses in red, links to performance.html.
- [ ] **R5 — Watchlist email digest** (screener.in steal): daily GitHub Action
      email for an exported watchlist; include R4 live-record line in footer.
- [ ] **Backlog (Tier B/C):** sector heatmap, Rewards/Risks bullets, delivery
      %/FII-DII inputs, shareable permalinks, feed outcomes into calibration —
      see roadmap §5. Only after the kill-list + R3–R5.

### ⚙️ Notes for whoever resumes
- Uncommitted: `scripts/` (local Playwright verification tooling) is untracked and
  gitignored — not part of any commit.
- Verification recipe used this session: Playwright screenshots at 1440×900 and
  390×844 for both IN and US markets; local serve `/api/quotes` 404 is expected.
- Working tree is otherwise clean; latest commit is `15f556a0`.

---

## TL;DR of where things stand

Seven independent, **uncommitted** threads are open right now (session 9 is on the
`feature/us-market` branch; sessions 0-5 below are on `main`'s working tree):
-2. **Session 10 — Card-UX cluster: the "why" / rationale layer** (backend + frontend,
   Sprint 2). New `backend/signals.py::build_rationale(rec)` derives, per record, a two-column
   confirming/risk signal set, an edge-vs-risk RSS roll-up (`{edge, risk, net, confidence}`), a
   one-line make-or-break trigger, and advisory gates (liquidity / volume-confirm / uptrend /
   earnings-veto). Stored as `rec["rationale"]`, wired into `run_scan.py` after enrichment,
   backfilled onto both `breakouts.json` files. **Transparency only — never the ranker**; the
   conviction score in `score.py` stays purely technical. Frontend renders it in the detail pane
   and degrades gracefully on old JSON. Verified via `_verify_frontend.py` (IN + US). See
   "Session 10" below.
-1. **Session 9 — US market conviction score validated + recalibrated + 2 new
   high-conviction tiers** (backend + frontend). The US mirror (`data/us/`,
   `BREAKOUTAI_MARKET=US`) had never had its score backtested. Found the US Method-A
   base rate is far lower than India's (26.7% vs 38.8%) and root-caused why; per-market
   score calibration shipped (India untouched, verified bit-identical); then built two
   new backtested, out-of-sample-validated "high-conviction" setup tiers (51% and 45%
   hit rates, vs the 26.7% base and the 50% breakeven line). See "Session 9" below.
0. **Session 8 — Single 0-100 "conviction" score** (backend + frontend, continues sessions 6/7).
   New `backend/score.py` blends only backtest-validated features into one ranking number;
   Bayesian shrinkage so one bad breakout no longer flashes red; the one-day analog backtested
   and de-emphasized. Now the frontend's default sort. See "Session 8" section below.
1. **Session 7 — Fundamentals filter panel + sector/detail-pane sync fix** (backend + frontend,
   same conversation as session 6, continued). New `data/fundamentals.json` (market cap, P/E,
   revenue/profit growth, ROE, D/E via yfinance) merged into `breakouts.json`, plus a new
   collapsible filter panel on the frontend; also fixed a real bug where changing the sector
   filter didn't update the detail pane. See "Session 7" section below.
2. **Session 6 — Method E folded into production readiness scoring** (backend + frontend,
   builds directly on session 4's research). `run_scan.py`/`find_breakouts.py` now compute a
   live "relative strength vs Nifty" readiness tier, verified end-to-end incl. in-browser. See
   "Session 6" section below.
3. **Session 5 — Claude Skills for this repo's workflow** (tooling, no site changes). Two new
   files under `.claude/skills/`. See "Session 5" section below.
4. **Session 3 — "Ask AI" chatbot** (feature work, frontend + backend). Working, live-verified,
   not yet committed. See "Session 3" section below.
5. **Session 4 — breakout-detection method comparison** (backend research). A new
   `backend/methods.py` implements 5 alternative breakout definitions (+ a loosened variant +
   4 "multiple methods agree" combos + the E2 uptrend-gated variant added in session 6), all
   backtested against the existing engine on the whole market via `analyze_reliability.py`
   (research harness — B/C/D/D2/F remain research-only; only E/E2 graduated to production, in
   session 6). See "Session 4" section below for the full results.

Sessions 4, 6, 7, 8 are one continuous line of work (same conversation) and touch overlapping
files (`backend/find_breakouts.py`, `run_scan.py`, `analyze_reliability.py`,
`combined_breakout_scanner_platform.html`, the regenerated `data/*.json`) — resume/commit them
together as one body of work. Sessions 3 (Ask AI) and 5 (Claude Skills) remain independent.

---

## Session 10: Card-UX cluster — the "why" / rationale layer

### Why
The conviction score (`score.py`) ranks stocks well but is a single opaque number. The
Card-UX cluster (competitor-ideas #1–#4) asked for a **transparency layer**: for each pick,
show *why* it's on the list, what would confirm or kill it, and the one thing to watch — in
plain language, without touching the ranker.

### What shipped
- **`backend/signals.py`** — `build_rationale(rec)` derives, per enriched record:
  - a two-column **confirming / risk** signal set (readiness, base depth, distance-to-resistance,
    volume, trend, method — read off fields already on the record);
  - an edge-vs-risk **RSS** roll-up `{edge, risk, net, confidence}` (magnitudes combined in
    quadrature, not a new score — purely a display summary);
  - a one-line **make-or-break** trigger (the price level / event that decides the setup);
  - advisory **gates** (liquidity / volume-confirm / uptrend / earnings-veto), thresholds from
    the new gate-constants block in `settings.py` (`GATE_MIN_AVG_TURNOVER`, `GATE_VOL_CONFIRM_MULT`,
    `REQUIRE_UPTREND`, `GATE_EARNINGS_VETO_DAYS`).
  - Result stored as `rec["rationale"]`. `python signals.py` backfills an existing
    `breakouts.json` in place.
- **`run_scan.py`** — calls `build_rationale` on every record *after* the holdings/sectors/
  fundamentals enrichment merges, so the rationale sees the fully-populated record.
- **Frontend** — detail pane renders it via `renderRationale` / `renderMakeOrBreak` /
  `renderGates`; the whole block auto-hides when a record has no `rationale`, so old JSON
  degrades to the classic read.

### Discipline held
The rationale is a **transparency layer only — never the ranker.** `score.py` stays purely
technical (reliability + base depth + method). RSS/edge/risk are display-only summaries of
signals already on the record; nothing here feeds back into sort order or conviction.

### Verified
Backfilled both `data/breakouts.json` and `data/us/breakouts.json`; schema validated. Ran
`_verify_frontend.py` over `http.server` for IN + US on rich and sparse picks — all checks
pass, only the pre-existing `/api/quotes` 404 remains.

### Still open / not done
Backfill + docs are committed to the working tree but **not yet pushed to the `data`
serving branch** — next scheduled `run_scan.py` regenerates it there. Gate thresholds are
first-pass defaults, not yet backtested for predictiveness (same display-only discipline as
the pattern badge until validated).

---

## Session 9: US conviction score validated + recalibrated + 2 new high-conviction tiers

### Why
The US market mirror (built on this branch before this session, `data/us/`, ~4,465
stocks via `BREAKOUTAI_MARKET=US`) reused India's conviction-score weights/prior as-is
— never backtested against actual US data. User asked to make the US score as
high-hit-rate and honestly validated as possible, "like a professional trading
analyst," then explicitly pushed further: don't just rank well, find a genuinely
high-precision subset — "whatever we find needs to be really good," but "we don't
focus on quantity... I don't mind [more picks] but I want the hit rate to be good,"
and keep small/cheap stocks in (floor at avg volume ≥100k shares, price ≥$1 — no
market-cap or price-tier filter).

### What was found (whole-market replay, 20,814 Method-A events, 4,166 stocks, 3yr)
- **US base rate is 26.7%** vs India's 38.8%. Root cause: the fixed ±6%-of-resistance
  stop/target band is implicitly tuned to Indian volatility — **51.7% of US events
  resolve NEITHER side within 10 days** (80% for low-ATR stocks). Confirmed via a
  volatility-neutral ATR-scaled regrade (±2×ATR10): base rate becomes 41.8%, flat
  across volatility (p=0.55), and India's strongest feature (persistence) nearly
  vanishes (22.3%→34.7% fixed-rule vs 40.6%→41.6%/p=0.27 ATR-scaled) — most of its US
  edge was a volatility-persistence artifact, not real signal. Production still uses
  the fixed-band rule (switching it changes the displayed stop/history/track record
  sitewide — flagged as a future option, not done this session).
- **Per-market score recalibration** (60/40 train/test split by stock): US weights
  rel/depth/method = 0.30/0.70/0.00 (India 0.60/0.25/0.15; D method-confirmation
  measured -12.2pt harmful on US data, p=0.002, dropped). TEST-set tertile spread
  14.4%→39.4% (p<1e-96), top decile 43.4%.
- **High-precision search** (point-in-time features: closing range, cross-sectional
  RS percentile vs the whole universe that date, breadth, base tightness/age, $
  liquidity, cross-method co-fires; TRAIN-only gate search, ONE test-set evaluation of
  finalists): **"squeeze-confirmed breakout"** — volatility-squeeze release (Method C)
  + Method-A breakout within ~5 bars + ATR≥4.5% of price + close within 3% of trigger
  + liquidity floor — **51.1% hit rate (n=190, 176 stocks), 52.0% held out on TEST**,
  roughly double the 26.7% base. A looser rule (A-breakout + ATR≥4.5% + liquidity
  floor, no squeeze/gap needed) scored 45.3%/n=3,215 (46.2% TEST) — real lift, kept as
  a second tier. A gap≥4% variant died on TEST (47.4%) — excluded.

### What was built
- **`backend/settings.py`**: new score-calibration block (`SCORE_BASE_RATE`,
  `SCORE_W_REL/W_DEPTH/W_METHOD`, `SCORE_Q_RANGE`, `RELIABILITY_CAUTION_BELOW/GOOD_AT`)
  branching on `MARKET`; new `HC_*` tier-threshold constants gated by `HC_ENABLED =
  MARKET == "US"`.
- **`backend/score.py`**: reads the new settings instead of hardcoded India values.
- **`backend/find_breakouts.py`**: `_reliability_note()` uses market-aware thresholds;
  `build_summary()` gained the two new tiers (`readiness.signal` = `"high_conviction"`
  / `"strong_breakout"`, conviction floors 90/80) plus `_last_is_fresh_fire()` — see
  the bug below.
- **`backend/run_scan.py`**: computes `is_breakout_c` (research Method C, promoted for
  US only) alongside the existing E/E2 columns.
- **`combined_breakout_scanner_platform.html`**: `verdictExplainer` branches for both
  new signals (checked before the generic `breakout.today` case, since tier-2 always
  coincides with it); an amber "★ High conviction" pill on the detail header and
  watchlist rows for `high_conviction` only.
- **A real bug caught before shipping, worth remembering for any future live signal
  built this way**: the first cut used raw/undeduped trigger columns as each tier's
  anchor condition. Both `is_breakout` and `is_breakout_c` cluster on consecutive days
  during one continuous move (confirmed: e.g. ATYR fired raw `is_breakout_c` on 18
  days across only 12 backtest-counted clusters) — the backtest's
  `_dedup_with_cooldown` counts only the first day of each cluster (enforcing minimum
  *spacing between kept fires*, not "wait for quiet"). Without matching that, an
  acceptance test (replaying history through the actual production code) caught tier-1
  inflating from a backtested n=190/51.1% to n=830/46.1%, and tier-2 from n=3,215/45.3%
  to n=6,521/44.2%. Fixed via `_last_is_fresh_fire()` — a local reimplementation of
  the same cooldown-dedup algorithm (can't import `analyze_reliability.py` directly:
  circular import, it imports `add_indicators` from `find_breakouts`). After the fix,
  a whole-market replay via the actual imported helper matched the backtest almost
  exactly: tier 1 n=197/51.3%, tier 2 n=3,215/45.3% (exact).
- **`IMPLEMENT_US_HIGH_CONVICTION.md`** (repo root, new) — the full spec written for
  the implementing model, with the exact rule table, thresholds, and acceptance-test
  methodology. Left in place as reference/documentation even though the plan changed
  from "spawn a separate implementer" to "implement directly in this session."

### Follow-on: US market cap was showing as mislabeled ₹ crore (real bug, found while
### the user was previewing the above in-browser)
User caught it live: Moderna Inc's detail header showed "₹3,165 Cr" — a US stock with
a rupee-crore market cap. Root cause: `backend/fundamentals.py` unconditionally did
`marketCap / 1e7` into a field literally named `market_cap_cr`, and the frontend's
`fmtMarketCap()`/market-cap filter thresholds always assumed ₹ crore — neither had any
`MARKET` awareness, so a US stock's raw USD cap got the same India-only conversion and
label. Separately, ~5.6% of US stocks (251/4,465) showed a blank "—" because
`fetch_fundamentals.py` had cached a permanent fetch-failure miss for them. See
[[yfinance-fundamentals-coverage]] for the full writeup. Fixed both, this session:
- **Backend**: `fundamentals.py` now stores the raw, native-currency market cap under
  a renamed `market_cap` field (was `market_cap_cr`) — all scale/currency formatting
  moved to the frontend, keyed on `MARKET`. `fetch_fundamentals.py`'s miss-cache key
  renamed to match; `run_scan.py`'s merge-count log line updated.
- **Migrated in place** (not re-fetched — cheap, avoids an unnecessary yfinance
  re-scrape): `data/fundamentals.json`, `data/us/fundamentals.json`, and the embedded
  `fundamentals` sub-objects inside both `data/breakouts.json` and
  `data/us/breakouts.json` — renamed the key and multiplied by 1e7 to restore the raw
  value.
- **Recovered the missing 251**: cleared their cached all-`None` misses and re-ran
  `fetch_fundamentals.py` — 249/251 (99.2%) recovered on retry (transient yfinance
  flakiness, same accepted pattern already documented for earnings data), merged into
  `data/us/breakouts.json`. Only `ZTR` (a closed-end fund with no `marketCap` in
  yfinance at all) remains genuinely missing.
- **Frontend**: `fmtMarketCap()` now branches on `MARKET` (India: ₹ crore/lakh-crore
  unchanged; US: $ with K/M/B/T suffixes). The market-cap filter's bucket thresholds
  (`FUND_FIELDS`'s "mcap" entry) became functions of `MARKET` (US tiers: Micro <$300M,
  Small $300M–2B, Mid $2B–10B, Large $10B+) rather than static ₹-crore numbers. A
  static HTML tooltip on the "Mkt Cap" label had the same hardcoded "₹ crore" text —
  given an id and refreshed in `switchMarket()`. Per the user's explicit preference:
  when `market_cap` is `null` (the `ZTR` case), the whole "Mkt Cap" row is now hidden
  rather than showing a "—" placeholder.
- **Verified**: live Playwright pass — AAPL $4.53T, NVDA $4.72T, MRNA $31.65B, CRSP
  $5.92B (all plausible real-world figures), `ZTR`'s row correctly hidden, India's TCS
  still shows ₹7.57 L Cr unchanged, zero console errors. The market-cap filter itself
  cross-checked exactly against an independent Python query (940/4,463 stocks ≥$10B,
  both routes agree).

### Verified end-to-end
India regression: `build_summary()` on 6 India large-caps produces byte-identical
conviction/reliability-note output vs the committed `data/breakouts.json`, both before
and after the fresh-fire fix. US acceptance: whole-market replay via the real
`_last_is_fresh_fire` helper reproduces the backtest (tier 1 n=197/51.3% vs backtest
190/51.1%; tier 2 n=3,215/45.3% exact match). Whole-market US smoke: 0 crashes across
4,464 stocks. Ran the full `run_scan.py` (fed from the DuckDB cache to avoid a second
yfinance rate-limit hit): 4,463 cards, 1 `high_conviction` (CRSP, conviction 90) and 2
`strong_breakout` (RIVN 84, NGNE 80) that day — consistent with the ~6/month and
~97/month backtested cadences. Verified live via Playwright against the served site:
badge, conviction number, explainer text (both tiers), and watchlist star pill all
render correctly; zero console errors; screenshot confirmed visual consistency.

### Current honest state
- **Not committed.** Modified: `backend/find_breakouts.py`, `backend/run_scan.py`,
  `backend/score.py`, `backend/settings.py`, `backend/fundamentals.py`,
  `backend/fetch_fundamentals.py`, `combined_breakout_scanner_platform.html`,
  `data/fundamentals.json`, `data/us/fundamentals.json`, `data/breakouts.json`
  (fundamentals-embedded market_cap migrated only — not a full rescan),
  `data/us/breakouts.json`/`data/us/track_record.json` (regenerated + the market-cap
  fix merged in, real current data). New: `IMPLEMENT_US_HIGH_CONVICTION.md`. This is
  on branch `feature/us-market` — separate from sessions 0/3-8 below, which are
  `main`'s working tree.
- Note: `data/breakouts.json`/`data/fundamentals.json` (India) also show as modified
  in `git status` — their embedded/cached `market_cap` was migrated in place too
  (schema-rename fix only, no rescan, no price/conviction changes — confirmed via the
  same India regression check used for the scoring work) since the field rename is
  shared code (`backend/fundamentals.py`), not something that could be scoped to US
  only.
- `backend/universe.py`'s US-universe widening (S&P500+Nasdaq100+Russell2000 →
  full market-cap-floor screen, ~4,668 symbols) predates this session (already
  modified when this session started) — untouched by this session's work.
- The local static server (`python -m http.server 8000`) may still be running from
  this session's verification.

### Next steps
- Decide on committing (this branch, not `main` — user's standing rule).
- Open option, not requested: switch US grading to the ATR-scaled stop/target (would
  raise the US base rate to a volatility-neutral ~42% and change which features read
  as predictive) — this changes the displayed stop, history, and track record
  sitewide, a product decision, not just a backend one.
- Re-check the squeeze-confirmed tier's hit rate after a few live months — its most
  recent backtested half-year (2026H1) ran weaker (~43-46%) than the full-sample
  51-52%, still well above base but worth confirming it holds.
- `IMPLEMENT_US_HIGH_CONVICTION.md` has the full rule table/thresholds if this ever
  needs to be re-derived, re-tuned, or extended to a third market.

### How to re-verify
```
cd backend
python run_scan.py                      # BREAKOUTAI_MARKET=US env var must be set
grep -c '"high_conviction"' ../data/us/breakouts.json
grep -c '"strong_breakout"' ../data/us/breakouts.json
```
Then serve (`python -m http.server 8000` at repo root), switch to US market, and open
a tagged symbol (search any `high_conviction` stock from the grep above).

---

## Session 8: Single 0-100 "conviction" score (rank most→least likely to break out)

### Why
Looking at the site, the user noticed Schneider Electric Infra showed "all greens" (worked
analog, good precedents) while Apollo showed "some red flags" (a faded one-day analog, and at a
glance a weaker-looking read) — and asked for ONE indicator/score to rank stocks most→least
likely to break out, AND to stop highlighting red flags based on a single occurrence if it isn't
actually important. Crucially they flagged the asymmetry: our reliability signal is backtested on
years/thousands of events, whereas the "this setup happened before" analog is literally one past
day — so why is the one-day thing shown as boldly as the aggregate stat?

### The honest finding that shaped the build (all backtested whole-market, 2026-07-04)
- Apollo's *aggregate* reliability (62% then, 59% on the re-run) is actually BETTER than
  Schneider's (52%). The "red flag" the user saw on Apollo was the single faded analog — the
  noisy input, not the validated one. The user's instinct was correct.
- **Composite score works**: blending only the *validated* features (shrunk trailing reliability
  + base depth + method confirmation) stratifies follow-through **34.5% → 41.0%** across score
  tertiles (p<0.001), and is best at pushing the *worst* setups down. Beat reliability-alone
  (+5.7pts) slightly with the full blend (+6.5pts).
- **The one-day analog IS only weakly predictive**: when it "worked", the breakout followed
  through 36.7%; when it "faded", 33.0% — a +3.7pt difference (p=0.011 only because n=4,600).
  Both near the ~38% base rate. So a "faded" badge meaning "33% vs 37%" does NOT deserve
  red-flag styling next to the aggregate track record (which swings 36.5%→43%). Confirmed: keep
  it (weakly real + educational) but strip its visual weight.

### What was built
- **`backend/score.py`** (new) — the whole scoring brain, pure/importable:
  - `reliability_estimate(worked, total)` — **Bayesian shrinkage** `(worked + 4*0.39)/(total+4)`,
    prior = the ~39% market base rate. 0-of-1 → ~0.31 (neutral), NOT 0.0. This is the direct fix
    for "one bad breakout flashing red." Powers both the caution text and the score's biggest term.
  - `breakout_quality()` — `0.60*rel + 0.25*base_depth + 0.15*method_confirmation`, validated
    features ONLY (explicitly excludes ADX, vol-surge, patterns, vol_contraction, and the analog).
  - `conviction()` — `100*(0.55*imminence + 0.45*quality_norm)` → the 0-100 the UI ranks on.
    Imminence comes from the readiness tier (breaking > high > medium-watch > medium > low).
- **`backend/find_breakouts.py`** — `build_summary` now computes `readiness.conviction` (0-100),
  and its reliability caution was rewritten via a new `_reliability_note()` helper: uses the
  shrunk estimate, requires ≥3 past events before ANY negative claim ("limited history" below
  that), caution only when genuinely low (<0.33), "reliable" when ≥0.45, else neutral. The RS
  tier uses its own history, same as before.
- **`backend/analyze_reliability.py`** — added `test_score()` (backtests 3 score weightings by
  stratifying follow-through, replayed with no lookahead — trailing counts = prior breakouts
  only) and `test_analog_predictiveness()` (the "is the one-day analog actually predictive" test
  above). Recorded rs/d co-fire + analog worked/similarity per Method-A event to support these.
- **`combined_breakout_scanner_platform.html`**:
  - New **"Breakout conviction" sort, made the default**; conviction shown as a big colored
    number in the detail header and on every watchlist row (rows now rank by it).
  - **Analog de-emphasized**: box restyled from purple-accent to muted gray; header reworded
    "This setup has happened before" → "One similar-looking day in the past · for reference"; the
    bold red/green "✗ faded"/"✓ followed through" badge → a quiet gray "· that day didn't follow
    through"; added a caption that the conviction score + track record are what to weigh. The
    multi-event "Historical Precedents" list KEEPS its ✓/✗ — that's the validated aggregate record.

### Verified end-to-end
Smoke-tested `score.py` (shrinkage: 0/1→0.31 ✓) and the updated `build_summary` on 10 stocks
(Apollo → conviction 82 + green "Reliable 59%"; ICICIBANK → 48 + earned caution on 30 RS
events). Ran whole-market `run_scan.py`: all 1,823 stocks got a conviction (range 8–89, mean 25);
top of the list = actively breaking/outperforming names with strong records (BLUSPRING 89, SUVEN
86, CUPID 84, APOLLO 82). Playwright browser check confirmed: default sort = conviction, header
shows the big green 82 for Apollo, analog badge is muted gray ("· that day didn't follow
through") under the reworded header + caption, zero console errors. Opened in the user's browser.

### Current honest state
- **Not committed.** New this thread: `backend/score.py`. Modified: `backend/find_breakouts.py`,
  `backend/analyze_reliability.py`, `combined_breakout_scanner_platform.html`, and the regenerated
  `data/breakouts.json`/`predictions_log.jsonl`/`track_record.json`.
- Server still running on :8000 from verification.

### Next steps / open ideas (not requested, just noted)
- The imminence-vs-quality blend weights (0.55/0.45) and conviction display bands (65/40) are
  reasoned, not backtested — the *quality* half is validated, the blend is a product choice.
- Could add a tooltip/expander explaining what conviction is built from (currently only a hover
  title on the header number).
- Base-depth and method terms add little to Method-A-event stratification specifically (method
  co-fires rarely on those days); they mainly matter for the RS-tier stocks' conviction. Fine,
  but if simplifying later, reliability+depth alone (+6.4pts) is nearly as good as the full blend.

### How to re-verify
```
cd backend; python analyze_reliability.py    # see sections 2b (score) + 2c (analog) 
python run_scan.py                            # regenerate breakouts.json with conviction
```
Then serve and sort by "Breakout conviction" (the default).

---

## Session 7: Fundamentals filter panel + sector/detail-pane sync fix

### Why
Three things came up while the user was testing the site after session 6: (1) confusion about
why Apollo Micro Systems showed "Breaking out now" while its historical analog showed a faded
-15.6% precedent, (2) clicking a sector in the Sector Radar only filtered the watchlist, not the
detail pane on the right, and (3) a request to add a screener-style fundamentals filter panel
(Market Cap, P/E, Revenue Growth, Profit Growth, ROE, ROCE, D/E Ratio, from a shared screenshot),
asking us to check what data we actually have for it.

### 1. Explained (no code change): Apollo's "Breaking out now" vs. the faded analog
Not a bug — two independent engines that are allowed to disagree. "Breaking out now" is Method
A's mechanical rule (today's close > 50-day resistance, volume surge, uptrend). The "This setup
has happened before... faded -15.6%" line comes from a completely separate engine (`analogs.py`,
"The Read") that ignores breakout status entirely — it finds the single historical bar on that
stock whose EMA-stack/coiling/ADX/distance-to-52w-high/distance-to-resistance *shape* most
resembles today's (z-scored distance), independent of whether that day was itself a breakout.
Apollo's match was only 48% similar (a moderate, not tight, match — the engine suppresses
anything below its similarity threshold rather than show a bad precedent). So the honest read:
the mechanical trigger fired, Apollo's *aggregate* past-breakout history is actually good (62%
follow-through, no caution shown), and separately, the single closest-shaped precedent we could
find (not necessarily a breakout day, and a weak match) happened to fade. Flagged to the user
that the current copy ("This setup has happened before") doesn't make clear it's matching general
chart *shape*, not "past breakouts like this one" — a real wording improvement if they want it,
not built yet (nobody asked for the copy change, only the explanation).

### 2. Fixed: sector filter (and any filter) not updating the detail pane
Root cause: `applyFilters()` (`combined_breakout_scanner_platform.html`) only ever called
`renderWatchlist()` — the right-side detail pane's `current` stock was only ever set by
`selectStock()`, which nothing in the filter path called. Fix: `applyFilters()` now checks
whether `current` is still inside the newly-filtered (and MAX_RESULTS-sliced) list; if not, it
calls `selectStock()` on the new top-ranked match. If `current` is still in the list, it's left
alone — deliberately, so typing in the search box doesn't yank the detail view away mid-keystroke
on every character. Verified live: clicking "Industrials" in the Sector Radar now switches the
detail pane from ICICIBANK to APOLLO (top-ranked Industrials stock); confirmed again during the
fundamentals-filter testing below (applying a Large Cap + ROE≥20 combination correctly moved the
detail pane from ICICIBANK, which fell out once ROE was added, to MARICO, which satisfies both).

### 3. Built: Fundamentals filter panel (Market Cap, P/E, Growth, ROE, D/E)
Checked what data existed first: `data/breakouts.json` had zero fundamentals fields (confirmed;
matches CLAUDE.md's already-open TODO #6). Tested yfinance's `.info` against 7 real stocks
(ICICIBANK, RELIANCE, TCS, APOLLO, CUPID, NKIND, 3MINDIA) — Market Cap, P/E, Revenue Growth,
Profit Growth (`earningsGrowth`), and ROE are reliably available (correctly `None` for
loss-making names); D/E is available for most but not banks (not a standard metric for them).
**ROCE is not a yfinance field at all** — India-screener-style metric, not standard Yahoo Finance
data. Decided with the user (planned via EnterPlanMode first): **skip ROCE for v1** rather than
add a new scrape source for one field, and **build only the screenshot's "Fundamental" tab** —
no Technical/Chart tabs (nothing specified for them) and no "Only latest quarter results" toggle
(yfinance's `.info` gives trailing/current snapshot values, not a quarterly-vs-annual split, so
honoring that toggle would need a new quarterly-financials data source).

**What was built**, following the exact same pattern as sectors/holdings (standalone,
decoupled, quarterly-slow reference data merged in by `run_scan.py`, not part of the daily scan):
- **`backend/fundamentals.py`** (new) — `fetch_fundamentals(symbol)`, mirrors `sectors.py`'s
  `fetch_sector()` shape. Pulls `marketCap`/`trailingPE`/`revenueGrowth`/`earningsGrowth`/
  `returnOnEquity`/`debtToEquity` from yfinance's `.info`, with unit conversions verified against
  known real-world figures before trusting them (market cap ÷1e7 → ₹ Crore; Yahoo's
  `debtToEquity` ÷100 → the conventional ratio, e.g. TCS's near-zero debt and Reliance's ~0.37
  D/E both matched public knowledge; growth/ROE fractions ×100 → %).
- **`backend/fetch_fundamentals.py`** (new) — line-for-line the same resumable/standalone
  pattern as `fetch_sectors.py` (readiness-prioritized, skips already-fetched symbols, saves
  every 50, caches misses). Ran whole-market: **1,820/1,823 stocks fetched successfully in
  ~630s (~10.5 min)**, only 3 misses.
- **`backend/run_scan.py`** — new merge block (same shape as the holdings/sectors ones),
  `s["fundamentals"] = fundamentals.get(s["symbol"])`, `None` when absent.
- **`combined_breakout_scanner_platform.html`** — new collapsible "⚙ Fundamentals" panel
  (same expand/collapse pattern as the Sector Radar), with one row per metric: Market Cap and
  P/E get the exact preset bucket boundaries from the user's screenshot (Micro/Small/Mid/Large;
  Deep Value/Value/Fair/Growth/High Growth) as clickable chips; Revenue Growth, Profit Growth,
  ROE, and D/E get a plain custom Min/Max + Apply (no invented preset boundaries, since none were
  shown for those four). `filters.fundamentals` extends the existing `filters` state;
  `passesFundamentals(s)` extends `currentVisible()` — a stock with no fundamentals data fails
  any *active* fundamentals filter but is otherwise unaffected (same graceful-degradation
  convention as `sector: "Unclassified"`).

### Verified end-to-end
Ran the full pipeline (`fetch_fundamentals.py` whole-market → `run_scan.py`) and confirmed via
Playwright against the live local server: opened the panel, clicked "Large (1L+)" market cap →
112 matches (cross-checked independently via a Python query on the JSON, same count); added a
custom ROE ≥ 20 on top → 40 matches, detail pane correctly moved from ICICIBANK (ROE 16.4%,
disqualified) to MARICO (ROE 41.4%, mcap ₹108,666 Cr — genuinely satisfies both filters); cleared
both, back to all 1,823. Zero browser console errors throughout. Screenshot confirmed the panel's
visual styling matches the rest of the site (same card/purple-accent language) with no separate
design-system drift.

### Current honest state
- **Not committed.** New this thread: `backend/fundamentals.py`, `backend/fetch_fundamentals.py`,
  `data/fundamentals.json`. Modified: `backend/run_scan.py` (fundamentals merge block, on top of
  session 6's benchmark-fetch changes), `combined_breakout_scanner_platform.html` (fundamentals
  panel + the sector/detail-pane sync fix, on top of session 6's `verdictExplainer` change),
  `data/breakouts.json`/`data/predictions_log.jsonl`/`data/track_record.json` (regenerated again
  by this thread's `run_scan.py` run — real, current data).
- `data/fundamentals.json` is quarterly-slow reference data, same cadence philosophy as
  `holdings.json`/`sectors.json` — no need to re-run `fetch_fundamentals.py` again soon.

### Next steps
- Decide on committing (bundle with sessions 4/6 since files overlap).
- Optional, not requested yet: a "Fundamentals" display card in the stock detail pane (currently
  filters-only — a user can filter by ROE but can't see a stock's ROE anywhere in its own card).
- Optional: reword the analog engine's "This setup has happened before" copy to clarify it's
  matching general chart shape, not specifically past breakouts — flagged to the user, not
  requested as a change yet.
- If ROCE is wanted later, the natural path is a screener.in-style scrape (mirror
  `holdings_screener.py`), not a yfinance field — it doesn't exist there.

### How to re-verify
```
cd backend; python fetch_fundamentals.py   # only needed if data/fundamentals.json is stale/missing
python run_scan.py                          # merges it into breakouts.json
```
Then serve locally and expand the "⚙ Fundamentals" panel in the top filter bar.

---

## Session 6: Method E folded into production readiness scoring

### Why
Session 4 identified Method E (relative strength vs Nifty) as the strongest standalone backtest
result and explicitly left "fold it into readiness scoring" as a decision for later, not an
implementation task. This session made that decision and built it.

### Decisions made (in order)
1. **Mechanism**: independent, parallel top-tier readiness trigger with its own label — not
   ANDed with Method A. Round 2 of session 4's backtest showed ANDing (the `AE_combo`) washes
   E's edge back to baseline (38.9% ≈ A's 38.8%), so gating would throw the edge away rather than
   sharpen it.
2. **Trend gate**: before wiring in, tested whether gating Method E on the existing `uptrend`
   column (which Method A's `is_breakout` already requires) costs accuracy — raw E has no trend
   filter and could otherwise flag a downtrending stock as high-readiness, breaking the site's
   "high readiness always means uptrend" invariant. Added `add_method_e2_relative_strength_
   uptrend` to `backend/methods.py`, re-ran the whole-market backtest (2,055 stocks, 57,719 graded
   events): **E2 (uptrend-gated) = 41.6% hit rate, n=15,220** vs **E (ungated) = 41.1%, n=19,747**
   — statistically indistinguishable, and the gate only costs ~23% of E's volume (E/E2 Jaccard
   overlap = 65%, most of E's fires already happen in an uptrend). **Decision: ship E2, not raw
   E** — the safety gate is free.

### What was built
- **`backend/methods.py`**: added `add_method_e2_relative_strength_uptrend()` (E masked by
  `uptrend`), registered in `add_all_methods()`; docstring updated to note E/E2 are no longer
  research-only.
- **`backend/analyze_reliability.py`**: registered `E2_relative_strength_uptrend` in
  `BASE_METHODS` and `RELEVANT_EXTRAS`, added to the `print_examples()` call list in `main()`.
- **`backend/run_scan.py`**: imports `add_method_e_relative_strength`, `add_method_e2_relative_
  strength_uptrend`, `fetch_benchmark` from `methods`. Fetches the Nifty benchmark once per run
  (mirrors `analyze_reliability.py`'s pattern); inside the per-stock loop, computes
  `is_breakout_e`/`is_breakout_e2` on `feat` right after `add_indicators()` and before
  `build_summary()`. Deliberately calls the two E-specific functions directly rather than
  `add_all_methods()`, to avoid computing the unshipped B/C/D/D2/F methods (VCP pivot-scanning
  etc.) for every stock in production.
- **`backend/find_breakouts.py` — `build_summary()`**:
  - New readiness rung, inserted between the `not in_uptrend` gate and the existing near/coiling
    checks (only reachable when `in_uptrend` is true, since `is_breakout_e2` already requires it):
    `signal: "relative_strength"`, label "Outperforming the market — new relative-strength high
    vs Nifty", `score: "high"`, `watch: True`. Every other ladder branch gets `signal: None` via
    a single `readiness.setdefault("signal", None)` rather than editing each branch.
  - A **separate, independent reliability caution** for this trigger — computed from that stock's
    own historical `is_breakout_e2` follow-through rate (reusing the generic `followthrough`
    column, confirmed populated unconditionally per-day, not gated on `is_breakout`), using the
    same <40%-is-a-caution threshold as Method A's existing caution. The two reliability sources
    are kept strictly mutually exclusive in the code (`if readiness["signal"] ==
    "relative_strength": ... elif ...: # Method A's existing block`) so a stock on-watch via the
    RS trigger never shows a caption computed from Method A's unrelated history.
  - **Deliberately out of scope**: the `history` block (past_breakouts/examples, powers the
    "Historical Precedents" card) stays Method-A-only — no second parallel history section. The
    generic resistance-based entry guidance ("watch for a close above ₹X...") also stays as-is
    even when the RS trigger fires — it's the site's one standard entry playbook, not a claim
    about why the stock is listed, so it's generic rather than wrong. Both are reasonable future
    polish, not needed for this change.
  - `analogs.py`'s `detect_analog()` is untouched — confirmed (via an Explore pass) it has no
    dependency on `is_breakout`/`readiness` at all.
- **`combined_breakout_scanner_platform.html`**: one new branch in the `verdictExplainer` logic
  (checked before the generic `score === "high"` case), keyed on `s.readiness.signal ===
  "relative_strength"`, with copy explaining the RS-line concept in plain English. This was the
  **one** place that needed a frontend change — confirmed via Explore that sort
  (`SORTERS.readiness`), the `primedOnly` filter, `READINESS_STYLE` coloring, and the Sector
  Radar's "primed" count all already key only off `readiness.score`/`readiness.watch`
  (bucket/boolean), not label text or reason, so they handle the new trigger correctly with zero
  changes.
- **`backend/settings.py`**: comment-only update noting `RS_BENCHMARK`/`RS_LOOKBACK` are now used
  in production too, not just research (no new settings needed).

### Verified (end-to-end, not just unit-level)
- Smoke-tested the new production code path (`add_indicators` → `add_method_e_relative_strength`
  → `add_method_e2_relative_strength_uptrend` → `build_summary`) on 10 liquid large-caps first —
  confirmed `readiness["signal"]` and the new reliability text behave correctly, including stocks
  with thin RS-event history and stocks where Method A's own caution should still apply unchanged
  (e.g. SUNPHARMA correctly kept its Method-A-flavored caution; ICICIBANK correctly got the new
  RS-specific one).
- Ran the full whole-market `run_scan.py` (2026-07-04): completed in 100.6s, 1,823 cards produced,
  **62 stocks tagged with the new `relative_strength` signal today** (e.g. ICICIBANK, BAJFINANCE,
  ADANIENT, CUPID, AUROPHARMA) — confirmed via `grep` on the regenerated `data/breakouts.json`
  (raw bytes correct UTF-8 em-dash; earlier terminal "�" was just a Windows console codepage
  rendering artifact, not a data bug).
- Used Python's `playwright` package (already installed; no `chromium-cli`/Node available in this
  environment) to drive an actual headless-Chromium session against the locally-served site,
  searched for ICICIBANK, clicked into its detail view, and confirmed in the live DOM: the
  readiness label, the correct "outperforming the Nifty" explainer text (not the old
  resistance/coiling text), the RS-specific reliability caution ("only 17% ... historically
  unreliable"), the purple "high"-tier styling, and zero browser console errors. Screenshot
  confirmed visually consistent with the rest of the site's design language.

### Current honest state
- **Not committed.** `git status` (as of this note): modified `backend/analyze_reliability.py`,
  `backend/find_breakouts.py`, `backend/run_scan.py`, `backend/settings.py`,
  `combined_breakout_scanner_platform.html`, `data/breakouts.json`, `data/predictions_log.jsonl`,
  `data/track_record.json` (the last three regenerated by the verification `run_scan.py` run —
  real, current data, not stale); untracked `backend/methods.py` (new this thread, also touched
  by session 4).
- The local static server (`python -m http.server 8000`) is still running from this session's
  verification step, same pattern as session 3 left it running — if it's been killed, restart
  with `python -m http.server 8000` from the repo root.
- `track.py`'s live daily-call logging automatically started grading episodes for the new
  RS-triggered watch windows too (intended, not a side effect to fix). Its one-time walk-forward
  *seed* simulation (`_assess_row`) doesn't know about the new trigger, but only runs once, the
  first time `predictions_log.jsonl` doesn't exist — that log already exists, so this doesn't
  matter unless the log is ever deleted and reseeded from scratch.

### Next steps
- Decide on committing (user's rule: commit on a branch, not `main`) — natural to bundle with
  session 4's `methods.py`/`settings.py` changes since they're the same file, different sessions.
- Not urgent, optional future polish (see "deliberately out of scope" above): a parallel
  history/examples section for the RS trigger's own past events, and/or RS-aware entry guidance
  copy instead of the generic resistance-based text.
- Keep an eye on `data/predictions_log.jsonl` growth now that a second, independent trigger can
  open "watch" episodes — relevant to the already-known TODO #4 in `CLAUDE.md` (git/log growth at
  whole-market scale).

### How to re-verify
```
cd backend; python run_scan.py         # regenerate breakouts.json with the new signal
grep -c '"relative_strength"' ../data/breakouts.json   # how many stocks got it today
```
Then serve (`python -m http.server 8000` at repo root) and open a tagged symbol in the browser.

---

## Session 5: Claude Skills for this repo's workflow (tooling)

### Why
Across sessions 4 (and its own wrap-up), the same two loops got redone from scratch each time:
smoke-test → whole-market backtest → plain-language interpretation (session 4, repeated for D,
D2, E, and the combos); and consolidating an ending session into `HANDOFF.md` + memory. User asked
which Claude Skills would help, and agreed to build the two most repeated ones.

### What was built
- **`.claude/skills/wrap-session/SKILL.md`** (new) — end-of-session skill: check `git status`
  first, rewrite `HANDOFF.md` per-thread (Why/What/Bugs/Results/Next steps/re-run commands), update
  the memory system (existing files get appended to, not overwritten; `session-handoff-pointer.md`
  updated last since it's the one file guaranteed to be read first next time; `MEMORY.md`'s index
  kept in sync), then report back what's committed vs. not plus a concrete next-session opening
  message. This skill was used to write this very entry.
- **`.claude/skills/backtest-method/SKILL.md`** (new) — codifies session 4's workflow: read
  `backend/settings.py`/`backend/methods.py` first (don't reinvent existing constants/trigger
  functions), smoke-test any new/changed method on ~10 liquid large-caps before a whole-market run
  (specifically watching for event-clustering — fix via `_dedup_with_cooldown()`), run
  `backend/analyze_reliability.py`, then interpret with fixed talking points: what `n` means and
  why methods differ by orders of magnitude, the 50%-hit-rate-equals-breakeven framing (the grading
  rule is a strict 1:1 reward:risk bet), "combining methods isn't automatically additive" (AE
  washed out despite E's solo edge), and always include live stock/date walkthroughs. Ends by
  recording results into the `multi-method-breakout-comparison` memory file.

### Notes for later (not action items, just recorded so they aren't re-litigated)
- **Trigger mechanics**: a Skill fires either via an explicit `/skill-name`, or organically when a
  request matches its description — but only once it's in the harness's available-skills list,
  which may not refresh mid-session for Skills just created (confirmed: this session's own
  `/wrap-session` invocation needed the list to refresh before it could be called).
- **Coupling to future site changes**: `wrap-session` is pure process (paths + the
  commit-on-a-branch-not-`main` rule) — routine frontend/backend feature work never requires
  updating it. `backtest-method` is coupled to `backend/methods.py`/`analyze_reliability.py`/
  `settings.py` by name, but self-heals for *new* methods since it tells the agent to read
  `methods.py` fresh rather than trusting a hardcoded list. The one real staleness risk: if the
  grading rule's reward:risk ratio ever stops being 1:1 (currently `settings.STOP_LOSS_FRACTION`-
  driven), the skill's "50% = breakeven" framing goes stale and the skill file itself needs editing.

### Next steps
None required — both skills are complete and usable as-is. Only revisit `backtest-method` if the
1:1 reward:risk grading rule changes, or the named backend files/functions get renamed.

---

## Session 4: breakout-detection method comparison (research)

### Why
User wants to raise accuracy / find stocks confirmed by multiple independent signals, not just
widen the pool of suggestions. Motivated by looking at other tools (Chartink screeners, VCP
scanners) and asking "is our one breakout definition (Method A) actually the best we can do, and
would combining several distinct definitions do better?"

### What was built
- **`backend/methods.py`** (new) — five alternative breakout definitions (B-F) plus a loosened
  variant of D, each a trigger function that takes the dataframe `find_breakouts.add_indicators()`
  already produces and adds one boolean column. Nothing here is called from `run_scan.py` — it's
  research-only, imported by `analyze_reliability.py`.
  - **B — VCP** (`add_method_b_vcp`): true multi-leg volatility contraction — finds consecutive
    pivot highs (via `patterns.find_pivots`), measures the peak-to-trough depth and average volume
    of each leg between them, requires depths to shrink AND volume to decline leg-over-leg, then
    triggers on a close above the final pivot high with a volume confirmation.
  - **C — Squeeze** (`add_method_c_squeeze`): Bollinger Band width compresses into the bottom 15%
    of its own trailing 120-day range, then price closes above the upper band with volume — a
    volatility-regime trigger, no price-level condition.
  - **D — Trend inception** (`add_method_d_trend_inception`): +DI crosses above -DI (added
    `plus_di`/`minus_di` as real columns in `find_breakouts.add_indicators` for this — previously
    computed as throwaway locals) while ADX is both rising and above a threshold, AND the full
    8/21/50/200 EMA stack is in perfect bullish order. Strict, rare, catches the START of a trend.
  - **D2 — Trend inception, loosened** (`add_method_d2_trend_inception_loose`): same DI-cross idea,
    but a lower ADX bar (`settings.DI_ADX_THRESHOLD_LOOSE=15`) and the broader existing `uptrend`
    column instead of the strict 4-EMA stack. Built specifically to test whether D's edge survives
    with more data, or depends on being strict.
  - **E — Relative strength** (`add_method_e_relative_strength`): stock-price ÷ Nifty (`^NSEI`,
    fetched once per run via `fetch_benchmark()`) ratio hits a new 50-day high — an IBD-style "RS
    line" signal, independent of the stock's own absolute chart.
  - **F — Episodic pivot** (`add_method_f_episodic_pivot`): opening gap >=5% AND volume >=5x the
    50-day average. **Only tests the technical proxy** — confirming an actual fundamental catalyst
    (earnings surprise etc.) needs an earnings-date/surprise data source that doesn't exist in this
    pipeline yet (no `fetch_earnings.py`). Don't treat F's result as testing the real "catalyst"
    hypothesis, only the "raw gap+volume shock" precondition of it.
- **`backend/settings.py`** — new tunables block for all of the above (VCP_*, SQUEEZE_*, DI_ADX_*,
  RS_*, EP_*), kept separate from the existing Method-A thresholds.
- **`backend/analyze_reliability.py`** (extended, not rewritten) — this is the harness:
  - `BASE_METHODS` dict maps method name -> trigger column (A through F + D2).
  - `COMBOS` dict defines 4 "multiple methods agree on the same stock-day" pseudo-methods: `AE_combo`,
    `AD_combo`, `ED_combo`, `AED_combo`. Built directly from the already-deduped base columns.
  - **`_dedup_with_cooldown()`** — important fix found via smoke-testing: a permissive method (e.g.
    E) can stay triggered for several days in a row during one continuous move — those aren't
    independent trials, they're the same move counted repeatedly. Added a cooldown (same window as
    the grading period, `settings.FOLLOWTHROUGH_WINDOW`) so a method can't re-fire on the same stock
    until it's gone quiet. Applies to all methods/combos. (Combos don't need their own extra cooldown
    — they inherit the spacing from whichever base method fired, since combos can only be true where
    their slower/rarer component is true.)
  - **`test_methods()`** — per-method/combo hit-rate + frequency table, with a two-proportion
    z-test vs. Method A as baseline.
  - **`report_overlap()`** — pairwise Jaccard similarity between the 7 base methods (not combos) —
    answers "are these actually independent signals, or mostly re-detecting the same days?"
  - **`print_examples()`** — real stock/date/price walkthroughs for any method/combo, called from
    `main()` for D, D2, E, and all 4 combos. Only shows the context fields (`+DI/-DI`, RS ratio,
    gap%) that are actually *relevant to that method* (`RELEVANT_EXTRAS` dict) — those columns exist
    on every event regardless of method since they're computed unconditionally, so without this
    filter irrelevant numbers were showing up next to methods that don't use them.
  - `main()` unchanged in spirit: still prints persistence + feature tests on Method A alone (not
    polluted by the other methods' events), then the new method-comparison + overlap + examples
    sections.
- Two bugs caught and fixed mid-session (both via smoke-testing on a 10-stock sample before the
  whole-market run, which is the right instinct — catch these before a 5+ minute run, not after):
  1. Event clustering (see cooldown fix above) — first run showed method E firing ~45 days/stock
     over 3 years before the fix, ~14/stock after.
  2. A misleading "avg R-multiple" column in `test_methods()` — the underlying `r_multiple` column
     is actually a ₹-risk amount (entry − stop), not a normalized multiple; averaging that across
     stocks of wildly different share prices is meaningless. Dropped from the report entirely.
  3. `print_examples()` initially showed "+DI/-DI" and "gap%" next to methods that don't use them
     (e.g. RS ratio next to a Method D example), and phrased a successful event as "actual: -0.7%
     ... (hit target before stop)" — contradictory-looking, since the target can be hit intraday
     then price pulls back by day 10; those are two different measurements. Fixed: gated extras by
     method (`RELEVANT_EXTRAS`), and reworded to show target/stop as % distance from entry with the
     10-day close clearly labeled "for reference only."

### Results (whole market, 2,055 stocks scanned, run 2026-07-04)

| Method | n | hit rate | vs. A |
|---|---|---|---|
| A — Donchian/Minervini (existing, baseline) | 9,810 | 38.8% | — |
| B — VCP | 167 | 34.1% | not significant, p=0.217 |
| C — Squeeze | 5,999 | 40.2% | not significant, p=0.078 |
| D — Trend inception (strict) | 182 | **48.9%** | significant, p=0.006 |
| D2 — Trend inception (loosened) | 693 | 42.6% | not significant, p=0.051 |
| E — Relative strength vs Nifty | 19,747 | **41.1%** | significant, p<0.001 |
| F — Episodic pivot (gap+volume, no catalyst data) | 1,587 | 35.3% | significant *worse*, p=0.007 |
| AE combo | 4,203 | 38.9% | no improvement over A alone |
| AD combo | 36 | 50.0% | not significant yet, p=0.170 |
| ED combo | 47 | **51.1%** | not significant yet, p=0.086 |
| AED combo (all 3) | 25 | 48.0% | too rare to read |

Sanity check: Method A's 38.8% here matches the already-documented 32-46% persistence-bucket range
in `CLAUDE.md` — the new harness reproduces known results, nothing broke. (Note: this run found
9,810 Method-A events vs. a previously-documented 17,695 — not a bug, `HISTORY_YEARS=3` is a
rolling window so the exact count depends on when you run it.)

**Interpretation:**
- **E is the strongest standalone result** — both the most frequent (10.3 events/stock/3yr) and
  meaningfully, significantly better than baseline. Only 17% event-overlap with A, so it's not
  redundant. This is the most defensible candidate to actually fold into scoring.
- **D is a real edge but doesn't survive loosening** — D2 fired 4x more often (693 vs 182 — it's
  essentially a superset of D, 26% Jaccard = ~all of D's events are inside D2's) but the hit rate
  fell to 42.6% and lost significance. The strictness itself is doing the work, not gatekeeping
  arbitrarily — don't relax D's thresholds expecting more volume "for free."
- **Combining is not automatically additive** — AE's hit rate (38.9%) is barely different from A
  alone (38.8%), despite E individually being better. E's edge apparently comes disproportionately
  from days it flags that A does NOT also flag; intersecting with A washes it back toward baseline.
- **AD/ED are the only results to cross the 50% breakeven line** (this rule is a 1:1 reward:risk
  bet, so <50% loses money before costs at that exact payoff) — promising, but thin (36/47 events
  market-wide over 3 years) and not statistically significant yet. Worth re-checking once more
  history/time has passed, not something to trust today.
- **F (unconfirmed gap+volume) underperforms baseline** — a real, if humbling, result: an
  unconfirmed shock is a *weaker* signal than the existing trend-template breakout, not stronger.
  This does NOT disprove the user's actual "confirmed by a fundamental catalyst" hypothesis — it
  only shows the raw-shock precondition isn't sufficient alone. Testing the real hypothesis needs
  earnings-date/surprise data this pipeline doesn't have.
- **B (VCP) is inconclusive** — worse than baseline but on too small a sample (167) to call it,
  and the strict "every leg must shrink AND every leg's volume must decline" condition may just be
  miscalibrated. Needs threshold tuning, not a verdict.

### Concrete worked examples (real stocks, for understanding the mechanism — see table above for
the honest hit rate; these are hand-picked successes to illustrate what each method actually saw)

- **AVANTIFEED, 2026-02-03** — satisfied both D and E simultaneously (an `ED_combo` event). Price
  ₹959.80 had just cleared its 50-day high of ₹890. D's read: +DI=37.3 vs -DI=17.0 (momentum
  clearly bullish) with ADX=26.4 (trend accelerating). E's read: also outperforming the Nifty more
  than in the last 50 days. Stop ₹836.60 (-12.8%), target ₹1,083.00 (+12.8%). Target hit before
  stop — a win. For reference (not part of the pass/fail rule), the close 10 trading days later
  was +41.6% — well past the target.
- **ANANDRATHI, 2026-06-19** — pure Method E, no price-level breakout involved. Price ₹1,867.50;
  the only trigger was the price÷Nifty ratio hitting a fresh 50-day high. Stop ₹1,752 (-6.2%),
  target ₹1,983 (+6.2%). Won; close 10 days later was +10.4%.
- **RELIABLE, 2026-05-27** — pure Method D, barely above its own 50-day high (₹138.90 vs ₹138.80).
  What made it a D signal: +DI (16.4) had just crossed -DI (15.5) with ADX (20.1) actively rising.
  Stop ₹130.47 (-6.1%), target ₹147.33 (+6.1%). Won; close 10 days later was +2.6% (smaller, still
  positive follow-through).

### Next steps (not yet decided/built)
1. **Fold E into `readiness`/`reliability` scoring** — the most defensible next move, since it's
   well-powered and already validated the same way the existing persistence/base-depth features
   were. Not yet done — a decision, not just an implementation task.
2. **Keep watching D, AD, ED** — re-run `analyze_reliability.py` again after more time/history has
   accumulated to see if the thin combo samples (36/47 events) firm up or wash out. Don't act on
   them yet.
3. **B (VCP) needs threshold tuning** before a verdict — try relaxing `VCP_MIN_LEGS` or the
   volume-decline requirement and re-test.
4. **F needs real earnings-catalyst data** to test the user's actual hypothesis — would need a new
   `fetch_earnings.py` (same standalone/resumable pattern as `fetch_sectors.py`/`fetch_holdings.py`),
   sourcing earnings dates + surprise % (e.g. via `yfinance`'s `.earnings_dates`, coverage for
   NSE/BSE names untested). Not started.
5. **Nothing here is committed.** New: `backend/methods.py`. Modified: `backend/settings.py`,
   `backend/find_breakouts.py` (added `plus_di`/`minus_di` columns — additive, doesn't change any
   existing output), `backend/analyze_reliability.py`. Decide on committing (user's rule: commit on
   a branch, not `main`).

### How to re-run
```
cd backend
python analyze_reliability.py
```
Whole-market run takes several minutes (2,055 stocks, 11 methods/combos graded). It prints to
stdout only — doesn't write any file — so redirect if you want to keep the log, e.g.
`python analyze_reliability.py > ../scratch_reliability.log 2>&1`.

---

## Session 3: Ask AI chatbot (feature work)

This session built a brand-new **"Ask AI" chatbot feature** — a slide-over chat panel that can
answer questions about any stock (not just the one open), run discovery queries across the whole
market, and search the live web, backed by a **7-model fallback chain across Groq/Gemini/Cerebras/
NVIDIA NIM/DeepSeek** so no single provider's free-tier rate limit blocks the feature. It works and
has been verified live multiple times. **Nothing from this session is committed yet** — decide
whether to commit (user's setup: commit on a branch, not `main`) once you've looked it over.

### What was built
#### 1. Ask AI — the feature itself
- **`backend/ask_ai.py`** (new) — the whole brain of the feature. A standard tool-calling model
  (NOT Groq's `compound` auto-tool system, which can't take custom tools) gets four tools it can
  call as many times as a question needs:
  - `lookup_stock(symbol_or_name)` — exact/fuzzy-resolves ANY ticker or company name (not just
    whichever stock is open in the app) to its full computed context, via `difflib` fuzzy matching.
  - `search_stocks(sector, min_adx, primed_only, in_uptrend, sentiment, sort_by, limit)` — filtered/
    sorted slice of the whole ~1,800-stock universe for discovery questions.
  - `run_sql(select_query)` — read-only SQL (SELECT-only, semicolon/keyword-blocked) over an
    in-memory DuckDB table of every stock's flattened fields, for open-ended aggregate questions
    `search_stocks`'s fixed params can't express. Has a `sector_group` column that mirrors the
    frontend's own `sectorGroup()` split (see bug #2 below).
  - `web_search(query)` — only called when the model decides it needs live info; tries Groq's
    `compound`/`compound-mini` (built-in search) then falls back to **Gemini's own native Google
    Search grounding** (a completely separate API — see below) if those are out of quota.
  - System prompt forces `[Our data]`/`[Web]` tagging on every claim, bans direct buy/sell
    instructions, and requires trying `web_search` before saying "not in our data" (see bug #1).
- **`backend/chat_server.py`** (new) — tiny stdlib `http.server` proxy so the API key(s) never
  touch client-side JS. `GET /api/health` (shows the live fallback chain), `POST /api/ask`.
  Run: `cd backend; python chat_server.py` → `http://localhost:8010`.
- **Frontend** (`combined_breakout_scanner_platform.html`, modified) — a purple "Ask AI" floating
  button (bottom-right) opens a slide-over panel. Shows `[Our data]`/`[Web]` as colored badges,
  a "🔍 looked up X · searched stocks" tools-used footer, an amber "⇄ fell back across: ..." line
  (only shown when a fallback actually happened), and a Sources list for web-search citations.
  JS: `initAskAi()`, `sendAskAi()`, `appendAskAiMessage()`, `toolCallLabel()`. Talks to
  `http://localhost:8010/api/ask`.
- **`backend/.env.example`** (new) — documents every env var. Real secrets live in `backend/.env`
  (gitignored, confirmed via `git check-ignore`) — **already populated this session** with working
  `GROQ_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY` (DeepSeek has no funded balance yet — see
  below). Do **not** re-ask the user to paste keys in chat; they're already in the file.

#### 2. Multi-backend fallback chain (the resilience layer)
Built after repeatedly hitting Groq's free-tier daily token caps mid-session. Two independent
chains, both rebuilt from `.env` on every request (no restart needed to pick up new keys):

- **Main chain** (`_build_main_chain()` in `ask_ai.py`), tried in order, falls through to the next
  on ANY failure (quota/auth/outage/network):
  1. `groq-70b` (`llama-3.3-70b-versatile`) — best reasoning, smallest daily budget (100K tokens/day)
  2. `gemini` (`gemini-2.5-flash`) — verified solid at multi-step tool chaining
  3. `cerebras` (`gpt-oss-120b`) — verified: authenticates fine for plain chat, but hit a
     `token_quota_exceeded` (TPM) 429 on its first real tool-calling attempt — free tier appears
     tight for tool-calling-sized requests. Structurally correct integration.
  4. `nvidia-nim` (`deepseek-ai/deepseek-v4-flash`) — authenticates fine, but hit a request-limit
     error (`ResourceExhausted: Worker local total request limit reached (2609/32)`) on its first
     real tool-calling attempt. Free NIM tier is documented elsewhere as "200 req/day" — the 32-ish
     figure suggests a much tighter concurrent/burst limit than that daily figure implies. Worth
     re-testing once quota clears.
  5. `groq-gptoss20b` (`openai/gpt-oss-20b`) — separate Groq budget
  6. `groq-8b` (`llama-3.1-8b-instant`) — cheap/fast but **weak at chaining multiple tool calls**
     (see bug #1) and has been observed leaking raw `<function=...>` tool-call syntax into its
     answer text (defended against — see bug #3's cleanup). Kept near-last on purpose.
  7. `deepseek` (`deepseek-v4-flash`) — **needs a funded balance** at platform.deepseek.com;
     currently fails fast with "Insufficient Balance" and falls through harmlessly. NOTE:
     `deepseek-chat`/`deepseek-reasoner` deprecate 2026-07-24 — don't default to them.
- **Search chain** (`_build_search_chain()` + `_gemini_native_search()`), tried in this order:
  1. `groq-compound` (`groq/compound`) — separate budget (uses `llama-4-scout-17b`+`gpt-oss-120b`
     internally)
  2. `groq-compound-mini` — **shares its budget with `groq-70b`** (both run on
     `llama-3.3-70b-versatile` internally) — a non-obvious coupling that bit us mid-session
  3. `gemini-native-search` — Gemini's own Google Search grounding via a **different API**
     (`POST https://generativelanguage.googleapis.com/v1beta/interactions`, header
     `x-goog-api-key`, body `{"model", "input", "tools":[{"type":"google_search"}]}` — NOT the
     OpenAI-compat endpoint). Response shape: `steps[]` list; find `type=="model_output"`, its
     `content[]` parts have `type=="text"` + `text`, and `annotations[]` with `url` for citations.
     Genuinely separate quota from everything else.
- `GET /api/health` shows the live chain state any time — check this first when resuming.

#### 3. Real bugs found and fixed (all verified live, not just theoretical)
1. **Gave up instead of searching the web** — asked "MOIL last 4 qtr earnings", it correctly said
   "not in our data" then just stopped instead of trying `web_search`. Root cause: `llama-3.1-8b-
   instant` (a weak fallback model) doesn't reliably chain a 2nd tool call on its own judgement.
   Fixed via an explicit system-prompt rule forcing `web_search` before giving up, and reordering
   `groq-8b` to the back of the chain so stronger models serve first.
2. **Sector-count mismatch** — "which sector has the most primed stocks" returned 15 for Consumer
   Cyclical via SQL, but the Sector Radar panel shows 34. Root cause: the flattened SQL `sector`
   column held the combined "Sector · Industry" string, so `GROUP BY sector` splintered into
   per-industry rows instead of matching the frontend's broad grouping. Fixed by adding a
   `sector_group` column that mirrors the frontend's `sectorGroup()` split exactly — now matches.
3. **Crash: `AttributeError: 'list' object has no attribute 'get'`** — the whole `ask()` call would
   500 whenever Gemini returned a rate-limit error, because **Gemini wraps error bodies in a JSON
   array** (`[{"error": {...}}]`), unlike every other provider's plain dict. `_one_completion`'s
   error parsing now handles both shapes defensively.
4. Also (informational, not a bug): `llama-3.1-8b-instant` occasionally leaks raw
   `<function=lookup_stock>{...}</function>`-style text into its final answer. Defended with
   `_clean_answer()` (regex strip), applied at every place an answer is returned.
5. Original bug this whole rebuild was chasing (from before the tool-calling rewrite): asking about
   a *different* stock than the one open in the app (e.g. "can you check CGPower as well" while
   ICICIBANK was open) returned a hallucinated wrong price via `[Web]`. Fixed by giving the model
   real `lookup_stock`/`search_stocks`/`run_sql` tools instead of a single pre-baked JSON blob.

### Current honest state (as of session 3 end)
- Both servers were left running: static site on `:8000`, Ask AI backend on `:8010`.
  If they've been killed, restart: `python -m http.server 8000` (repo root) and
  `cd backend; python chat_server.py` (separate terminal).
- **All three search-capable backends (`groq-compound`, `groq-compound-mini`,
  `gemini-native-search`) were simultaneously out of free daily/rate quota** at session end, purely
  from the volume of live testing done that day. Not a code bug — should recover with time.
- `backend/.env` has real keys for all 5 providers (Groq, Gemini, Cerebras, NVIDIA NIM, DeepSeek).
  Groq and Gemini are confirmed working end-to-end for real tool-calling conversations. Cerebras
  and NVIDIA NIM authenticate fine but both hit rate limits on their first real tool-calling
  attempt — untested whether they're usable once quota clears. DeepSeek needs credits added at
  platform.deepseek.com before it serves anything (key valid, balance 0).
- Nothing from this thread is committed. `git status` shows: modified
  `combined_breakout_scanner_platform.html`; new `backend/ask_ai.py`, `backend/chat_server.py`,
  `backend/.env.example` (untracked, all intentional). `backend/.env` is correctly gitignored.

### How to resume
1. `curl http://localhost:8010/api/health` — check whether servers survived / which backends
   currently have quota (main_chain / search_chain lists).
2. If down: restart both servers (commands above).
3. Open `http://localhost:8000/combined_breakout_scanner_platform.html`, click the purple
   "Ask AI" button, try a grounded question first (e.g. "why is this stock primed?") since those
   don't need `web_search` and are most likely to have quota.
4. Decide on committing this session's work (user commits on a branch, not `main`).

### Pending / next (Ask AI specifically)
- Re-test `cerebras` and `nvidia-nim` backends once their rate limits clear (both authenticate
  fine, both hit limits on the very first real tool-calling attempt — genuinely unverified whether
  they hold up under normal use, unlike Groq/Gemini which are confirmed working).
- Verify DeepSeek actually contributes once funded (currently valid key, zero balance).
- Consider: is the ~1,284-token fixed overhead per call reducible further (e.g. trimming tool
  descriptions, or only sending the SQL schema when `run_sql` is actually likely to be used)?
- `tool_search_stocks`'s `sector` param loosely substring-matches both `sector_group` and full
  `sector` — fine for now, not exhaustively tested against ambiguous sector names.
- No conversation reset when switching stocks mid-chat in the UI (old messages stay; only the
  context label updates) — cosmetic, not urgent.
- `chat_server.py` is dev-only: no auth, open CORS, single-process stdlib server — not for real
  deployment as-is.
- Longer-term idea floated but not built: Gemini's search grounding could plausibly replace/
  supplement `patterns.py`'s decorative pattern badge or feed into reliability scoring — speculative,
  not scoped.

---

## Pending / next (older, still open — see CLAUDE.md's "Still TODO" for full detail)
- Fundamentals (P/E, ROE, mcap) via `yfinance.info` — same fetch-script pattern as sectors.
- Enable the daily GitHub Action (confirm it survives yfinance rate limits from GitHub's IPs).
- Git/log growth (`breakouts.json` ~3.1MB/day, `predictions_log.jsonl` unbounded) — move serving
  data off `main` / prune the log.
- Retire or fold the decorative pattern badge into `readiness`/`reliability` scoring properly.
- Holdings re-scrape is done (1,816/1,822 have full quarterly history) — finished and
  committed/pushed in session 3 (commit `5b88f65`), unrelated to the Ask AI work above.

## Key files touched, by thread
- **Session 5 (Claude Skills):** New: `.claude/skills/wrap-session/SKILL.md`,
  `.claude/skills/backtest-method/SKILL.md`. Uncommitted (`.claude/` is currently untracked).
- **Session 4 (breakout methods):** New: `backend/methods.py`. Modified: `backend/settings.py`,
  `backend/find_breakouts.py`, `backend/analyze_reliability.py`. All uncommitted.
- **Session 3 (Ask AI):** New: `backend/ask_ai.py`, `backend/chat_server.py`,
  `backend/.env.example`, `backend/.env` (gitignored, has real keys). Modified:
  `combined_breakout_scanner_platform.html`. All uncommitted.
- **Already committed & pushed (session 3, unrelated to Ask AI):** `5b88f65` — finished
  holdings.json re-scrape (1,816/1,822 stocks), regenerated `breakouts.json`.
