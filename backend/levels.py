"""
Support / resistance zone detection via swing-pivot clustering.

Why this exists: the internal breakout engine uses a rolling N-day high/low
(settings.LOOKBACK_HIGH) as its trigger — that's fine as a mechanical, backtested
signal, but it's a poor thing to *draw on a chart*. It fires on a single touch and
picks whatever the extreme happened to be, so the "support" it reports can be a
months-old low the stock never revisited (a huge, meaningless gap below price).

This module instead follows the trader-standard method (see Humble Trader's
"how to draw support & resistance" — the 3-point rule):

  1. Find swing pivots — local highs/lows where price actually turned.
  2. Cluster pivots that sit at a similar price into one horizontal *zone*
     (old resistance becomes support after a break, so highs and lows are pooled).
  3. Rank each zone by how many times price touched it (the 3-point rule),
     weighted by the volume on those touches and how recent they are.
  4. Report the nearest *validated* zone above price (resistance) and below it
     (support), each with its touch count and a 0..1 strength.

A zone that price has reversed at repeatedly, on volume, is a real level. When no
multi-touch zone exists on a side (e.g. a stock at fresh highs has nothing
overhead), that side is returned as None rather than inventing a number.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

import settings
from patterns import find_pivots


def _cluster(points: list[dict], tol_pct: float) -> list[list[dict]]:
    """Merge price points that sit within tol_pct of a cluster's anchor into one
    zone. `points` must be sorted by price ascending; the anchor is the lowest
    member so members only ever extend upward within tolerance (no runaway drift
    from greedy neighbour-chaining)."""
    clusters: list[dict] = []
    for p in points:
        for c in clusters:
            if abs(p["price"] - c["anchor"]) / c["anchor"] * 100 <= tol_pct:
                c["members"].append(p)
                break
        else:
            clusters.append({"anchor": p["price"], "members": [p]})
    return [c["members"] for c in clusters]


def _zone(members: list[dict]) -> dict:
    """Collapse a cluster of pivot touches into one reported level."""
    prices = np.array([m["price"] for m in members], dtype=float)
    weights = np.array([m["vol_ratio"] for m in members], dtype=float)
    # Volume-weight the level toward the touches that mattered (high-volume reversals).
    level = float(np.average(prices, weights=weights)) if weights.sum() > 0 else float(prices.mean())
    touches = len(members)
    vol_strength = float(np.mean([m["vol_ratio"] for m in members]))
    recency = float(max(m["recency"] for m in members))  # 0..1, 1 = most recent
    # Strength in 0..1: touches dominate (the 3-point rule), then volume, then recency.
    strength = (min(touches / settings.SR_STRONG_TOUCHES, 1.0) * 0.6
                + min(vol_strength / 2.0, 1.0) * 0.25
                + recency * 0.15)
    return {
        "level": round(level, 2),
        "touches": touches,
        "strength": round(strength, 2),
        "confirmed": touches >= settings.SR_STRONG_TOUCHES,
        "recency": round(recency, 2),
        "kind": "horizontal",
    }


def detect_levels(df: pd.DataFrame,
                  lookback: int | None = None,
                  k: int | None = None,
                  tol_pct: float | None = None,
                  min_touches: int | None = None) -> dict:
    """Return {"resistance": zone|None, "support": zone|None, "method": str} for
    the latest bar of `df`. A "zone" is {level, touches, strength, confirmed,
    recency}. resistance = nearest validated zone above price; support = nearest
    validated zone below price."""
    lookback = lookback or settings.SR_LOOKBACK
    k = k or settings.SR_PIVOT_K
    tol_pct = tol_pct if tol_pct is not None else settings.SR_CLUSTER_TOL_PCT
    min_touches = min_touches or settings.SR_MIN_TOUCHES

    method = ("swing-pivot clustering (3-point rule, volume-weighted) over "
              f"~{lookback} trading days")
    d = df.tail(lookback).reset_index(drop=True)
    n = len(d)
    if n < 40:
        return {"resistance": None, "support": None, "method": method}

    highs = d["high"].values.astype(float)
    lows = d["low"].values.astype(float)
    vols = d["volume"].values.astype(float)
    price = float(d["close"].values[-1])
    avg_vol = float(np.nanmean(vols)) or 1.0

    ph, pl = find_pivots(highs, lows, k=k)

    points: list[dict] = []
    for i in ph:
        points.append({"price": float(highs[i]),
                       "vol_ratio": float(vols[i] / avg_vol),
                       "recency": i / (n - 1)})
    for i in pl:
        points.append({"price": float(lows[i]),
                       "vol_ratio": float(vols[i] / avg_vol),
                       "recency": i / (n - 1)})
    if not points:
        return {"resistance": None, "support": None, "method": method}

    points.sort(key=lambda p: p["price"])
    zones = [_zone(members) for members in _cluster(points, tol_pct)]

    # Nearest *validated* (>= min_touches) zone on each side of the current price,
    # within SR_MAX_DISTANCE_PCT — a level the stock hasn't been near in months is
    # not an actionable line (that runaway case is handled by resolve_display_levels'
    # dynamic-EMA fallback). A small dead-band avoids reporting a level price sits on.
    near = settings.SR_MAX_DISTANCE_PCT
    res_cands = [z for z in zones if price * 1.001 < z["level"] <= price * (1 + near / 100)
                 and z["touches"] >= min_touches]
    sup_cands = [z for z in zones if price * (1 - near / 100) <= z["level"] < price * 0.999
                 and z["touches"] >= min_touches]
    resistance = min(res_cands, key=lambda z: z["level"]) if res_cands else None
    support = max(sup_cands, key=lambda z: z["level"]) if sup_cands else None

    for z in (resistance, support):
        if z is not None:
            z["distance_pct"] = round((z["level"] / price - 1) * 100, 2)

    return {"resistance": resistance, "support": support, "method": method}


def _ema_dynamic_support(df: pd.DataFrame, price: float) -> dict | None:
    """When a trending stock has run away from all horizontal structure, the level
    it would fall back to is its rising moving average, not a months-old swing low.
    Return the nearest *rising* EMA sitting below price (50-day preferred, else
    21-day) as a 'dynamic' support, or None if neither qualifies."""
    latest = df.iloc[-1]
    best = None
    for w in (50, 21):
        col = f"ema{w}"
        if col not in df.columns:
            continue
        series = df[col].dropna()
        if len(series) < 7 or pd.isna(latest[col]):
            continue
        val = float(latest[col])
        rising = val > float(series.iloc[-6])           # higher than ~a week ago
        if val < price * 0.999 and rising:
            # nearest (highest) qualifying EMA below price
            if best is None or val > best[0]:
                best = (val, w)
    if best is None:
        return None
    val, w = best
    return {
        "level": round(val, 2),
        "kind": "dynamic",
        "label": f"rising {w}-day EMA",
        "distance_pct": round((val / price - 1) * 100, 2),
        "touches": None,
        "strength": None,
        "confirmed": False,
    }


def resolve_display_levels(df: pd.DataFrame) -> dict:
    """The single source of truth for the support/resistance the *user sees* (chart
    lines + Key Levels card). Prefers a validated nearby horizontal zone; when no
    nearby horizontal support exists (a stock extended above its structure), falls
    back to the rising-EMA dynamic support. Resistance stays None when there's
    genuinely nothing overhead (a stock at fresh highs) — that's the honest read,
    not a number to invent."""
    sr = detect_levels(df)
    price = float(df["close"].dropna().values[-1])
    resistance = sr["resistance"]
    support = sr["support"]
    if support is None:
        support = _ema_dynamic_support(df, price)
    return {"resistance": resistance, "support": support, "method": sr["method"]}


if __name__ == "__main__":
    # Tiny self-test: a synthetic series that reverses repeatedly at 100 (resistance)
    # and 80 (support), currently sitting at 90, should recover both zones.
    import numpy as _np
    rng = _np.random.default_rng(0)
    seg = []
    for _ in range(6):
        seg += [82, 88, 95, 99, 96, 90, 84, 81, 85, 92, 98, 100, 97, 91, 86, 80]
    close = _np.array(seg, dtype=float)
    high = close + 1.0
    low = close - 1.0
    vol = rng.integers(1000, 2000, size=len(close)).astype(float)
    # force the reversal bars to look high-volume
    df = pd.DataFrame({"high": high, "low": low, "close": close, "volume": vol})
    df.loc[len(df) - 1, "close"] = 90.0
    out = detect_levels(df, lookback=len(df), k=2, tol_pct=2.5, min_touches=2)
    print("resistance:", out["resistance"])
    print("support:   ", out["support"])
    print("method:    ", out["method"])
