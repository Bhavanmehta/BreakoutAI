"""
Standalone: populate data/delivery.json with per-stock NSE delivery-% — a cheap
"realness" confirm on a breakout.

"Delivery %" (DELIV_PER in NSE's *full* bhavcopy) is the share of a day's traded
quantity that was actually taken to demat (held overnight) rather than squared off
intraday. A HIGH delivery-% on the breakout bar means buyers are HOLDING — genuine
accumulation, not day-trade churn — so it's a useful transparency token beside the
breakout read. It is NEVER a ranker input (score.py); it only surfaces in the
rationale layer (signals.py), matching the roadmap's decorative-first discipline.

Unlike holdings/sectors (which fetch per-symbol), delivery comes from ONE whole-market
file per trading day, so this is cheap: ~30 requests total (one per trading day in the
lookback window), computing latest + trailing-average delivery-% for every symbol at
once. Not resumable per-symbol (there's nothing to resume — each day is one file); it
simply recomputes the rolling window fresh each run. If NSE is unreachable / rate-
limiting and too few days come back, it leaves any existing delivery.json UNTOUCHED
rather than clobbering good data with a thin window.

IN-ONLY: NSE publishes DELIV_PER; there is no free US equivalent (the consolidated
tape doesn't expose held-vs-churned), so on a US run this is a no-op.

Usage:
    python fetch_delivery.py            # default DELIVERY_LOOKBACK_DAYS trading days
    python fetch_delivery.py 15         # just the last 15 trading days
    python fetch_delivery.py --check    # run the CSV-parse self-check (no network)
"""
from __future__ import annotations
import csv
import io
import json
import sys
import time
from datetime import date, timedelta

import settings


def parse_delivery(raw: str, series_ok=("EQ",)) -> dict[str, float]:
    """Parse one full-bhavcopy CSV -> {SYMBOL: delivery_pct} for the given series.

    NSE's *full* bhavcopy (sec_bhavdata_full) is quirky: the header names carry leading
    spaces (" SERIES", " DELIV_PER"), values are space-padded, and rows with no delivery
    figure use "-". Keys and values are stripped, non-numeric / "-" rows are dropped, and
    only the requested series (EQ = rolling-settlement equity, the breakout universe) is
    kept — so the output is clean {plain SYMBOL: float %}."""
    out: dict[str, float] = {}
    for row in csv.DictReader(io.StringIO(raw)):
        r = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        if r.get("SERIES") not in series_ok:
            continue
        sym, pct = r.get("SYMBOL"), r.get("DELIV_PER")
        if not sym or not pct or pct == "-":
            continue
        try:
            out[sym] = float(pct)
        except ValueError:
            continue
    return out


def _collect(lookback_days: int, max_calendar: int):
    """Walk backward from today, fetching full bhavcopy for each trading day until we
    have `lookback_days` of them. Returns (series, days): series maps SYMBOL -> list of
    delivery-% MOST-RECENT-FIRST; days is the list of trading dates collected."""
    from jugaad_data.nse import full_bhavcopy_raw

    series: dict[str, list[float]] = {}
    days: list[date] = []
    for delta in range(max_calendar):
        if len(days) >= lookback_days:
            break
        d = date.today() - timedelta(days=delta)
        if d.weekday() >= 5:        # skip weekends without spending a request
            continue
        try:
            raw = full_bhavcopy_raw(d)
        except Exception:
            continue                # holiday / not-yet-published / transient — just walk on
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        day = parse_delivery(raw)
        if not day:
            continue                # empty / unexpected format for this date
        days.append(d)
        for sym, pct in day.items():
            series.setdefault(sym, []).append(pct)
        print(f"  {d.isoformat()} | {len(day)} symbols | {len(days)}/{lookback_days} days")
        time.sleep(0.4)             # be polite to NSE
    return series, days


def _save(data: dict):
    payload = dict(sorted(data.items()))
    with open(settings.DELIVERY_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))


def build(lookback_days: int | None = None):
    if not settings.HAS_DELIVERY:
        print(f"[delivery] MARKET={settings.MARKET}: delivery-% is IN-only (no free US "
              "equivalent); nothing to fetch.")
        return {}

    lookback_days = lookback_days or settings.DELIVERY_LOOKBACK_DAYS
    print(f"[delivery] fetching up to {lookback_days} trading days of NSE full bhavcopy...")
    series, days = _collect(lookback_days, lookback_days * 2 + 12)

    # Don't clobber a good cache with a thin one: if NSE rate-limited us down to a
    # handful of days, the trailing averages would be meaningless — bail, keep the file.
    if len(days) < settings.DELIVERY_MIN_DAYS:
        print(f"[delivery] only {len(days)} trading day(s) fetched (< "
              f"{settings.DELIVERY_MIN_DAYS} needed); NSE likely unreachable / rate-"
              "limiting — leaving existing delivery.json untouched.")
        return None

    as_of = days[0].isoformat()
    out: dict[str, dict] = {}
    for sym, vals in series.items():
        if len(vals) < settings.DELIVERY_MIN_DAYS:
            continue                # too few observations to trust an average
        out[sym] = {
            "latest": round(vals[0], 1),                 # most recent trading day
            "avg": round(sum(vals) / len(vals), 1),      # trailing average over the window
            "days": len(vals),
            "as_of": as_of,
        }
    _save(out)
    print(f"[delivery] {len(days)} trading days -> delivery.json with {len(out)} symbols "
          f"(as_of {as_of}).")
    return out


def _selfcheck():
    """No-network check of the tricky bit: NSE's space-padded headers, "-" delivery rows,
    and non-EQ series are all handled so only clean EQ rows survive."""
    sample = (
        "SYMBOL, SERIES, DATE1, PREV_CLOSE, DELIV_QTY, DELIV_PER\n"
        "RELIANCE, EQ, 01-Jan-2026, 100, 5000,  62.50\n"   # normal EQ row
        "ILLIQ, BE, 01-Jan-2026, 50, 10,  99.00\n"          # non-EQ series -> dropped
        "NODLV, EQ, 01-Jan-2026, 10, 0,  -\n"               # '-' delivery -> dropped
        "TCS, EQ, 01-Jan-2026, 200, 3000,  71.20\n"
    )
    got = parse_delivery(sample)
    assert got == {"RELIANCE": 62.5, "TCS": 71.2}, got
    print("fetch_delivery self-check OK:", got)


if __name__ == "__main__":
    if "--check" in sys.argv:
        _selfcheck()
    else:
        lim = next((int(a) for a in sys.argv[1:] if a.isdigit()), None)
        build(lim)
