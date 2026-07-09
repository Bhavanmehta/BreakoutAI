"""
signals.py — the "rationale" transparency layer (Sprint 2, competitor-ideas #1–4).

Turns the single opaque `readiness.conviction` number into a legible, skimmable
rationale WITHOUT touching the ranker. `build_rationale(rec)` reads only fields
already present on a scan record (plus market-aware `settings.*`) and returns an
additive `rationale` block:

    confirming / risk  — two facing lists of {key, label, weight} tokens (#1)
    rss                — √Σ(confirming²) vs √Σ(risk²), net + confidence (#2)
    make_or_break      — one templated plain-English deciding-variable line (#3)
    gates              — advisory pass/fail/na chips: liquidity / vol-confirm / earnings (#4)

DESIGN DISCIPLINE — read this before changing weights:
  * The token WEIGHTS here are HEURISTIC DISPLAY VALUES, not validated coefficients.
    They are computed from decorative-but-available record fields and are framed as
    "why" in the UI, never as the ranked score.
  * NONE of this is ever fed back into score.py, which stays strict (reliability +
    depth + method only). This mirrors that module's validated/decorative split.
  * `rss` AUGMENTS, it does not replace: `readiness.conviction` stays THE number the
    list sorts on and the header shows.
  * The gates are ADVISORY ONLY: they annotate, they never cap tier or reorder.

No network. Pure function of the record. Backfill mode at the bottom lets us
regenerate + verify an existing breakouts.json with no full re-scan:

    BREAKOUTAI_MARKET=IN python backend/signals.py data/breakouts.json
    BREAKOUTAI_MARKET=US python backend/signals.py data/us/breakouts.json

(The market env var must match the file — it selects currency + gate thresholds.)
"""
from __future__ import annotations

import math
from datetime import date

import settings

# Heuristic token weights (0–100, display only). Named so the intent is obvious;
# tune freely — they never touch scoring.
_W = {
    "vcp_tight": 85, "vcp_ok": 60,
    "volume": 80, "ema_stack": 75, "coiling": 70,
    "trend_adx": 65, "squeeze": 55, "followthrough": 78, "signal_hc": 90,
    "signal_strong": 82, "signal_rs": 70,
    # risk
    "extended": 55, "unreliable": 65, "no_uptrend": 70,
    "thin_liq": 60, "earnings_soon": 75, "deep_base": 45,
}


# --------------------------------------------------------------------------- #
# small market-aware formatting helpers (mirror the frontend's fmtPrice/turnover)
# --------------------------------------------------------------------------- #
def _sym() -> str:
    return settings.CURRENCY_SYMBOL


def _fmt_price(v) -> str:
    """A price in the active market's currency, compact (matches the UI's fmtPrice)."""
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    s = f"{v:,.2f}" if abs(v) < 100 else f"{round(v):,}"
    return f"{_sym()}{s}"


def _fmt_turnover(v) -> str:
    """Average daily turnover, native units: ₹X.X cr/day (IN) or $X.XM/day (US)."""
    if v is None:
        return "—"
    if settings.MARKET == "US":
        if v >= 1e9:
            return f"${v / 1e9:.1f}B/day"
        if v >= 1e6:
            return f"${v / 1e6:.1f}M/day"
        return f"${round(v / 1e3)}K/day"
    cr = v / 1e7  # ₹ crore
    return f"{_sym()}{cr:.1f}cr/day"


