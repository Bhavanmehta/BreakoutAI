// Data for docs/moonshots-review.html
// window.__DATA = [ {id, kind:'feature'|'suggestion', tag, title, blurb, explain, current, updated, poc, extra}, ... ]

window.__DATA = [
  {
    id: 's1-time-machine',
    kind: 'feature',
    tag: 'Tier S · S1',
    title: 'The Time Machine (honesty slider)',
    blurb: 'Drag a date scrubber and the entire site re-renders exactly as it looked that day — same picks, same conviction, no hindsight editing possible.',
    explain: 'Archive each day\'s list-payload (few hundred KB gzipped) to a <code>snapshots/YYYY-MM-DD/</code> path; the frontend loads the snapshot instead of the live file. Anyone can rewind and independently verify a call wasn\'t touched after the fact. <b>Requires killing the force-push habit first</b> (Phase 0 synergy). Tagline: "Our site has a rewind button. Ask your favorite guru for theirs." ~2–3 days of frontend work once daily snapshots exist.',
    poc: `
      <div class="m-card">
        <div class="m-row">
          <span class="mono">◀ rewind</span>
          <span class="badge"><span class="d" style="background:var(--amb)"></span>Viewing archive: 2025-11-03</span>
          <span class="mono">rewind ▶</span>
        </div>
        <div class="bar" style="margin-top:10px"><i style="width:34%;background:var(--cyn)"></i></div>
        <div class="m-card" style="margin-top:10px">
          <div class="m-row"><div><span class="tkr">TCS</span><span class="exch">NSE</span></div><div class="conf">conviction 71 (as archived)</div></div>
        </div>
      </div>`
  },
  {
    id: 's2-call-receipts',
    kind: 'feature',
    tag: 'Tier S · S2',
    title: 'Verifiable Call Receipts (git-as-notary)',
    blurb: 'Every fired signal gets a permanent receipt permalink with the git commit SHA that contains it — provably un-editable, blockchain vibes with zero blockchain.',
    explain: 'Anyone can check the SHA on GitHub and prove the call wasn\'t edited after the fact. Auto-generate a shareable OG card (Satori/resvg in the nightly Action) so every receipt is a beautiful X/WhatsApp card. When the call resolves, it gets stamped HIT ✓ / MISS ✗ — shareable both ways, because sharing losses too is the actual differentiator and the viral loop.',
    poc: `
      <div class="m-card">
        <div class="m-row">
          <div><span class="tkr">AAPL</span><span class="exch">NASD</span></div>
          <div class="badge"><span class="d" style="background:var(--grn)"></span>HIT ✓</div>
        </div>
        <div class="mono" style="margin-top:8px">entry $187.40 · stop $181.20 · target $199.80</div>
        <div class="mono" style="margin-top:4px">commit 8f1a02c · 2025-10-14 09:31 ET</div>
        <div class="pill2" style="margin-top:8px;display:inline-block">🔗 permanent receipt — verify on GitHub</div>
      </div>`
  },
  {
    id: 's3-game-film-replay',
    kind: 'feature',
    tag: 'Tier S · S3',
    title: 'Game-Film Replay ("The Tape")',
    blurb: 'Any graded call replays bar-by-bar like sports footage: candles stream in, entry marker drops, stop/target bands shade, then the verdict stamps.',
    explain: 'We already ship per-stock OHLC + levels, so this is a lightweight-charts animation loop plus a headless-chrome capture step for MP4/GIF export — frontend-only for v1. Daily ritual: "Yesterday\'s Tape," an auto-replay of the most instructive graded call, exported nightly for free daily social content, forever.',
    poc: `
      <div class="m-card">
        <div class="m-row"><div class="mono">TCS · bar 6 of 10</div><div class="badge"><span class="d" style="background:var(--grn)"></span>HIT ✓ stamped</div></div>
        <div class="barrow"><span class="lab">Playback</span><div class="bar"><i style="width:60%;background:var(--cyn)"></i></div><span class="conf">60%</span></div>
        <div class="barrow"><span class="lab">Target band</span><div class="bar"><i style="width:100%;background:var(--grn)"></i></div><span class="conf">+2.3 ATR</span></div>
        <div class="barrow"><span class="lab">Stop band</span><div class="bar"><i style="width:45%;background:var(--red)"></i></div><span class="conf">−1.0 ATR</span></div>
        <div class="pill2" style="margin-top:6px;display:inline-block">⏮ ⏯ ⏭ — step with ←/→, 15s cinematic replay</div>
      </div>`
  },
  {
    id: 's4-uncertainty-design',
    kind: 'feature',
    tag: 'Tier S · S4',
    title: 'Uncertainty-Native Design Language',
    blurb: 'The UI refuses to look confident until the data earns it — card visual weight literally scales with statistical evidence.',
    explain: 'Unproven buckets (n&lt;20) render sketch/blueprint style: dashed borders, ghosted ink, hand-drawn feel. As Wilson intervals tighten, cards literally solidify — opacity up, borders harden, the conviction number\'s blur radius shrinks to crisp. A "proven" tier at n≥50 gets solid ink plus a subtle foil sheen. No design system on earth does epistemic honesty as a visual language; it\'s on-brand and screenshot-bait.',
    poc: `
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <div class="m-card" style="border-style:dashed;opacity:.55;flex:1;min-width:140px">
          <div class="tkr" style="opacity:.7">SMCP</div>
          <div class="conf">n = 6 · sketch mode</div>
        </div>
        <div class="m-card" style="flex:1;min-width:140px">
          <div class="tkr">RELI</div>
          <div class="conf">n = 28 · solidifying</div>
        </div>
        <div class="m-card" style="border-color:#fbbf24;box-shadow:0 0 0 1px #fbbf2433;flex:1;min-width:140px">
          <div class="tkr">TCS</div>
          <div class="conf">n = 62 · proven, foil sheen</div>
        </div>
      </div>`
  },
  {
    id: 's5-machine-vs-you',
    kind: 'feature',
    tag: 'Tier S · S5',
    title: 'Machine vs You (the behavioral mirror)',
    blurb: 'The Shadow Portfolio mechanically paper-trades every high-conviction call; users journal their own entries on the same calls and see "you vs the machine."',
    explain: 'The site publishes its live P&L curve vs NIFTY/SPY from next-bar-open, ATR-stop paper trades. Then the twist: overlay the user\'s own discretionary entries on the same calls — did discretion add alpha or destroy it? Average user delta shown anonymously ("humans who overrode the stop underperformed by 2.1%"). A retention machine and a behavioral-finance product in one; nobody mirrors the user back at themselves.',
    poc: `
      <div class="m-card">
        <div class="barrow"><span class="lab">Machine P&amp;L</span><div class="bar"><i style="width:68%;background:var(--grn)"></i></div><span class="conf">+3.2R</span></div>
        <div class="barrow"><span class="lab">You (journal)</span><div class="bar"><i style="width:41%;background:var(--amb)"></i></div><span class="conf">+1.1R</span></div>
        <div class="crux"><b>Delta: −2.1R</b> — you overrode the stop twice this month; both times it cost you.</div>
      </div>`
  },
  {
    id: 's6-calibration-duel',
    kind: 'feature',
    tag: 'Tier S · S6',
    title: 'Calibration Duel (prediction-market lite)',
    blurb: 'Users tap HIT or MISS on today\'s fresh fires before outcomes exist; we grade them alongside the model and publish Brier-score leaderboards.',
    explain: 'Gamified calibration training — users literally learn probability discipline from playing against our ledger ("you\'re 61% calibrated; the machine is 74%"). Build is small: one tiny KV store (Vercel KV / Upstash free tier) plus the existing grading loop.',
    poc: `
      <table class="tbl">
        <tr><th>Player</th><th>Calls called</th><th>Brier score</th></tr>
        <tr><td>The Machine</td><td>412</td><td>0.19</td></tr>
        <tr><td>@traderpriya</td><td>88</td><td>0.24</td></tr>
        <tr><td>You</td><td>34</td><td>0.31</td></tr>
      </table>
      <div class="pill2" style="margin-top:8px;display:inline-block">Tap HIT/MISS before 9:30am — leaderboard resets weekly</div>`
  },
  {
    id: 'a1-weather-map',
    kind: 'feature',
    tag: 'Tier A · A1',
    title: 'Breakout Weather Map',
    blurb: 'A literal weather forecast for the market: sectors as map regions, pressure systems = tightening coils, lightning = fired breakouts, wind = FII/DII flow.',
    explain: 'Daily forecast copy: "High pressure building over PSU banks — 12 coils tightening, 2 near trigger. Storm watch: US semis." Weather is the most universally understood uncertainty interface humans have, and no finance product uses it properly.',
    poc: `
      <div class="m-card">
        <div class="mono" style="margin-bottom:6px">Today's forecast</div>
        <div class="m-row" style="flex-wrap:wrap;gap:6px;justify-content:flex-start">
          <span class="badge"><span class="d" style="background:var(--amb)"></span>⛈ Storm watch: US semis</span>
          <span class="badge"><span class="d" style="background:var(--cyn)"></span>🌤 High pressure: PSU banks</span>
          <span class="badge"><span class="d" style="background:var(--vio)"></span>💨 FII wind: inflow</span>
        </div>
        <div class="prose" style="margin-top:8px;font-size:12px">"High pressure building over PSU banks — 12 coils tightening, 2 near trigger."</div>
      </div>`
  },
  {
    id: 'a2-echo-radar',
    kind: 'feature',
    tag: 'Tier A · A2',
    title: 'Cross-Market Echo Radar',
    blurb: 'When a US theme fires (semis, defense, rails), auto-flag Indian sympathy names with backtested lag stats — and the reverse.',
    explain: 'We are structurally the only retail tool watching both tapes with one graded engine — this is our dual-market unfair advantage, and it alone is a moat nobody single-market can copy.',
    poc: `
      <div class="m-card">
        <div class="m-row"><div><span class="tkr">LMT</span><span class="exch">NYSE</span> <span class="mono">fired →</span> <span class="tkr">HAL</span><span class="exch">NSE</span></div></div>
        <div class="crux"><b>Echo lag: 3–7 sessions</b> — US defense breakouts led Indian defense names in 8 of 11 past episodes.</div>
      </div>`
  },
  {
    id: 'a3-failure-museum',
    kind: 'feature',
    tag: 'Tier A · A3',
    title: 'The Failure Museum',
    blurb: 'A curated, permanent gallery of our worst calls with honest post-mortems — inverting the industry norm of burying losses.',
    explain: 'Each exhibit explains what the setup looked like, why it scored high, why it died, and what rule changed because of it. Trust compounds; each exhibit doubles as a masterclass and as the changelog for methodology versions.',
    poc: `
      <div class="m-card">
        <div class="m-row"><div><span class="tkr">ZOMATO</span><span class="exch">NSE</span></div><div class="badge"><span class="d" style="background:var(--red)"></span>MISS ✗</div></div>
        <div class="prose" style="margin-top:8px;font-size:12px">Scored high on volume thrust; died into an unresolved earnings gate. <b>Rule change:</b> the earnings veto became binding, not advisory.</div>
      </div>`
  },
  {
    id: 'a4-regime-dial',
    kind: 'feature',
    tag: 'Tier A · A4',
    title: 'Regime Dial (the site changes with the market)',
    blurb: 'A physical-feeling hero dial fusing market mood + trend regime; the whole site subtly re-themes with it.',
    explain: 'Chop regime desaturates colors, mutes conviction language, and surfaces the regime-bucketed reliability warning ("breakout hit rate in chop: 22% — tread light"). The product itself becomes more or less confident as conditions warrant — the UI is the risk lecture.',
    poc: `
      <div class="m-card">
        <div class="barrow"><span class="lab">Regime</span><div class="bar"><i style="width:30%;background:var(--faint)"></i></div><span class="conf">Chop</span></div>
        <div class="crux"><b>Breakout hit rate in chop: 22%</b> — tread light. Card colors desaturate automatically while this regime holds.</div>
      </div>`
  },
  {
    id: 'a5-sonification',
    kind: 'feature',
    tag: 'Tier A · A5',
    title: 'Market Sonification (ambient mode)',
    blurb: 'Opt-in ambient audio: coil tightness hums, a breakout fires a chime, a stop-out thuds — leave the tab open and hear the market coil.',
    explain: 'Weird, memorable, genuinely useful for screen-fatigued traders — and an accessibility win for anyone who can\'t stare at charts all day. Different timbre per market keeps IN and US distinguishable by ear alone.',
    poc: `
      <div class="m-card">
        <div class="m-row"><div class="mono">🔊 Ambient mode: ON</div><div class="badge"><span class="d" style="background:var(--grn)"></span>3 names coiling</div></div>
        <div class="barrow"><span class="lab">TCS hum</span><div class="bar"><i style="width:72%;background:var(--cyn)"></i></div><span class="conf">tightening</span></div>
        <div class="barrow"><span class="lab">INFY chime</span><div class="bar"><i style="width:100%;background:var(--grn)"></i></div><span class="conf">fired ♪</span></div>
      </div>`
  },
  {
    id: 'b1-terminal-mode',
    kind: 'suggestion',
    tag: 'Tier B · B1',
    title: 'Terminal Mode (command palette + dense theme)',
    blurb: 'Cmd/Ctrl-K command palette (B RELIANCE opens the card, G perf jumps to performance) plus a dense Bloomberg-ish theme toggle.',
    explain: 'Power users evangelize keyboard-first tools; this is a cheap, high-leverage affordance for the segment most likely to become a paying, loyal user.',
    current: 'There\'s no way to jump to a symbol or page without mouse navigation — everything is click-driven through menus and scrolling.',
    updated: 'Cmd/Ctrl-K opens a command palette: type a ticker to jump straight to its card, or a short verb like "G perf" to jump to the performance page. Pair with a dense Bloomberg-ish theme toggle for power users.'
  },
  {
    id: 'b2-morning-brief',
    kind: 'suggestion',
    tag: 'Tier B · B2',
    title: 'Morning Scrollytelling Brief',
    blurb: 'A 60-second vertical-swipe story: mood → top 3 setups → yesterday\'s graded result → one lesson.',
    explain: 'Generated nightly from data we already ship; becomes a mobile morning ritual, using the same story mechanics users already know from Instagram/Snapchat.',
    current: 'There\'s no mobile-native morning ritual — users have to open the full site and parse tables to get today\'s read.',
    updated: 'A 60-second vertical swipe story generated nightly: mood → top 3 setups → yesterday\'s graded result → one lesson. Zero new backend work — just a different template over data we already compute.'
  },
  {
    id: 'b3-streak-counter',
    kind: 'suggestion',
    tag: 'Tier B · B3',
    title: 'Streak & "days since last methodology lie" counter',
    blurb: 'A cheeky public counter: days of unbroken ledger, calls graded, zero edits.',
    explain: 'Turns ops discipline into brand — the honesty layer becomes a number visitors can watch climb, the opposite of every "guru" feed that quietly deletes losers.',
    current: 'Ops discipline (an unbroken ledger, zero retroactive edits) is invisible to visitors — it\'s asserted in internal docs, not shown anywhere on the site.',
    updated: 'A cheeky public counter on the homepage: days of unbroken ledger, total calls graded, zero edits. Turns invisible discipline into a number that compounds trust every day it doesn\'t reset.'
  },
  {
    id: 'b4-ic-memo',
    kind: 'suggestion',
    tag: 'Tier B · B4',
    title: 'One-tap "explain like a hedge-fund memo"',
    blurb: 'Per card, render the existing rationale fields as a tight 5-line IC memo: thesis / trigger / risk / sizing cue / kill condition.',
    explain: 'Same underlying data, 10× more authoritative reading experience — no new backend work, just a different template over fields we already compute.',
    current: 'Per-card rationale fields exist but are rendered as loose bullet text, not a scannable decision memo.',
    updated: 'A one-tap toggle renders the same fields as a tight 5-line IC memo: thesis / trigger / risk / sizing cue / kill condition — the format professional allocators actually read.'
  },
  {
    id: 'b5-print-mode',
    kind: 'suggestion',
    tag: 'Tier B · B5',
    title: 'Print mode — the daily sheet',
    blurb: 'A gorgeous one-page PDF "daily sheet" (top setups + levels table) auto-generated nightly.',
    explain: 'Old-school traders love a sheet; it circulates on desks and WhatsApp groups with our name on every page — free, durable distribution that a pure web app never gets.',
    current: 'Nothing leaves the browser tab — there\'s no artifact traders can print, save, or forward to a group chat.',
    updated: 'Auto-generate a one-page PDF "daily sheet" (top setups + levels table) every night in the existing Action. Zero marginal cost, and it becomes a shareable, offline-durable object with our name on it.'
  },
  {
    id: 'sequencing',
    kind: 'feature',
    tag: 'Sequencing',
    title: 'Rollout order — cheap and differentiating first',
    blurb: 'S2 + B3 first (cheap, immediately differentiating); S1 next (rides P0\'s force-push fix); then S3+B2; then S4; then S5/S6; then A1/A2.',
    explain: 'Rule stays the rule: none of this ships ahead of Phase 0 safety items, and the accuracy program (Phase 1) remains the standing priority. Spectacle on top of a red ledger is just lipstick; on top of a green one, it\'s a category killer.',
    poc: `
      <div class="wf-row"><div class="wf-lab" style="width:26px">1</div><div class="wf-block" style="background:#0f2030;color:#7dd3fc;flex:1">S2 receipts + B3 counter — cheap, immediately differentiating</div></div>
      <div class="wf-row"><div class="wf-lab" style="width:26px">2</div><div class="wf-block" style="background:#12351f;color:#5fe08e;flex:1">S1 time machine — rides P0's force-push fix</div></div>
      <div class="wf-row"><div class="wf-lab" style="width:26px">3</div><div class="wf-block" style="background:#241a30;color:#c4b5fd;flex:1">S3 replay + B2 brief — daily content flywheel</div></div>
      <div class="wf-row"><div class="wf-lab" style="width:26px">4</div><div class="wf-block" style="background:#241d0a;color:#fbbf24;flex:1">S4 uncertainty design language — adopt progressively</div></div>
      <div class="wf-row"><div class="wf-lab" style="width:26px">5</div><div class="wf-block" style="background:#2a1414;color:#fca5a5;flex:1">S5 / S6 — once auth-lite (per-user key) exists</div></div>
      <div class="wf-row"><div class="wf-lab" style="width:26px">6</div><div class="wf-block" style="background:#0d1f16;color:#86efac;flex:1">A1 / A2 — after the flow-data layer (Phase 2) lands</div></div>`
  }
];
