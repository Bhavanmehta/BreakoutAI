"""Pure decision functions: no I/O, no broker calls, no clock reads.
Same code path for backtest and live -- feed it numbers, get decisions back.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from black76 import delta
from config import WING_WIDTH, TARGET_SHORT_DELTA, MIN_CREDIT_PTS, LOT_SIZE


@dataclass(frozen=True)
class ChainLeg:
    strike: float
    ltp: float
    iv: float


@dataclass(frozen=True)
class OptionChain:
    calls: dict[float, ChainLeg]
    puts: dict[float, ChainLeg]


@dataclass(frozen=True)
class Leg:
    strike: float
    opt_type: str  # "CALL" | "PUT"
    qty: int  # signed contract quantity: negative = short, positive = long
    entry_price: float = 0.0  # fill price this leg was opened at (0.0 until filled)


@dataclass(frozen=True)
class CondorSpec:
    short_call_k: float
    long_call_k: float
    short_put_k: float
    long_put_k: float
    net_credit: float
    short_call_delta: float
    short_put_delta: float


@dataclass(frozen=True)
class VerticalSpec:
    side: str  # "CALL" | "PUT" -- side the new vertical is opened on
    short_k: float
    long_k: float
    resulting_net_delta: float


def entry_filters(
    open_price: float, prev_close: float, ltp_1015: float, or_high: float, or_low: float
) -> tuple[bool, str]:
    checks = [
        (abs(ltp_1015 - open_price) / open_price <= 0.005, "10:15 drift from open > 0.5%"),
        ((or_high - or_low) / open_price <= 0.0075, "opening range width > 0.75% of open"),
        (abs(open_price - prev_close) / prev_close <= 0.005, "gap from prev close > 0.5%"),
    ]
    for ok, reason in checks:
        if not ok:
            return False, reason
    return True, "all entry filters passed"


def late_entry_ok(
    spot: float, prev_close: float, max_move_pct: float
) -> tuple[bool, str]:
    """Volatility-sanity gate for a late (post-OR) delta-anchored entry. Unlike
    entry_filters there is no opening range to lean on, so we only refuse to sell
    premium into a strongly trending tape: skip when spot has moved more than
    max_move_pct vs prev close. Returns (ok, reason)."""
    move_pct = abs(spot - prev_close) / prev_close * 100.0
    if move_pct > max_move_pct:
        return False, (
            f"late entry blocked: move {move_pct:.2f}% vs prev close "
            f"> {max_move_pct:g}% (tape trending)"
        )
    return True, f"late entry ok: move {move_pct:.2f}% within {max_move_pct:g}%"


def select_condor_at(
    chain: OptionChain, or_high: float, or_low: float, F: float, T: float, r: float,
    target_short_delta: float, wing_width: float, min_credit: float = MIN_CREDIT_PTS,
) -> tuple[Optional[CondorSpec], str]:
    """select_condor with the short-delta target, wing width and min-credit floor
    passed in, so one chain can be swept into a ladder of (delta, wing) presets.
    select_condor is this called with the config defaults."""
    short_call_k = short_call_delta = None
    for k in sorted(k for k in chain.calls if k >= or_high):
        d = delta(F, k, chain.calls[k].iv, T, r, "CALL")
        if d <= target_short_delta:
            short_call_k, short_call_delta = k, d
            break
    if short_call_k is None:
        return None, f"no call strike >= {or_high:g} with delta <= {target_short_delta:g}"

    short_put_k = short_put_delta = None
    for k in sorted((k for k in chain.puts if k <= or_low), reverse=True):
        d = delta(F, k, chain.puts[k].iv, T, r, "PUT")
        if abs(d) <= target_short_delta:
            short_put_k, short_put_delta = k, d
            break
    if short_put_k is None:
        return None, f"no put strike <= {or_low:g} with |delta| <= {target_short_delta:g}"

    long_call_k = short_call_k + wing_width
    long_put_k = short_put_k - wing_width
    if long_call_k not in chain.calls or long_put_k not in chain.puts:
        return None, "wing strikes not present in chain"

    net_credit = (
        chain.calls[short_call_k].ltp - chain.calls[long_call_k].ltp
        + chain.puts[short_put_k].ltp - chain.puts[long_put_k].ltp
    )
    if net_credit < min_credit:
        return None, f"net credit {net_credit:.2f} < min_credit {min_credit:g}"

    spec = CondorSpec(
        short_call_k, long_call_k, short_put_k, long_put_k,
        net_credit, short_call_delta, short_put_delta,
    )
    return spec, "ok"


def select_condor(
    chain: OptionChain, or_high: float, or_low: float, F: float, T: float, r: float
) -> tuple[Optional[CondorSpec], str]:
    """Scan for the first call >= or_high and put <= or_low whose |delta| is at
    or below TARGET_SHORT_DELTA. Pass the OR high/low for OR-breakout entry, or
    spot for both bounds to get a pure delta-anchored condor centered on spot."""
    return select_condor_at(chain, or_high, or_low, F, T, r, TARGET_SHORT_DELTA, WING_WIDTH)


def select_butterfly(
    chain: OptionChain, or_high: float, or_low: float, F: float, T: float, r: float
) -> tuple[Optional[CondorSpec], str]:
    """Short iron butterfly: BOTH shorts at the ATM strike (nearest listed strike
    to spot present on both sides), longs at +/- WING_WIDTH. Deliberately shares
    select_condor's signature so RiskManager can hold either as a pluggable
    selector; the OR bounds (or_high/or_low) are ignored -- a fly is always
    spot-anchored, whether entered on the OR-breakout path or the late path.

    Returns a CondorSpec whose short_call_k == short_put_k == ATM; every downstream
    consumer (breakevens, hedge-wall, P&L, delta) already treats those two fields
    generically, so no special-casing is needed once the spec is built."""
    if not chain.calls or not chain.puts:
        return None, "empty option chain"
    # ATM = listed strike nearest spot that exists on BOTH call and put sides,
    # so all four legs (short C/P at ATM, long C/P at the wings) are quotable.
    common = sorted(set(chain.calls) & set(chain.puts))
    if not common:
        return None, "no strike present on both call and put sides"
    atm = min(common, key=lambda k: abs(k - F))

    long_call_k = atm + WING_WIDTH
    long_put_k = atm - WING_WIDTH
    if long_call_k not in chain.calls or long_put_k not in chain.puts:
        return None, "wing strikes not present in chain"

    net_credit = (
        chain.calls[atm].ltp - chain.calls[long_call_k].ltp
        + chain.puts[atm].ltp - chain.puts[long_put_k].ltp
    )
    if net_credit < MIN_CREDIT_PTS:
        return None, f"net credit {net_credit:.2f} < MIN_CREDIT_PTS {MIN_CREDIT_PTS}"

    short_call_delta = delta(F, atm, chain.calls[atm].iv, T, r, "CALL")
    short_put_delta = delta(F, atm, chain.puts[atm].iv, T, r, "PUT")
    spec = CondorSpec(
        atm, long_call_k, atm, long_put_k,
        net_credit, short_call_delta, short_put_delta,
    )
    return spec, "ok"


def net_portfolio_delta(
    legs: list[Leg], F: float, T: float, r: float, ivs: dict[tuple[float, str], float]
) -> float:
    """Signed qty*delta summed and normalized per condor (lots*LOT_SIZE).
    Sign convention: short put delta is positive (qty negative * delta_put negative),
    so a crash (F down, |delta_put| up) drives net delta POSITIVE."""
    if not legs:
        return 0.0
    lots = max(1, max(abs(leg.qty) for leg in legs) // LOT_SIZE)
    denom = lots * LOT_SIZE
    total = sum(
        leg.qty * delta(F, leg.strike, ivs[(leg.strike, leg.opt_type)], T, r, leg.opt_type)
        for leg in legs
    )
    return total / denom


def select_roll(
    chain: OptionChain, tested_side: str, residual_delta: float, F: float, T: float, r: float
) -> tuple[Optional[VerticalSpec], str]:
    """The tested (breached) side's vertical stays; the untested side was just
    closed. Pick a new short strike for the untested side (+ WING_WIDTH long)
    that brings net delta closest to 0."""
    untested_side = "PUT" if tested_side == "CALL" else "CALL"
    strikes = chain.puts if untested_side == "PUT" else chain.calls

    best: Optional[tuple[float, float, float]] = None  # (short_k, long_k, resulting)
    for short_k, short_leg in strikes.items():
        long_k = short_k - WING_WIDTH if untested_side == "PUT" else short_k + WING_WIDTH
        long_leg = strikes.get(long_k)
        if long_leg is None:
            continue
        d_short = delta(F, short_k, short_leg.iv, T, r, untested_side)
        d_long = delta(F, long_k, long_leg.iv, T, r, untested_side)
        contribution = d_long - d_short  # per-lot: long(+1) + short(-1)
        resulting = residual_delta + contribution
        if best is None or abs(resulting) < abs(best[2]):
            best = (short_k, long_k, resulting)

    if best is None:
        return None, f"no rollable strikes found on {untested_side} side"

    short_k, long_k, resulting = best
    spec = VerticalSpec(untested_side, short_k, long_k, resulting)
    if abs(resulting) <= 0.03:
        return spec, f"roll to {untested_side} {short_k}/{long_k}: net delta {resulting:.4f} within target"
    return spec, (
        f"roll to {untested_side} {short_k}/{long_k}: net delta {resulting:.4f} "
        f"exceeds 0.03 target, picked closest available"
    )


def breakevens(short_call_k: float, short_put_k: float, net_credit: float) -> tuple[float, float]:
    return short_put_k - net_credit, short_call_k + net_credit


if __name__ == "__main__":
    # Self-check: a synthetic symmetric chain around F=20000. Run from this dir
    # (bare imports of black76/config), same as black76.py's __main__.
    F, T, r, iv = 20000.0, 5 / 365.0, 0.069, 0.14
    ivp = {}
    calls, puts = {}, {}
    for k in range(19000, 21050, 50):
        calls[k] = ChainLeg(k, max(0.5, __import__("black76").call_price(F, k, iv, T, r)), iv)
        puts[k] = ChainLeg(k, max(0.5, __import__("black76").put_price(F, k, iv, T, r)), iv)
    ch = OptionChain(calls=calls, puts=puts)

    # (1) select_condor == select_condor_at with the config defaults
    a, _ = select_condor(ch, F, F, F, T, r)
    b, _ = select_condor_at(ch, F, F, F, T, r, TARGET_SHORT_DELTA, WING_WIDTH)
    assert a == b, "select_condor must delegate to select_condor_at with defaults"

    # (2) higher target delta -> shorts sit closer to spot (narrower body)
    lo, _ = select_condor_at(ch, F, F, F, T, r, 0.10, WING_WIDTH, min_credit=0)
    hi, _ = select_condor_at(ch, F, F, F, T, r, 0.30, WING_WIDTH, min_credit=0)
    assert lo and hi, "both presets should resolve on a full chain"
    assert hi.short_call_k <= lo.short_call_k and hi.short_put_k >= lo.short_put_k, \
        "0.30-delta condor must be tighter than the 0.10-delta one"
    assert hi.net_credit > lo.net_credit, "tighter condor must collect more credit"

    # (3) wing width flows through to the long strikes
    w, _ = select_condor_at(ch, F, F, F, T, r, 0.20, 300, min_credit=0)
    assert w.long_call_k - w.short_call_k == 300, "wing_width must set the long strike"
    print("PASS: select_condor_at ladder (delegation, delta tightness, wing width)")
