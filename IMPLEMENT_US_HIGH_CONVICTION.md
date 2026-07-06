# Implementation spec: US high-conviction setup tiers

Audience: an implementing model/engineer working in this repo. Everything here was
researched and validated 2026-07-05/06 (session 9, this branch: `feature/us-market`).
Your job is ONLY to wire the two validated tiers below into the US scan + frontend and
regenerate `data/us/breakouts.json`. **Do not re-derive, re-tune, or "improve" any
threshold** — every number was chosen on a 60% train split and validated once on the
held-out 40% of stocks; changing thresholds invalidates the published hit rates.

## 0. Context — what is already DONE (do not redo, do not revert)

The conviction-score recalibration for the US is **already implemented and verified**
in the working tree (uncommitted):

- `backend/settings.py` — score-calibration block (`SCORE_BASE_RATE` 0.27 US / 0.39 IN,
  `SCORE_W_REL/W_DEPTH/W_METHOD` = 0.30/0.70/0.00 US vs 0.60/0.25/0.15 IN,
  `SCORE_Q_RANGE`, `RELIABILITY_CAUTION_BELOW`/`RELIABILITY_GOOD_AT`).
- `backend/score.py` — reads those settings (was hardcoded India values).
- `backend/find_breakouts.py` — `_reliability_note()` uses the market-aware thresholds
  (+ a US-only "above the ~27% market average" suffix on the Reliable branch).
- **India output was verified bit-identical** across a 160-case behavior capture.
  Nothing you do may change India (`BREAKOUTAI_MARKET` unset/`IN`) behavior.

Validated backtest facts your UI copy will cite (whole-US-market replay, 20,814
Method-A events, 4,166 stocks, 3 years, grading = price hits +1R before -1R within 10
trading days, R = entry − resistance×0.94, i.e. a strict 1:1 reward:risk bet where 50%
is breakeven; US base rate **26.7%**):

| Tier | Rule (all conditions AND) | Full sample | Held-out stocks | Cadence |
|---|---|---|---|---|
| 1 `high_conviction` | squeeze-breakout today + A-breakout ≤5 bars ago + ATR≥4.5% + close ≤ +3% over trigger + liquidity floor | **51.1%** (n=190, 176 stocks) | **52.0%** (n=75) | ~6/mo market-wide |
| 2 `strong_breakout` | A-breakout today + ATR≥4.5% + liquidity floor | **45.3%** (n=3,215) | **46.2%** (n=1,321, 486 stocks) | ~97/mo |
| (context) top conviction-score decile | — | — | 43.4% | — |
| (context) all US A-breakouts | — | 26.7% | 26.8% | — |

Liquidity floor (user-specified): 20-day avg volume ≥ 100,000 shares AND price ≥ $1.
Deliberately NO market-cap / $5-price filter — the user wants small/cheap names kept.

## 1. `backend/settings.py` — add the tier thresholds

Add directly below the score-calibration block (which ends with `RELIABILITY_GOOD_AT`):

```python
# --- US high-conviction setup tiers (find_breakouts.build_summary; validated
# 2026-07-06 on a train/test split of the whole-market 3y replay — see
# IMPLEMENT_US_HIGH_CONVICTION.md for the numbers each threshold carries).
# NOT validated on India data — do not enable for IN without rerunning the backtest.
HC_ENABLED = MARKET == "US"
HC_ATR_MIN_PCT = 4.5             # 10-day ATR must be >= this % of price ("enough energy")
HC_EXT_MAX_PCT = 3.0             # tier-1 only: close <= this % above the 50d resistance
HC_COFIRE_BARS = 5               # tier-1 only: Method-A breakout within the last N bars (incl today)
HC_MIN_AVG_VOL_SHARES = 100_000  # 20-day avg volume floor (user-chosen; keeps small caps)
HC_MIN_PRICE = 1.0
```

## 2. `backend/run_scan.py` — compute the squeeze column for US

`add_method_c_squeeze` lives in `backend/methods.py` (line ~82), signature
`add_method_c_squeeze(df) -> df`, self-contained (needs only close/volume columns; adds
`is_breakout_c` + `bb_width`). It is currently research-only; promote it for US:

- Import it alongside the existing methods imports.
- In the per-stock loop, right after `add_method_e2_relative_strength_uptrend(feat)`:

```python
if settings.HC_ENABLED:
    feat = add_method_c_squeeze(feat)
```

Do NOT call `add_all_methods()` (that would compute the unshipped B/D/F for every
stock). Do NOT compute it for India (the gate above handles that).

## 3. `backend/find_breakouts.py` — the tier logic in `build_summary`

Insert AFTER `readiness.setdefault("signal", None)` and BEFORE the reliability-note
block (so the note and conviction pick the new signal up):

