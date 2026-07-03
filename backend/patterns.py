"""
Chart-pattern detection (real geometry, not hand-typed labels).

Approach: find swing pivots (local highs/lows), then match each pattern's
geometry with conservative rules. When nothing matches well we say so
("No clear pattern") rather than forcing a label.

These are heuristic approximations of how analysts eyeball charts — useful
signals, not guarantees. Each detector returns a confidence in [0, 1].

Patterns covered:
  * Ascending Triangle   (bullish continuation)
  * Cup & Handle         (bullish accumulation)
  * Double Bottom        (bullish reversal)
  * Head & Shoulders     (bearish reversal)
  * fallbacks: Tight Consolidation / No clear pattern
"""
from __future__ import annotations
import numpy as np


def find_pivots(highs, lows, k=5):
    """A bar is a pivot high if its high is the max within +/-k bars (and
    similarly for pivot lows). Returns two lists of integer indices."""
    n = len(highs)
    ph, pl = [], []
    for i in range(k, n - k):
        if highs[i] >= highs[i - k:i + k + 1].max():
            ph.append(i)
        if lows[i] <= lows[i - k:i + k + 1].min():
            pl.append(i)
    return ph, pl


def _pct(a, b):
    return (a - b) / b * 100.0


def detect_pattern(df, lookback=140):
    """Return {name, confidence, direction, description} for the most recent
    window of a stock's price history."""
    d = df.tail(lookback).reset_index(drop=True)
    n = len(d)
    if n < 40:
        return {"name": "Insufficient data", "confidence": 0.0, "direction": "neutral", "description": ""}

    highs = d["high"].values.astype(float)
    lows = d["low"].values.astype(float)
    closes = d["close"].values.astype(float)
    ph, pl = find_pivots(highs, lows, k=5)

    candidates = []

    # --- Ascending Triangle: flat resistance + rising lows ---
    if len(ph) >= 2 and len(pl) >= 2:
        rec_highs = [highs[i] for i in ph[-3:]]
        rec_lows = [lows[i] for i in pl[-3:]]
        flat_top = (max(rec_highs) - min(rec_highs)) / np.mean(rec_highs) * 100 < 3.5
        rising_lows = all(rec_lows[j] < rec_lows[j + 1] for j in range(len(rec_lows) - 1))
        if flat_top and rising_lows and len(rec_lows) >= 2:
            candidates.append(("Ascending Triangle", 0.72, "bullish",
                "Price keeps hitting a flat resistance while carving higher lows — buyers stepping in earlier each time. A bullish coil that often resolves upward."))

    # --- Cup & Handle: two rims at a similar level, rounded low between, small recent handle ---
    if len(ph) >= 2:
        best_cup = None
        for a in range(len(ph)):
            for b in range(a + 1, len(ph)):
                li, ri = ph[a], ph[b]
                if ri - li < 25:          # cup should span several weeks
                    continue
                left_rim, right_rim = highs[li], highs[ri]
                if abs(_pct(right_rim, left_rim)) > 5:   # rims roughly level
                    continue
                bottom_idx = li + int(np.argmin(lows[li:ri + 1]))
                depth = _pct((left_rim + right_rim) / 2, lows[bottom_idx])
                if not (10 <= depth <= 50):              # sensible cup depth
                    continue
                centred = 0.3 < (bottom_idx - li) / (ri - li) < 0.7
                handle = ri < n - 3                       # some action after the right rim
                if centred and handle:
                    # shallower recent pullback = handle
                    handle_dip = _pct(right_rim, lows[ri:].min())
                    if handle_dip < depth * 0.6:
                        best_cup = 0.68
        if best_cup:
            candidates.append(("Cup & Handle", best_cup, "bullish",
                "A rounded recovery back toward prior highs, now pausing in a shallow handle — classic accumulation before a breakout."))

    # --- Double Bottom: two lows at a similar level with a peak between, price recovering ---
    if len(pl) >= 2:
        l1, l2 = pl[-2], pl[-1]
        if l2 - l1 >= 15 and abs(_pct(lows[l2], lows[l1])) < 4:
            mid_peak = highs[l1:l2 + 1].max()
            if _pct(mid_peak, max(lows[l1], lows[l2])) > 6 and closes[-1] > lows[l2]:
                candidates.append(("Double Bottom", 0.6, "bullish",
                    "Two lows at a similar level with a bounce between — sellers failed twice at the same price. A reversal setup if it clears the middle peak."))

    # --- Head & Shoulders: three peaks, middle highest, shoulders ~level (bearish) ---
    if len(ph) >= 3:
        ls, hd, rs = ph[-3], ph[-2], ph[-1]
        if highs[hd] > highs[ls] and highs[hd] > highs[rs] and abs(_pct(highs[rs], highs[ls])) < 5:
            candidates.append(("Head & Shoulders", 0.6, "bearish",
                "Three peaks with a higher middle and level shoulders — a classic topping pattern that warns of a downside reversal."))

    if candidates:
        candidates.sort(key=lambda c: -c[1])
        name, conf, direction, desc = candidates[0]
        return {"name": name, "confidence": round(conf, 2), "direction": direction, "description": desc}

    # --- Fallbacks ---
    rng = _pct(highs[-20:].max(), lows[-20:].min())
    if rng < 12:
        return {"name": "Tight Consolidation", "confidence": 0.4, "direction": "neutral",
                "description": "Trading in a tight range — coiling into a base, but no named pattern has formed yet."}
    return {"name": "No clear pattern", "confidence": 0.0, "direction": "neutral",
            "description": "No recognizable chart pattern right now."}
