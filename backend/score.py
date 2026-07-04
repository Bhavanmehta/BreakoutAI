"""
A single "how good is this setup" score, built ONLY from features the whole-market
reliability study (analyze_reliability.py) actually validated as predictive of
follow-through — not from decorative signals that looked good but didn't hold up.

Validated inputs (see CLAUDE.md's reliability section):
  * trailing follow-through rate (persistence) — the STRONGEST signal (p<0.001):
    low-trailing-rate stocks hit ~32%, high-trailing hit ~46%.
  * base depth — robust (p<0.001): deeper bases follow through MORE (41% vs 35%).
  * relative-strength (E2) and trend-inception (D) method confirmation — both beat
    the 38.8% baseline significantly.

Deliberately NOT used: ADX, volume-surge magnitude, named chart patterns (all shown
non-predictive), vol_contraction (significant but counterintuitive and weak), and the
single "closest historical analog" outcome (one day — never validated as predictive;
see test_analog_predictiveness in analyze_reliability.py).

Two pieces:
  1. reliability_estimate() — a Bayesian-shrunk follow-through rate. A stock with one
     failed breakout reads ~0.31, NOT 0.0 — one occurrence shouldn't scream
     "unreliable". This is what powers the caution text AND the score's biggest term.
  2. breakout_quality() / conviction() — combine the validated features into one 0..1
     quality number, then blend with how imminent the setup is into a 0..100 score the
     UI can rank on.
"""
from __future__ import annotations

# Measured whole-market Method-A follow-through base rate (~38.8% in the latest run).
# Used as the prior a stock's own rate is shrunk toward when it has little history.
BASE_RATE = 0.39


def reliability_estimate(worked: int | None, total: int | None,
                         prior: float = BASE_RATE, strength: float = 4.0) -> float:
    """Bayesian-shrunk follow-through rate in [0,1].

    (worked + strength*prior) / (total + strength). With strength=4 and prior=0.39:
      0 of 1  -> 0.31   (one failure is NOT damning)
      0 of 5  -> 0.17   (a real, earned low)
      3 of 4  -> 0.58
      no history -> 0.39 (the market prior)
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
                     rs_on: bool = False, d_on: bool = False,
                     w_rel: float = 0.60, w_depth: float = 0.25,
                     w_method: float = 0.15) -> float:
    """Blend the validated features into one ordering score. Not a calibrated
    probability — a monotone quality rank (higher = historically more likely to follow
    through). Weights default to the values chosen from the backtest; the same function
    is replayed in analyze_reliability.py to confirm they stratify follow-through."""
    depth = _depth_component(base_depth_pct)
    method = (0.5 if rs_on else 0.0) + (0.5 if d_on else 0.0)
    return w_rel * rel_est + w_depth * depth + w_method * method


# Plausible range of breakout_quality() with the default weights, used only to rescale
# the raw blend to 0..1 for display (a monotone transform — doesn't change ordering).
_Q_LO, _Q_HI = 0.18, 0.78

# How "imminent" each readiness tier is — the other half of conviction. A stock that is
# both about to break AND historically reliable should top the list.
_IMMINENCE = {"breaking": 1.0, "high": 0.8, "medium_watch": 0.55, "medium": 0.4, "low": 0.15}


def conviction(rel_est: float, base_depth_pct: float | None, imminence_key: str,
               rs_on: bool = False, d_on: bool = False) -> int:
    """A single 0..100 the UI ranks on: blends how imminent the setup is (readiness
    tier) with how reliable it is if it triggers (validated quality). Reliability-
    weighted enough that a primed-but-flaky stock ranks below a primed-and-proven one,
    but imminence still matters so a coiling name doesn't outrank a breaking one on
    reliability alone."""
    q = breakout_quality(rel_est, base_depth_pct, rs_on, d_on)
    q_norm = min(max((q - _Q_LO) / (_Q_HI - _Q_LO), 0.0), 1.0)
    imm = _IMMINENCE.get(imminence_key, 0.15)
    return round(100 * (0.55 * imm + 0.45 * q_norm))
