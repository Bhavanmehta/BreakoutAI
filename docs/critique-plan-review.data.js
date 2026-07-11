// Data for docs/critique-plan-review.html
// window.__DATA = [ {id, kind:'feature'|'suggestion', tag, title, blurb, explain, current, updated, poc, extra}, ... ]

window.__DATA = [
  {
    id: 'perf-nostore',
    kind: 'suggestion',
    tag: '§2.1 · Performance',
    title: '25 MB of no-store JSON on every visit — the site\'s biggest speed bug',
    blurb: 'combined_breakout_scanner_platform.html eagerly fetches IN+US breakouts.json and both performance.json, all cache:no-store.',
    explain: 'The live scanner page fetches <b>breakouts.json</b> (9.7 MB India + 15 MB US, ≈25 MB total) and both <b>performance.json</b> files eagerly on load, every time, with <code>{cache:"no-store"}</code>, from <code>raw.githubusercontent.com</code> — not a CDN you control, and rate-limited. On mobile 4G this is the single biggest thing standing between a visitor and a first meaningful paint.',
    current: 'Both markets\' full JSON blobs load on every page visit regardless of which market the user is looking at, with no HTTP caching — a refresh re-downloads the same ~25 MB again.',
    updated: 'Lazy-load US data only when the user toggles to it. Split a light list payload (symbol/price/conviction/readiness — a few hundred KB) from a per-stock detail payload fetched on card open. Serve from Vercel static or Cloudflare Pages/R2 with real Cache-Control (short max-age + an as_of_date cache-buster instead of no-store).'
  },
  {
    id: 'hindsight-min-n',
    kind: 'suggestion',
    tag: '§2.2 · Statistics',
    title: 'HINDSIGHT_MIN_N: 5 → 20, with Wilson/shrunk confidence',
    blurb: 'n=5 gives roughly ±40pp confidence intervals but still flips the badge to a colored "proven" rate.',
    explain: 'At <code>HINDSIGHT_MIN_N = 5</code> resolved calls, the true hit rate could plausibly be anywhere across a ~40-percentage-point range, yet the UI displays a solid colored badge as if the number were reliable. Raising the threshold and showing an actual interval (or a rate shrunk toward the prior via <code>score.py::reliability_estimate</code>) turns a false-confidence signal into an honest one.',
    current: 'Any tier with 5+ resolved calls shows a flat colored hit-rate badge (e.g. "51.1% proven") with no indication of how wide the plausible range actually is.',
    updated: 'Raise the threshold to ≥20 resolved calls before a rate is treated as "proven." Below that, or always, display a Wilson confidence interval or a Bayesian-shrunk rate (prior = SCORE_BASE_RATE) instead of a bare percentage.'
  },
  {
    id: 'alpha-vs-index',
    kind: 'feature',
    tag: '§2.2 · Statistics',
    title: 'Benchmark-relative grading — alpha vs. index becomes a first-class number',
    blurb: 'Absolute hit rate is the headline; excess-vs-index is buried in a manually maintained log (ALPHA_WATCH.md).',
    explain: 'A 45% hit rate can still lose to buy-and-hold in a bull tape. <code>ALPHA_WATCH.md</code> already computes mean alpha and beat_rate vs. the index, but it\'s a manually maintained markdown file, not a number on the site. This POC promotes it to a headline stat next to hit rate everywhere hit rate appears.',
    poc: `
      <div class="m-card">
        <div class="m-row">
          <div><span class="tkr">IN</span><span class="exch">NSE</span></div>
          <div class="mono">n = 58 resolved</div>
        </div>
        <div class="barrow"><span class="lab">Hit rate</span><div class="bar"><i style="width:42%;background:var(--blu)"></i></div><span class="conf">42%</span></div>
        <div class="barrow"><span class="lab">Alpha vs Nifty</span><div class="bar"><i style="width:38%;background:var(--red)"></i></div><span class="conf">−1.1%</span></div>
        <div class="badge" style="margin-top:8px"><span class="d" style="background:var(--red)"></span>Trails index</div>
      </div>
      <div class="m-card" style="margin-top:10px">
        <div class="m-row">
          <div><span class="tkr">US</span><span class="exch">NASD</span></div>
          <div class="mono">n = 71 resolved</div>
        </div>
        <div class="barrow"><span class="lab">Hit rate</span><div class="bar"><i style="width:47%;background:var(--blu)"></i></div><span class="conf">47%</span></div>
        <div class="barrow"><span class="lab">Alpha vs S&P</span><div class="bar"><i style="width:56%;background:var(--grn)"></i></div><span class="conf">+0.6%</span></div>
        <div class="badge" style="margin-top:8px"><span class="d" style="background:var(--grn)"></span>Beats index</div>
      </div>`
  },
  {
    id: 'calibration-curve',
    kind: 'feature',
    tag: '§2.2 · Statistics',
    title: 'Calibration curve — reliability diagram + Brier score',
    blurb: 'Conviction 0–100 is published; whether a 70-conviction call actually wins ~70% of the time is not shown anywhere.',
    explain: 'A reliability diagram buckets predicted conviction against realized win rate. If the dots track the diagonal, the model is calibrated — a legitimate marketing claim ("we show our calibration — ask your newsletter to"). Brier score gives a single trend number to publish quarter over quarter. No competitor does this today.',
    poc: `
      <table class="tbl">
        <tr><th>Predicted bucket</th><th>Realized win %</th><th>n</th></tr>
        <tr><td>0–20</td><td>17%</td><td>34</td></tr>
        <tr><td>20–40</td><td>29%</td><td>52</td></tr>
        <tr><td>40–60</td><td>48%</td><td>61</td></tr>
        <tr><td>60–80</td><td>66%</td><td>39</td></tr>
        <tr><td>80–100</td><td>81%</td><td>18</td></tr>
      </table>
      <div class="crux"><b>Brier score 0.19</b> — trending down each quarter would mean the model is learning to say "70" and mean it.</div>`
  },
  {
    id: 'slippage-fill',
    kind: 'suggestion',
    tag: '§2.2 · Statistics',
    title: 'No slippage/fill modeling — grade from signal close, not next-bar open',
    blurb: 'Backtest and live grading both use the signal bar\'s own close as the fill price — a price nobody could have traded at.',
    explain: 'Grading a call from the same bar\'s close means the "entry" price is set after the outcome window has already started; it isn\'t a price achievable in real trading. Moving to next-bar open (minus a spread proxy) and reporting both gross and net numbers matters most for NSE small-caps and thin US names, where the gap between close and next open can be large.',
    current: 'Hit/miss outcomes and R-multiples are computed using the exact close print that generated the signal.',
    updated: 'Grade from the next bar\'s open price, subtract a per-market spread/slippage proxy, and publish both the gross (no-cost) and net (after-slippage) numbers side by side.'
  },
  {
    id: 'zero-tests',
    kind: 'suggestion',
    tag: '§2.3 · Trust & Ops',
    title: 'Zero tests around money-adjacent statistics',
    blurb: 'No pytest suite protects the ledger, the scoring math, or the no-lookahead guarantee.',
    explain: 'The live ledger and its honesty gating are the project\'s most valuable asset, and nothing currently stops a bad commit from silently corrupting it. A minimal pytest suite — schema validation, a no-lookahead assertion (every resolve date is after its signal date), a ledger-only-grows check, and the India byte-identical regression already run ad hoc — is cheap insurance for the thing that matters most.',
    current: 'Correctness of the scoring/grading pipeline is verified manually and ad hoc (e.g. one-off regression checks run during a session), with no CI gate.',
    updated: 'Add a pytest suite that runs in CI on every PR: (a) output-JSON schema validation, (b) no-lookahead assertion, (c) ledger-only-grows check, (d) the India byte-identical regression as an automated test.'
  },
  {
    id: 'force-push-branch',
    kind: 'suggestion',
    tag: '§2.3 · Trust & Ops',
    title: 'Force-pushed data branch = no rollback',
    blurb: 'The data branch can be force-pushed, silently truncating the ledger with no way back.',
    explain: 'If a bad run overwrites the data branch with a shorter or corrupted ledger, there is currently no guard and no backup to restore from — the crown jewel has no seatbelt.',
    current: 'daily-scan.yml pushes the updated ledger directly; a bug or bad merge can silently shrink or corrupt predictions_log.jsonl with no alarm and no way to recover the prior state.',
    updated: 'Add a pre-push gate that refuses to push if the new ledger row count is less than the previous one, and keep N daily tarball backups as workflow artifacts so any bad push can be rolled back in minutes.'
  },
  {
    id: 'open-proxy',
    kind: 'suggestion',
    tag: '§2.3 · Trust & Ops',
    title: 'Open Yahoo proxy + one shared WATCHLIST_SECRET',
    blurb: 'api/quotes.py has no rate limiting; every user\'s watchlist shares one secret and one namespace.',
    explain: 'An open proxy with no rate limiting invites abuse (and possible upstream bans from Yahoo). A single shared WATCHLIST_SECRET means there\'s effectively one global watchlist namespace instead of per-user isolation — anyone who has the secret can read or tamper with everyone\'s list.',
    current: 'Requests to the quotes proxy are unauthenticated and unlimited; the watchlist feature is gated by one static secret shared across all users.',
    updated: 'Add basic rate limiting (per-IP or per-key) to the quotes proxy, and move to per-user watchlist keys instead of one shared secret.'
  },
  {
    id: 'regime-bucketed',
    kind: 'feature',
    tag: '§2.2 · Statistics',
    title: 'Regime-bucketed reliability — same tier, different world',
    blurb: 'Score weights are fixed constants; hit rate isn\'t broken out by market_mood regime.',
    explain: 'Breakout strategies are notorious for working almost entirely in one regime (usually risk-on / bull tape) and silently failing in others. Bucketing the same hit-rate/expectancy numbers by <code>market_mood</code> — cheap, since the field already exists — turns a hidden regime dependency into a visible, honestly-labeled caveat.',
    poc: `
      <table class="tbl">
        <tr><th>Regime</th><th>n</th><th>Hit rate</th><th>Expectancy (R)</th></tr>
        <tr><td>Risk-on</td><td>96</td><td>51%</td><td>+0.18</td></tr>
        <tr><td>Neutral</td><td>64</td><td>39%</td><td>−0.04</td></tr>
        <tr><td>Risk-off</td><td>41</td><td>24%</td><td>−0.31</td></tr>
      </table>
      <div class="crux"><b>Same score, different world.</b> A "high conviction" tag in a risk-off tape is currently indistinguishable from one in risk-on.</div>`
  },
  {
    id: 'fii-flow-null',
    kind: 'suggestion',
    tag: '§4 · Flow-data',
    title: 'fii_flow: null silently drops one leg of the Greed/Fear composite',
    blurb: 'One input to the India market-mood composite is a permanent null instead of real data.',
    explain: 'The Greed/Fear composite for India is computed with FII (foreign institutional investor) daily flow as one of its inputs, but that field is currently always <code>null</code> — so the composite is silently running on partial data with no indication to the user. NSE/NSDL publish daily FII/DII flows for free, which directly fixes this.',
    current: 'The market_mood composite for India includes an fii_flow term that is always null; the UI does not indicate the composite is missing an input.',
    updated: 'Pull NSE/NSDL daily FII/DII flow data (free) into the nightly ingest job and populate fii_flow for real; until then, surface a partial-data indicator on the composite badge instead of silently dropping the term.'
  },
  {
    id: 'accessibility',
    kind: 'suggestion',
    tag: '§2.4 · UX',
    title: 'Accessibility ≈ absent — readiness conveyed by color alone',
    blurb: '~4 aria-* attributes, 0 alt text, 0 tabindex across a 227 KB page; readiness dots are color-only.',
    explain: 'A colorblind user, or anyone on a low-contrast display, currently cannot tell a "ready" call from a "not ready" one — the only signal is dot color. Screen readers get almost nothing: no alt text on chart images, no aria-labels on interactive controls, and no visible focus outline for keyboard users.',
    current: 'Readiness state is shown as a colored dot only; interactive elements largely lack aria-labels, alt text, and focus styles.',
    updated: 'Pair every color dot with a short text badge (e.g. "Ready" / "Wait"), add aria-labels to controls and alt text to chart images, and add a visible focus ring for keyboard navigation.'
  },
  {
    id: 'insider-cluster-buy',
    kind: 'feature',
    tag: '§4 · Flow-data',
    title: 'Insider cluster-buy badge — EDGAR Form-4 / SEBI PIT disclosures',
    blurb: 'Free structured filings data (edgartools / NSE PIT filings) can flag ≥2 insiders buying in the open market within days of each other.',
    explain: 'Both markets have a free, well-known signal hiding in public filings: US Form 3/4/5 via SEC EDGAR (parsed with edgartools), India promoter buys via NSE\'s SEBI PIT disclosure API. The classic validated pattern is a cluster buy — multiple insiders buying in the open market within a short window. Per the ship-signal discipline, this starts as a display-only badge, gets backtested against the ledger, and only touches scoring once it\'s proven to lift follow-through.',
    poc: `
      <div class="m-card">
        <div class="m-row">
          <div><span class="tkr">RELI</span><span class="exch">NSE</span></div>
          <div class="px up">₹2,845 <span class="mono">+1.2%</span></div>
        </div>
        <div class="badge" style="margin-top:8px"><span class="d" style="background:var(--vio)"></span>Insider cluster buy — 3 filers, ₹3.4Cr, 6d</div>
        <div class="pill2" style="margin-top:8px;display:inline-block">display-only — not yet in scoring</div>
      </div>`
  },
  {
    id: 'live-record-strip',
    kind: 'feature',
    tag: '§2.5 · Product',
    title: 'Homepage live-record strip + watchlist email digest',
    blurb: 'R4/R5 are already specced: publish the live record above the fold, and email subscribers their watchlist daily.',
    explain: 'Trade-Ideas and screener.in both retain users with exactly this pattern: a public, updating live-record strip builds trust on arrival, and a daily/weekly digest email is the highest-leverage retention feature available (screener.in\'s saved-screen digest is the direct analog). Both are already scoped in the roadmap (R4/R5) — this POC shows the homepage strip.',
    poc: `
      <div class="m-card">
        <div class="mono" style="margin-bottom:8px">Live record — last 5 resolved</div>
        <div class="m-row" style="flex-wrap:wrap;gap:6px;justify-content:flex-start">
          <span class="badge"><span class="d" style="background:var(--grn)"></span>TCS +0.8R</span>
          <span class="badge"><span class="d" style="background:var(--red)"></span>AAPL −1.0R</span>
          <span class="badge"><span class="d" style="background:var(--grn)"></span>INFY +1.4R</span>
          <span class="badge"><span class="d" style="background:var(--grn)"></span>MSFT +0.5R</span>
          <span class="badge"><span class="d" style="background:var(--red)"></span>HDFC −1.0R</span>
        </div>
        <div class="pill2" style="margin-top:10px;display:inline-block">📩 Subscribe — daily digest of your watchlist</div>
      </div>`
  },
  {
    id: 'phased-plan',
    kind: 'feature',
    tag: '§5 · Plan',
    title: 'The phased plan — Phase 0 through 4, in sequence',
    blurb: 'Nothing from Phase 3–4 ships before Phase 0 is done; Phase 1 stays the standing priority until live alpha ≥ 0 at n≥50.',
    explain: 'The plan is deliberately sequenced: protect the ledger and fix serving first (Phase 0), then fix the actual accuracy program (Phase 1 — standing priority until live alpha at n≥50 reads ≥0), then the flow-data moat (Phase 2, parallelizable with P1), then retention features (Phase 3), then differentiators (Phase 4). Features attract users once; a public track record that stays green keeps them forever.',
    poc: `
      <div class="wf-row"><div class="wf-lab" style="width:74px">Phase 0</div><div class="wf-block" style="background:#2a1414;color:#fca5a5;flex:1">Protect the ledger — tests, backups, serving fix</div></div>
      <div class="wf-row"><div class="wf-lab" style="width:74px">Phase 1</div><div class="wf-block" style="background:#0f2030;color:#7dd3fc;flex:1">Accuracy program — standing priority until alpha ≥ 0 @ n≥50</div></div>
      <div class="wf-row"><div class="wf-lab" style="width:74px">Phase 2</div><div class="wf-block" style="background:#241a30;color:#c4b5fd;flex:1">Flow-data moat — insider, bulk/block, FII/DII</div></div>
      <div class="wf-row"><div class="wf-lab" style="width:74px">Phase 3</div><div class="wf-block" style="background:#12351f;color:#5fe08e;flex:1">Retention — alerts, digest, screener filters</div></div>
      <div class="wf-row"><div class="wf-lab" style="width:74px">Phase 4</div><div class="wf-block" style="background:#241d0a;color:#fbbf24;flex:1">Differentiators — backtester, journal, permalinks</div></div>`
  },
  {
    id: 'steal-list',
    kind: 'feature',
    tag: '§3 · Competitive',
    title: 'The steal-list — what best-in-class does that we don\'t yet',
    blurb: 'No competitor combines a validated breakout engine, a public forward record with confidence intervals, and a dual India+US view — that\'s the moat.',
    explain: 'Each reference site does one thing extremely well: Finviz\'s filter density, Trade-Ideas\' published live record, screener.in\'s saved-screen digest, OpenInsider\'s dense sortable Form-4 table. None of them publish a graded, confidence-interval-backed forward track record across two markets — that combination is the tagline every competitor page above fails to earn.',
    poc: `
      <table class="tbl">
        <tr><th>Site</th><th>Best at</th><th>What we steal</th></tr>
        <tr><td>Finviz</td><td>70+ filters, instant heatmaps</td><td>Filterable screener over scan output</td></tr>
        <tr><td>Trade-Ideas</td><td>Publishes its AI's live record</td><td>Homepage live-record strip</td></tr>
        <tr><td>screener.in</td><td>Saved screens + email digests</td><td>Watchlist email digest</td></tr>
        <tr><td>OpenInsider</td><td>Free, dense, sortable Form-4 UI</td><td>Insider-flow table design</td></tr>
      </table>`
  }
];
