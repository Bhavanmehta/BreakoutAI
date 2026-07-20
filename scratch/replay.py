"""Replay the live pick ledgers (data branch, as_of 2026-07-17) under proposed
rule changes, without touching the production pipeline.

Variants:
  V0  baseline           - ledger as recorded (entry = signal-day close)
  V1  confirmation gate  - only keep picks whose FIRST tracked close finished
                           above the entry level; outcome as ledger recorded.
                           Pure selection effect, computed from ledger arrays
                           (works for both markets, no OHLC needed).
  V2  gate + real entry  - V1's picks, but entered at the NEXT day's open after
                           confirmation; stop stays at the same absolute level,
                           target re-anchored to entry2 + (entry2 - stop);
                           re-graded over a fresh 10-bar window. Needs OHLC.
  V3  real entry, no gate- entry at day+1 open, same re-anchoring. Needs OHLC.

US stop-model variants (OHLC subset): flat6, atr15 (current), atr20, atr15min6.
"""
import json, math, os, sys

W = 10  # settings.FOLLOWTHROUGH_WINDOW

def load_ohlc(sym, mkt):
    """Prefer fresh data-branch extract; fall back to stale local cache."""
    paths = ([f"scratch/databranch/ohlc/{sym}.json", f"data/ohlc/{sym}.json"] if mkt == "IN"
             else [f"scratch/usohlc/{sym}.json", f"scratch/databranch/us/ohlc/{sym}.json", f"data/us/ohlc/{sym}.json"])
    for p in paths:
        if os.path.exists(p):
            try:
                d = json.load(open(p, encoding="utf-8"))
                return d["bars"]  # [date, o, h, l, c]
            except Exception:
                pass
    return None

def grade(highs, lows, i, stop, target, n, window=W):
    for j in range(i + 1, min(i + window, n - 1) + 1):
        if lows[j] <= stop:
            return "lost", j - i
        if highs[j] >= target:
            return "won", j - i
    if n - 1 - i >= window:
        return "expired", None
    return "open", None

def atr10(bars, i):
    """10-day ATR ending at bar i (needs i>=10)."""
    if i < 10:
        return None
    trs = []
    for j in range(i - 9, i + 1):
        _, o, h, l, c = bars[j][:5]
        pc = bars[j - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)

def summarize(tag, results):
    res = [r for r in results if r in ("won", "lost")]
    w = sum(1 for r in res if r == "won")
    l = len(res) - w
    exp = sum(1 for r in results if r == "expired")
    op = sum(1 for r in results if r == "open")
    hr = 100.0 * w / len(res) if res else float("nan")
    print(f"  {tag:<28} n_resolved={len(res):>3}  won={w:>3} lost={l:>3} "
          f"expired={exp:>3} open={op:>2}  hit={hr:5.1f}%")
    return w, l, hr

def run_market(mkt, perf_path):
    d = json.load(open(perf_path, encoding="utf-8"))
    eps = [e for e in d["episodes"] if e["status"] in ("won", "lost", "expired")
           and e.get("entry") and e.get("stop") and e.get("target")]
    print(f"\n================ {mkt}  (resolved episodes: {len(eps)}) ================")

    # ---------- V0 / V1 from ledger arrays alone ----------
    v0, v1 = [], []
    gate_skipped = 0
    for e in eps:
        v0.append(e["status"])
        cl = e.get("closes") or []
        if cl and cl[0] > e["entry"]:
            v1.append(e["status"])
        else:
            gate_skipped += 1
    print("Ledger-only (both markets, full sample):")
    summarize("V0 baseline", v0)
    summarize("V1 confirm-close gate", v1)
    print(f"  {'':<28} gate filtered out {gate_skipped}/{len(eps)} picks")
    # what did the gate throw away?
    tossed = [e["status"] for e in eps if not (e.get("closes") and e["closes"][0] > e["entry"])]
    tw = sum(1 for s in tossed if s == "won"); tl = sum(1 for s in tossed if s == "lost")
    print(f"  {'':<28} tossed picks were: {tw} won / {tl} lost / "
          f"{sum(1 for s in tossed if s=='expired')} expired")

    # ---------- OHLC-based variants ----------
    cov, nocov, mismatch = 0, 0, 0
    r_v0chk, r_v2, r_v3 = [], [], []
    stopv = {"flat6": [], "atr15": [], "atr20": [], "atr15min6": []}
    for e in eps:
        bars = load_ohlc(e["symbol"], mkt)
        if not bars:
            nocov += 1; continue
        dates = [b[0] for b in bars]
        if e["date"] not in dates:
            nocov += 1; continue
        i = dates.index(e["date"])
        n = len(bars)
        # need at least i+2 and some forward bars
        if i + 2 > n - 1:
            nocov += 1; continue
        o = [b[1] for b in bars]; h = [b[2] for b in bars]
        lo = [b[3] for b in bars]; c = [b[4] for b in bars]
        cov += 1
        # sanity: replicate ledger grade with stored stop/target
        st, _ = grade(h, lo, i, e["stop"], e["target"], n)
        if st != e["status"]:
            mismatch += 1
        r_v0chk.append(st)
        stop_dist = e["entry"] - e["stop"]
        # V3: enter at day+1 open, stop same absolute, 1R re-anchored
        e3 = o[i + 1]
        st3, _ = grade(h, lo, i + 1, e["stop"], e3 + (e3 - e["stop"]), n)
        r_v3.append(st3 if e3 > e["stop"] else "lost")
        # V2: gate on close[i+1] > entry, enter at open[i+2]
        if c[i + 1] > e["entry"]:
            e2 = o[i + 2]
            st2, _ = grade(h, lo, i + 2, e["stop"], e2 + (e2 - e["stop"]), n)
            r_v2.append(st2 if e2 > e["stop"] else "lost")
        # US stop variants (from signal-day close entry, ledger-style)
        if mkt == "US":
            a = atr10(bars, i)
            ent = e["entry"]
            models = {"flat6": ent * 0.94}
            if a:
                for name, mult, mn in [("atr15", 1.5, 4.0), ("atr20", 2.0, 4.0),
                                       ("atr15min6", 1.5, 6.0)]:
                    dist = max(mult * a, ent * mn / 100.0)
                    dist = min(dist, ent * 0.12)
                    models[name] = ent - dist
            for name, s in models.items():
                stv, _ = grade(h, lo, i, s, ent + (ent - s), n)
                stopv[name].append(stv)
    print(f"OHLC-based (covered {cov}/{len(eps)}, no-coverage {nocov}, "
          f"replication mismatches {mismatch}):")
    summarize("V0 re-graded (sanity)", r_v0chk)
    summarize("V2 gate + next-open entry", r_v2)
    summarize("V3 next-open entry, no gate", r_v3)
    if mkt == "US":
        print("US stop-model variants (same entries, symmetric 1R targets):")
        for name, res in stopv.items():
            cur = " <- current" if name == "atr15" else (" <- IN-style" if name == "flat6" else "")
            summarize(f"stop {name}{cur}", res)

run_market("IN", "scratch/perf_in.json")
run_market("US", "scratch/perf_us.json")
