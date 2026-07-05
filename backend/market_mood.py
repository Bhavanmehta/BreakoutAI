"""
Market-wide "mood" gauge (0-100 fear/greed regime index) -- NOT a per-stock signal, a
single top-of-page number reflecting today's overall market backdrop. Runs INSIDE
run_scan.py (see settings.py's docstring for why, and why this differs from the
separate holdings/sectors/fundamentals/news/social scripts).

Four independent components, equally weighted so no single wobbly source dominates.
Any one that fails to fetch is dropped and the rest reweight proportionally (same
graceful-degradation pattern as holdings/news/social -- a stock's card never breaks
just because one enrichment source had a bad day):

  trend    -- Nifty 50 close vs its MOOD_TREND_SMA_WINDOW-day SMA. A market running
              well above its own recent average reads as "greed" (and vice versa),
              independent of any single stock's own setup.
  vix      -- India VIX (^INDIAVIX via yfinance), inverted -- low implied volatility
              reads as calm/greedy, a VIX spike reads as fear.
  fii_flow -- today's NSE-published FII/FPI net equity flow (Rs cr), z-scored against
              its own trailing MOOD_FII_ROLLING_DAYS history (persisted in
              data/fii_dii_history.json) -- "are foreign investors buying more or
              less than their own recent norm", not an absolute threshold, since what
              counts as a "big" flow day varies with market conditions.
  breadth  -- % of today's whole scanned universe that closed up, i.e. classic
              advance/decline breadth. We already scan ~1,800 stocks daily for the
              breakout search, so this reuses that instead of fetching separate NSE
              sector indices.

NSE's FII/DII endpoint (nseindia.com/api/fiidiiTradeReact) is India's only free source
for daily *market-wide* aggregate flow -- per-stock daily FII/DII is not public
anywhere (see CLAUDE.md); this is a genuinely different, coarser data point from the
quarterly per-stock holdings in holdings.json.
"""
from __future__ import annotations
import json

import pandas as pd
import requests

import settings

_NSE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/reports/fii-dii",
}


def fetch_fii_dii_today() -> dict | None:
    """Latest published market-wide net FII/FPI and DII equity flow (Rs cr), or None
    if NSE's endpoint is unreachable/blocked -- best-effort, never fatal to the scan."""
    try:
        s = requests.Session()
        s.get("https://www.nseindia.com", headers=_NSE_HEADERS, timeout=10)
        resp = s.get("https://www.nseindia.com/api/fiidiiTradeReact", headers=_NSE_HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        rows = resp.json() or []
    except (requests.RequestException, ValueError):
        return None

    out = {"date": None, "fii_net_cr": None, "dii_net_cr": None}
    for r in rows:
        try:
            net = float(r["netValue"])
        except (KeyError, TypeError, ValueError):
            continue
        category = (r.get("category") or "").upper()
        if category.startswith("FII"):
            out["fii_net_cr"] = net
        elif category == "DII":
            out["dii_net_cr"] = net
        out["date"] = r.get("date") or out["date"]
    if out["fii_net_cr"] is None and out["dii_net_cr"] is None:
        return None
    return out


def _load_fii_dii_history() -> list[dict]:
    if settings.FII_DII_HISTORY_JSON.exists():
        with open(settings.FII_DII_HISTORY_JSON, encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_fii_dii_history(history: list[dict]):
    with open(settings.FII_DII_HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, separators=(",", ":"))


def update_fii_dii_history(today: dict | None) -> list[dict]:
    """Append today's flow (if fetched) to the small persisted rolling history, dedup by
    date, capped to FII_DII_HISTORY_DAYS. Returns the updated history (oldest first)."""
    history = _load_fii_dii_history()
    if today and today.get("date"):
        history = [h for h in history if h["date"] != today["date"]]
        history.append(today)
    history.sort(key=lambda h: h["date"])
    history = history[-settings.FII_DII_HISTORY_DAYS:]
    _save_fii_dii_history(history)
    return history


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _trend_score(nifty: pd.DataFrame | None) -> float | None:
    if nifty is None or len(nifty) < settings.MOOD_TREND_SMA_WINDOW:
        return None
    closes = nifty["bm_close"]
    sma = closes.rolling(settings.MOOD_TREND_SMA_WINDOW).mean().iloc[-1]
    last = closes.iloc[-1]
    if not sma:
        return None
    dist_pct = (last - sma) / sma * 100.0
    clamp = settings.MOOD_TREND_CLAMP_PCT
    return _clamp((dist_pct + clamp) / (2 * clamp) * 100, 0, 100)


def _vix_score(vix: pd.DataFrame | None) -> float | None:
    if vix is None or len(vix) == 0:
        return None
    level = vix["bm_close"].iloc[-1]
    calm, panic = settings.MOOD_VIX_CALM, settings.MOOD_VIX_PANIC
    return _clamp((panic - level) / (panic - calm) * 100, 0, 100)


def _fii_flow_score(history: list[dict]) -> float | None:
    window = [h["fii_net_cr"] for h in history[-settings.MOOD_FII_ROLLING_DAYS:]
              if h.get("fii_net_cr") is not None]
    if len(window) < 5:   # need a minimally meaningful sample before z-scoring
        return None
    s = pd.Series(window)
    std = s.std()
    if not std or pd.isna(std):
        return None
    z = _clamp((s.iloc[-1] - s.mean()) / std, -settings.MOOD_FII_CLAMP_Z, settings.MOOD_FII_CLAMP_Z)
    return (z + settings.MOOD_FII_CLAMP_Z) / (2 * settings.MOOD_FII_CLAMP_Z) * 100


def _breadth_score(summaries: list[dict]) -> float | None:
    changes = [s["change_pct"] for s in summaries if s.get("change_pct") is not None]
    if not changes:
        return None
    advancers = sum(1 for c in changes if c > 0)
    return advancers / len(changes) * 100


def _label(score: float) -> str:
    if score < 20:
        return "Extreme Fear"
    if score < 40:
        return "Fear"
    if score < 60:
        return "Neutral"
    if score < 80:
        return "Greed"
    return "Extreme Greed"


def compute_market_mood(nifty: pd.DataFrame | None, vix: pd.DataFrame | None,
                         summaries: list[dict], fii_today: dict | None) -> dict:
    """The single market-wide 0-100 mood score + its component breakdown. Any missing
    component is dropped and the rest reweight proportionally (equal weights among
    whatever's available) rather than the whole gauge going blank."""
    history = update_fii_dii_history(fii_today)
    components = {
        "trend": _trend_score(nifty),
        "vix": _vix_score(vix),
        "fii_flow": _fii_flow_score(history),
        "breadth": _breadth_score(summaries),
    }
    available = {k: v for k, v in components.items() if v is not None}
    rounded_components = {k: (round(v, 1) if v is not None else None) for k, v in components.items()}
    if not available:
        return {"score": None, "label": None, "components": rounded_components, "fii_dii_today": fii_today}
    score = round(sum(available.values()) / len(available), 1)
    return {"score": score, "label": _label(score), "components": rounded_components,
            "fii_dii_today": fii_today}
