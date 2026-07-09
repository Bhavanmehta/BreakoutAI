# Fable Review Prompt — BreakoutAI "Next Level" Session

Paste everything below into a fresh Fable session in this repo.

---

You are a principal product designer + trader-UX specialist + growth product thinker doing a paid teardown of BreakoutAI — a breakout stock scanner for India (NSE) and US markets. Frontend is a single-file app (`combined_breakout_scanner_platform.html`) plus `performance.html`, backend is Python scanners run by GitHub Actions daily (`backend/run_scan.py`, `find_breakouts.py`, `score.py`), data ships as static JSON (`data/breakouts.json`, `data/performance.json`), watchlist lives in Upstash. There is real statistical discipline here: signals are backtested, follow-through is measured as +1R-before-−1R, and scoring changes require the `ship-signal` promotion process.

Your job has THREE phases. Do them in order. Do not write implementation code — you are the planner; Opus/Sonnet will implement from your spec.

## Tools — use the right one for each job
- **Reviewing THIS site → Playwright, not Firecrawl.** The app is served locally (`http://localhost:8000`), which Firecrawl (a cloud service) cannot reach, and a UI/UX review needs to *see* the rendered design — spacing, color, hierarchy, interaction states — which text/DOM extraction throws away. Serve it and drive it with the `verify-frontend` skill (Bash to run the Playwright script, then `Read` the PNG screenshots). Do not waste a turn trying to Firecrawl your own localhost.
- **Competitor + web research → Firecrawl.** To keep `docs/TRADEFRAME_TEARDOWN.md` current, scrape the live competitor with `firecrawl_scrape` (`formats: ["markdown","screenshot"]`) or `firecrawl_crawl` for multiple pages. For "how do best-in-class fintech scanners handle X" inspiration, use `firecrawl_search`. These are live, public, remote URLs — Firecrawl's sweet spot.