```python
# --- US high-conviction tiers (train/test-validated; IMPLEMENT_US_HIGH_CONVICTION.md).
# Tier 1: a volatility squeeze releasing into a confirmed breakout, bought near the
# trigger, in a name with enough daily range to plausibly move +-1R inside the 10-day
# grading window, above the liquidity floor. Historically ~51% follow-through (n=190)
# vs the 26.7% US base. Tier 2: today's Method-A breakout with the same energy + floor
# gates (~46%, n=3,215). Both tiers deliberately reuse already-computed columns.
if settings.HC_ENABLED:
    atr_v = float(latest["atr_short"]) if pd.notna(latest["atr_short"]) else None
    atr_pct = (atr_v / price * 100) if atr_v and price else None
    ext_pct = (price / resistance - 1) * 100 if resistance else None
    a_recent = bool(df["is_breakout"].tail(settings.HC_COFIRE_BARS).any())
    liquid = (avg_vol is not None and avg_vol >= settings.HC_MIN_AVG_VOL_SHARES
              and price >= settings.HC_MIN_PRICE)
    energetic = atr_pct is not None and atr_pct >= settings.HC_ATR_MIN_PCT
    if (bool(latest.get("is_breakout_c", False)) and a_recent and liquid and energetic
            and ext_pct is not None and ext_pct <= settings.HC_EXT_MAX_PCT):
        readiness.update({"label": "High-conviction setup — volatility squeeze released "
                                    "into a confirmed breakout, entry still near the trigger",
                          "watch": True, "score": "high", "signal": "high_conviction"})
    elif broke_out_today and liquid and energetic:
        readiness["signal"] = "strong_breakout"   # label stays "Breaking out now"
```

Notes:
- `atr_short` is the existing 10-day TR mean (shift(1)) column — exactly the ATR the
  backtest used. `avg_vol` (20-day, shift(1)) and `resistance` already exist above.
- `ext_pct` may be negative (close below resistance) — that's allowed; only the upper
  bound is gated. This matches the backtest.
- Tier 1 may fire on a day that is not itself a Method-A breakout (the A-fire can be
  up to 4 bars earlier); it overrides whatever rung the ladder chose, including
  `relative_strength` — intended.
- The existing reliability-note `elif readiness["watch"]:` branch will give both new
  signals the Method-A history note — correct, leave as is.

Then, AFTER `readiness["conviction"] = conviction(...)` is computed, add:

```python
    # Rank floors, not probabilities: held-out hit rates are 52% (tier 1) and 46%
    # (tier 2) vs 43% for the score's own top decile — a badge stock must outrank
    # any pure-score stock. max() keeps ordering within each tier quality-driven.
    if readiness["signal"] == "high_conviction":
        readiness["conviction"] = max(readiness["conviction"], 90)
    elif readiness["signal"] == "strong_breakout":
        readiness["conviction"] = max(readiness["conviction"], 80)
```

