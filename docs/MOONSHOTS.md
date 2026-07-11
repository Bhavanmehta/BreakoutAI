# BreakoutAI — Moonshots & Signature UX

_Companion to `SITE_CRITIQUE_AND_PLAN.md`. These are the "level above the industry" ideas —
each tied to our unique asset (the self-grading forward ledger) or the dual IN/US view.
Feasibility notes assume the current stack: static JSON, nightly GitHub Actions, Vercel._

---

## Tier S — Signature moves (nobody in the industry has these)

### S1. The Time Machine (honesty slider)
A date scrubber in the header. Drag it back and **the entire site re-renders exactly as it
looked on that date** — same picks, same conviction scores, same mood dial. No hindsight
editing possible, and users can verify it themselves.
- Tagline: *"Our site has a rewind button. Ask your favorite guru for theirs."*
- Build: archive each day's `breakouts.json` (list-payload only, ~few hundred KB gzipped)
  to a `snapshots/YYYY-MM-DD/` path. **Requires killing the force-push (P0 item #1 —
  synergy).** Frontend: load snapshot instead of live file. ~2–3 days of work.

### S2. Verifiable Call Receipts (git-as-notary)
Every fired signal gets a **permanent receipt permalink**: symbol, entry, stop, target,
conviction, timestamp — plus the **git commit SHA** that contains it. Anyone can check the
SHA on GitHub and prove the call wasn't edited after the fact. Blockchain vibes, zero
blockchain, zero cost.
- Auto-generate an OG share-card image (Satori/resvg in the Action) so every receipt is a
  beautiful X/WhatsApp card. When the call resolves, the receipt gets stamped **HIT ✓ /
  MISS ✗** — shareable both ways, because we share losses too. That's the viral loop.

### S3. Game-Film Replay ("The Tape")
Any graded call can be **replayed bar-by-bar like sports footage**: candles stream in,
entry marker drops, make-or-break line glows, stop/target bands shade, then the verdict
stamps HIT/MISS. 15-second cinematic per call, keyboard ←/→ to step bars.
- Daily ritual: "Yesterday's Tape" — auto-replay of the most instructive graded call.
- Export as MP4/GIF in the nightly Action → free daily social content, forever.
- Build: we already ship per-stock OHLC + levels; this is a lightweight-charts animation
  loop + a headless-chrome capture step. Frontend-only for v1.

### S4. Uncertainty-Native Design Language
The UI **refuses to look confident until the data earns it.** Card visual weight is
driven by statistical evidence:
- Unproven buckets (n < 20): rendered in a sketch/blueprint style — dashed borders,
  ghosted ink, hand-drawn feel.
- As Wilson intervals tighten, cards literally solidify: opacity up, borders harden,
  the conviction number's blur radius shrinks to crisp.
- A "proven" tier at n ≥ 50 gets solid ink + a subtle foil sheen.
No design system on earth does *epistemic honesty as a visual language*. It's on-brand,
it's screenshot-bait, and it makes the honesty layer felt, not just stated.

### S5. Machine vs You (the behavioral mirror)
The **Shadow Portfolio**: the site mechanically paper-trades every high-conviction call
(next-bar open, ATR stops) and publishes its live P&L curve vs NIFTY/SPY. Then the twist:
users journal their own entries *on the same calls*, and the UI overlays **"you vs the
machine"** — did your discretion add alpha or destroy it? Average user delta shown
anonymously ("humans who overrode the stop underperformed by 2.1%").
- This is a retention machine and a behavioral-finance product in one. Nobody mirrors
  the user back at themselves.

### S6. Calibration Duel (prediction-market lite)
Each morning, users tap HIT or MISS on today's fresh fires **before** outcomes exist.
We grade them alongside the model and publish leaderboards + each user's **Brier score**
("you're 61% calibrated; the machine is 74%"). Gamified calibration training — users
literally learn probability discipline from playing against our ledger.
- Build: one tiny KV store (Vercel KV / Upstash free tier) + the existing grading loop.

---

## Tier A — Big swings

### A1. Breakout Weather Map
A literal **weather forecast for the market**: sectors as map regions, pressure systems =
clusters of tightening coils, lightning strikes = fired breakouts, wind = FII/DII flow.
Daily forecast copy: *"High pressure building over PSU banks — 12 coils tightening,
2 near trigger. Storm watch: US semis."* Weather is the most universally understood
uncertainty interface humans have — and no finance product uses it properly.

### A2. Cross-Market Echo Radar (our dual-market unfair advantage)
When a US theme fires (semis, defense, rails), auto-flag Indian sympathy names with
**backtested lag stats** ("US defense breakouts led Indian defense names by 3–7 sessions
in 8 of 11 past episodes") — and the reverse. We are structurally the only retail tool
watching both tapes with one graded engine. This feature alone is a moat.

### A3. The Failure Museum
A curated, permanent gallery of our **worst calls** with honest post-mortems: what the
setup looked like, why it scored high, why it died, what rule changed because of it.
Inverts every industry norm (competitors bury losses). Trust compounds; each exhibit is
also a masterclass. Doubles as the changelog for methodology versions.

### A4. Regime Dial (the site changes with the market)
A physical-feeling hero dial fusing market mood + trend regime. The whole site subtly
re-themes with it — chop regime desaturates colors, mutes conviction language, and
surfaces the regime-bucketed reliability warning ("breakout hit rate in chop: 22% —
tread light"). The product itself becomes more/less confident as conditions warrant.
The UI *is* the risk lecture.

### A5. Market Sonification (ambient mode)
Opt-in audio layer: coil tightness = a rising harmonic hum per watchlist name, a breakout
fire = one clean chime (different timbre per market), stop-out = low thud. Leave the tab
open and **hear** the market coil. Weird, memorable, genuinely useful for
screen-fatigued traders — and an accessibility win.

---

## Tier B — Fast wins that still feel premium

- **B1. Terminal Mode** — `Cmd/Ctrl-K` command palette (`B RELIANCE` opens the card,
  `G perf` jumps to performance), plus a dense Bloomberg-ish theme toggle. Power users
  evangelize keyboard-first tools.
- **B2. Morning Scrollytelling Brief** — a 60-second vertical-swipe story (mood → top 3
  setups → yesterday's graded result → one lesson). Mobile ritual; generated nightly
  from data we already ship.
- **B3. Streak & "days since last methodology lie" counter** — a cheeky public counter:
  days of unbroken ledger, calls graded, zero edits. Turns ops discipline into brand.
- **B4. One-tap "explain like a hedge-fund memo"** — per card, render the existing
  rationale fields as a tight 5-line IC memo (thesis / trigger / risk / sizing cue /
  kill condition). Same data, 10× more authoritative reading experience.
- **B5. Print mode** — a gorgeous one-page PDF "daily sheet" (top setups + levels table)
  auto-generated nightly. Old-school traders love a sheet; it circulates on desks and
  WhatsApp groups with our name on every page.

---

## Sequencing suggestion
1. **S2 receipts + B3 counter** (cheap, immediately differentiating, marketing engine).
2. **S1 time machine** (rides on P0's force-push fix; proof-of-honesty spectacle).
3. **S3 replay + B2 brief** (daily content flywheel).
4. **S4 uncertainty design language** (adopt progressively as components get touched).
5. **S5/S6** once auth-lite (per-user key) exists.
6. **A1/A2** after the flow-data layer (Phase 2) lands — they feed on it.

Rule stays the rule: none of this ships ahead of Phase 0 safety items, and the accuracy
program (Phase 1) remains the standing priority. Spectacle on top of a red ledger is
just lipstick; on top of a green one, it's a category killer.
