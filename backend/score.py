"""
A single "how good is this setup" score, built ONLY from features the whole-market
reliability study (analyze_reliability.py) actually validated as predictive of
follow-through — not from decorative signals that looked good but didn't hold up.

Validated inputs, PER MARKET (constants live in settings.py's score-calibration
block; India numbers in CLAUDE.md's reliability section, US replay 2026-07-05 on
20,814 whole-market Method-A events):
  * trailing follow-through rate (persistence) — India's STRONGEST signal (p<0.001,
    ~32% vs ~46%); real but weaker in the US (22.3% vs 34.7%, p<0.001), hence the
    smaller US weight.
  * base depth — robust in both markets (India p<0.001, 41% vs 35%; US the strongest
    feature, 14.6% shallow vs 39.1% deep, p<0.001), hence the larger US weight.
  * method confirmation — India only (E2/D/L all beat baseline). In the US this
    term is weighted 0: D co-fire measured -12.2pt HARMFUL (p=0.002, n=130) and E2
    co-fire's +9.5pt lift is already captured by depth+reliability in the blend.
    L (Method L / is_sb_deep_base, a deep-base squeeze-breakout tier) was checked
    the same way 2026-07-12 on 9,581 whole-market-cached Method-A events: +6.5pt
    lift, 44.4% vs 37.9%, p<0.001, n_on=1637 -- the only one of I/J/K/L that
    cleared significance (I +1.6pt p=0.15 n=2705; J -0.5pt p=0.68 n=2386; K +2.8pt
    p=0.15 n=667 -- none distinguishable from noise at this sample size), so only
    L was wired in; see backend/_scratch_ijkl_cofire.py for the replay.

Deliberately NOT used: ADX, volume-surge magnitude, named chart patterns (all shown
non-predictive in both markets), vol_contraction (direction flips between markets;
under volatility-neutral ATR grading it reverses entirely — an artifact carrier, not
a signal), market regime / SPX-vs-200dma (US: -0.5pt, p=0.52 — tested, rejected), and
the single "closest historical analog" outcome (weak in India, +10.2pt raw in the US
but adds nothing over the blend out-of-sample; see test_analog_predictiveness).

Two pieces:
  1. reliability_estimate() — a Bayesian-shrunk follow-through rate. A stock with one
     failed breakout reads ~0.31, NOT 0.0 — one occurrence shouldn't scream
     "unreliable". This is what powers the caution text AND the score's biggest term.
  2. breakout_quality() / conviction() — combine the validated features into one 0..1
     quality number, then blend with how imminent the setup is into a 0..100 score the
     UI can rank on.
"""
from __future__ import annotations

import settings

# Measured whole-market Method-A follow-through base rate for the active market
# (India ~38.8% -> 0.39; US ~26.7% -> 0.27 — see settings' score-calibration block).
# Used as the prior a stock's own rate is shrunk toward when it has little history.
BASE_RATE = settings.SCORE_BASE_RATE


def reliability_estimate(worked: int | None, total: int | None,
                         prior: float = BASE_RATE, strength: float = 4.0) -> float:
    """Bayesian-shrunk follow-through rate in [0,1].

    (worked + strength*prior) / (total + strength). With strength=4 and India's 0.39:
      0 of 1  -> 0.31   (one failure is NOT damning)
      0 of 5  -> 0.17   (a real, earned low)
      3 of 4  -> 0.58
      no history -> 0.39 (the market prior; 0.27 when running the US market)
    So the estimate only moves far from the base rate once there's enough evidence to
    justify it — which is exactly what stops a single occurrence from flashing red.
    """
    if not total or total <= 0:
        return prior
    return (worked + strength * prior) / (total + strength)


def _depth_component(base_depth_pct: float | None) -> float:
    """Map base depth (a negative %, e.g. -25 for a 25%-deep base) to [0,1] — deeper
    is better, per the study. 8% deep -> 0, 50% deep -> 1, clamped."""
    if base_depth_pct is None:
        return 0.5
    depth = abs(base_depth_pct)
    return min(max((depth - 8.0) / 42.0, 0.0), 1.0)


def breakout_quality(rel_est: float, base_depth_pct: float | None,
                     rs_on: bool = False, d_on: bool = False, l_on: bool = False,
                     w_rel: float = settings.SCORE_W_REL,
                     w_depth: float = settings.SCORE_W_DEPTH,
                     w_method: float = settings.SCORE_W_METHOD) -> float:
    """Blend the validated features into one ordering score. Not a calibrated
    probability — a monotone quality rank (higher = historically more likely to follow
    through). Weights default to the active market's backtest-chosen values (settings'
    score-calibration block); the same function is replayed in analyze_reliability.py
    to confirm they stratify follow-through.

    method sub-weights (0.5 / 0.5 / 0.25, capped at 1.0) are NOT independently
    settings-tunable — they were fixed by the one-off replays in
    backend/_scratch_ijkl_cofire.py (l_on 2026-07-12) and CLAUDE.md's reliability
    section (rs_on/d_on 2026-07-04), same as the top-level w_method being 0 for US."""
    depth = _depth_component(base_depth_pct)
    method = min(1.0, (0.5 if rs_on else 0.0) + (0.5 if d_on else 0.0) + (0.25 if l_on else 0.0))
    return w_rel * rel_est + w_depth * depth + w_method * method


# Plausible range of breakout_quality() with the active market's weights, used only to
# rescale the raw blend to 0..1 for display (a monotone transform — doesn't change
# ordering). Per market because the weight mix shifts the achievable range.
_Q_LO, _Q_HI = settings.SCORE_Q_RANGE

# How "imminent" each readiness tier is — the other half of conviction. A stock that is
# both about to break AND historically reliable should top the list.
_IMMINENCE = {"breaking": 1.0, "high": 0.8, "medium_watch": 0.55, "medium": 0.4, "low": 0.15}


def conviction(rel_est: float, base_depth_pct: float | None, imminence_key: str,
               rs_on: bool = False, d_on: bool = False, l_on: bool = False) -> int:
    """A single 0..100 the UI ranks on: blends how imminent the setup is (readiness
    tier) with how reliable it is if it triggers (validated quality). Reliability-
    weighted enough that a primed-but-flaky stock ranks below a primed-and-proven one,
    but imminence still matters so a coiling name doesn't outrank a breaking one on
    reliability alone."""
    q = breakout_quality(rel_est, base_depth_pct, rs_on, d_on, l_on)
    q_norm = min(max((q - _Q_LO) / (_Q_HI - _Q_LO), 0.0), 1.0)
    imm = _IMMINENCE.get(imminence_key, 0.15)
    return round(100 * (0.55 * imm + 0.45 * q_norm))