(Wrap both snippets under the same `if settings.HC_ENABLED:` scope or guard with it —
`readiness["signal"]` can only take these values when it's on.)

## 4. Frontend — `combined_breakout_scanner_platform.html`

The sort/filter/color/Sector-Radar logic all key off `readiness.score`/`watch` only
(verified in an earlier session) — no changes needed there. Two additions:

1. **`verdictExplainer` branches** (where `signal === "relative_strength"` is already
   special-cased; add these BEFORE the generic `score === "high"` case):
   - `high_conviction`: "A rare, backtested combination: this stock's volatility had
     compressed to multi-month lows (a squeeze), and it's now releasing — a breakout
     through resistance confirmed it within the last week, price is still within 3% of
     the trigger, and the stock moves enough (daily range ≥4.5%) for the target to be
     reachable. Across 190 such setups in the last 3 years, ~51% hit +1R before the
     stop — about double the 27% US average. The strongest signal this scanner
     produces; still roughly a coin-flip on a 1:1 risk/reward, not a guarantee."
   - `strong_breakout`: "Today's breakout has real energy behind it: the stock cleared
     its 50-day high on heavy volume and its daily range (≥4.5% of price) makes the
     +1R target realistically reachable within days. Setups like this followed through
     ~46% of the time historically (n≈3,200) vs the 27% US market average."
2. **A badge chip** on the detail header + watchlist rows for `high_conviction` only
   (e.g. a small amber/gold "★ High conviction" pill next to the conviction number),
   reusing the site's existing pill styling. `strong_breakout` needs no chip — its
   conviction floor (80) and explainer are enough; don't crowd the UI.

Keep styling consistent with the existing design language (Tailwind classes already in
the file); no new fonts/colors beyond an accent for the badge.

## 5. Acceptance tests (run all before regenerating)

1. **India regression** — `cd backend; python -c "import find_breakouts, settings;
   print(settings.MARKET, settings.HC_ENABLED)"` must print `IN False`. Then run a
   10-stock India `build_summary` smoke (fetch via `get_prices`) and confirm no
   `high_conviction`/`strong_breakout` signals appear and conviction values are
   unchanged from before your edit (spot-check 2-3 stocks against the current
   committed `data/breakouts.json`).
2. **US replay acceptance** — the strongest check: replay history from the US DuckDB
   and confirm the tier-1 rule reproduces the backtest. Skeleton:

```python
import os, sys
os.environ["BREAKOUTAI_MARKET"] = "US"
sys.path.insert(0, r"...\backend")
import duckdb, numpy as np, pandas as pd, settings
from find_breakouts import add_indicators
from methods import add_method_c_squeeze
con = duckdb.connect(str(settings.DUCKDB_PATH), read_only=True)
px = con.execute("SELECT date, open, high, low, close, volume, symbol FROM ohlcv_daily").df()
con.close()
px["date"] = pd.to_datetime(px["date"]).dt.tz_localize(None).dt.normalize()
n_ev = n_hit = 0
for sym, g in px.groupby("symbol"):
    f = add_indicators(g.sort_values("date").reset_index(drop=True))
    f = add_method_c_squeeze(f)
    atr_pct = f["atr_short"] / f["close"] * 100
    ext = (f["close"] / f["resistance"] - 1) * 100
    a_recent = f["is_breakout"].rolling(settings.HC_COFIRE_BARS).max().astype(bool)
    tier1 = (f["is_breakout_c"] & a_recent & (atr_pct >= 4.5) & (ext <= 3)
             & (f["avg_vol"] >= 1e5) & (f["close"] >= 1) & f["followthrough"].notna())
    # 10-bar cooldown dedup so one squeeze doesn't count as several trials
    idx = np.flatnonzero(tier1.values); last = -99
    for i in idx:
        if i - last > 10:
            n_ev += 1; n_hit += bool(f["followthrough"].iloc[i]); last = i
print(n_ev, n_hit / n_ev)
```

   **Pass criteria: n_ev in 170–280 and hit rate ≥ 0.47.** (The backtest measured
   n=190 / 51.1%; the production flag uses raw A-fires in the 5-bar window where the
   backtest used deduped events, so slightly more events is expected.) If n_ev is
   hundreds+ or the hit rate is near 27%, a gate is wired wrong — stop and compare
   each condition's daily counts against this doc.
3. **US scan smoke** — run `build_summary` on ~10 US symbols from the DuckDB and
   confirm: signals appear only where all gates hold; badge stocks get conviction ≥90.
   On any given day expect **0–3 tier-1 stocks market-wide** (it's ~6/month) — zero on
   the latest day is normal, not a bug; the replay in step 2 is the real check.

## 6. Regenerate `data/us/breakouts.json`

`cd backend`, set `BREAKOUTAI_MARKET=US`, run `python run_scan.py`. If yfinance
rate-limits the batch fetch (it did once tonight), rerun feeding prices from the
DuckDB instead — monkeypatch before `run_scan.run()`:

```python
import run_scan, duckdb, pandas as pd, settings
con = duckdb.connect(str(settings.DUCKDB_PATH), read_only=True)
prices = con.execute("SELECT date, open, high, low, close, volume, symbol FROM ohlcv_daily").df()
con.close()
prices["date"] = pd.to_datetime(prices["date"]).dt.tz_localize(None).dt.normalize()
by_sym = {s: g.sort_values("date").reset_index(drop=True) for s, g in prices.groupby("symbol")}
run_scan.fetch_prices_yfinance_batch = lambda syms, **kw: {s: by_sym[s] for s in syms if s in by_sym}
run_scan.run()
```

(`track.py` replaces same-`(date,symbol)` rows — rerunning the same day is safe.)
Then serve the site locally (`python -m http.server 8000` from the repo root, with
the US market toggle) and verify in the browser: explainer text, badge chip, sort
order (badge stocks on top), zero console errors.

## 7. Do NOT

- Do not change `STOP_LOSS_FRACTION`, `FOLLOWTHROUGH_WINDOW`, or the grading rule —
  every published number above is defined against them.
- Do not enable the tiers for India, and do not alter the score-calibration block.
- Do not add extra filters (market cap, $5 price, sectors) — explicitly rejected by
  the user; the only floors are 100k shares / $1.
- Do not "clean up" `methods.py`'s other research methods into production.
- Do not commit to `main` — this branch (`feature/us-market`) only; the user decides
  when to merge.

## 8. Known honest caveats (leave discoverable for future sessions)

- The most recent half-year (2026H1) ran ~43-46% for tier 1 — still ~+17pt over base,
  but the edge breathes with regime; revisit after a few live months.
- Part of both tiers' edge is the ATR gate selecting names volatile enough to resolve
  the ±1R band inside 10 days at all (51.7% of all US A-events resolve NEITHER side —
  the fixed ~6%-of-resistance stop is small relative to typical US volatility). Under
  a volatility-neutral ±2×ATR regrade the whole market's base is 41.8% and most
  feature edges shrink; a future session may want to switch US grading to ATR-scaled
  stops (product decision — changes the displayed stop, history and track record).
- Persistence (trailing follow-through), the biggest India score term, is mostly a
  volatility proxy in the US — that's why the US weights lean on base depth instead.
