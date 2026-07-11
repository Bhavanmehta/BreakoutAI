"""India-market post-mortem re-run (durable / CI-friendly).

Reads the live IN ledger (performance.json + sectors.json) and re-checks the two
findings that survived the Jul-10 (n=148) review, comparing against that baseline:

  1. Does the 80+ conviction tier still UNDERPERFORM the 60s bucket?
     (the only pattern that persisted from the thin Jul-6 sample -> the sole
      candidate for a score.py calibration tweak, IF it holds with more calls)
  2. Did the sector tilt hold (Healthcare/Tech strong; Industrials/Cons-Def weak)?

Everything the Jul-6 pass "found" (below-pivot, stop-width) was small-sample noise
that inverted at n=148 -- so this script's job is NOT to mine new patterns, it is to
tell you whether the ONE real signal has earned a scoring change yet, or whether to
keep monitoring. Pure stdlib so it runs on a bare CI runner with no pip install.

Usage:  python scripts/postmortem_in.py [DATA_DIR]   (DATA_DIR default: "data")
Writes a Markdown report to $POSTMORTEM_OUT (default: postmortem_report.md) and
echoes a plain-text version to stdout.
"""
import json, os, sys, statistics as st
from collections import defaultdict
from datetime import datetime, timezone

DATA_DIR = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("POSTMORTEM_DATA_DIR", "data")
OUT = os.environ.get("POSTMORTEM_OUT", "postmortem_report.md")

# ---- Jul-10 baseline (n=148) we are checking for persistence against ----------
BASE = {
    "as_of": "2026-07-10", "resolved": 148, "win_rate": 0.568,
    "expectancy_r": 0.135, "mean_ret": 0.83, "alpha": 0.63, "beat_rate": 0.512,
    "conv": {"50s": 0.52, "60s": 0.615, "70s": 0.561, "80s+": 0.467},
    "sect": {"Healthcare": 0.76, "Technology": 0.83,
             "Industrials": 0.46, "Consumer Defensive": 0.33},
}

perf = json.load(open(os.path.join(DATA_DIR, "performance.json")))
try:
    sect = json.load(open(os.path.join(DATA_DIR, "sectors.json")))
except FileNotFoundError:
    sect = {}
eps = perf["episodes"]
ana = perf.get("analytics", {}) or {}


def cur_ret(e):
    return (e["closes"][-1] / e["entry"] - 1.0) * 100.0 if e.get("closes") else None


def risk_pct(e):
    return (e["entry"] - e["stop"]) / e["entry"] * 100.0


def slice_by(keyfn, label, minshow=1):
    lines = [f"=== by {label} ==="]
    g = defaultdict(lambda: {"won": 0, "lost": 0, "open": 0, "rets": []})
    for e in eps:
        for k in keyfn(e):
            b = g[k]
            b[e["status"]] = b.get(e["status"], 0) + 1
            r = cur_ret(e)
            if r is not None:
                b["rets"].append(r)
    for k in sorted(g, key=lambda k: -(g[k]["won"] + g[k]["lost"])):
        b = g[k]
        res = b["won"] + b["lost"]
        n = res + b["open"]
        if res < minshow:
            continue
        wr = f"{b['won'] / res:.0%}" if res else "  -"
        mret = f"{st.mean(b['rets']):+.2f}%" if b["rets"] else "   -"
        lines.append(f"  {str(k)[:24]:24} n={n:3} won={b['won']:2} lost={b['lost']:2} "
                     f"resolved={res:3} winrate={wr:>4} meanret={mret}")
    return "\n".join(lines)


# ---- headline ----------------------------------------------------------------
won = sum(1 for e in eps if e["status"] == "won")
lost = sum(1 for e in eps if e["status"] == "lost")
resolved = won + lost
wr = won / resolved if resolved else 0.0
exp = ana.get("expectancy", {}) or {}
bm = ana.get("benchmark", {}) or {}


def delta(now, base, pct=True):
    d = now - base
    u = "pp" if pct else ""
    return f"{d:+.1f}{u}" if pct else f"{d:+.3f}"


# ---- the watch-item: 80+ conviction tier -------------------------------------
def conv_bucket(c):
    return f"{(c // 10) * 10}s" if c < 80 else "80s+"


cg = defaultdict(lambda: {"won": 0, "lost": 0, "rets": []})
for e in eps:
    b = cg[conv_bucket(e["conviction"])]
    if e["status"] in ("won", "lost"):
        b[e["status"]] += 1
    r = cur_ret(e)
    if r is not None:
        b["rets"].append(r)


def rate(bkt):
    r = cg[bkt]["won"] + cg[bkt]["lost"]
    return (cg[bkt]["won"] / r if r else None), r


r80, n80 = rate("80s+")
r60, n60 = rate("60s")
if r80 is None or r60 is None:
    conv_verdict = "INSUFFICIENT DATA — a bucket has no resolved calls yet."
    conv_flag = "monitor"
elif r80 < r60 and r80 < wr:
    grown = n80 >= 25
    conv_flag = "act" if grown else "monitor"
    conv_verdict = (
        f"PERSISTED. 80+ tier hits {r80:.0%} ({n80} resolved) vs 60s {r60:.0%} "
        f"({n60}) and below the {wr:.0%} book average. "
        + ("Sample has grown past ~25 resolved — a score.py calibration tweak "
           "(cap/ää down-weight the raw 80+ contribution) is now worth DESIGNING. "
           "Review before implementing." if grown else
           f"Still only {n80} resolved in the 80+ tier — directionally persistent "
           "but thin; keep monitoring one more cycle before touching scoring."))
