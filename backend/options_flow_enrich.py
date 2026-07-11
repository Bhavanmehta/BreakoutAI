"""
Standalone research prototype: join options_flow_scan.py's output (unusual single-leg
options activity, from Polygon.io's free tier) against this repo's own breakouts.json
(technical setup / conviction scoring from run_scan.py) to surface the one signal
neither dataset can produce alone: "this stock has unusual options size on it AND is
already a live, validated technical breakout candidate."

WHY THIS IS THE ACTUAL EDGE OVER A PAID DISCORD ALERT BOT: Bullflow/Unusual Whales sell
real-time sweep classification (bullish/bearish, exact timestamp) -- see the module
docstring in options_flow_scan.py for why that's not free-tier-reproducible. What they
do NOT sell is a cross-reference against YOUR conviction/readiness/pattern/RS pipeline,
because they don't have it. A stock flagged for large options prints that is *also*
sitting at conviction >= 70 with a confirmed uptrend is a materially different, more
actionable signal than either fact alone -- and it costs nothing beyond what this repo
already computes daily in run_scan.py.

This script does NOT invent a bullish/bearish call from the options data (still can't --
same free-tier limitation as options_flow_scan.py). It combines "unusual size present"
(options_flow_research.json) with "technical setup quality" (breakouts.json) into one
ranked confluence list.

Usage:
    python options_flow_scan.py META AMZN NVDA   # 1. populate options_flow_research.json
    python options_flow_enrich.py                # 2. join it against breakouts.json
    python options_flow_enrich.py --demo          # or: see the output shape using
                                                   #     real breakouts.json entries +
                                                   #     clearly-labeled sample flow data,
                                                   #     when you don't have a POLYGON_API_KEY yet
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import settings

ENRICHED_JSON = settings.DATA_DIR / "options_flow_enriched.json"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _breakouts_by_symbol(breakouts: dict) -> dict:
    return {s["symbol"]: s for s in breakouts.get("stocks", []) if s.get("symbol")}


def _confluence_score(flags: list[str], stock: dict, call_put: str = "Call") -> tuple[int, list[str]]:
    """0-100ish score + human-readable reasons. Weighted toward the technical side,
    since that's the part we can actually stand behind with real methodology; the
    options side only ever contributes a fixed bonus for "size was unusual", not a
    directional call."""
    if not stock:
        return 0, ["no technical data for this ticker in breakouts.json"]

    readiness = stock.get("readiness", {}) or {}
    trend = stock.get("trend", {}) or {}
    breakout = stock.get("breakout", {}) or {}
    pattern = stock.get("pattern", {}) or {}

    conviction = readiness.get("conviction") or 0
    score = conviction  # base: this repo's own 0-100 conviction score
    reasons = [f"conviction {conviction}/100 ({readiness.get('label', 'n/a')})"]

    is_call = call_put == "Call"

    if breakout.get("today"):
        score += 15 if is_call else -15
        reasons.append("breaking out today" + ("" if is_call else " -- but this is a PUT, i.e. betting against the breakout"))
    if trend.get("in_uptrend"):
        score += 10 if is_call else -10
        reasons.append("confirmed uptrend" + ("" if is_call else " -- PUT is contrarian to the uptrend"))

    pattern_dir = pattern.get("direction")
    pattern_name = pattern.get("name")
    aligned = (pattern_dir == "bullish" and is_call) or (pattern_dir == "bearish" and not is_call)
    conflicted = (pattern_dir == "bullish" and not is_call) or (pattern_dir == "bearish" and is_call)
    if pattern_name != "No clear pattern" and aligned:
        score += 5
        reasons.append(f"{call_put} aligns with {pattern_dir} pattern ({pattern_name})")
    elif pattern_name != "No clear pattern" and conflicted:
        score -= 15
        reasons.append(f"{call_put} CONFLICTS with {pattern_dir} pattern ({pattern_name})")

    if "high_notional" in flags:
        score += 8
        reasons.append("unusually large $ notional in options")
    if "large_avg_trade_size" in flags:
        score += 7
        reasons.append("unusually large average options trade size")

    return max(0, min(100, score)), reasons


def enrich(flow: dict, breakouts: dict) -> list[dict]:
    by_symbol = _breakouts_by_symbol(breakouts)
    out = []
    for f in flow.get("flagged", []):
        stock = by_symbol.get(f["ticker"], {})
        conf_score, reasons = _confluence_score(f["flag_reasons"], stock, f["call_put"])
        out.append({
            "ticker": f["ticker"],
            "option_ticker": f["option_ticker"],
            "call_put": f["call_put"],
            "strike": f["strike"],
            "expiration": f["expiration"],
            "notional_est": f["notional_est"],
            "avg_trade_size": f["avg_trade_size"],
            "options_flag_reasons": f["flag_reasons"],
            "confluence_score": conf_score,
            "confluence_reasons": reasons,
            "price": stock.get("price"),
            "sector": stock.get("sector"),
            "conviction": (stock.get("readiness") or {}).get("conviction"),
            "in_uptrend": (stock.get("trend") or {}).get("in_uptrend"),
            "pattern": (stock.get("pattern") or {}).get("name"),
            "make_or_break": (stock.get("rationale") or {}).get("make_or_break"),
        })
    out.sort(key=lambda r: r["confluence_score"], reverse=True)
    return out


def _demo_flow(breakouts: dict) -> dict:
    """Sample options-flow numbers layered onto REAL breakouts.json entries (real
    conviction/trend/pattern data from this repo's own last scan), so the shape and
    ranking logic can be inspected without a POLYGON_API_KEY / live run. The options
    numbers themselves (volume/vwap/notional) are illustrative, not fetched -- clearly
    marked below and in the printed output."""
    by_symbol = _breakouts_by_symbol(breakouts)
    picks = [
        ("AVR", "call", 0.03, "2026-08-21", 4200, 26.10, 187),
        ("MRNA", "call", 0.02, "2026-08-21", 6100, 41.85, 240),
        ("DWSN", "call", 0.05, "2026-09-18", 1800, 8.40, 95),
        ("TENB", "put", -0.04, "2026-08-21", 3300, 33.20, 160),
    ]
    flagged = []
    for ticker, cp, moneyness, exp, volume, vwap, transactions in picks:
        stock = by_symbol.get(ticker)
        if not stock:
            continue
        spot = stock.get("price", 100)
        strike = round(spot * (1 + moneyness), 2)
        avg_trade_size = round(volume / transactions, 1)
        notional = round(volume * vwap * 100, 2)
        reasons = []
        if notional >= settings.OPTIONS_FLOW_MIN_NOTIONAL:
            reasons.append("high_notional")
        if avg_trade_size >= settings.OPTIONS_FLOW_MIN_AVG_TRADE_SIZE:
            reasons.append("large_avg_trade_size")
        flagged.append({
            "ticker": ticker,
            "option_ticker": f"O:{ticker}260821{'C' if cp == 'call' else 'P'}{int(strike*1000):08d}",
            "call_put": "Call" if cp == "call" else "Put",
            "strike": strike,
            "expiration": exp,
            "date": breakouts.get("as_of_date"),
            "volume": volume,
            "transactions": transactions,
            "vwap": vwap,
            "avg_trade_size": avg_trade_size,
            "notional_est": notional,
            "flag_reasons": reasons or ["demo_sample"],
        })
    return {"date": breakouts.get("as_of_date"), "generated_at": "DEMO -- not a live Polygon pull",
            "flagged": flagged}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--demo", action="store_true",
                         help="Use illustrative sample options-flow numbers on real "
                              "breakouts.json tickers instead of a real options_flow_research.json "
                              "(use when you don't have POLYGON_API_KEY set up yet)")
    args = parser.parse_args()

    breakouts = _load_json(settings.BREAKOUTS_JSON)
    if not breakouts:
        raise SystemExit(f"{settings.BREAKOUTS_JSON} not found -- run run_scan.py first.")

    if args.demo:
        flow = _demo_flow(breakouts)
        print("*** DEMO MODE: options-flow numbers below are illustrative samples, NOT "
              "a real Polygon pull. Technical/conviction data is real, from your last "
              f"breakouts.json (as_of {breakouts.get('as_of_date')}). ***\n")
    else:
        flow = _load_json(settings.OPTIONS_FLOW_JSON)
        if not flow:
            raise SystemExit(f"{settings.OPTIONS_FLOW_JSON} not found -- run "
                              "options_flow_scan.py first, or pass --demo.")

    ranked = enrich(flow, breakouts)

    ENRICHED_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(ENRICHED_JSON, "w", encoding="utf-8") as f:
        json.dump({"date": flow.get("date"), "demo": args.demo, "ranked": ranked}, f,
                   ensure_ascii=False, indent=2)

    for r in ranked:
        print(f"[{r['confluence_score']:>3}] {r['ticker']:<6} {r['call_put']:<4} "
              f"${r['strike']} exp {r['expiration']}  notional~${r['notional_est']:,.0f}")
        for reason in r["confluence_reasons"]:
            print(f"        - {reason}")
    print(f"\n{len(ranked)} rows written to {ENRICHED_JSON}")


if __name__ == "__main__":
    main()
