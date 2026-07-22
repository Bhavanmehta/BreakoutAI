"""
Ticker -> Dhan security_id resolver for NSE cash equities.

The daily scan speaks in bare NSE symbols ("RELIANCE", "TATASTEEL", ...). Dhan's
historical API speaks in numeric security ids ("2885", "3499", ...). This module
builds and caches the {SYMBOL: security_id} map that bridges the two.

Source of truth is Dhan's detailed scrip master. We prefer a local dev copy
(dhan_ironcondor/master_sample.csv, gitignored) and fall back to downloading the
public CSV so it also works on CI where the local copy is absent. The parsed map
is cached as a small JSON (backend/.dhan_scrip_map.json) so we only pay the parse
cost once every few days.

Relevant scrip-master columns (detailed format):
    EXCH_ID, SEGMENT, SECURITY_ID, INSTRUMENT, UNDERLYING_SYMBOL, SERIES, ...
NSE cash equity == EXCH_ID "NSE" + SEGMENT "E" + INSTRUMENT "EQUITY".
"""
from __future__ import annotations
import csv
import io
import json
import os
import time
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_MAP_PATH = _HERE / ".dhan_scrip_map.json"
_LOCAL_CSV = _REPO / "dhan_ironcondor" / "master_sample.csv"
_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
_MAX_AGE_DAYS = float(os.environ.get("DHAN_SCRIP_MAX_AGE_DAYS", "7"))

_CACHE: dict[str, str] | None = None   # in-memory, per process


def _fresh(path: Path) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < _MAX_AGE_DAYS * 86400


def _iter_master_rows():
    """Yield dict rows from Dhan's detailed scrip master.

    Uses the local dev copy when present, otherwise downloads the public CSV.
    """
    if _LOCAL_CSV.exists():
        with _LOCAL_CSV.open("r", encoding="utf-8", errors="replace", newline="") as f:
            yield from csv.DictReader(f)
        return
    req = urllib.request.Request(_MASTER_URL, headers={"User-Agent": "BreakoutAI/1.0"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    yield from csv.DictReader(io.StringIO(text))


def _build_map() -> dict[str, str]:
    m: dict[str, str] = {}
    for row in _iter_master_rows():
        if not (row.get("EXCH_ID") == "NSE"
                and row.get("SEGMENT") == "E"
                and row.get("INSTRUMENT") == "EQUITY"):
            continue
        sym = (row.get("UNDERLYING_SYMBOL") or "").strip().upper()
        sec = (row.get("SECURITY_ID") or "").strip()
        if not sym or not sec:
            continue
        # If a symbol appears more than once, prefer the plain EQ series.
        if sym in m and (row.get("SERIES") or "").strip().upper() != "EQ":
            continue
        m[sym] = sec
    return m


def _load() -> dict[str, str]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if _fresh(_MAP_PATH):
        try:
            data = json.loads(_MAP_PATH.read_text())
            if data:
                _CACHE = data
                return _CACHE
        except Exception:
            pass
    _CACHE = _build_map()
    try:
        _MAP_PATH.write_text(json.dumps(_CACHE))
    except Exception:
        pass
    return _CACHE


def resolve_security_id(symbol: str) -> str | None:
    """Return the Dhan security_id for an NSE symbol, or None if unknown."""
    if not symbol:
        return None
    return _load().get(symbol.strip().upper())


def map_size() -> int:
    return len(_load())


if __name__ == "__main__":
    import sys
    m = _load()
    print(f"scrip map: {len(m)} NSE equities "
          f"(source: {'local CSV' if _LOCAL_CSV.exists() else 'Dhan download'})")
    for s in (sys.argv[1:] or ["RELIANCE", "TATASTEEL", "NESTLEIND", "TCS", "INFY"]):
        print(f"  {s:<14} -> {resolve_security_id(s)}")
