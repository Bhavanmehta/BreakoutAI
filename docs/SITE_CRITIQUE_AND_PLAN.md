# BreakoutAI — Full Critique & "Best-in-Industry" Plan

_Date: 2026-07-09 · Prepared as a combined review: web design / UX, financial-analyst rigor, hedge-fund methodology, and competitive research. Intended as the working spec for implementing models._

---

## 1. Honest verdict — where the project stands today

**Grade: B (strong foundation, pre-product-market-fit polish).** The single most valuable
asset is one most retail tools *fake*: a forward-only live ledger
(`predictions_log.jsonl`) with hit rates computed only from calls logged before outcomes
were known, per-market backtested calibration (India 38.8% base vs US 26.7%), validated
high-conviction tiers (51.1% / 45.3% vs 26.7% base), and honesty gating ("unproven live —
N graded"). That discipline — including the acceptance-test replay that caught the
fresh-fire dedup bug before shipping — is genuinely rare. **Protect it above everything.**

But three things stand between "impressive engineering" and "best in industry":

1. **The live record is currently negative.** `ALPHA_WATCH.md` baseline: IN mean alpha
   −0.96% (beat_rate 33%, expectancy_r −0.143), US −0.53% (beat_rate 32%, expectancy_r
   +0.154). Sample is thin (resolved_n 14/26) so it's mostly noise — but the #1 project
   goal must be driving this positive, not adding features. A beautiful site publishing
   negative-alpha calls is a beautiful liability.
2. **Delivery is fragile and slow.** ~25 MB of `no-store` JSON fetched from
   `raw.githubusercontent.com` on every visit (9.7 MB IN + 15 MB US `breakouts.json`,
   fetched eagerly). Not a CDN you control, rate-limited, punishing on mobile.
3. **Zero tests** around money-adjacent statistics, plus a force-pushed `data` branch
   that could silently truncate the ledger — the project's crown jewel has no seatbelt.

---

## 2. Critique detail (ranked by impact)

### 2.1 Data serving & performance — CRITICAL
- `combined_breakout_scanner_platform.html` (~L1031/1051/1066) fetches
  `breakouts.json` **and eagerly** `us/breakouts.json` + both `performance.json`, all
  `{cache:"no-store"}`, from `raw.githubusercontent.com/.../data/` (~L672). ≈25 MB per
  visit, re-downloaded every refresh.
- Fixes (in order): lazy-load US on first toggle; split a light "list" payload
  (symbol/price/conviction/readiness, ~a few hundred KB) from per-stock detail fetched
  on card open; serve from Vercel static or Cloudflare Pages/R2 with real
  `Cache-Control` (short `max-age` + `as_of_date` cache-buster instead of `no-store`).

### 2.2 Statistical rigor — HIGH (this is the accuracy program)
- **Fixed ±6% stop/target band, not ATR-scaled.** Already proven distortionary: 51.7% of
  US events resolve neither side in 10 days; ATR-scaled regrade lifts US base to ~41.8%
  and is flat across volatility. A flat % band measures volatility, not signal. Move
  grading to entry ± k·ATR (product decision: changes displayed stops/history sitewide —
  do it once, early, with a clearly labeled "methodology v2" cutover date rather than
  living with a known-biased ruler).
- **`HINDSIGHT_MIN_N = 5` is too low.** n=5 gives ~±40 pp CI; the badge flips to a
  colored "proven" rate at 5. Raise threshold to ≥20, and display Wilson intervals or
  Bayesian-shrunk rates (prior = `SCORE_BASE_RATE`, machinery already exists in
  `score.py::reliability_estimate`).
- **No walk-forward / regime segmentation.** Score weights are fixed constants; the
  60/40 split was by stock, not time. Add: (a) time-based walk-forward re-validation,
  (b) hit-rate reporting bucketed by `market_mood` regime — cheap, and exposes whether
  breakouts only work in bull tape (they usually do).
- **No slippage/fill modeling.** Grade from next-bar open (not signal close) minus a
  spread proxy; report gross and net. Matters most for NSE small-caps and thin US names.
- **Benchmark-relative grading.** Alpha vs index exists diagnostically (`ALPHA_WATCH.md`)
  but the headline "hit rate" is absolute. In a bull tape a 45% hit rate can still lose
  to buy-and-hold — the site should report excess-vs-index as a first-class number, not
  a manual log.
- **Calibration curve.** You publish conviction 0–100; publish reliability-diagram data
  (predicted bucket vs realized) + Brier score once n allows. No competitor does this;
  it becomes a marketing weapon ("we show our calibration — ask your newsletter to").
- **`fii_flow: null`** silently drops one leg of the Greed/Fear composite. Surface
  partial-data states; better, actually populate it (free — see §4 India).
- **Multiple-testing honesty.** Scanning ~6,300 names daily manufactures false positives.
  Add a random-entry null baseline line to performance.html ("random picks over the same
  window: X%").

### 2.3 Trust & ops — HIGH
- **No tests.** Minimum bar: pytest with (a) output-JSON schema validation, (b)
  no-lookahead assertion (every resolve date > signal date), (c) ledger-only-grows
  check, (d) the India byte-identical regression already used ad hoc in session 9.
- **Force-pushed `data` branch = no rollback.** Add a pre-push gate (ledger row count ≥
  previous; refuse if shrunk) + keep N daily tarball backups as workflow artifacts.
- **Open Yahoo proxy** (`api/quotes.py`) with no rate limiting invites abuse; shared
  `WATCHLIST_SECRET` means one watchlist namespace for everyone.

### 2.4 Frontend / UX — MEDIUM-HIGH
- **Accessibility ≈ absent**: ~4 `aria-*`, 0 `alt`, 0 `tabindex` across 227 KB;
  readiness conveyed by color dots only. Add text badges + aria-labels + focus styles.
- **Single 227 KB HTML** with CDN Tailwind + lightweight-charts. Acceptable for now, but
  adopt a minimal Vite build when convenient (purged CSS, modules, lintable).
- **Error states**: raw.githubusercontent 429 currently shows the "browsers block local
  files" message in production — misleading; branch on origin.
- Known open items from the kill-list still stand: mixed-signal copy (1c), mobile 404s +
  tap interception (#4), backtest-vs-live inline reconciliation (#5).
- **What's already good** (keep, and market harder): the disclaimer, honesty badges, the
  rationale "why" layer, make-or-break line, muted one-day-analog treatment,
  performance.html as a separate page.

### 2.5 Product gaps vs "one-stop for traders"
Missing, in rough order of user value: **alerts/notifications** (highest-retention
feature; R4/R5 email digest already planned — do it), user-defined **screener filters**
(sector/price/RS/ADX/float ranges — you're pre-ranked-list-only today), **earnings
calendar** as a forward view (data already in `earnings.json`; `GATE_EARNINGS_VETO_DAYS`
exists — surface it), **insider / bulk-block / dark-pool flow layer** (see §4),
**portfolio & trade journal** tied to the site's own signals, **options flow/IV** (US
users expect it; paid data, defer), **public backtester** ("test this rule" — the
`analyze_reliability.py` engine could power a constrained version cheaply),
**shareable permalinks** per pick.

---

## 3. What the reference sites teach us (steal-list)

| Site | What they do best | What we steal |
|---|---|---|
| **Finviz** | 70+ screener filters, instant heatmaps, zero-login utility | Filterable screener over our scan output; sector heatmap (already Tier-B) |
| **Trade-Ideas** | Publishes its AI's live record; 500+ alert types | Homepage live-record strip (R4 — planned); alert engine |
| **screener.in (IN)** | Saved screens + email digests, clean fundamentals | Watchlist email digest (R5 — planned); saved custom screens |
| **Chartink (IN)** | User-authored scan formulas, community scans | Constrained "build your own scan" on our validated primitives |
| **TrendSpider** | Automated trendlines/backtesting as UI | "Backtest this setup" button per card, powered by our engine |
| **OpenInsider** | Free, dense, sortable Form-4 UI | The insider-flow table design; cluster-buy signal |
| **Unusual Whales** | Making flow data (options/dark pool) a consumer product | The flow-layer concept; their guides confirm FINRA free data is context-grade, not timing-grade |
| **QuiverQuant** | Alt-data (Congress, insiders) productized + API from ~$30/mo | Cheap backfill source if we don't want to parse EDGAR ourselves |

Positioning insight: nobody in either market combines (a) a validated breakout engine,
(b) a public forward track record with confidence intervals, and (c) an India+US dual
market view. That triangle is the moat — "the only scanner that grades itself in
public" is the tagline every competitor page above fails to earn.

---

## 4. Free / low-cost flow-data sources (dark pool, insider, smart money)

### US
| Data | Source | Cost | Notes |
|---|---|---|---|
| Insider trades (Form 3/4/5) | SEC EDGAR via **`edgartools`** (github.com/dgunning/edgartools) or EDGAR full-text/daily index | **Free** | Structured parsing of insider buys/sells; nightly job fits our GitHub Actions pattern. Signal to build: **cluster buys** (≥2 insiders, ≥$100k, open-market) — the classic validated one. |
| Insider + Congress trades, API | **Quiver Quantitative API** | ~$30/mo | Fastest path if we skip EDGAR parsing; also Congress/lobbying datasets. |
| Dark pool (ATS) volume | **FINRA ATS weekly data** (finra.org, free download API) | **Free** | Weekly, aggregated per stock per ATS. Good for "% of volume off-exchange rising" context badge; useless for intraday timing — set expectations in UI. |
| Daily short-sale volume | **FINRA Reg SHO daily files** | **Free** | Daily short volume ratio per stock; combinable into a squeeze-context feature. |
| Options flow | Polygon/UW/CBOE | $50–200/mo | Defer — real cost, and off-thesis until the core record is positive. |

### India
| Data | Source | Cost | Notes |
|---|---|---|---|
| Bulk & block deals | NSE archives via **NSEPython** (`nse-large-deal-api`) or **NseKit** | **Free** | Big-player footprints; India's closest analog to dark-pool prints. |
| Insider trading (SEBI PIT disclosures) | NSE corporate-filings API via same libs | **Free** | Promoter buys are a strong, well-known India signal; pairs perfectly with breakout cards. |
| FII/DII daily flows | NSE/NSDL daily | **Free** | **Directly fixes the `fii_flow: null` hole in market mood.** |
| Delivery % | NSE bhavcopy (already ingesting bhavcopy) | **Free** | Already on the Tier-B list; cheap to add. |

**Discipline requirement (per `ship-signal` skill):** every new flow signal enters as a
*display-only* badge, gets backtested against the ledger (does insider-cluster-buy or a
bulk-deal within N days actually lift follow-through?), and only then can touch scoring.
The data alone isn't the moat — validated *interaction* with our breakout signal is
("breakouts + promoter buying: X% vs Y% base") and nobody else can publish that table.

---

## 5. The plan (phased, each phase shippable)

### Phase 0 — Protect the crown jewels (~1 week)
1. Ledger safety: pre-push shrink-guard + daily backup artifacts in `daily-scan.yml`.
2. pytest suite: schema, no-lookahead, ledger-growth, India byte-identical regression.
3. Serving fix: lazy-load US; split list vs detail payloads; move data behind a real
   CDN host; replace `no-store` with short max-age + date cache-buster.
4. Kill-list leftovers: 1c mixed-signal copy, #4 mobile 404s/taps, #5 backtest-vs-live inline.

### Phase 1 — Accuracy program (the alpha problem, 2–3 weeks)
5. ATR-scaled stops/targets as "methodology v2" (one clean cutover, labeled in UI).
6. `HINDSIGHT_MIN_N` → 20 + Wilson/shrunk displayed rates.
7. Regime-bucketed reliability (by `market_mood`) + time-based walk-forward revalidation.
8. Next-bar-open + slippage-haircut grading; publish gross and net.
9. Benchmark-relative (excess-vs-index) as a first-class published metric + random-entry
   null baseline on performance.html. Automate the ALPHA_WATCH row.
10. Calibration curve + Brier score on performance.html once n permits.

### Phase 2 — Flow-data moat (2–3 weeks, parallelizable with P1)
11. India: NSE bulk/block deals + SEBI PIT insider disclosures + FII/DII flows
    (fixes `fii_flow`) + delivery % — all free, all fit the nightly-Action pattern.
12. US: EDGAR Form-4 via edgartools (cluster-buy detector); FINRA daily short volume +
    weekly ATS %-off-exchange context badges.
13. Backtest each vs the ledger; promote only what lifts follow-through (ship-signal).

### Phase 3 — Retention product (2–4 weeks)
14. R4 homepage live-record strip + R5 watchlist email digest (already specced).
15. Alerts: email/push on new high_conviction / strong_breakout fires.
16. Screener filters over scan output (Finviz-style) + saved screens.
17. Earnings calendar view + surfaced earnings-veto gate.
18. Accessibility pass (aria, text badges, focus, contrast) + honest prod error states.

### Phase 4 — Differentiators (later)
19. Per-card "backtest this setup" (constrained analyze_reliability run).
20. Portfolio/journal tied to site signals; per-user watchlist keys.
21. Shareable permalinks + OG-image cards per pick (free viral loop).
22. Sector heatmap, baskets, Ask-BreakoutAI MCP layer (parked spec exists).

### KPIs to run the project by
- **North star: live excess-vs-index (alpha) and beat_rate at resolved_n ≥ 50 per market.**
- Calibration: Brier score / reliability diagram trend.
- Hit rate of high-conviction tier vs its backtest (51.1% claim must survive live).
- Page: first-meaningful-paint on mobile 4G; JSON bytes per visit (target <1 MB).
- Retention: digest subscribers, watchlist adds, return visits.

### Sequencing rule
Nothing from Phase 3–4 ships before Phase 0 is done; Phase 1 is the standing priority
until live alpha at n≥50 reads ≥0. Features attract users once; a public track record
that's *green* keeps them forever.