else:
    conv_flag = "reverted"
    conv_verdict = (
        f"REVERTED. 80+ tier now hits {r80:.0%} ({n80} resolved) vs 60s {r60:.0%} "
        f"— no longer the laggard. Like the Jul-6 'patterns', it regressed to the "
        f"mean. Do NOT touch scoring; the book is behaving.")

# ---- sector tilt persistence -------------------------------------------------
sg = defaultdict(lambda: {"won": 0, "lost": 0})
for e in eps:
    s = sect.get(e["symbol"], {}).get("sector", "?")
    if e["status"] in ("won", "lost"):
        sg[s][e["status"]] += 1
sect_lines = []
for name, base_wr in BASE["sect"].items():
    b = sg.get(name, {"won": 0, "lost": 0})
    r = b["won"] + b["lost"]
    now = b["won"] / r if r else None
    if now is None:
        sect_lines.append(f"  {name:20} no resolved calls yet")
        continue
    strong_now = now >= wr
    was_strong = base_wr >= BASE["win_rate"]
    held = "HELD" if strong_now == was_strong else "FLIPPED"
    sect_lines.append(f"  {name:20} was {base_wr:.0%} -> now {now:.0%} ({r} resolved)  [{held}]")

# ---- assemble slices ---------------------------------------------------------
slices = "\n\n".join([
    slice_by(lambda e: e["signals"], "signal"),
    slice_by(lambda e: [conv_bucket(e["conviction"])], "conviction bucket"),
    slice_by(lambda e: [sect.get(e["symbol"], {}).get("sector", "?")], "sector", minshow=4),
])
W = [risk_pct(e) for e in eps if e["status"] == "won"]
L = [risk_pct(e) for e in eps if e["status"] == "lost"]
stopw = (f"stop width  winners: mean={st.mean(W):.1f}% median={st.median(W):.1f}%  "
         f"losers: mean={st.mean(L):.1f}% median={st.median(L):.1f}%") if W and L else "stop width: n/a"

# ---- recommendation ----------------------------------------------------------
rec = {
    "act": "**Action:** the 80+ conviction signal has earned a look — design a "
           "score.py calibration change (present it before shipping). Nothing else "
           "warrants code changes.",
    "monitor": "**Recommendation:** keep monitoring — no code changes. The signal "
               "is real but the sample is still too thin to justify touching scoring.",
    "reverted": "**Recommendation:** stand down — the watch-item reverted to the mean. "
                "No code changes; the book is working.",
}[conv_flag]

now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
md = f"""# BreakoutAI — India post-mortem re-run (automated)

*Generated {now_utc} · ledger `as_of` {perf.get('as_of_date','?')} · resolved **{resolved}** (was {BASE['resolved']} on {BASE['as_of']})*

This job re-checks the single durable finding from the Jul-10 review. Context: the
Jul-6 "patterns" (below-pivot entries, stop-width) were 14-sample noise that inverted
at n=148, so the only open question is whether the **80+ conviction tier** has under-
performed for long enough to justify a scoring tweak.

## Top line
| metric | {BASE['as_of']} baseline | now | Δ |
|---|---|---|---|
| resolved calls | {BASE['resolved']} | {resolved} | {resolved - BASE['resolved']:+d} |
| win rate | {BASE['win_rate']:.0%} | {wr:.0%} | {(wr - BASE['win_rate'])*100:+.1f}pp |
| expectancy (R) | {BASE['expectancy_r']:+.3f} | {exp.get('expectancy_r', float('nan')):+.3f} | {delta(exp.get('expectancy_r', BASE['expectancy_r']), BASE['expectancy_r'], pct=False)} |
| mean window return | {BASE['mean_ret']:+.2f}% | {exp.get('mean_window_return_pct', float('nan')):+.2f}% | — |
| alpha vs Nifty | {BASE['alpha']:+.2f}% | {bm.get('mean_alpha_pct', float('nan')):+.2f}% | — |
| beat rate | {BASE['beat_rate']:.0%} | {bm.get('beat_rate', float('nan')):.0%} | — |

## Watch-item — does the 80+ conviction tier still underperform?
{conv_verdict}

## Sector tilt — did it hold?
```
{chr(10).join(sect_lines)}
```

## Full slices
```
{slices}

{stopw}
```

## Recommendation
{rec}

---
*Auto-generated by `scripts/postmortem_in.py` via `.github/workflows/postmortem-jul20.yml`. Do NOT ship code changes off this report alone — review first.*
"""

with open(OUT, "w", encoding="utf-8") as f:
    f.write(md)

# plain-text-ish echo for the CI log
print(f"resolved={resolved} win_rate={wr:.1%} expectancy_r={exp.get('expectancy_r')}")
print(f"80+ tier: {('%.0f%%'%(r80*100)) if r80 is not None else 'n/a'} ({n80} resolved)  "
      f"vs 60s: {('%.0f%%'%(r60*100)) if r60 is not None else 'n/a'} ({n60})  -> {conv_flag.upper()}")
print("\n".join(sect_lines))
print(f"\nReport written to {OUT}")