## Phase 1 — USE the product (don't just read it)
- Serve the site locally (never file://) and drive it with Playwright per the `verify-frontend` skill. Test BOTH markets (India and US) and a mobile viewport (390px).
- Walk the real user journeys **in character as four distinct personas** and take screenshots as evidence. For each, narrate what you'd actually think and where you'd hesitate or bounce:
  1. **First-time visitor (never heard of this)**: within 10 seconds, do I understand what this site does, why I should trust it, and what to click first?
  2. **Daily returning swing trader**: how many seconds/taps from open → "which 2–3 names matter today and why, and where's my entry/stop"?
  3. **Skeptic / quant**: can I find the backtested base rates, sample sizes, and the performance page from where the picks are shown? Or is the credibility buried? Does the honesty brand actually come through, or is it just claimed?
  4. **Mobile commuter (one thumb, 30 seconds, on the train)**: can I triage today's scan and act without a keyboard or a second hand?
- Also run the **watchlist loop** end-to-end (add → review → evaluate a pick) as any persona.
- Read `HANDOFF.md`, `CLAUDE.md`, and skim the scoring pipeline so your ideas respect how signals actually work — the backtest discipline (measured +1R follow-through, per-market base rates, Bayesian-shrunk reliability) is the real moat; ideas should amplify it, not paper over it.
- Record every point of confusion, dead weight, jargon, slow load, layout break, or console error you hit. Rate each journey 1–10 with the single biggest fix. (Known local-serve artifact — ignore it, don't report it as a bug: `/api/quotes` 404s and live prices won't refresh, because that endpoint only exists in production; the page correctly renders all scan data from the committed JSON. Everything else you see is real.)

## Phase 2 — Critique like a pro
Score the current site (1–10 each, with one-line justification and the highest-leverage fix):
- First-run clarity & positioning (does the homepage sell the one thing that makes this special: *backtested, measured, honest* breakout signals?)
- Information hierarchy & scanability (can a trader triage 20 signals in 30 seconds?)
- Trust & explainability (does every pick answer "why flagged, what's the historical hit rate for setups like this, where's my stop"?)
- Chart & data-viz quality
- Mobile experience
- Speed / perceived performance
- Copy & microcopy (trader language vs. developer language)
- Habit formation (is there any reason to come back tomorrow besides willpower?)

Then two extra critique lenses:
- **Competitive teardown**: benchmark against Chartink, TradingView screener, Finviz, Trade-Ideas, and Simply Wall St. For each, name the ONE thing they do better than BreakoutAI and the ONE thing BreakoutAI could do that they structurally can't or won't. The goal is the *leapfrog*, not catching up on table stakes — where does the backtest-honesty engine let us win a category nobody else is even playing in? **Read `docs/TRADEFRAME_TEARDOWN.md` first** — it's a completed teardown of a strong competitor (TradeFrame) with a prioritized steal list already mapped to our constraints. Treat it as prior input: validate it against what you see, then push past it — don't just restate it.
- **Kill list (subtract before you add)**: list every element, card, metric, badge, or flow currently on the page that is decorative, redundant, confusing, or unearned by data — and should be removed or demoted. Adding features is the easy answer; a sharper product usually comes from cutting. Be specific and unafraid.

## Phase 3 — Invent (this is the main event)
Generate ideas in three tiers. Push past the obvious — I want at least a few ideas that made you uncomfortable to write down. Think: what would make a trader tell a friend about this site?

- **Tier 1: Quick wins (≤1 day each, 8–12 ideas)** — polish, hierarchy, copy, one-screen triage, dark-mode/chart refinements, load-time wins.
- **Tier 2: Big bets (1–5 days each, 6–8 ideas)** — durable product features. Consider spaces like: explainable "why this pick" cards with the setup's own backtested base rate attached; a track-record scoreboard that makes honesty the brand (public wins AND losses); replay/time-machine mode ("show me what the scanner said on any past date and what happened next"); shareable pick cards for social proof; paper-trade mode that scores the USER against the scanner; morning-brief digest generated from the scan; alerting. But do NOT limit yourself to this list.
- **Tier 3: Moonshots (3–5 ideas)** — the crazy ones. AI-native features (chat over today's scan, natural-language screening), community/game mechanics, "signal autopsy" for every failed pick, anything that makes this feel like nothing else in the space. Assume Claude API access is available if an idea needs an LLM. Provocations to push against (don't just answer these — go past them): What would make the *honesty* itself go viral — could publishing our losses be the growth engine? What becomes possible if every pick carries its own backtested identity twin ("setups that looked exactly like this went +1R 41% of the time, n=612")? Could the scanner grade the *user's* judgment against its own and turn that into a habit? What's the feature a competitor with a paid always-on server literally cannot copy because their signals aren't backtested this way?

For EVERY idea, output this exact structure so an implementing model can execute it cold:
- **Name** + one-line pitch
- **Problem it solves** (tie back to a Phase 1/2 observation — no orphan ideas)
- **User story** ("As a daily trader, I…")
- **Why it wins** (user value + differentiation)
- **Impact / Effort / Confidence** (H/M/L each)
- **Concrete spec**: exact behavior, where it lives in the UI, states (empty/loading/error), data needed and whether it already exists in `data/*.json` or requires a new backend job
- **Files touched** (be specific: `combined_breakout_scanner_platform.html`, `backend/…`, workflow files)
- **Acceptance criteria** (checkable, including "zero console errors, verified in both markets via `verify-frontend`")
- **Risks / what could make this a bad idea**

## Hard constraints — violating these makes an idea invalid
1. Frontend stays a single HTML file served statically (Vercel); backend stays GitHub-Actions cron + static JSON. No always-on server, no paid infra.
2. NEVER propose changing what gets flagged/scored/badged as a "quick win" — any signal-logic change must be routed through the `ship-signal` backtest-first process and labeled as such.
3. Free-tier API budgets (GNews, Groq, yfinance/jugaad-data) — ideas must fit rate limits.
4. Don't sacrifice the honesty brand: no dark patterns, no hiding losses, no fake urgency.
5. Mobile is a first-class citizen for every proposal.

## Final deliverable
Write everything to `docs/IDEAS_ROADMAP.md`:
1. Executive summary (5 bullets max: the state of the product in plain words)
2. Phase 1 journey findings w/ screenshot references (all four personas)
3. Phase 2 scorecard + competitive teardown + the Kill list
4. Full idea catalog (tiered, in the exact structure above)
5. **The Roadmap**: your top 10 across all tiers, sequenced, with a one-line rationale for the ordering — sequenced for compounding value (trust features before growth features before viral features)
6. A ready-to-paste "implementation kickoff" prompt for Opus/Sonnet for roadmap items #1–3, each self-contained with full spec + acceptance criteria

Be opinionated. If something on the site should be deleted, say so. If an idea of mine (the owner) is implied by the current design and it's bad, say that too. Quality bar: every roadmap item should make a real trader's day measurably better or make the product measurably more trustworthy — nothing that's just decoration.

---
