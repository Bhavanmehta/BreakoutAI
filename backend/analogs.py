"""
Historical-analog engine — "when has this stock looked like it does today, and what
happened next?"

The idea (what makes "The Read" impactful): a stock's setup is a shape in feature
space — where price sits relative to its EMA stack, how tight the range has coiled,
how far it is from its 52-week high, how strong the trend is, how close resistance is.
For today's bar we find the single most *geometrically similar* past bar ON THE SAME
STOCK and report how price actually moved in the days after it. Not a promise — a
grounded precedent: "today most resembles 18 Jan 2023 (87% similar); that instance ran
+22% over the next 20 days."

We reuse the columns add_indicators() already computes — no new indicators — and
z-score each feature across the stock's own history so different scales are comparable.
Cheap: one vectorized distance sweep per stock.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

import settings

# The feature vector describing "what the setup looks like" on a given bar. Every
# component is scale-free (a ratio or an index), then z-scored below so no single
# feature dominates the distance purely because of its units.
def _feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"]
    feats = pd.DataFrame(index=df.index)
    # EMA-stack geometry: where price sits vs each average, and the spacing between them.
    feats["px_vs_ema8"] = (close - df["ema8"]) / close
    feats["ema8_vs_21"] = (df["ema8"] - df["ema21"]) / close
    feats["ema21_vs_50"] = (df["ema21"] - df["ema50"]) / close
    feats["ema50_vs_200"] = (df["ema50"] - df["ema200"]) / close
    # Coil / trend / location context.
    feats["vol_contraction"] = df["vol_contraction"]
    feats["dist_52w"] = df["dist_from_52w_high"]
    feats["adx"] = df["adx"]
    feats["dist_resistance"] = (df["resistance"] - close) / close
    return feats


def detect_analog(df: pd.DataFrame,
                  max_z_distance: float = 2.5,
                  min_history: int | None = None) -> dict | None:
    """Find today's closest historical analog on this stock and report its forward
    outcome. Returns None when history is too short or nothing matches closely enough.

    max_z_distance : reject the best match if it's still farther than this (in the
        z-scored feature space) — better to show nothing than a bad "precedent".
    """
    if min_history is None:
        min_history = settings.MIN_HISTORY_BARS
    if len(df) < min_history:
        return None

    feats = _feature_frame(df)
    # Standardize each feature across this stock's own history.
    mu = feats.mean()
    sd = feats.std(ddof=0).replace(0, np.nan)
    z = (feats - mu) / sd

    n = len(df)
    complete = z.notna().all(axis=1).values
    # "Today" = the most recent bar with a full feature vector. yfinance's single-symbol
    # path keeps the current still-forming session as a trailing NaN-close row, so we
    # can't assume the last row is usable — find the last complete one.
    today_idxs = np.flatnonzero(complete)
    if today_idxs.size == 0:
        return None
    today_idx = int(today_idxs[-1])
    today = z.iloc[today_idx]

    # A candidate past bar must have (a) all features present and (b) a full forward
    # runway so its outcome is actually known. Also exclude the most recent stretch so
    # the "analog" doesn't just echo the last couple of weeks (overlapping window).
    max_fwd = max(settings.FORWARD_WINDOWS)
    exclude_recent = settings.FOLLOWTHROUGH_WINDOW * 2
    valid = complete.copy()
    # need a full forward window ahead, and drop the recent overlap + today itself
    cutoff = today_idx - max(max_fwd, exclude_recent)
    valid[cutoff + 1:] = False
    if not valid.any():
        return None

    diff = z.values - today.values  # broadcast today across all rows
    dist = np.sqrt(np.nansum(diff * diff, axis=1))
    dist[~valid] = np.inf
    best = int(np.argmin(dist))
    if not np.isfinite(dist[best]) or dist[best] > max_z_distance * np.sqrt(z.shape[1]):
        return None

    ev = df.iloc[best]
    # similarity: map z-distance -> (0,1]; identical shape -> 1, farther -> lower.
    similarity = round(float(1.0 / (1.0 + dist[best])), 3)

    def _pct(col):
        v = ev.get(col)
        return round(float(v) * 100, 1) if pd.notna(v) else None

    worked = ev.get("followthrough")
    return {
        "date": ev["date"].strftime("%Y-%m-%d"),
        "similarity": similarity,
        "then_price": round(float(ev["close"]), 2),
        "fwd_5d_pct": _pct("fwd_ret_5d"),
        "fwd_10d_pct": _pct("fwd_ret_10d"),
        "fwd_20d_pct": _pct("fwd_ret_20d"),
        "worked": bool(worked) if worked in (True, False) else None,
    }


if __name__ == "__main__":
    # Quick manual check against a few names.
    from get_prices import get_prices
    from find_breakouts import add_indicators
    for sym in ["RELIANCE", "TCS", "CGPOWER"]:
        prices = get_prices(sym)
        if prices is None:
            print(f"{sym:10s} -> no data")
            continue
        a = detect_analog(add_indicators(prices))
        print(f"{sym:10s} -> {a}")
