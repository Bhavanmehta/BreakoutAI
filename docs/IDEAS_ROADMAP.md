# BreakoutAI — Ideas Roadmap
_Fable review, 2026-07-06. Evidence: live-site Playwright walkthrough (desktop 1440px + mobile 390px, IN & US, 14 screenshots in scratchpad/shots), performance.html live scrape, Firecrawl teardowns of Chartink, Finviz, Trade-Ideas, StockEdge, screener.in, Simply Wall St (TradingView skipped — page too heavy to scrape usefully)._

---

## 1. Skeptic scorecard — what the live site earns today

| Feature | Grade | Evidence |
|---|---|---|
| **Live performance page (honesty)** | **A** | 168 calls since Jul 3, nothing backfilled, breakeven line stated, per-signal live-vs-backtest table. No competitor scraped does this. This is the moat. |
| Backtest receipts on cards | B+ | "Reliable — about 76% of its 36 past breakouts followed through" (CUPID card). Unique — but currently contradicted by the live record (43% graded, −0.14R expectancy). Needs reconciliation copy, not removal. |
| THE READ / AI narrative | B | Clear and specific — but the CUPID card says **"Breaking out now"** in the headline and **"On watch — conditions are lining up"** in the subtitle. A skeptic reads that as the site not knowing what it's saying. |
| Mood gauge, IN/US toggle, radar UX | B | Worked in every shot; toggle is instant; visual identity is coherent. |
| Mobile experience | B− | Cards render well after tap, but two 404'd resources in console, and taps repeatedly hit "element intercepted / not visible" retries (overlapping sticky elements incl. the Ask AI FAB). |
| **Conviction score** | **C** | Site displays a giant **"99"** on CUPID while its own hindsight table shows the 80–100 bucket at **0 wins / 2 losses** and no bucket except 70–79 (50%) has a hit rate at all. The score is not yet predictive and the UI doesn't say so. |
| Performance page UX | C | Full page renders at **26,321 px tall** — every one of 168 calls as an expanded row. Unreadable as a single page. |
| Watchlist | C− | Multiple Playwright timeouts, click interception, 500ms+ retries on both desktop panel and mobile. Flakiest surface on the site. |

**Live numbers a skeptic will quote back at us:** graded win rate 43% (6W/8L of 14), expectancy −0.14R/call, alpha vs NIFTY −1.0%, next-day move −0.31%. Sample is tiny (154 still open) — say so loudly before someone else does.

## 2. Kill / fix list (do before any new feature)

1. ✅ **DONE (2026-07-06).** **Kill the naked conviction number.** Until a hindsight bucket has ≥5 resolved calls, render conviction with an inline badge: "unproven live — 14 calls graded". The 99-vs-0/2 gap is the single biggest credibility hole on the site. — *Live-record badge added to desktop + mobile scanner card templates; joins against the same live-record JSON performance.html reads, `HINDSIGHT_MIN_N=5`. See R1.*
2. ✅ **DONE (2026-07-06).** **Kill the 26k-px performance page.** Default to graded calls collapsed rows + "load more"; keep the summary header exactly as is (it's excellent). — *Calls now render as collapsed rows, default filter "graded", `PAGE_SIZE=25` client-side load-more. See R2.*
3. **Kill the mixed-signal copy.** One card, one verdict: "Breaking out now" and "On watch" must never co-render. Fix in the copy-generation step of the Action.
4. **Fix the two mobile 404 assets** and the tap-interception (z-index/pointer-events on the FAB and sticky header).
5. **Reconcile backtest vs live inline.** Anywhere a historical reliability % appears, append "live so far: X/Y" from the same JSON the performance page reads.

## 3. Competitor steals (detail in TRADEFRAME_TEARDOWN.md §5)

- **screener.in** → watchlist **email digests** (GitHub Action can send daily; zero backend).
- **Finviz** → one-screen **sector heatmap** from breadth data we already compute.
- **Trade-Ideas** → they market "transparent AI record"; we actually have one — **put the live expectancy strip on the homepage**, wins and losses both.
- **Simply Wall St** → **Rewards / Risks bullet pairs** per stock from fundamentals we already fetch.
- **StockEdge** → India edge: **delivery % + FII/DII flow** as signal inputs.
- **Chartink** → **shareable permalinks** encoding filter state in the URL.

## 4. Roadmap — top 5, with paste-ready kickoff prompts

### R1 — Conviction honesty layer (kill-list #1 + #5) — ✅ DONE 2026-07-06
```
Read docs/IDEAS_ROADMAP.md §2 items 1 and 5. In the site generator, wherever a
conviction score or historical reliability % is rendered, join against the same
live-record JSON performance.html uses and render an inline live-record badge
("unproven live — N graded" until a bucket has ≥5 resolved, else the bucket hit
rate). Update both desktop card and mobile card templates. Verify with a
Playwright screenshot of a conviction-99 card before/after.
```

### R2 — Performance page pagination — ✅ DONE 2026-07-06
```
performance.html currently renders all 168+ calls in one 26,000px page. Keep the
summary stats + hindsight + by-signal tables untouched; below them, render calls
as collapsed rows, default filter "graded", batches of 25 with a Load-more
button, pure client-side JS (static site, no backend). Screenshot desktop +
mobile to confirm full-page height < 4000px at default state.
```

### R3 — Watchlist reliability pass
```
Read docs/IDEAS_ROADMAP.md §1 (watchlist row) and §2 item 4. Reproduce with
Playwright at 390px and 1440px: taps on watchlist rows are intercepted by
overlapping elements and two resources 404. Fix z-index/pointer-events on the
Ask AI FAB and sticky header, fix the 404s, then re-run the tap script until
zero intercepted-click retries.
```

### R4 — Homepage live-record strip (Trade-Ideas steal, honesty as brand)
```
Add a slim strip under the homepage header sourced from the performance JSON:
"Live record: N calls · X% graded win rate · expectancy R · alpha vs index",
green or red as the data says, linking to performance.html. Never cherry-pick;
render losses in red. Static JSON read at build time by the Action.
```

### R5 — Watchlist email digest (screener.in steal)
```
Extend the daily GitHub Action: for stocks in a user-exported watchlist file,
send a daily email digest (signal changes, conviction moves, graded outcomes)
via a mail API secret. Start with a single hardcoded watchlist as dogfood.
Include the live-record line from R4 in the footer of every email.
```

## 5. Backlog (tiers)

- **Tier B:** sector heatmap (Finviz) · Rewards/Risks bullets (SWS) · delivery %/FII-DII inputs (StockEdge) · shareable filter permalinks (Chartink) · feed live outcomes back into conviction calibration (perf page already says "not (yet) fed back into it" — make that sentence expire).
- **Tier C:** shared/community watchlists · Ask-AI deep links per card · education modules (StockEdge Learn) · US options/futures breadth.
