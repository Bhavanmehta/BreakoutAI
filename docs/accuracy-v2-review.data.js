/* Data for docs/accuracy-v2-review.html — sourced from docs/ACCURACY_V2_SPEC.md */
window.__DATA = [

// ---------- Layer E — Entry protocol ----------
{
  id: "entry-protocol",
  kind: "suggestion",
  tag: "Layer E · Entry",
  title: "Entry fill: signal-close → next-bar open (E1), with a retest-limit mode (E2)",
  blurb: "Fixes a fictional fill price, and adds an option that never chases.",
  explain: "Today the backtest (and the live grading) enters a trade at the <b>same day's close</b> that produced the signal — but the scan runs <i>after</i> the close, so no real user could ever get that fill (spec finding A5). E1 makes next-bar open the honest baseline for every mode. E2 goes further: instead of paying up for the breakout, it places a resting limit order at the breakout pivot, live for 5 bars, and cancels if untouched — with an extension veto (never chase if already &gt;1.0×ATR14 above pivot). Fill rate for E2 must be tracked too, since a cheaper entry that often doesn't fill is a real cost, not a free lunch.",
  current: "<b>E0 (today):</b> fill = signal day's own close.<br><br>Problem: this price isn't tradeable — the scan runs after the close prints. Every backtest number and every live grade is silently using a fill nobody can get.",
  updated: "<b>E1 (new default):</b> fill = next bar's open. Honest baseline for all grading.<br><br><b>E2 (optional, opt-in):</b> resting limit at the pivot price, live 5 bars, cancel if untouched. Never used if signal close is already &gt;1.0×ATR14 above pivot (extension veto) — in that case E2 is the <i>only</i> allowed mode."
},

// ---------- Layer X — Exit barriers ----------
{
  id: "exit-barriers",
  kind: "feature",
  tag: "Layer X · Exit",
  title: "Triple-barrier exits (ATR-scaled), replacing the flat ±6% band",
  blurb: "Profit / stop / time barriers sized off structure and volatility, not a fixed percent.",
  explain: "Today every call gets the same ±6% stop and target regardless of the stock's volatility — a flat percent band. That mismeasures noise: high-ATR names get stopped out on ordinary chop, low-ATR names sit in dead trades for weeks (51.7% of US events already resolve neither barrier in 10 days under the current rule). The v2 barrier is structure- and volatility-aware: the profit target and stop distance scale with ATR14, and the stop is anchored to the actual breakout pivot, not just a flat offset from entry.",
  poc: `<div class="m-card">
    <div class="m-row"><div><span class="tkr">RIGL</span><span class="exch">NASDAQ</span></div><div class="px">Entry (E1) <span class="mono">$31.60</span></div></div>
    <div class="barrier-viz">
      <div class="bv-line profit"><span class="l">Profit barrier <small>P + 2.0 × ATR14 &nbsp;(sweep 1.5 / 2.0 / 2.5)</small></span><span class="mono up">$34.86</span></div>
      <div class="bv-line entry"><span class="l">Entry <small>next-bar open (E1)</small></span><span class="mono">$31.60</span></div>
      <div class="bv-line stop"><span class="l">Stop barrier <small>max(pivot − 0.5×ATR, entry − 1.5×ATR) &nbsp;(buffer sweep 0.25/0.5/0.75)</small></span><span class="mono down">$29.65</span></div>
      <div class="bv-line time"><span class="l">Time barrier <small>15 bars &nbsp;(sweep 10/15/20) — exit at close, record actual return, never a scratch</small></span><span class="mono">bar 15</span></div>
    </div>
    <div class="conf" style="margin-top:8px">All fills next-bar-open after the barrier is touched; gaps fill at the gapped price — no fantasy fills exactly at the barrier level.</div>
  </div>`
},

// ---------- Layer R — Regime gate ----------
{
  id: "regime-gate",
  kind: "feature",
  tag: "Layer R · Regime",
  title: "Regime gate — \"hostile regime\" badge instead of a silent full-size call",
  blurb: "Full size only when index trend + breadth + vol regime all agree; otherwise flagged, not deleted.",
  explain: "There's currently no execution-time regime check — a breakout fires identically whether the tape is healthy or hostile. v2 adds a three-part gate (index above its 50DMA · breadth &gt;40% of the scan universe above 50DMA, sweep 30/40/50 · 20d realized index vol below its 1-year 80th percentile). Candidates that fail still get logged and shown — never silently deleted — so the gate's value can be proven out-of-sample instead of just deleting inconvenient history.",
  poc: `<div class="m-card">
    <div class="m-row"><div><span class="tkr">DIXON</span><span class="exch">NSE</span></div><div class="px up">₹14,220 ▲2.4%</div></div>
    <div class="badge" style="margin-top:8px;border-color:#3a2418;background:#1f1410;color:#fca5a5"><span class="d" style="background:var(--red)"></span> Hostile regime — breadth 31% (need &gt;40%), full size withheld</div>
    <div class="crux" style="margin-top:8px"><b>Still shown, still logged:</b> setup quality unchanged, but conviction is capped and the card is flagged so the gate's edge can be measured, not assumed.</div>
    <div style="margin-top:10px" class="tbl-wrap"><table class="tbl">
      <tr><th>Gate check</th><th>Threshold (sweep)</th><th>Today</th></tr>
      <tr><td>Index vs 50DMA</td><td>above</td><td class="up">✓ NIFTY above</td></tr>
      <tr><td>Breadth</td><td>&gt;40% (30/40/50)</td><td class="down">✕ 31%</td></tr>
      <tr><td>Vol regime</td><td>20d vol &lt; 1y 80th pct</td><td class="up">✓ 62nd pct</td></tr>
    </table></div>
  </div>`
},

// ---------- Layer M — Meta-label model ----------
{
  id: "meta-label-model",
  kind: "feature",
  tag: "Layer M · Meta-label",
  title: "Meta-label model — conviction becomes a calibrated win probability",
  blurb: "GBT (or logistic if it doesn't win out-of-sample) scores every candidate on 11 leakage-audited features.",
  explain: "Conviction is currently a hand-weighted composite. v2 trains a model to predict P(profit barrier hit first) from features knowable at signal time — extension in ATRs, base length/depth, volatility contraction ratio, breakout-day volume multiple, closing range of the breakout bar, RS percentile (63d), ADX(14), distance from 52w high, prior failed breakouts on the same base, days to next earnings, and regime features. A gradient-boosted model is tested against a plain logistic baseline — if GBT doesn't beat logistic out-of-sample, the logistic ships instead. Two selection rules are tested: p̂ ≥ 0.45 cutoff, or top-K per day (K=5 IN / 5 US, cross-sectional). This is also what feeds the calibration/Wilson work from the site critique.",
  poc: `<div class="m-card">
    <div class="lbl" style="margin-top:0">Feature importance (top drivers, illustrative)</div>
    <div class="barrow"><span class="lab">Prior failed breakouts (#8)</span><div class="bar"><i style="width:82%;background:var(--vio)"></i></div><span class="mono">0.18</span></div>
    <div class="barrow"><span class="lab">Vol contraction ratio</span><div class="bar"><i style="width:70%;background:var(--vio)"></i></div><span class="mono">0.15</span></div>
    <div class="barrow"><span class="lab">RS percentile (63d)</span><div class="bar"><i style="width:63%;background:var(--vio)"></i></div><span class="mono">0.13</span></div>
    <div class="barrow"><span class="lab">Breakout-day volume ×</span><div class="bar"><i style="width:55%;background:var(--vio)"></i></div><span class="mono">0.11</span></div>
    <div class="barrow"><span class="lab">Extension (ATRs above pivot)</span><div class="bar"><i style="width:40%;background:var(--vio)"></i></div><span class="mono">0.08</span></div>
    <div class="lbl">Calibration check — predicted vs realized (must be monotone to ship)</div>
    <div class="barrow"><span class="lab">Decile 1 (p̂ 0.1)</span><div class="bar"><i style="width:12%;background:var(--grn)"></i></div><span class="mono">11% realized</span></div>
    <div class="barrow"><span class="lab">Decile 5 (p̂ 0.5)</span><div class="bar"><i style="width:48%;background:var(--grn)"></i></div><span class="mono">46% realized</span></div>
    <div class="barrow"><span class="lab">Decile 9 (p̂ 0.9)</span><div class="bar"><i style="width:88%;background:var(--grn)"></i></div><span class="mono">85% realized</span></div>
    <div class="conf" style="margin-top:4px">GBT vs logistic baseline compared out-of-sample; whichever wins ships. Conviction badge becomes "p̂ 62% win prob." instead of an opaque 0–100.</div>
  </div>`
},

// ---------- Layer P — Sizing ----------
{
  id: "sizing-r",
  kind: "suggestion",
  tag: "Layer P · Sizing",
  title: "Constant-risk sizing (0.5R) so IN and US become directly comparable",
  blurb: "Position size is set by the stop distance, and results are reported in R, not %.",
  explain: "For the shadow portfolio / expectancy aggregation, every trade risks a fixed 0.5R where R = entry − stop. This is what makes an India large-cap trade and a US small-cap trade comparable on one scale — a 2% move on a wide-stop name and an 8% move on a tight-stop name can represent the exact same R, which % returns alone hide.",
  current: "No explicit sizing rule; performance reported in raw % returns, which conflates a stock's stop distance with the size of the move — a big % winner on a wide-stop, high-ATR name isn't necessarily a better trade than a smaller % winner on a tight stop.",
  updated: "Every trade sized to risk a constant 0.5R (R = entry − stop). Portfolio expectancy reported in R units — directly comparable across IN and US, across tight-stop and wide-stop names."
},

// ---------- Evaluation protocol — walk-forward ----------
{
  id: "walk-forward-protocol",
  kind: "suggestion",
  tag: "Protocol · Validation",
  title: "Walk-forward validation with purge + embargo, replacing pooled random CV",
  blurb: "Train only on the past, embargo the boundary, roll quarterly — never shuffle time.",
  explain: "Randomly shuffling historical rows into train/test (pooled CV) leaks information: overlapping barrier windows on the same stock end up split across train and test, and the model implicitly sees future regime information. Walk-forward trains only on data up to time T, embargoes 10 bars at the boundary, tests forward, then rolls the window quarterly — and any label whose barrier window overlaps the train/test boundary on the same stock is purged entirely. Every market (IN, US) is evaluated separately and never blended into one headline number.",
  current: "No formal walk-forward — the original 60/40 train/test split was by <b>stock</b>, not by <b>time</b>, so the model could implicitly learn regime information from periods it will later be \"tested\" on.",
  updated: "Rolling quarterly walk-forward: train on ≤T → 10-bar embargo → test forward → roll. Overlapping-label purge removes any row whose barrier window straddles the train/test boundary. IN and US scored separately, always.",
  poc: null,
  extra: `<div class="lbl">How the rolling window works</div>
    <div class="mock"><div class="m-card">
      <div class="wf-row"><span class="wf-lab">Fold 1</span><div class="wf-block train">Train — Q1–Q2 2024</div><div class="wf-block embargo">embargo 10d</div><div class="wf-block test">Test Q3 2024</div></div>
      <div class="wf-row"><span class="wf-lab">Fold 2</span><div class="wf-block train">Train — Q1–Q3 2024</div><div class="wf-block embargo">embargo 10d</div><div class="wf-block test">Test Q4 2024</div></div>
      <div class="wf-row"><span class="wf-lab">Fold 3</span><div class="wf-block train">Train — Q1 2024–Q1 2025</div><div class="wf-block embargo">embargo 10d</div><div class="wf-block test">Test Q2 2025</div></div>
      <div class="conf" style="margin-top:8px">Final config is chosen using train-period folds only, then confirmed <b>once</b> on the untouched final test period. If that one confirmation shot fails, it's back to the drawing board — not back to the sweep.</div>
    </div></div>`
},

// ---------- Costs / slippage ----------
{
  id: "slippage-costs",
  kind: "suggestion",
  tag: "Protocol · Costs",
  title: "Slippage haircut by turnover bucket — report gross AND net, always",
  blurb: "A per-market, per-liquidity-tier cost table applied to every fill.",
  explain: "There's no execution-cost modeling today — grades are computed on clean OHLC with no friction. v2 applies a pre-registered slippage haircut by market and liquidity tier to every entry/exit, and every metric is reported both gross (no costs) and net (with costs) side by side, so a strategy that only looks good before frictions can't quietly ship.",
  current: "Grading uses raw close/high/low with zero cost assumption — a large-cap NSE name and an illiquid US micro-cap are graded identically, and every published number is effectively \"gross.\"",
  updated: "Pre-registered slippage table applied to every fill: <b>IN large-cap 0.15%</b> · <b>IN small-cap 0.35%</b> · <b>US liquid 0.10%</b> · <b>US thin 0.30%</b>. Every metric reported gross <i>and</i> net."
},

// ---------- Deliverable: ablation table ----------
{
  id: "ablation-table",
  kind: "feature",
  tag: "§2.5 · Deliverable",
  title: "The ablation table — each layer must earn its keep in net expectancy",
  blurb: "Baseline vs +X vs +X+E vs +X+E+R vs +X+E+R+M, per market, per regime. This is the actual deliverable.",
  explain: "This is what the whole spec is building toward: one committed results table showing baseline (today's E0 + flat ±6%) against each additional layer, stacked. A layer only ships if it improves net expectancy in R — not hit rate. No cell is interpreted below n=50, and every run (including failed sweeps) is logged to a results file committed to the repo so nothing gets silently re-tuned.",
  poc: `<div class="m-card">
    <div class="lbl" style="margin-top:0">§2.5 Ablation table — US market (illustrative numbers)</div>
    <table class="tbl">
      <tr><th>Config</th><th>Expectancy (R, net)</th><th>Hit rate</th><th>Profit factor</th><th>n</th></tr>
      <tr><td>Baseline (E0 + flat ±6%)</td><td class="down">−0.47R</td><td>26.7%</td><td>0.61</td><td>612</td></tr>
      <tr><td>+X (ATR triple-barrier)</td><td class="down">−0.09R</td><td>41.8%</td><td>0.89</td><td>598</td></tr>
      <tr><td>+X +E (next-bar-open / retest)</td><td class="up">+0.06R</td><td>43.1%</td><td>1.04</td><td>571</td></tr>
      <tr><td>+X +E +R (regime gate)</td><td class="up">+0.14R</td><td>46.0%</td><td>1.19</td><td>402</td></tr>
      <tr><td>+X +E +R +M (meta-label, top-K)</td><td class="up">+0.24R</td><td>50.2%</td><td>1.38</td><td>241</td></tr>
    </table>
    <div class="conf" style="margin-top:8px">Ship threshold: net expectancy ≥ +0.20R with n ≥ 200 in at least one market, stable sign across ≥70% of walk-forward test windows. Rows below n=50 are never interpreted. Every one of these runs — including failed sweep values — is logged to a committed results file, not just the winners.</div>
    <div class="lbl">Per-regime breakdown (required by §2.5)</div>
    <table class="tbl">
      <tr><th>Regime</th><th>Expectancy (R, net)</th><th>n</th></tr>
      <tr><td>Greed</td><td class="up">+0.31R</td><td>118</td></tr>
      <tr><td>Neutral</td><td class="up">+0.19R</td><td>96</td></tr>
      <tr><td>Fear</td><td class="down">−0.08R</td><td>27 <span class="pill2">n&lt;50 — not interpreted</span></td></tr>
    </table>
  </div>`
},

// ---------- Quick win: extension veto ----------
{
  id: "extension-veto",
  kind: "feature",
  tag: "§3 · Quick win",
  title: "Extension veto — \"wait for retest\" instead of \"buy now\" when a name is stretched",
  blurb: "Zero model risk, ships regardless of backtest outcome. Never publish buy-now &gt;1 ATR above pivot.",
  explain: "One of the four quick wins that ship regardless of what the backtest concludes. Extension above pivot turns out not to be the site's main problem (median +0.7%, only 18–23% of calls enter &gt;2% extended — spec finding A2 corrects the original D2 hypothesis), but for the minority of names that <i>are</i> stretched, the honest move is to label them \"wait for retest\" with the exact pivot price rather than a buy-now call.",
  poc: `<div class="m-card">
    <div class="m-row"><div><span class="tkr">AGIO</span><span class="exch">NASDAQ</span></div><div class="px up">$44.10 ▲3.6%</div></div>
    <div class="badge" style="margin-top:8px;border-color:#3a2f14;background:#14110a;color:#fbbf24"><span class="d" style="background:var(--amb)"></span> Extended 2.3 ATR above pivot ($40.85)</div>
    <div class="crux" style="margin-top:8px"><b>Wait for retest:</b> too far above the $40.85 pivot to chase (E2-only zone). A close back near $40.85–$41.50 with volume confirms; no buy-now call issued while this stretched.</div>
  </div>`
},

// ---------- Quick win: earnings veto binding ----------
{
  id: "earnings-veto-enforced",
  kind: "suggestion",
  tag: "§3 · Quick win",
  title: "Earnings veto — flag exists, make it binding",
  blurb: "GATE_EARNINGS_VETO_DAYS is computed today but doesn't stop a publish.",
  explain: "The earnings-proximity gate already exists in code (`GATE_EARNINGS_VETO_DAYS`) but today it's informational only — a name can still fire and publish as a normal call even with earnings a day or two out, which is a well-known way to eat an unrelated gap risk. The fix is mechanical: when the veto window is active, the call is capped/blocked at publish time, not just annotated.",
  current: "<code>GATE_EARNINGS_VETO_DAYS</code> is computed and available, but a name with earnings in the veto window can still publish as a full-conviction \"buy\" call — the flag doesn't gate anything yet.",
  updated: "Same flag becomes binding at publish time: names inside the earnings-veto window are capped to \"Watch\" (or withheld from high-conviction) instead of publishing at full tier — earnings-gap risk stops silently riding along on a breakout signal."
},

// ---------- Quick win: one-failed-breakout memory ----------
{
  id: "failed-breakout-memory",
  kind: "suggestion",
  tag: "§3 · Quick win",
  title: "One-failed-breakout memory — retries need more proof",
  blurb: "If this exact base already failed once, require a bigger volume multiple on the retry.",
  explain: "\"Prior failed breakouts on this same base\" is feature #8 in the meta-label list and — per the illustrative importance ranking — one of the strongest drivers institutions weight hard. Rather than wait for the full model to ship, this is codified as a simple hard rule right now: if the same base has already failed once, the retry needs a materially higher breakout-day volume multiple to qualify, rather than being treated as a fresh, independent signal.",
  current: "Each breakout attempt on a base is scored independently — a name that already faked out once gets no extra scrutiny on the retry; it's evaluated exactly like a first attempt.",
  updated: "If the same base has a prior failed breakout on record, the retry must clear a higher breakout-day volume-multiple bar to qualify at all — codified as a hard rule now, ahead of the full meta-label model."
},

// ---------- Ruler bias / A3 ----------
{
  id: "ruler-bias",
  kind: "suggestion",
  tag: "§3b · Evidence (A3)",
  title: "The current ruler contaminates the feature science itself",
  blurb: "Under a flat % band, every feature correlated with volatility gets mechanically over- or under-rewarded.",
  explain: "This is the deepest finding in the evidence addendum. Because \"neither barrier hit\" is graded as a flat FAILURE, and resolution probability scales with a stock's ATR under a fixed %-of-price band, any feature that happens to correlate with volatility gets its weight inflated or crushed for reasons that have nothing to do with whether it actually predicts follow-through. Base depth (US weight 0.70 today) is a prime suspect — deep bases tend to be volatile stocks, so the current ruler may be rewarding volatility, not signal. Volatility contraction was previously measured as <i>negative</i> under this ruler; the spec explicitly flags that this can reverse entirely once graded honestly. Every feature — accepted and previously rejected — has to be re-measured under the new ATR-scaled ruler before any weight is trusted.",
  current: "Feature weights (e.g. base depth at 0.70 for US) were fit under the flat ±6% ruler, where resolution odds scale with a name's own ATR — so volatility-correlated features look predictive whether or not they actually are. Vol-contraction was measured negative and rejected on this same broken ruler.",
  updated: "Every feature — including previously <b>rejected</b> ones (vol contraction, market regime, method co-fires) and previously <b>accepted</b> ones (base depth, trailing reliability) — gets re-validated from scratch under the ATR-scaled triple barrier. Current weights are suspect until re-measured; nothing is grandfathered in."
},

// ---------- A7 outcome taxonomy ----------
{
  id: "outcome-taxonomy",
  kind: "suggestion",
  tag: "§3b · Evidence (A7)",
  title: "One outcome taxonomy everywhere — live and backtest currently disagree",
  blurb: "Live win_rate excludes 'expired'; the backtest reference rate counts it as a loss. performance.html shows both as if comparable.",
  explain: "Live `win_rate` on the site only counts won/lost trades and drops `expired` (neither-barrier-hit) episodes from the denominator, while the backtest reference base rates (26.7% US / 38.8% IN) count every neither-resolved episode as a failure. `performance.html` currently displays both numbers side by side as though they're apples-to-apples — they aren't. The fix is one shared taxonomy {target, stop, expired} used identically everywhere, with all three shares always shown, and expired episodes graded by their actual window return (the closes are already stored) instead of being silently excluded or silently counted as a loss.",
  current: "Live <code>win_rate</code> (build_performance.py) excludes <code>expired</code> outcomes from its denominator. The backtest reference rate counts <code>expired</code> as a failure. Both numbers sit next to each other on performance.html looking directly comparable — they're computed on different rules.",
  updated: "Single taxonomy — <b>target / stop / expired</b> — used identically in live and backtest reporting. All three shares always shown (never dropped). Expired episodes graded by their actual realized window return instead of being scratched or auto-failed."
},

];
