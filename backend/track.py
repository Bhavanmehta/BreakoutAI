"""
Track record — the forward test.

Every run we log the current call for each stock to an append-only file
(data/predictions_log.jsonl). Then we evaluate past calls against what the
price actually did afterwards, and write a rolling scorecard (data/track_record.json).

Why this matters: the per-stock "follow-through rate" in breakouts.json is a
*backtest* (measured on the same history the rules were tuned on). This track
record instead grades calls going *forward* — which is the honest way to build
confidence before opening it to beta users.

An "actionable" call = readiness is on-watch (primed / approaching). It counts as
"worked" if price hit +1R before -1R (stop) within FOLLOWTHROUGH_WINDOW trading days
of the call — R = entry - stop, where stop = resistance * STOP_LOSS_FRACTION, the same
stop shown in the live entry guidance (see find_breakouts.add_indicators).

On the very first run the log is empty, so we seed it with a walk-forward
simulation of the last SEED_DAYS trading days (each day's signal uses only data up
to that day). Those are marked source="simulated"; real daily calls are "live".
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

import settings

LOG_PATH = settings.DATA_DIR / "predictions_log.jsonl"
TRACK_PATH = settings.DATA_DIR / "track_record.json"
HORIZON = settings.FOLLOWTHROUGH_WINDOW
STOP_LOSS_FRACTION = settings.STOP_LOSS_FRACTION
SEED_DAYS = 90


def _assess_row(row):
    """Mirror of the live readiness rule, computed from one feature row.
    Returns (watch, score, sentiment, in_uptrend)."""
    in_uptrend = bool(row["uptrend"])
    close = float(row["close"])
    res = row["resistance"]
    near = (res == res) and res and (0 <= (res - close) / close * 100 <= 3)  # within 3% below
    breakout = bool(row["is_breakout"])
    coiling = (row["vol_contraction"] == row["vol_contraction"]) and row["vol_contraction"] < 1
    if breakout:
        watch, score = True, "high"
    elif in_uptrend and near and coiling:
        watch, score = True, "high"
    elif in_uptrend and near:
        watch, score = True, "medium"
    else:
        watch, score = False, "low"
    if close > row["ema50"] and close > row["ema200"]:
        sentiment = "Bullish"
    elif close < row["ema50"] and close < row["ema200"]:
        sentiment = "Bearish"
    else:
        sentiment = "Neutral"
    return watch, score, sentiment, in_uptrend


def _evaluate(feat, call_date, entry_price):
    """Did price hit +1R before -1R (stop) within HORIZON trading days after call_date?
    R = entry_price - stop, where stop = settings.stop_from(resistance-at-call-date,
    10-day ATR-at-call-date) — mirrors the live entry guidance and
    find_breakouts.add_indicators exactly.
    Returns True/False, or None if pending (not enough forward data yet) or the risk
    isn't well-defined for this call (no resistance level yet)."""
    dates = feat["date"].dt.strftime("%Y-%m-%d").tolist()
    if call_date not in dates:
        return None
    idx = dates.index(call_date)
    if idx + HORIZON >= len(feat):
        return None  # still pending
    res = feat["resistance"].iloc[idx]
    if res != res or not res:  # NaN or 0
        return None
    atr = feat["atr_short"].iloc[idx] if "atr_short" in feat.columns else None
    stop = settings.stop_from(res, atr)
    if stop is None:
        return None
    risk = entry_price - stop
    if risk <= 0:
        return None
    target = entry_price + risk
    highs = feat["high"].values
    lows = feat["low"].values
    for j in range(idx + 1, idx + 1 + HORIZON):
        if lows[j] <= stop:
            return False
        if highs[j] >= target:
            return True
    return False


def _load_log():
    if not LOG_PATH.exists():
        return []
    out = []
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _save_log(rows):
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def update_and_evaluate(feat_by_symbol, summaries, as_of_date):
    log = _load_log()
    seen = {(r["date"], r["symbol"]) for r in log}

    # --- One-time seed: walk-forward simulation of recent history ---
    if not log:
        for sym, feat in feat_by_symbol.items():
            recent = feat.dropna(subset=["ema200"]).tail(SEED_DAYS)
            for _, row in recent.iterrows():
                d = row["date"].strftime("%Y-%m-%d")
                if d == as_of_date:
                    continue  # today is added live below
                watch, score, sentiment, up = _assess_row(row)
                log.append({"date": d, "symbol": sym, "price": round(float(row["close"]), 2),
                            "score": score, "watch": watch, "sentiment": sentiment,
                            "in_uptrend": up, "pattern": None, "source": "simulated"})
        seen = {(r["date"], r["symbol"]) for r in log}

    # --- Today's live calls (upsert) ---
    for s in summaries:
        key = (as_of_date, s["symbol"])
        rec = {"date": as_of_date, "symbol": s["symbol"], "price": s["price"],
               "score": s["readiness"]["score"], "watch": s["readiness"]["watch"],
               "sentiment": s["breakout"]["sentiment"], "in_uptrend": s["trend"]["in_uptrend"],
               "pattern": s["pattern"]["name"], "source": "live"}
        if key in seen:
            log = [rec if (r["date"], r["symbol"]) == key else r for r in log]
        else:
            log.append(rec)
            seen.add(key)

    _save_log(log)

    # --- Evaluate: grade each on-watch EPISODE once (the first day it turns
    #     actionable), so a stock lingering near resistance for weeks isn't
    #     counted as many separate calls. ---
    by_sym = {}
    for r in log:
        by_sym.setdefault(r["symbol"], []).append(r)

    evaluated = worked = pending = 0
    recent_scored = []
    for sym, rows in by_sym.items():
        rows.sort(key=lambda x: x["date"])
        feat = feat_by_symbol.get(sym)
        prev_watch = False
        for r in rows:
            is_episode_start = bool(r.get("watch")) and not prev_watch
            prev_watch = bool(r.get("watch"))
            if not is_episode_start:
                continue
            outcome = _evaluate(feat, r["date"], r["price"]) if feat is not None else None
            if outcome is None:
                pending += 1
                continue
            evaluated += 1
            worked += 1 if outcome else 0
            recent_scored.append({"date": r["date"], "symbol": sym, "price": r["price"],
                                  "score": r["score"], "source": r["source"], "worked": outcome})

    recent_scored.sort(key=lambda x: x["date"], reverse=True)
    live_logged = sum(1 for r in log if r.get("source") == "live")

    track = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "horizon_days": HORIZON,
        "stop_loss_fraction": STOP_LOSS_FRACTION,   # legacy/fallback; see stop_model
        "stop_model": settings.STOP_MODEL_DESC,
        "criterion": f"hit +1R (risk-defined target) before the stop, within {HORIZON} trading days",
        "actionable_evaluated": evaluated,
        "followed_through": worked,
        "hit_rate": round(worked / evaluated, 3) if evaluated else None,
        "pending": pending,
        "live_calls_logged": live_logged,
        "note": ("Includes a walk-forward simulation of recent history (source=simulated); "
                 "live daily calls accumulate from now on."),
        "recent": recent_scored[:20],
    }
    with open(TRACK_PATH, "w", encoding="utf-8") as f:
        json.dump(track, f, indent=2, ensure_ascii=False)
    return track
