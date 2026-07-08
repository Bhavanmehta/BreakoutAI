"""
Export compact per-stock OHLC (+ EMA/RSI/volume/resistance/breakout overlays) for the
frontend's annotated chart (lightweight-charts). Reads the `ohlcv_features` table the
scan already populates and writes one small JSON per symbol to
`data/ohlc/<safe-symbol>.json`.

Why per-stock files: the frontend fetches only the open stock's series on demand, so
the annotated candles + our resistance line / EMAs / volume / RSI / breakout markers
can be drawn without a backend — same static, committed-data model as breakouts.json.
See CLAUDE.md TODO #8 (chart migration). Committed daily; the git-growth mitigation is
TODO #4 (this file grew from ~12 KB avg at 150 bars/2 EMAs to a fair bit more once
volume/ema8/ema21/rsi were added at 220 bars — still small per-fetch, just worth
knowing if the daily commit size becomes a problem).

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
from methods import latest_vcp_structure

# ~10 months of daily context -- enough to give the 200-day EMA real runway to show its
# own trend (not just a flat-looking tail) without bloating each file too much further.
BARS = 220
OHLC_DIR = settings.DATA_DIR / "ohlc"

# Columns pulled from ohlcv_features. ema8/ema21/ema50/ema200 are all drawn as chart
# overlays (ema21/ema50 also feed the swing-pivot + dynamic-support detection in
# levels.py); volume backs the volume histogram pane; rsi backs the RSI pane;
# is_breakout marks the days we flag with a marker.
_COLS = ["symbol", "date", "open", "high", "low", "close", "volume",
         "ema8", "ema21", "ema50", "ema200", "rsi", "is_breakout"]


# Windows reserves these device names as filenames -- any case, any extension
# (CON.json included). A real US ticker "CON" produced data/us/ohlc/CON.json,
# which breaks `git checkout` on stock Windows machines if it's ever committed.
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL",
                 *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _safe(sym: str) -> str:
    """Filesystem/URL-safe symbol → filename stem (e.g. 'M&M' → 'M_M', 'BAJAJ-AUTO' →
    'BAJAJ_AUTO'; reserved Windows device names get a trailing '_': 'CON' → 'CON_').
    The frontend applies the identical transform when fetching."""
    stem = re.sub(r"[^A-Za-z0-9]", "_", sym)
    if stem.upper() in _WIN_RESERVED:
        stem += "_"
    return stem


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


def _vcp_obj(g: pd.DataFrame, drawn_start: int) -> dict | None:
    """Most recent qualifying VCP contraction → the compact object the chart draws a
    zigzag from: {"points": [[date, price], ...], "pivot": level, "confirmed": date|None}.
    Points interleave pivot highs and leg troughs (high, low, high, low, ..., high) so
    the frontend can draw the contraction legs directly. Omitted (None) when no window
    qualifies or when the base starts before the drawn BARS window (a partial zigzag
    would be misleading)."""
    s = latest_vcp_structure(g)
    if not s or s["pivots"][0] < drawn_start:
        return None
    dates = g["date"].tolist()
    highs = g["high"].tolist()
    lows = g["low"].tolist()

    def _pt(pos: int, price: float) -> list:
        return [pd.Timestamp(dates[pos]).strftime("%Y-%m-%d"), _r(price)]

    points = []
    for i, p in enumerate(s["pivots"]):
        points.append(_pt(p, highs[p]))
        if i < len(s["troughs"]):
            points.append(_pt(s["troughs"][i], lows[s["troughs"][i]]))
    return {
        "points": points,
        "pivot": _r(s["pivot_level"]),
        "confirmed": (pd.Timestamp(dates[s["confirmed"]]).strftime("%Y-%m-%d")
                      if s["confirmed"] is not None else None),
    }


def _emit_one(sym: str, g: pd.DataFrame) -> dict:
    g = g.sort_values("date")
    # Detect levels on the full available history (pivots need the context), then tail
    # to BARS for the drawn candles.
    levels = resolve_display_levels(g)
    vcp = _vcp_obj(g.reset_index(drop=True), max(0, len(g) - BARS))
    g = g.tail(BARS)
    bars, volume, ema8, ema21, ema50, ema200, rsi, breakouts = [], [], [], [], [], [], [], []
    for _, row in g.iterrows():
        d = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
        bars.append([d, _r(row["open"]), _r(row["high"]), _r(row["low"]), _r(row["close"])])
        volume.append(None if pd.isna(row.get("volume")) else int(row["volume"]))
        ema8.append(_r(row.get("ema8")))
        ema21.append(_r(row.get("ema21")))
        ema50.append(_r(row.get("ema50")))
        ema200.append(_r(row.get("ema200")))
        rsi.append(_r(row.get("rsi")))
        if bool(row.get("is_breakout")):
            breakouts.append(d)
    return {
        "symbol": sym,
        "as_of": bars[-1][0] if bars else None,
        "resistance": _line(levels["resistance"]),
        "support": _line(levels["support"]),
        "bars": bars,
        "volume": volume,
        "ema8": ema8,
        "ema21": ema21,
        "ema50": ema50,
        "ema200": ema200,
        "rsi": rsi,
        "breakouts": breakouts,
        "vcp": vcp,
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
