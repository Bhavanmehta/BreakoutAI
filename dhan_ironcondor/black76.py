"""Black-76 pricing for options on a futures underlying. stdlib math only.

F is always the current-month NIFTY FUT LTP, never cash spot -- the whole
system prices/deltas off the future because that's the hedge instrument.
"""
from __future__ import annotations
import math
from datetime import datetime, date, time as dtime

SQRT_2PI = math.sqrt(2.0 * math.pi)


def N(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def phi(x: float) -> float:
    return math.exp(-x * x / 2.0) / SQRT_2PI


def _d1_d2(F: float, K: float, sigma: float, T: float) -> tuple[float, float]:
    if T <= 0 or sigma <= 0 or F <= 0 or K <= 0:
        raise ValueError(f"invalid inputs F={F} K={K} sigma={sigma} T={T}")
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def call_price(F: float, K: float, sigma: float, T: float, r: float) -> float:
    d1, d2 = _d1_d2(F, K, sigma, T)
    return math.exp(-r * T) * (F * N(d1) - K * N(d2))


def put_price(F: float, K: float, sigma: float, T: float, r: float) -> float:
    d1, d2 = _d1_d2(F, K, sigma, T)
    return math.exp(-r * T) * (K * N(-d2) - F * N(-d1))


def price(F: float, K: float, sigma: float, T: float, r: float, opt_type: str) -> float:
    if opt_type.upper().startswith("C"):
        return call_price(F, K, sigma, T, r)
    return put_price(F, K, sigma, T, r)


def delta_call(F: float, K: float, sigma: float, T: float, r: float) -> float:
    d1, _ = _d1_d2(F, K, sigma, T)
    return math.exp(-r * T) * N(d1)


def delta_put(F: float, K: float, sigma: float, T: float, r: float) -> float:
    d1, _ = _d1_d2(F, K, sigma, T)
    return -math.exp(-r * T) * N(-d1)


def delta(F: float, K: float, sigma: float, T: float, r: float, opt_type: str) -> float:
    if opt_type.upper().startswith("C"):
        return delta_call(F, K, sigma, T, r)
    return delta_put(F, K, sigma, T, r)


def gamma(F: float, K: float, sigma: float, T: float, r: float) -> float:
    d1, _ = _d1_d2(F, K, sigma, T)
    return math.exp(-r * T) * phi(d1) / (F * sigma * math.sqrt(T))


def vega(F: float, K: float, sigma: float, T: float, r: float) -> float:
    """Per 1 vol-point (1% IV), matching how the risk desk reads vega."""
    d1, _ = _d1_d2(F, K, sigma, T)
    return F * math.exp(-r * T) * phi(d1) * math.sqrt(T) / 100.0


def time_to_expiry(now: datetime, expiry_date: date) -> float:
    """Continuous intraday fraction of a year, 0DTE-safe: minutes until 15:30 IST
    on expiry_date, divided by 365*1440."""
    expiry_cutoff = datetime.combine(expiry_date, dtime(15, 30), tzinfo=now.tzinfo)
    minutes = (expiry_cutoff - now).total_seconds() / 60.0
    minutes = max(minutes, 1e-6)  # never let T hit exactly 0 (division by zero in d1)
    return minutes / (365.0 * 1440.0)


def implied_vol(
    price_target: float, F: float, K: float, T: float, r: float, opt_type: str,
    lo: float = 0.01, hi: float = 3.0, iterations: int = 80,
) -> float:
    """Bisection on sigma. Newton diverges near-expiry OTM (near-zero vega) --
    bisection is slower but always converges. Do not switch to Newton."""
    price_fn = call_price if opt_type.upper().startswith("C") else put_price

    f_lo = price_fn(F, K, lo, T, r) - price_target
    f_hi = price_fn(F, K, hi, T, r) - price_target
    if f_lo > 0 and f_hi > 0:
        return lo
    if f_lo < 0 and f_hi < 0:
        return hi

    for _ in range(iterations):
        mid = (lo + hi) / 2.0
        f_mid = price_fn(F, K, mid, T, r) - price_target
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2.0


if __name__ == "__main__":
    # (1) ATM call reference value
    c = call_price(F=100, K=100, sigma=0.2, T=0.25, r=0.05)
    assert abs(c - 3.938) < 0.01, f"ATM call {c} != 3.938"
    print(f"PASS: ATM call price = {c:.4f}")

    # (2) put-call parity
    F, K, sigma, T, r = 100.0, 105.0, 0.25, 0.1, 0.069
    call, put = call_price(F, K, sigma, T, r), put_price(F, K, sigma, T, r)
    parity_rhs = math.exp(-r * T) * (F - K)
    assert abs((call - put) - parity_rhs) < 1e-9, "put-call parity violated"
    print(f"PASS: put-call parity ({call - put:.6f} == {parity_rhs:.6f})")

    # (3) delta bounds + delta parity
    dc, dp = delta_call(F, K, sigma, T, r), delta_put(F, K, sigma, T, r)
    assert 0 < dc < 1, f"delta_call out of range: {dc}"
    assert -1 < dp < 0, f"delta_put out of range: {dp}"
    assert abs((dc - dp) - math.exp(-r * T)) < 1e-9, "delta parity violated"
    print(f"PASS: deltas in range, dc-dp={dc - dp:.6f} == exp(-rT)={math.exp(-r * T):.6f}")

    # (4) implied_vol round-trip
    true_sigma = 0.22
    known_price = call_price(F, K, true_sigma, T, r)
    solved = implied_vol(known_price, F, K, T, r, "CALL")
    assert abs(solved - true_sigma) < 1e-4, f"IV round-trip failed: {solved} != {true_sigma}"
    print(f"PASS: implied_vol round-trip {solved:.6f} ~= {true_sigma}")

    print("ALL PASS")
