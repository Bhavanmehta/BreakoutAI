"""
Export compact per-stock OHLC (+ EMA/resistance/breakout overlays) for the frontend's
annotated chart (lightweight-charts). Reads the `ohlcv_features` table the scan already
populates and writes one small JSON per symbol to `data/ohlc/<safe-symbol>.json`.

Why per-stock files: the frontend fetches only the open stock's series on demand (~3–5 KB),
so the annotated candles + our resistance line / EMAs / breakout markers can be drawn
without a backend — same static, committed-data model as breakouts.json. See CLAUDE.md
TODO #8 (chart migration). Committed daily; the git-growth mitigation is TODO #4.

Usage:
    python export_ohlc.py                 # standalone: reads the DuckDB
    export_ohlc.export_from_frame(df)     # from run_scan: reuse the in-memory features
"""
from __future__ import annotations
import json
import re
import time

import pandas as pd

import settings
from levels import resolve_display_levels

# ~7 months of daily context — enough to show the base and the breakout without bloating
# each file. Bump if the charts feel too short.
BARS = 150
OHLC_DIR = settings.DATA_DIR / "ohlc"

# Columns pulled from ohlcv_features. ema50/ema200 are the two overlays we draw; ema21 +
# volume feed the swing-pivot support/resistance detection (levels.py); is_breakout marks
# the days we flag with a marker.
_COLS = ["symbol", "date", "open", "high", "low", "close", "volume",
         "ema21", "ema50", "ema200", "is_breakout"]


def _safe(sym: str) -> str:
    """Filesystem/URL-safe symbol → filename stem (e.g. 'M&M' → 'M_M', 'BAJAJ-AUTO' →
    'BAJAJ_AUTO'). The frontend applies the identical transform when fetching."""
    return re.sub(r"[^A-Za-z0-9]", "_", sym)


def _r(x):
    return None if x is None or pd.isna(x) else round(float(x), 2)


def _line(zone: dict | None) -> dict | None:
    """A support/resistance zone → the compact object the chart draws a horizontal
    line from. Only *horizontal* zones become a drawn line; a 'dynamic' support (a
    rising EMA) is already visible as the EMA overlay, so we don't stamp a static
    line at its momentary value — return None and let the card explain it in words."""
    if not zone or zone.get("kind") != "horizontal":
        return None
    return {"level": zone["level"], "touches": zone.get("touches"),
            "confirmed": bool(zone.get("confirmed"))}


def _emit_one(sym: str, g: pd.DataFrame) -> dict:
    g = g.sort_values("date")
    # Detect levels on the full available history (pivots need the context), then tail
    # to BARS for the drawn candles.
    levels = resolve_display_levels(g)
    g = g.tail(BARS)
    bars, ema50, ema200, breakouts = [], [], [], []
    for _, row in g.iterrows():
        d = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
        bars.append([d, _r(row["open"]), _r(row["high"]), _r(row["low"]), _r(row["close"])])
        ema50.append(_r(row.get("ema50")))
        ema200.append(_r(row.get("ema200")))
        if bool(row.get("is_breakout")):
            breakouts.append(d)
    return {
        "symbol": sym,
        "as_of": bars[-1][0] if bars else None,
        "resistance": _line(levels["resistance"]),
        "support": _line(levels["support"]),
        "bars": bars,
        "ema50": ema50,
        "ema200": ema200,
        "breakouts": breakouts,
    }


def export_from_frame(features_df: pd.DataFrame) -> int:
    """Write one OHLC file per symbol from an in-memory features frame (full history is
    fine — we tail the last BARS per symbol). Returns the number of files written."""
    OHLC_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for sym, g in features_df.groupby("symbol"):
        data = _emit_one(sym, g)
        if not data["bars"]:
            continue
        with open(OHLC_DIR / f"{_safe(sym)}.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        n += 1
    return n


def export_from_duckdb() -> int:
    """Standalone path: read the last BARS bars per symbol straight from the DuckDB. Done
    as one small query per symbol (a single full-table window query over ~1.3M rows can
    blow DuckDB's memory budget and abort with INTERRUPT)."""
    import duckdb
    con = duckdb.connect(str(settings.DUCKDB_PATH), read_only=True)
    symbols = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM ohlcv_features").fetchall()]
    OHLC_DIR.mkdir(parents=True, exist_ok=True)
    # Pull enough bars for swing-pivot level detection (SR_LOOKBACK), even though only the
    # last BARS are drawn — _emit_one detects levels on the full slice, then tails to BARS.
    fetch = max(BARS, settings.SR_LOOKBACK)
    q = (f"SELECT {', '.join(_COLS)} FROM ohlcv_features WHERE symbol = ? "
         f"ORDER BY date DESC LIMIT {fetch}")
    n = failed = 0
    for sym in symbols:
        try:
            g = con.execute(q, [sym]).df()
        except Exception as e:   # a corrupted on-disk segment shouldn't abort the whole export
            failed += 1
            continue
        if g.empty:
            continue
        data = _emit_one(sym, g)
        if not data["bars"]:
            continue
        with open(OHLC_DIR / f"{_safe(sym)}.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        n += 1
    con.close()
    if failed:
        print(f"  (skipped {failed} symbols with unreadable/corrupted DuckDB segments)")
    return n


if __name__ == "__main__":
    t0 = time.time()
    count = export_from_duckdb()
    print(f"Wrote {count} OHLC files to {OHLC_DIR.relative_to(settings.REPO_DIR)} in {time.time()-t0:.1f}s")