def _num(v):
    """Coerce to float or None (records occasionally carry null/strings)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# derived inputs
# --------------------------------------------------------------------------- #
def _days_to_earnings(rec) -> int | None:
    """Trading-calendar-agnostic day count from `as_of` to `earnings.next.date`.

    None when there's no scheduled earnings (offline/old JSON, or `earnings: null`).
    """
    earn = rec.get("earnings") or {}
    nxt = earn.get("next") or {}
    nxt_date = nxt.get("date")
    as_of = rec.get("as_of")
    if not nxt_date or not as_of:
        return None
    try:
        d0 = date.fromisoformat(str(as_of)[:10])
        d1 = date.fromisoformat(str(nxt_date)[:10])
    except ValueError:
        return None
    return (d1 - d0).days


def _avg_turnover(rec):
    """Average daily turnover = 20d avg volume × price (native currency)."""
    vol = rec.get("volume") or {}
    avg = _num(vol.get("avg"))
    price = _num(rec.get("price"))
    if avg is None or price is None:
        return None
    return avg * price


# --------------------------------------------------------------------------- #
# confirming / risk token builders
# --------------------------------------------------------------------------- #
def _confirming(rec) -> list[dict]:
    out = []
    add = lambda key, label, weight: out.append(
        {"key": key, "label": label, "weight": int(weight)})

    # Tight / shallow base — `base_depth_pct` is stored as a negative depth.
    depth = _num(rec.get("base_depth_pct"))
    if depth is not None:
        d = abs(depth)
        if d <= 10:
            add("vcp_tight", f"Tight base — only {d:.0f}% deep", _W["vcp_tight"])
        elif d <= 16:
            add("vcp_ok", f"Contained base — {d:.0f}% deep", _W["vcp_ok"])

    # Volume surge (the same ratio the breakout test already requires).
    vol = rec.get("volume") or {}
    ratio = _num(vol.get("ratio"))
    if vol.get("surge") or (ratio is not None and ratio >= settings.VOL_SURGE_MULT):
        r = f"{ratio:.1f}× average" if ratio is not None else "above average"
        add("volume", f"Volume {r}", _W["volume"])

    # Above a rising short/mid EMA stack.
    stack = rec.get("ema_stack") or {}
    above = [k for k in ("ema8", "ema21", "ema50")
             if (stack.get(k) or {}).get("position") == "ABOVE"]
    if len(above) == 3:
        add("ema_stack", "Above the rising 8/21/50 EMAs", _W["ema_stack"])
    elif len(above) == 2:
        add("ema_stack", "Above 2 of 3 short-term EMAs", _W["ema_stack"] - 20)

    # Coiling just below the pivot (small negative distance to resistance).
    res = rec.get("resistance") or {}
    dist = _num(res.get("distance_pct"))
    if dist is not None and -4.0 <= dist < 0:
        add("coiling", f"Coiling {abs(dist):.1f}% below the pivot", _W["coiling"])

    # Strong, directional trend (ADX).
    adx = rec.get("adx") or {}
    adx_v = _num(adx.get("value"))
    if adx_v is not None and adx_v >= 25:
        add("trend_adx", f"Strong trend (ADX {adx_v:.0f})", _W["trend_adx"])

    # Volatility squeeze / contraction.
    volat = rec.get("volatility") or {}
    state = str(volat.get("state") or "")
    cr = _num(volat.get("contraction_ratio"))
    if "queeze" in state or "oiling" in state or (cr is not None and cr < 0.85):
        add("squeeze", "Volatility squeeze (range contracting)", _W["squeeze"])

    # Proven follow-through on this name.
    hist = rec.get("history") or {}
    ft = _num(hist.get("followthrough_rate"))
    pb = _num(hist.get("past_breakouts")) or 0
    if ft is not None and pb > 0 and ft >= settings.RELIABILITY_GOOD_AT:
        add("followthrough", f"Proven follow-through ({round(ft * 100)}%)",
            _W["followthrough"])

    # Named engine signal (RS / strong / high-conviction).
    sig = (rec.get("readiness") or {}).get("signal")
    if sig == "high_conviction":
        add("signal_hc", "High-conviction squeeze-release setup", _W["signal_hc"])
    elif sig == "strong_breakout":
        add("signal_strong", "Strong, high-volume breakout", _W["signal_strong"])
    elif sig == "relative_strength":
        add("signal_rs", "Relative-strength high vs the index", _W["signal_rs"])

    return out


def _risk(rec, days_to_earnings, turnover) -> list[dict]:
    out = []
    add = lambda key, label, weight: out.append(
        {"key": key, "label": label, "weight": int(weight)})

    # Over-extended above the pivot (chasing risk).
    res = rec.get("resistance") or {}
    dist = _num(res.get("distance_pct"))
    if dist is not None and dist > settings.HC_EXT_MAX_PCT:
        add("extended", f"Extended {dist:.1f}% above the pivot", _W["extended"])

    # Deep / loose base.
    depth = _num(rec.get("base_depth_pct"))
    if depth is not None and abs(depth) > 25:
        add("deep_base", f"Deep base — {abs(depth):.0f}% correction", _W["deep_base"])

    # Thin / weak validated track record.
    if (rec.get("readiness") or {}).get("reliable") is False:
        add("unreliable", "Thin / weak track record here", _W["unreliable"])

    # Not in an uptrend (Stage-2 filter fails).
    if (rec.get("trend") or {}).get("in_uptrend") is False:
        add("no_uptrend", "Not in a confirmed uptrend", _W["no_uptrend"])

    # Thin liquidity (below the advisory turnover floor).
    if turnover is not None and turnover < settings.GATE_MIN_AVG_TURNOVER:
        add("thin_liq", f"Thin liquidity ({_fmt_turnover(turnover)})", _W["thin_liq"])

    # Earnings imminent.
    if days_to_earnings is not None and 0 <= days_to_earnings <= settings.GATE_EARNINGS_VETO_DAYS:
        d = days_to_earnings
        when = "today" if d == 0 else ("tomorrow" if d == 1 else f"in {d} days")
        add("earnings_soon", f"Earnings {when}", _W["earnings_soon"])

    return out


# --------------------------------------------------------------------------- #
# RSS conviction (root-sum-square edge vs risk) — DISPLAY ONLY
# --------------------------------------------------------------------------- #
def _rss(confirming, risk, rec) -> dict:
    edge = round(math.sqrt(sum(t["weight"] ** 2 for t in confirming)))
    rk = round(math.sqrt(sum(t["weight"] ** 2 for t in risk)))
    net = round(100 * (edge - rk) / (edge + rk + 1e-6))
    net = max(-100, min(100, net))

    # Confidence = evidence strength (heuristic). Grows with the amount of
    # confirming evidence and a proven track record; risk tokens temper it.
    hist = rec.get("history") or {}
    pb = _num(hist.get("past_breakouts")) or 0
    track_bonus = 2 if pb >= 3 else (1 if pb > 0 else 0)
    conf = round(1 + len(confirming) + track_bonus - 0.5 * len(risk))
    conf = max(0, min(10, conf))

    return {"edge": edge, "risk": rk, "net": net, "confidence": conf}


# --------------------------------------------------------------------------- #
# make-or-break (#3) — one deterministic, templated sentence. No LLM.
# --------------------------------------------------------------------------- #
def _make_or_break(rec) -> str:
    res = rec.get("resistance") or {}
    pivot = _num(res.get("level"))
    breakout = rec.get("breakout") or {}
    trend = rec.get("trend") or {}
    dist = _num(res.get("distance_pct"))
    p = _fmt_price(pivot) if pivot is not None else "the pivot"

    if breakout.get("today") or (dist is not None and dist > 0):
        # Already through: holding the reclaimed level on a close is the crux.
        return (f"Holds the {p} breakout level on a daily close — "
                f"a close back below it invalidates the move.")
    if not trend.get("in_uptrend", True):
        return (f"Reclaims a confirmed uptrend first — until then the {p} pivot "
                f"is the line to clear, but the setup isn't Stage-2 yet.")
    # Coiling below: clearing the pivot on volume is the deciding variable.
    return (f"Clears and closes above the {p} pivot on above-average volume — "
            f"that's the single trigger that turns this base into a breakout.")


# --------------------------------------------------------------------------- #
# advisory gates (#4) — pass / fail / na chips
# --------------------------------------------------------------------------- #
def _gates(rec, days_to_earnings, turnover) -> list[dict]:
    gates = []

    # 1) Liquidity floor (advisory).
    if turnover is None:
        gates.append({"key": "liquidity", "label": "Liquidity",
                      "pass": None, "detail": "n/a — volume unavailable"})
    else:
        ok = turnover >= settings.GATE_MIN_AVG_TURNOVER
        gates.append({"key": "liquidity", "label": "Avg $ volume", "pass": bool(ok),
                      "detail": f"{_fmt_turnover(turnover)}"
                                + ("" if ok else f" (below {_fmt_turnover(settings.GATE_MIN_AVG_TURNOVER)})")})

    # 2) Breakout-bar volume confirm — n/a until an actual breakout.
    breakout = rec.get("breakout") or {}
    vol = rec.get("volume") or {}
    ratio = _num(vol.get("ratio"))
    mult = settings.GATE_VOL_CONFIRM_MULT
    if not breakout.get("today"):
        gates.append({"key": "vol_confirm", "label": f"Breakout volume ≥{mult:g}×",
                      "pass": None, "detail": "n/a — not broken out"})
    else:
        ok = ratio is not None and ratio >= mult
        gates.append({"key": "vol_confirm", "label": f"Breakout volume ≥{mult:g}×",
                      "pass": bool(ok),
                      "detail": f"{ratio:.1f}× average" if ratio is not None else "volume unknown"})

    # 3) Earnings window veto (advisory).
    if days_to_earnings is None:
        gates.append({"key": "earnings", "label": "Earnings window",
                      "pass": None, "detail": "no scheduled earnings"})
    elif 0 <= days_to_earnings <= settings.GATE_EARNINGS_VETO_DAYS:
        d = days_to_earnings
        when = "today" if d == 0 else ("tomorrow" if d == 1 else f"in {d} days")
        gates.append({"key": "earnings", "label": "Earnings window",
                      "pass": False, "detail": f"reports {when}"})
    else:
        d = days_to_earnings
        detail = f"next report in {d} days" if d >= 0 else "no upcoming report"
        gates.append({"key": "earnings", "label": "Earnings window",
                      "pass": True, "detail": detail})

    return gates


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #
def build_rationale(rec: dict) -> dict:
    """Assemble the additive `rationale` block for one scan record.

    Pure + best-effort: reads only record fields + market-aware settings, makes no
    network calls, and degrades gracefully (empty lists / null gates) when inputs
    are missing. NEVER feeds scoring — this is a transparency layer only.
    """
    days_to_earnings = _days_to_earnings(rec)
    turnover = _avg_turnover(rec)

    confirming = _confirming(rec)
    risk = _risk(rec, days_to_earnings, turnover)

    return {
        "confirming": confirming,
        "risk": risk,
        "rss": _rss(confirming, risk, rec),
        "make_or_break": _make_or_break(rec),
        "gates": _gates(rec, days_to_earnings, turnover),
    }


# --------------------------------------------------------------------------- #
# backfill mode — regenerate `rationale` over an existing breakouts.json in place
# --------------------------------------------------------------------------- #
def _backfill(path: str) -> None:
    import json

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    stocks = data.get("stocks", [])
    ok = 0
    for rec in stocks:
        try:
            rec["rationale"] = build_rationale(rec)
            ok += 1
        except Exception as e:  # never let one bad record abort the backfill
            print(f"  skip {rec.get('symbol')}: {e}")
    # Preserve the compact serving format used by run_scan.py.
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"backfilled rationale for {ok}/{len(stocks)} stocks in {path} "
          f"(market={settings.MARKET})")


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: BREAKOUTAI_MARKET=IN|US python backend/signals.py <breakouts.json>")
        raise SystemExit(2)
    _backfill(sys.argv[1])
