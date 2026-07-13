"""GET /api/options_backtest -- LOCAL-ONLY backtesting backend for the
"Options Trade Assessor" (OPTIONS_ASSESSOR_PLAN.md section A, items 1/2/3/5).

Data source: Dhan Expired Options API
    POST https://api.dhan.co/v2/charts/rollingoption
    (docs: https://dhanhq.co/docs/v2/expired-options-data/)

Empirically verified against live Dhan (2026-07-12, NIFTY WEEK ATM CALL,
fromDate=2026-07-01 toDate=2026-07-04, interval=5):
  * expiryCode is a 1-BASED index counting FORWARD from the nearest unexpired
    expiry at each bar's point in time: 1 = nearest weekly, 2 = next weekly out
    (same window/strike returned close[0]=200.55 for code 1 vs 286.80 for code
    2 -- more time value => further expiry). expiryCode=0 is REJECTED with
    {"errorType":"Input_Exception","errorCode":"DH-905",
     "errorMessage":"expiryCode is required"} (0 is treated as missing).
  * One call returns ONLY the leg named by drvOptionType: CALL fills data.ce
    and leaves data.pe null; PUT fills data.pe. So a full download makes 2
    calls per (chunk, strike offset).
  * Response shape: {"data": {"ce"|"pe": {iv[], oi[], strike[], spot[],
    open[], high[], low[], close[], volume[], timestamp[]}}} -- parallel
    arrays, NO {"status": "success"} wrapper (unlike /optionchain). Errors
    come back as {"errorType", "errorCode", "errorMessage"}.
  * timestamp[] is plain UTC epoch seconds: first bar of 2026-07-01 is
    1782877500 = 03:45:00Z = 09:15 IST market open; last bar of the day is
    09:55Z = 15:25 IST. Bars are stored here EXACTLY as returned (UTC epoch);
    all day/time-of-day bucketing adds IST_OFFSET (+19800 s) at read time.
  * strike "ATM+N"/"ATM-N" moves N strike STEPS (NIFTY: 50 pts -- ATM+2 gave
    24000.0 vs ATM's 23900.0). The rolling series re-strikes with spot; the
    actual strike of every bar is in strike[] and stored per-bar.

Cache: stdlib sqlite3 at backend/backtest_cache.db (gitignored). Vercel's
serverless runtime has no persistent disk, so every action is guarded: on
Vercel this endpoint returns 501 "local only" (run scripts/dev_server.py).

Actions (GET query params, mirroring api/options_chain.py conventions):
  ?action=download&symbol=NIFTY&from=2026-07-01&to=2026-07-03&interval=5
          &strikes=ATM-1..ATM+1[&expiry_flag=WEEK][&expiry_code=1]
  ?action=status
  ?action=bars&symbol=NIFTY&strike=ATM&type=CE&interval=5[&from=..&to=..][&limit=500]
  ?action=backtest&symbol=NIFTY&strategy=long|straddle[&side=CE][&strike=ATM]
          [&entry_time=09:20][&eod_time=15:15][&sl_pct=0.20][&target_pct=0.40]
          [&lots=1][&lot_size=..][&interval=5][&costs={json overrides}]
  ?action=replay  -- same params as backtest strategy=long, buckets each
          trade by the assessor's verdict tier (Python port of
          scripts/options_math.js assess(); identical thresholds/formulas).

Verdict math: faithful port of scripts/options_math.js (bsPrice/bsGreeks/
probTouch/expectedMove/breakeven/assess). Known accepted divergence: normCdf
here is math.erf-based (same as api/options_chain.py) instead of the JS
Abramowitz-Stegun polynomial -- |diff| < 7.5e-8, thresholds unaffected.

Local smoke test:  python api/options_backtest.py   (runs assert self-checks)
"""
from __future__ import annotations
import datetime
import json
import math
import os
import re
import sqlite3
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

# Local-only feature: sibling import is fine under scripts/dev_server.py (repo
# root on sys.path). On Vercel each api/*.py bundles in isolation and this
# import would fail -- stub enough for the handler to boot and 501 cleanly.
try:
    from api.options_chain import ProviderError, SymbolNotFoundError, _env, _load_env_file, _resolve_symbol
except ImportError:  # pragma: no cover -- Vercel isolation; endpoint is 501-guarded there
    class ProviderError(Exception):
        pass

    class SymbolNotFoundError(ProviderError):
        pass

    def _env(name):
        return os.environ.get(name)

    def _load_env_file():
        pass

    def _resolve_symbol(symbol):
        raise ProviderError("symbol resolution unavailable outside local dev")

ROLLING_URL = "https://api.dhan.co/v2/charts/rollingoption"
DB_PATH = Path(__file__).resolve().parent.parent / "backend" / "backtest_cache.db"
IST_OFFSET = 19800          # Dhan timestamps are UTC epoch; IST = UTC+5:30
MAX_CHUNK_DAYS = 30         # documented Dhan limit per rollingoption call
THROTTLE_SECONDS = 3.0      # same conservative pace as /optionchain (~1 req/3s)
VALID_INTERVALS = {1, 5, 15, 25, 60}

# Editable cost model (item 2 of the plan). Every figure the engine reports is
# net of these. Override per call with &costs={"brokerage_per_order":0,...}.
DEFAULT_COSTS = {
    "brokerage_per_order": 20.0,   # flat, per fill
    "stt_sell_pct": 0.001,         # 0.1% of premium, SELL side only
    "txn_fee_pct": 0.0003503,      # NSE options transaction fee, both sides
    "sebi_per_crore": 10.0,        # SEBI turnover fee
    "gst_pct": 0.18,               # on (brokerage + txn fee)
    "stamp_buy_pct": 0.00003,      # 0.003% of premium, BUY side only
}


class LocalOnlyError(Exception):
    """Raised when the backtest endpoint is hit on Vercel (no persistent disk).
    Mapped to HTTP 501 -- run `python scripts/dev_server.py` locally instead."""


def _guard_local():
    if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
        raise LocalOnlyError(
            "options_backtest is a local-only dev feature (Vercel serverless has "
            "no persistent disk for the SQLite cache) -- run `python scripts/dev_server.py`.")


# --- verdict math: Python port of scripts/options_math.js (assess & friends) --
# Kept numerically faithful: same formulas, same degenerate-input guards, same
# verdict thresholds. normCdf uses math.erf (accepted divergence, see docstring).
def _norm_cdf(x: float) -> float:
    if not math.isfinite(x):
        return 1.0 if x > 0 else 0.0
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _sig_t(iv: float, t_years: float):
    sigma = (iv or 0.0) / 100.0
    sqrt_t = math.sqrt(max(t_years, 0.0))
    return sigma, sqrt_t, sigma * sqrt_t


def _forward_of(spot: float | None, r: float, t_years: float, forward: float | None = None) -> float:
    if forward is not None:
        return forward
    return (spot or 0.0) * math.exp(r * max(t_years, 0.0))


# B1: forward via ATM put-call parity: F = K_atm + (C_atm - P_atm)*e^{rT}. NIFTY
# weeklies have no tradeable matching-expiry futures, so the forward is imputed
# from the chain's own ATM CE/PE ltp instead. Falls back to the pure carry
# forward (spot * e^{rT}) when the ATM ltps aren't available (mock provider,
# stale leg, manual entry, or -- as in the replay engine -- no ATM straddle
# fetched at all). Mirrors scripts/options_math.js impliedForward() exactly.
def implied_forward(atm_strike: float | None, atm_call_ltp: float | None, atm_put_ltp: float | None,
                     t_years: float, r: float = 0.065, spot: float | None = None) -> dict:
    growth = math.exp(r * max(t_years, 0.0))
    spot_fallback = (spot or 0.0) * growth
    if (atm_strike is None or atm_call_ltp is None or atm_put_ltp is None
            or not (atm_call_ltp > 0) or not (atm_put_ltp > 0)):
        return {"forward": spot_fallback, "source": "spot-fallback"}
    return {"forward": atm_strike + (atm_call_ltp - atm_put_ltp) * growth, "source": "parity"}


# Black-76 d1/d2 on the FORWARD (not spot): d1 = [ln(F/K) + sigma^2 T/2] / (sigma sqrt T).
# No separate "+r*T" drift term -- the forward already prices in the carry,
# unlike plain Black-Scholes where d1 carries that term against spot.
def _d1d2_forward(forward: float, strike: float, iv: float, t_years: float):
    sigma, sqrt_t, denom = _sig_t(iv, t_years)
    if denom <= 1e-12 or forward <= 0 or strike <= 0:
        m = math.log((forward or 1e-9) / (strike or 1e-9))
        big = 40.0 if m >= 0 else -40.0
        return big, big, sigma, sqrt_t
    d1 = (math.log(forward / strike) + 0.5 * sigma * sigma * t_years) / denom
    return d1, d1 - denom, sigma, sqrt_t


# Black-76 fair value (per unit of underlying). Pass `forward` directly when you
# have a parity-implied one (see implied_forward); otherwise pass `spot` (+
# optional `r`) and the pure carry forward spot*e^{rT} is used, which makes this
# numerically IDENTICAL to the old plain Black-Scholes price (the standard BS
# <-> Black-76 equivalence) -- existing spot-only callers are unaffected.
def bs_price(spot: float | None, strike: float, iv: float, t_years: float, opt_type: str = "CE",
             r: float = 0.065, forward: float | None = None) -> float:
    f = _forward_of(spot, r, t_years, forward)
    d1, d2, _sigma, _sqrt_t = _d1d2_forward(f, strike, iv, t_years)
    disc = math.exp(-r * max(t_years, 0.0))
    if opt_type.upper() == "PE":
        return max(disc * (strike * _norm_cdf(-d2) - f * _norm_cdf(-d1)), 0.0)
    return max(disc * (f * _norm_cdf(d1) - strike * _norm_cdf(d2)), 0.0)


# Greeks, Black-76 form. delta now carries the discount factor e^{-rT} (a
# deliberate change from the old plain-BS delta, which had none -- this IS the
# correct forward-measure delta). gamma/vega are on the forward; theta =
# -disc*F*phi(d1)*sigma/(2 sqrt T) + r*price, derived from d(price)/dT holding F
# fixed (the standard practitioner "sticky forward" theta) -- differs slightly
# from the old spot-based theta by design.
def bs_greeks(spot: float | None, strike: float, iv: float, t_years: float, opt_type: str = "CE",
              r: float = 0.065, forward: float | None = None) -> dict:
    f = _forward_of(spot, r, t_years, forward)
    d1, d2, sigma, sqrt_t = _d1d2_forward(f, strike, iv, t_years)
    disc = math.exp(-r * max(t_years, 0.0))
    pdf = _norm_pdf(d1)
    sqrt_t = sqrt_t or 1e-9
    gamma = (disc * pdf) / ((f * sigma * sqrt_t) or 1e-9)
    vega_annual = disc * f * pdf * sqrt_t
    theta_term = -(disc * f * pdf * sigma) / (2 * sqrt_t)
    if opt_type.upper() == "PE":
        delta = -disc * _norm_cdf(-d1)
        price = max(disc * (strike * _norm_cdf(-d2) - f * _norm_cdf(-d1)), 0.0)
    else:
        delta = disc * _norm_cdf(d1)
        price = max(disc * (f * _norm_cdf(d1) - strike * _norm_cdf(d2)), 0.0)
    theta_annual = theta_term + r * price
    return {"delta": delta, "gamma": gamma, "theta": theta_annual / 365.0, "vega": vega_annual / 100.0}


def prob_touch(spot: float, barrier: float, iv: float, t_years: float, r: float = 0.065) -> float:
    sigma, _sqrt_t, denom = _sig_t(iv, t_years)
    if spot <= 0 or barrier <= 0 or denom <= 1e-12:
        return 0.0
    a = math.log(barrier / spot)
    nu = r - 0.5 * sigma * sigma
    expo = math.exp(2 * nu * a / (sigma * sigma))
    if a > 0:
        p = _norm_cdf((nu * t_years - a) / denom) + expo * _norm_cdf((-nu * t_years - a) / denom)
    elif a < 0:
        p = _norm_cdf((a - nu * t_years) / denom) + expo * _norm_cdf((a + nu * t_years) / denom)
    else:
        p = 1.0
    return min(max(p, 0.0), 1.0)


def expected_move(spot: float, iv: float, t_years: float) -> float:
    sigma, sqrt_t, _denom = _sig_t(iv, t_years)
    return (spot or 0.0) * sigma * sqrt_t


def breakeven(strike: float, premium: float, opt_type: str = "CE") -> float:
    return strike - premium if opt_type.upper() == "PE" else strike + premium


def assess(trade: dict) -> dict:
    """Python port of options_math.js assess() (post B1-B3 overhaul). Same
    dirOk check, same reward/risk/rr, PoP, and EXACT verdict thresholds:
      rr>=2 & pop>=0.40 & thetaCostPctOfReward<=0.25 -> Favorable
      rr>=1 & pop>=0.30                              -> Marginal
      else Unfavorable; !dirOk -> "Check inputs".
    B1: entry greeks/forward use a parity-implied forward (trade["atmStrike"]/
    ["atmCallLtp"]/["atmPutLtp"]), falling back to spot*e^{rT} when absent.
    B2: trade["marketIv"] / trade["marketGreeks"] (straight from the live
    chain leg) replace the BS-derived iv/greeks field-by-field when usable.
    B3: trade["horizonDays"] (actual intraday hold, defaults to full `days`
    when omitted) replaces the old "half the time to expiry" mark projection
    and the old absolute thetaPctOfPrem<0.08 gate (unreachable for weekly ATM
    options) with a hold-scoped thetaCostPctOfReward<=0.25 gate.
    Prose `reasons` strings are omitted (UI copy, irrelevant to bucketing);
    verdict/tone/warnings/metrics match the JS shape."""
    opt_type = (trade.get("type") or "CE").upper()
    days = max(trade.get("days") or 0, 0)
    t_years = days / 365.0
    r = trade.get("r", 0.065)
    lot_size = trade.get("lotSize") or 75
    lots = trade.get("lots") or 1
    premium = trade["premium"]
    spot, strike, iv = trade["spot"], trade["strike"], trade["iv"]
    sl_u, tgt_u = trade["slUnderlying"], trade["targetUnderlying"]

    dir_ok = (tgt_u > spot and sl_u < spot) if opt_type == "CE" else (tgt_u < spot and sl_u > spot)

    # B2: market IV (from the live chain leg) replaces the manual/BS-implied IV
    # whenever it's usable.
    market_iv = trade.get("marketIv")
    eff_iv = market_iv if (market_iv is not None and market_iv > 0) else iv

    # B3: intraday holding horizon. The assessor page defaults this to "time
    # left in today's session"; when the caller omits it we assume a full hold
    # to expiry (days) -- the old, conservative behaviour.
    horizon_days_in = days if trade.get("horizonDays") is None else trade["horizonDays"]
    horizon_days = min(max(horizon_days_in, 0.0), days)
    horizon_t_years = horizon_days / 365.0
    t_left = max(t_years - horizon_t_years, 0.0)  # time-to-expiry REMAINING once the hold elapses

    # B1: forward via ATM put-call parity for the ENTRY greeks (falls back to
    # spot*e^{rT} when no live ATM CE/PE ltp is supplied).
    atm_strike = trade["atmStrike"] if trade.get("atmStrike") is not None else strike
    fwd = implied_forward(atm_strike, trade.get("atmCallLtp"), trade.get("atmPutLtp"),
                           t_years, r, spot)

    # Project option mark at target and at stop, decayed by the ACTUAL holding
    # window (tLeft), not "half the time to expiry" (the old, arbitrary
    # assumption that ignored the intraday-exit reality).
    mark_at_target = bs_price(tgt_u, strike, eff_iv, t_left, opt_type, r)
    mark_at_stop = bs_price(sl_u, strike, eff_iv, t_left, opt_type, r)

    units = lot_size * lots
    reward = (mark_at_target - premium) * units
    risk = (premium - mark_at_stop) * units
    rr = (reward / risk) if risk > 0 else (math.inf if reward > 0 else 0.0)

    # B2: market greeks (straight from the chain leg) replace the BS-computed
    # ones field-by-field whenever finite; BS fallback (using the parity
    # forward) otherwise (mock provider / stale leg).
    bs_g = bs_greeks(None, strike, eff_iv, t_years, opt_type, r, forward=fwd["forward"])
    mg = trade.get("marketGreeks") or {}

    def _pick(key):
        v = mg.get(key)
        return v if (v is not None and math.isfinite(v)) else bs_g[key]

    g = {"delta": _pick("delta"), "gamma": _pick("gamma"), "theta": _pick("theta"), "vega": _pick("vega")}
    theta_per_day = g["theta"] * units              # currency/day (negative)
    theta_pct_of_prem = abs(g["theta"]) / premium if premium > 0 else 0.0   # legacy display metric

    # B3: theta gate over the ACTUAL hold, as a fraction of the projected
    # reward -- replaces the old absolute "<8%/day" gate, which no weekly ATM
    # option could ever pass (theta routinely 15-25%/day of premium; the trade
    # never holds for a full day anyway).
    theta_cost_horizon = abs(theta_per_day) * horizon_days
    theta_cost_pct_of_reward = (theta_cost_horizon / reward) if reward > 0 else (
        math.inf if theta_cost_horizon > 0 else 0.0)

    # B3: PoP is the probability of touching the target WITHIN the horizon
    # (not the full days-to-expiry -- the trade is flattened EOD).
    pop = prob_touch(spot, tgt_u, eff_iv, horizon_t_years, r)
    be = breakeven(strike, premium, opt_type)
    em = expected_move(spot, eff_iv, t_years)
    move_needed = abs(be - spot)

    warnings = []
    if not dir_ok:
        warnings.append(f"Target/stop are on the wrong side of spot for a {opt_type} -- check direction.")
    if theta_cost_pct_of_reward > 0.25:
        warnings.append("Theta over the hold eats > 25% of projected reward -- needs the move fast.")
    if move_needed > em:
        warnings.append("Breakeven is beyond the +/-1 sigma expected move -- statistically a stretch.")
    if abs(g["delta"]) < 0.2:
        warnings.append("Deep-OTM (delta < 0.20) -- lottery-ticket odds.")

    if not dir_ok:
        verdict, tone = "Check inputs", "warn"
    elif math.isfinite(rr) and rr >= 2 and pop >= 0.40 and theta_cost_pct_of_reward <= 0.25:
        verdict, tone = "Favorable", "good"
    elif rr >= 1 and pop >= 0.30:
        verdict, tone = "Marginal", "warn"
    else:
        verdict, tone = "Unfavorable", "bad"

    return {
        "verdict": verdict, "tone": tone, "warnings": warnings,
        "metrics": {
            "pop": pop, "rr": rr, "reward": reward, "risk": risk,
            "thetaPerDay": theta_per_day, "thetaPctOfPrem": theta_pct_of_prem,
            "thetaCostPctOfReward": theta_cost_pct_of_reward,
            "delta": g["delta"], "gamma": g["gamma"], "vega": g["vega"], "theta": g["theta"],
            "breakeven": be, "expectedMove": em, "moveNeeded": move_needed,
            "markAtTarget": mark_at_target, "markAtStop": mark_at_stop,
            "forward": fwd["forward"], "forwardSource": fwd["source"], "horizonDays": horizon_days,
        },
    }


# --- SQLite cache ---------------------------------------------------------------
# NOTE: the plan's column list omitted `strike`, but the actual per-bar strike is
# essential (straddle re-strike detection + assess() input) and Dhan returns it
# in the rolling series -- so it's stored as an extra non-key column.
_SCHEMA = """CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT, expiry_flag TEXT, expiry_code INTEGER, strike_off INTEGER,
    opt_type TEXT, interval_min INTEGER, ts INTEGER,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, oi REAL, iv REAL, spot REAL, strike REAL,
    PRIMARY KEY (symbol, expiry_flag, expiry_code, strike_off, opt_type, interval_min, ts))"""


def _db() -> sqlite3.Connection:
    _guard_local()
    try:
        con = sqlite3.connect(DB_PATH)
    except OSError as exc:
        raise LocalOnlyError(f"cannot open SQLite cache at {DB_PATH}: {exc}") from exc
    con.execute(_SCHEMA)
    return con


# --- Dhan rollingoption client ---------------------------------------------------
def _rolling_post(body: dict, max_retries: int = 3) -> dict:
    """One rollingoption call. 429 -> linear backoff retry (5s, 10s, 15s), then
    ProviderError. Other errors mirror LiveDhanProvider._post's messages."""
    client_id, token = _env("DHAN_Client_ID"), _env("DHAN_Access_TOKEN")
    if not client_id or not token:
        raise ProviderError("DHAN_Client_ID / DHAN_Access_TOKEN not configured")
    headers = {"access-token": token, "client-id": client_id,
               "Content-Type": "application/json", "Accept": "application/json"}
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(ROLLING_URL, headers=headers, json=body, timeout=30)
        except requests.exceptions.RequestException as exc:
            raise ProviderError(f"network error calling Dhan: {exc}") from exc
        if resp.status_code == 429:
            if attempt < max_retries:
                time.sleep(5.0 * (attempt + 1))
                continue
            raise ProviderError("Dhan rate-limited this request (429) repeatedly -- wait and retry")
        if not resp.ok:
            raise ProviderError(f"Dhan HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(f"Dhan returned non-JSON response: {exc}") from exc
        if data.get("errorMessage"):  # no {"status":"success"} wrapper on this endpoint
            raise ProviderError(f"Dhan error response: {json.dumps(data)[:300]}")
        return data.get("data") or {}
    raise ProviderError("unreachable")  # pragma: no cover


def _off_str(off: int) -> str:
    return "ATM" if off == 0 else f"ATM{off:+d}"


def _parse_strikes(spec: str) -> list[int]:
    """'ATM' -> [0]; 'ATM-2..ATM+2' -> [-2,-1,0,1,2]; 'ATM+1' -> [1]."""
    def one(tok: str) -> int:
        tok = tok.strip().upper()
        if tok == "ATM":
            return 0
        m = re.fullmatch(r"ATM([+-]\d+)", tok)
        if not m:
            raise ValueError(f"bad strike token {tok!r} (want ATM / ATM+N / ATM-N)")
        return int(m.group(1))
    spec = (spec or "ATM").replace(" ", "")
    if ".." in spec:
        a, b = (one(t) for t in spec.split("..", 1))
        lo, hi = min(a, b), max(a, b)
        return list(range(lo, hi + 1))
    return [one(spec)]


def _date(s: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(s)
    except (TypeError, ValueError):
        raise ValueError(f"bad date {s!r} (want YYYY-MM-DD)")


# --- action: download -------------------------------------------------------------
def action_download(symbol: str, date_from: str, date_to: str, interval: int = 5,
                    strikes: str = "ATM", expiry_flag: str = "WEEK", expiry_code: int = 1) -> dict:
    """Chunked (<=30 day) pulls of both CE and PE for each strike offset,
    upserted into SQLite. `to` is INCLUSIVE here (converted to Dhan's exclusive
    toDate internally). ~3s throttle between Dhan calls."""
    d_from, d_to = _date(date_from), _date(date_to)
    if d_to < d_from:
        raise ValueError("`to` must be >= `from`")
    if interval not in VALID_INTERVALS:
        raise ValueError(f"interval must be one of {sorted(VALID_INTERVALS)}")
    expiry_flag = expiry_flag.upper()
    if expiry_flag not in ("WEEK", "MONTH"):
        raise ValueError("expiry_flag must be WEEK or MONTH")
    offsets = _parse_strikes(strikes)
    sym = symbol.upper()
    scrip, seg, _lot = _resolve_symbol(sym)
    instrument = "OPTIDX" if seg == "IDX_I" else "OPTSTK"
    exchange = "BSE_FNO" if sym == "SENSEX" else "NSE_FNO"

    # chunk [from, to] into <=30-day windows; Dhan toDate is non-inclusive
    to_excl = d_to + datetime.timedelta(days=1)
    chunks = []
    cur = d_from
    while cur < to_excl:
        end = min(cur + datetime.timedelta(days=MAX_CHUNK_DAYS), to_excl)
        chunks.append((cur, end))
        cur = end

    con = _db()
    calls = upserted = 0
    errors = []
    t0 = time.time()
    try:
        for c_from, c_to in chunks:
            for off in offsets:
                for opt_type, drv in (("CE", "CALL"), ("PE", "PUT")):
                    body = {
                        "exchangeSegment": exchange, "interval": str(interval),
                        "securityId": str(scrip), "instrument": instrument,
                        "expiryCode": int(expiry_code), "expiryFlag": expiry_flag,
                        "strike": _off_str(off), "drvOptionType": drv,
                        "requiredData": ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
                        "fromDate": c_from.isoformat(), "toDate": c_to.isoformat(),
                    }
                    if calls:
                        time.sleep(THROTTLE_SECONDS)
                    calls += 1
                    try:
                        data = _rolling_post(body)
                    except ProviderError as exc:
                        errors.append(f"{_off_str(off)} {opt_type} {c_from}..{c_to}: {exc}")
                        continue
                    leg = data.get("ce" if opt_type == "CE" else "pe") or {}
                    ts = leg.get("timestamp") or []
                    if not ts:
                        errors.append(f"{_off_str(off)} {opt_type} {c_from}..{c_to}: no bars returned")
                        continue

                    def col(name):
                        v = leg.get(name) or []
                        return v if len(v) == len(ts) else [None] * len(ts)
                    rows = list(zip(
                        [sym] * len(ts), [expiry_flag] * len(ts), [int(expiry_code)] * len(ts),
                        [off] * len(ts), [opt_type] * len(ts), [interval] * len(ts),
                        [int(t) for t in ts],
                        col("open"), col("high"), col("low"), col("close"),
                        col("volume"), col("oi"), col("iv"), col("spot"), col("strike"),
                    ))
                    con.executemany(
                        "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                    con.commit()
                    upserted += len(rows)
    finally:
        con.close()
    return {
        "symbol": sym, "expiry_flag": expiry_flag, "expiry_code": int(expiry_code),
        "interval": interval, "from": d_from.isoformat(), "to": d_to.isoformat(),
        "strikes": [_off_str(o) for o in offsets], "chunks": len(chunks),
        "calls_made": calls, "bars_upserted": upserted, "errors": errors,
        "seconds": round(time.time() - t0, 1),
    }


# --- action: status ---------------------------------------------------------------
def _ist_str(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts + IST_OFFSET, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")


def action_status() -> dict:
    con = _db()
    try:
        rows = con.execute(
            "SELECT symbol, expiry_flag, expiry_code, strike_off, opt_type, interval_min,"
            " COUNT(*), MIN(ts), MAX(ts) FROM bars"
            " GROUP BY symbol, expiry_flag, expiry_code, strike_off, opt_type, interval_min"
            " ORDER BY symbol, strike_off, opt_type").fetchall()
        total = con.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    finally:
        con.close()
    coverage = [{
        "symbol": r[0], "expiry_flag": r[1], "expiry_code": r[2],
        "strike": _off_str(r[3]), "strike_off": r[3], "opt_type": r[4],
        "interval": r[5], "bars": r[6],
        "first_ist": _ist_str(r[7]), "last_ist": _ist_str(r[8]),
        "first_ts": r[7], "last_ts": r[8],
    } for r in rows]
    return {"db_path": str(DB_PATH), "total_bars": total, "coverage": coverage}


# --- action: bars (raw explorer) ----------------------------------------------------
def _load_bars(con, symbol, expiry_flag, expiry_code, strike_off, opt_type, interval,
               d_from: datetime.date | None = None, d_to: datetime.date | None = None) -> list[dict]:
    q = ("SELECT ts, open, high, low, close, volume, oi, iv, spot, strike FROM bars"
         " WHERE symbol=? AND expiry_flag=? AND expiry_code=? AND strike_off=?"
         " AND opt_type=? AND interval_min=?")
    args = [symbol, expiry_flag, expiry_code, strike_off, opt_type, interval]
    if d_from:  # IST calendar day -> UTC epoch bounds
        q += " AND ts >= ?"
        args.append(int(datetime.datetime(d_from.year, d_from.month, d_from.day,
                                          tzinfo=datetime.timezone.utc).timestamp()) - IST_OFFSET)
    if d_to:
        q += " AND ts < ?"
        args.append(int(datetime.datetime(d_to.year, d_to.month, d_to.day,
                                          tzinfo=datetime.timezone.utc).timestamp()) - IST_OFFSET + 86400)
    q += " ORDER BY ts"
    cols = ("ts", "open", "high", "low", "close", "volume", "oi", "iv", "spot", "strike")
    return [dict(zip(cols, r)) for r in con.execute(q, args)]


def action_bars(symbol: str, strike: str = "ATM", opt_type: str = "CE", interval: int = 5,
                expiry_flag: str = "WEEK", expiry_code: int = 1,
                date_from: str | None = None, date_to: str | None = None, limit: int = 500) -> dict:
    off = _parse_strikes(strike)[0]
    con = _db()
    try:
        bars = _load_bars(con, symbol.upper(), expiry_flag.upper(), int(expiry_code), off,
                          opt_type.upper(), interval,
                          _date(date_from) if date_from else None,
                          _date(date_to) if date_to else None)
    finally:
        con.close()
    bars = bars[:max(int(limit), 1)]
    for b in bars:
        b["ist"] = _ist_str(b["ts"])
    return {"symbol": symbol.upper(), "strike": _off_str(off), "opt_type": opt_type.upper(),
            "interval": interval, "expiry_flag": expiry_flag.upper(),
            "expiry_code": int(expiry_code), "count": len(bars), "bars": bars}


# --- engine ------------------------------------------------------------------------
def _fill_cost(premium: float, units: float, is_buy: bool, costs: dict) -> float:
    turnover = premium * units
    brok = costs["brokerage_per_order"]
    txn = turnover * costs["txn_fee_pct"]
    sebi = turnover * costs["sebi_per_crore"] / 1e7
    gst = costs["gst_pct"] * (brok + txn)
    stt = 0.0 if is_buy else turnover * costs["stt_sell_pct"]
    stamp = turnover * costs["stamp_buy_pct"] if is_buy else 0.0
    return brok + txn + sebi + gst + stt + stamp


def _hm(hhmm: str) -> int:
    h, _, m = hhmm.partition(":")
    return int(h) * 60 + int(m)


def _bar_hm(ts: int) -> int:
    ist = ts + IST_OFFSET
    return (ist % 86400) // 60


def _bar_day(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts + IST_OFFSET, datetime.timezone.utc).strftime("%Y-%m-%d")


def _by_day(bars: list[dict]) -> dict[str, list[dict]]:
    days: dict[str, list[dict]] = {}
    for b in bars:
        days.setdefault(_bar_day(b["ts"]), []).append(b)
    return days


def _stats(trades: list[dict]) -> dict:
    """Post-cost summary + equity curve + daily P&L from a trade list."""
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    curve = []
    daily: dict[str, float] = {}
    wins, losses = [], []
    for t in sorted(trades, key=lambda x: x["exit_ts"]):
        equity += t["net_pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        curve.append({"ts": t["exit_ts"], "date": t["date"], "equity": round(equity, 2)})
        daily[t["date"]] = daily.get(t["date"], 0.0) + t["net_pnl"]
        (wins if t["net_pnl"] > 0 else losses).append(t["net_pnl"])
    n = len(trades)
    gross_win, gross_loss = sum(wins), -sum(losses)
    return {
        "trades": n,
        "win_rate": round(len(wins) / n, 4) if n else 0.0,
        "expectancy": round((gross_win - gross_loss) / n, 2) if n else 0.0,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0),
        "max_drawdown": round(max_dd, 2),
        "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "total_net_pnl": round(gross_win - gross_loss, 2),
        "total_costs": round(sum(t["costs"] for t in trades), 2),
        "equity_curve": curve,
        "daily_pnl": [{"date": d, "net_pnl": round(p, 2)} for d, p in sorted(daily.items())],
    }


def _run_long(con, symbol, expiry_flag, expiry_code, interval, side, strike_off,
              entry_time, eod_time, sl_pct, target_pct, units, costs,
              d_from=None, d_to=None) -> list[dict]:
    """Strategy 1: directional long CE/PE. Entry at first bar >= entry_time
    (bar open); SL = sl_pct premium loss, target = target_pct premium gain
    (intrabar via low/high; SL checked FIRST when both hit in one bar --
    conservative); EOD flat at eod_time (bar close)."""
    bars = _load_bars(con, symbol, expiry_flag, expiry_code, strike_off, side, interval, d_from, d_to)
    if not bars:
        raise ValueError("no cached bars for these params -- run action=download first (see action=status)")
    entry_hm, eod_hm = _hm(entry_time), _hm(eod_time)
    trades = []
    for day, day_bars in sorted(_by_day(bars).items()):
        day_bars = [b for b in day_bars if b["close"] is not None]
        entry_bar = next((b for b in day_bars if _bar_hm(b["ts"]) >= entry_hm), None)
        if entry_bar is None or not entry_bar.get("open"):
            continue
        entry = entry_bar["open"]
        sl_price = entry * (1 - sl_pct)
        tgt_price = entry * (1 + target_pct)
        exit_price = exit_ts = None
        reason = "eod"
        last = entry_bar
        for b in day_bars:
            if b["ts"] < entry_bar["ts"] or _bar_hm(b["ts"]) > eod_hm:
                continue
            last = b
            if b["low"] is not None and b["low"] <= sl_price:
                exit_price, exit_ts, reason = sl_price, b["ts"], "sl"
                break
            if b["high"] is not None and b["high"] >= tgt_price:
                exit_price, exit_ts, reason = tgt_price, b["ts"], "target"
                break
        if exit_price is None:
            exit_price, exit_ts = last["close"], last["ts"]
        cost = _fill_cost(entry, units, True, costs) + _fill_cost(exit_price, units, False, costs)
        gross = (exit_price - entry) * units
        trades.append({
            "date": day, "side": side, "strike": entry_bar.get("strike"),
            "strike_off": strike_off,
            "entry_ts": entry_bar["ts"], "entry_ist": _ist_str(entry_bar["ts"]),
            "exit_ts": exit_ts, "exit_ist": _ist_str(exit_ts),
            "entry": round(entry, 2), "exit": round(exit_price, 2), "exit_reason": reason,
            "units": units, "gross_pnl": round(gross, 2), "costs": round(cost, 2),
            "net_pnl": round(gross - cost, 2),
            "entry_iv": entry_bar.get("iv"), "entry_spot": entry_bar.get("spot"),
        })
    return trades


def _run_straddle(con, symbol, expiry_flag, expiry_code, interval, entry_time, eod_time,
                  units, costs, d_from=None, d_to=None) -> list[dict]:
    """Strategy 2: short ATM straddle with re-strike roll. Sell ATM CE+PE at the
    first bar >= entry_time; when the rolling series' strike changes (spot moved
    to a new ATM), close and re-enter; EOD flat.
    ponytail: the rolling-ATM series only quotes the CURRENT ATM strike, so the
    old straddle is closed at the LAST bar of the old strike (prev bar close) --
    one-bar slippage approximation; exact would need per-strike fixed series."""
    ce = _load_bars(con, symbol, expiry_flag, expiry_code, 0, "CE", interval, d_from, d_to)
    pe = _load_bars(con, symbol, expiry_flag, expiry_code, 0, "PE", interval, d_from, d_to)
    if not ce or not pe:
        raise ValueError("no cached ATM CE+PE bars -- run action=download first")
    pe_by_ts = {b["ts"]: b for b in pe}
    joint = [(c, pe_by_ts[c["ts"]]) for c in ce if c["ts"] in pe_by_ts
             and c["close"] is not None and pe_by_ts[c["ts"]]["close"] is not None]
    entry_hm, eod_hm = _hm(entry_time), _hm(eod_time)
    trades = []
    days: dict[str, list] = {}
    for pair in joint:
        days.setdefault(_bar_day(pair[0]["ts"]), []).append(pair)
    for day, pairs in sorted(days.items()):
        pairs = [p for p in pairs if entry_hm <= _bar_hm(p[0]["ts"]) <= eod_hm]
        if not pairs:
            continue

        def open_pos(pair):
            c, p = pair
            return {"strike": c.get("strike"), "ce_entry": c["open"] or c["close"],
                    "pe_entry": p["open"] or p["close"], "entry_ts": c["ts"]}

        def close_pos(pos, pair, reason):
            c, p = pair
            ce_exit, pe_exit = c["close"], p["close"]
            gross = ((pos["ce_entry"] - ce_exit) + (pos["pe_entry"] - pe_exit)) * units
            cost = (_fill_cost(pos["ce_entry"], units, False, costs) + _fill_cost(ce_exit, units, True, costs)
                    + _fill_cost(pos["pe_entry"], units, False, costs) + _fill_cost(pe_exit, units, True, costs))
            trades.append({
                "date": day, "strike": pos["strike"],
                "entry_ts": pos["entry_ts"], "entry_ist": _ist_str(pos["entry_ts"]),
                "exit_ts": c["ts"], "exit_ist": _ist_str(c["ts"]),
                "ce_entry": round(pos["ce_entry"], 2), "pe_entry": round(pos["pe_entry"], 2),
                "ce_exit": round(ce_exit, 2), "pe_exit": round(pe_exit, 2),
                "exit_reason": reason, "units": units,
                "gross_pnl": round(gross, 2), "costs": round(cost, 2),
                "net_pnl": round(gross - cost, 2),
            })

        pos = open_pos(pairs[0])
        prev = pairs[0]
        for pair in pairs[1:]:
            if pair[0].get("strike") != pos["strike"]:
                close_pos(pos, prev, "roll")     # exit at last bar of the OLD strike
                pos = open_pos(pair)             # re-enter at the new ATM
            prev = pair
        close_pos(pos, prev, "eod")
    return trades


def _costs_from(param: str | None) -> dict:
    costs = dict(DEFAULT_COSTS)
    if param:
        try:
            overrides = json.loads(param)
        except ValueError:
            raise ValueError("costs param must be a JSON object")
        unknown = set(overrides) - set(costs)
        if unknown:
            raise ValueError(f"unknown cost keys: {sorted(unknown)} (valid: {sorted(costs)})")
        costs.update({k: float(v) for k, v in overrides.items()})
    return costs


def _engine_params(q) -> dict:
    symbol = q("symbol", "NIFTY").upper()
    lot_param = q("lot_size")
    if lot_param:
        lot = int(lot_param)
    else:
        try:
            lot = _resolve_symbol(symbol)[2] or 75
        except Exception:
            lot = 75  # offline fallback; override with &lot_size=
    return {
        "symbol": symbol,
        "expiry_flag": q("expiry_flag", "WEEK").upper(),
        "expiry_code": int(q("expiry_code", "1")),
        "interval": int(q("interval", "5")),
        "entry_time": q("entry_time", "09:20"),
        "eod_time": q("eod_time", "15:15"),
        "sl_pct": float(q("sl_pct", "0.20")),
        "target_pct": float(q("target_pct", "0.40")),
        "lots": int(q("lots", "1")),
        "lot_size": lot,
        "side": q("side", "CE").upper(),
        "strike_off": _parse_strikes(q("strike", "ATM"))[0],
        "d_from": _date(q("from")) if q("from") else None,
        "d_to": _date(q("to")) if q("to") else None,
        "costs": _costs_from(q("costs")),
    }


def action_backtest(q) -> dict:
    p = _engine_params(q)
    strategy = q("strategy", "long").lower()
    units = p["lot_size"] * p["lots"]
    con = _db()
    try:
        if strategy == "long":
            trades = _run_long(con, p["symbol"], p["expiry_flag"], p["expiry_code"], p["interval"],
                               p["side"], p["strike_off"], p["entry_time"], p["eod_time"],
                               p["sl_pct"], p["target_pct"], units, p["costs"], p["d_from"], p["d_to"])
        elif strategy == "straddle":
            trades = _run_straddle(con, p["symbol"], p["expiry_flag"], p["expiry_code"], p["interval"],
                                   p["entry_time"], p["eod_time"], units, p["costs"], p["d_from"], p["d_to"])
        else:
            raise ValueError("strategy must be 'long' or 'straddle'")
    finally:
        con.close()
    stats = _stats(trades)
    equity_curve = stats.pop("equity_curve")
    daily_pnl = stats.pop("daily_pnl")
    params_out = {k: v for k, v in p.items() if k not in ("d_from", "d_to")}
    params_out["from"] = p["d_from"].isoformat() if p["d_from"] else None
    params_out["to"] = p["d_to"].isoformat() if p["d_to"] else None
    return {"strategy": strategy, "params": params_out, "summary": stats,
            "trades": trades, "daily_pnl": daily_pnl, "equity_curve": equity_curve}


# --- action: replay (the differentiator) ---------------------------------------------
def action_replay(q) -> dict:
    """Run Strategy 1, then re-run the assessor's verdict on each entry snapshot
    (bar premium/iv/spot/strike at entry) and bucket outcomes by verdict tier.

    Assessor inputs derived exactly like the live page derives them:
      * SL/target on the UNDERLYING via first-order delta approx from the
        premium SL/target (the page's slForPremiumLoss logic): for a CE,
        sl_underlying = spot - premium*sl_pct/|delta|, target the mirror.
      * days-to-expiry = calendar days from the entry date to the next weekly
        expiry weekday (&expiry_weekday=, default 3 = Thursday, matching
        MockDhanProvider) -- rollingoption doesn't return the expiry date.
        ponytail: calibration knob, set 1 if NIFTY weeklies move to Tuesday.
      * IV straight from the bar's iv[]; bars with no IV are skipped and
        counted in skipped_no_iv (no back-solving -- garbage-in guard)."""
    p = _engine_params(q)
    expiry_weekday = int(q("expiry_weekday", "3"))
    units = p["lot_size"] * p["lots"]
    # B3: horizonDays = the ACTUAL intraday hold (entry_time -> eod_time), as a
    # fraction of a calendar day -- trades are flattened EOD, they do not carry
    # to expiry, so PoP/theta-gate must be scoped to this window (not `days`).
    horizon_days = max(_hm(p["eod_time"]) - _hm(p["entry_time"]), 0) / 1440.0
    con = _db()
    try:
        trades = _run_long(con, p["symbol"], p["expiry_flag"], p["expiry_code"], p["interval"],
                           p["side"], p["strike_off"], p["entry_time"], p["eod_time"],
                           p["sl_pct"], p["target_pct"], units, p["costs"], p["d_from"], p["d_to"])
    finally:
        con.close()

    tiers: dict[str, dict] = {}
    replayed = []
    skipped = 0
    for t in trades:
        iv, spot, strike = t.get("entry_iv"), t.get("entry_spot"), t.get("strike")
        if not iv or not spot or not strike:
            skipped += 1
            continue
        entry_date = datetime.date.fromisoformat(t["date"])
        days = (expiry_weekday - entry_date.weekday()) % 7  # 0 on expiry day itself
        t_years = max(days, 0) / 365.0
        delta = bs_greeks(spot, strike, iv, max(t_years, 1e-4), p["side"])["delta"]
        if abs(delta) < 1e-4:
            skipped += 1
            continue
        prem_sl = t["entry"] * p["sl_pct"]
        prem_tgt = t["entry"] * p["target_pct"]
        sign = 1.0 if p["side"] == "CE" else -1.0
        sl_u = spot - sign * prem_sl / abs(delta)
        tgt_u = spot + sign * prem_tgt / abs(delta)
        # horizonDays can't exceed days-to-expiry on expiry day itself (days=0).
        this_horizon = min(horizon_days, days) if days > 0 else 0.0
        a = assess({"spot": spot, "strike": strike, "type": p["side"], "iv": iv, "days": days,
                    "premium": t["entry"], "slUnderlying": sl_u, "targetUnderlying": tgt_u,
                    "lotSize": p["lot_size"], "lots": p["lots"], "horizonDays": this_horizon})
        rr = a["metrics"]["rr"]
        replayed.append({
            "date": t["date"], "verdict": a["verdict"],
            "rr": round(rr, 3) if math.isfinite(rr) else None,
            "pop": round(a["metrics"]["pop"], 4),
            "theta_pct_of_prem": round(a["metrics"]["thetaPctOfPrem"], 4),
            "theta_cost_pct_of_reward": round(a["metrics"]["thetaCostPctOfReward"], 4)
                if math.isfinite(a["metrics"]["thetaCostPctOfReward"]) else None,
            "horizon_days": round(a["metrics"]["horizonDays"], 4),
            "forward": round(a["metrics"]["forward"], 2), "forward_source": a["metrics"]["forwardSource"],
            "days_to_expiry": days, "entry": t["entry"], "exit": t["exit"],
            "exit_reason": t["exit_reason"], "net_pnl": t["net_pnl"],
        })
        tier = tiers.setdefault(a["verdict"], {"trades": 0, "wins": 0, "net_pnl": 0.0,
                                               "_rr": [], "_pop": []})
        tier["trades"] += 1
        tier["wins"] += 1 if t["net_pnl"] > 0 else 0
        tier["net_pnl"] += t["net_pnl"]
        if math.isfinite(rr):
            tier["_rr"].append(rr)
        tier["_pop"].append(a["metrics"]["pop"])

    for tier in tiers.values():
        n = tier["trades"]
        rr_list, pop_list = tier.pop("_rr"), tier.pop("_pop")
        tier["win_rate"] = round(tier["wins"] / n, 4)
        tier["expectancy"] = round(tier["net_pnl"] / n, 2)
        tier["net_pnl"] = round(tier["net_pnl"], 2)
        tier["avg_rr"] = round(sum(rr_list) / len(rr_list), 3) if rr_list else None
        tier["avg_pop"] = round(sum(pop_list) / n, 4)

    params_out = {k: v for k, v in p.items() if k not in ("d_from", "d_to")}
    params_out["from"] = p["d_from"].isoformat() if p["d_from"] else None
    params_out["to"] = p["d_to"].isoformat() if p["d_to"] else None
    params_out["expiry_weekday"] = expiry_weekday
    return {"strategy": "long", "params": params_out, "trade_count": len(replayed),
            "skipped_no_iv": skipped, "tiers": tiers, "trades": replayed}


# --- dispatch / HTTP ------------------------------------------------------------------
def dispatch(qs: dict) -> dict:
    """Route a parsed query-string dict (parse_qs shape) to an action. Raises
    LocalOnlyError / SymbolNotFoundError / ProviderError / ValueError for the
    HTTP layer to map (501 / 404 / 502 / 400)."""
    def q(name, default=None):
        v = (qs.get(name) or [None])[0]
        return v.strip() if isinstance(v, str) and v.strip() else default

    action = (q("action") or "").lower()
    if action == "download":
        for req in ("symbol", "from", "to"):
            if not q(req):
                raise ValueError(f"{req} query param is required for action=download")
        return action_download(q("symbol"), q("from"), q("to"), int(q("interval", "5")),
                               q("strikes", "ATM"), q("expiry_flag", "WEEK"), int(q("expiry_code", "1")))
    if action == "status":
        return action_status()
    if action == "bars":
        if not q("symbol"):
            raise ValueError("symbol query param is required for action=bars")
        return action_bars(q("symbol"), q("strike", "ATM"), q("type", "CE"), int(q("interval", "5")),
                           q("expiry_flag", "WEEK"), int(q("expiry_code", "1")),
                           q("from"), q("to"), int(q("limit", "500")))
    if action == "backtest":
        return action_backtest(q)
    if action == "replay":
        return action_replay(q)
    raise ValueError("action must be 'download', 'status', 'bars', 'backtest' or 'replay'")


class handler(BaseHTTPRequestHandler):
    """Vercel-convention handler kept for uniformity with other api/*.py --
    on Vercel every action 501s (LocalOnlyError) by design."""

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, status: int, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        try:
            self._send_json(200, dispatch(qs))
        except LocalOnlyError as exc:
            self._send_json(501, {"error": str(exc)})
        except SymbolNotFoundError as exc:
            self._send_json(404, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except ProviderError as exc:
            self._send_json(502, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})


if __name__ == "__main__":
    # Self-check: verdict-math parity anchors + engine plumbing (no network).
    _load_env_file()

    # Anchors from scripts/options_math.js self-test inputs.
    a = assess({"spot": 25000, "strike": 25100, "type": "CE", "iv": 13, "days": 3, "premium": 90,
                "slUnderlying": 24850, "targetUnderlying": 25350, "lotSize": 75, "lots": 1})
    assert a["verdict"] in ("Favorable", "Marginal", "Unfavorable"), a
    bad = assess({"spot": 25000, "strike": 25100, "type": "CE", "iv": 13, "days": 3, "premium": 90,
                  "slUnderlying": 25200, "targetUnderlying": 24800, "lotSize": 75, "lots": 1})
    assert bad["verdict"] == "Check inputs" and bad["warnings"], bad
    # Put-call parity on the ported bs_price (spot-fallback forward == old plain BS
    # numerically, by the standard BS <-> Black-76 equivalence).
    S, K, iv, T = 25000, 25000, 13, 7 / 365
    parity = S - K * math.exp(-0.065 * T)
    assert abs((bs_price(S, K, iv, T, "CE") - bs_price(S, K, iv, T, "PE")) - parity) < 1e-3
    # probTouch bounds + touch >= ITM-style sanity.
    pt = prob_touch(S, 25200, iv, T)
    assert 0.0 <= pt <= 1.0
    # Greeks parity with the JS conventions (Black-76 delta now carries e^-rT).
    g = bs_greeks(S, K, iv, T, "CE")
    assert 0 < g["delta"] < 1 and g["theta"] < 0 and g["gamma"] > 0 and g["vega"] > 0

    # --- B1: impliedForward -----------------------------------------------------
    fwd_cp = implied_forward(25000, 130, 100, 7 / 365, 0.065, 25000)
    expect_fwd = 25000 + (130 - 100) * math.exp(0.065 * 7 / 365)
    assert abs(fwd_cp["forward"] - expect_fwd) < 1e-6 and fwd_cp["source"] == "parity", fwd_cp
    fwd_fb = implied_forward(25000, None, None, 7 / 365, 0.065, 24950)
    assert abs(fwd_fb["forward"] - 24950 * math.exp(0.065 * 7 / 365)) < 1e-6 and fwd_fb["source"] == "spot-fallback"
    # Round-trip: price off a forward, feed those ltps back, recover F.
    Frt, Krt, iv_rt, Trt = 25137.42, 25100, 14, 5 / 365
    Crt = bs_price(None, Krt, iv_rt, Trt, "CE", forward=Frt)
    Prt = bs_price(None, Krt, iv_rt, Trt, "PE", forward=Frt)
    Frec = implied_forward(Krt, Crt, Prt, Trt, 0.065, Frt)["forward"]
    assert abs(Frec - Frt) < 1e-6, (Frec, Frt)
    # Black-76 delta = e^-rT * N(d1) (discounted, unlike old raw N(d1)).
    gF = bs_greeks(None, K, iv, T, "CE", forward=S * math.exp(0.065 * T))
    assert 0 < gF["delta"] < math.exp(-0.065 * T), gF

    # --- B2/B3: horizonDays shortens PoP; hold-scoped theta gate is reachable ---
    a_full = assess({"spot": 25000, "strike": 25100, "type": "CE", "iv": 15, "days": 5, "premium": 90,
                      "slUnderlying": 24850, "targetUnderlying": 25350, "lotSize": 75, "lots": 1, "horizonDays": 5})
    a_short = assess({"spot": 25000, "strike": 25100, "type": "CE", "iv": 15, "days": 5, "premium": 90,
                       "slUnderlying": 24850, "targetUnderlying": 25350, "lotSize": 75, "lots": 1, "horizonDays": 0.25})
    assert a_short["metrics"]["pop"] < a_full["metrics"]["pop"], (a_short["metrics"]["pop"], a_full["metrics"]["pop"])
    weekly_atm = assess({"spot": 25000, "strike": 25000, "type": "CE", "iv": 12, "days": 1, "premium": 45,
                          "slUnderlying": 24940, "targetUnderlying": 25120, "lotSize": 75, "lots": 1, "horizonDays": 0.4})
    assert weekly_atm["metrics"]["thetaPctOfPrem"] >= 0.08, weekly_atm  # proves the OLD gate was unreachable
    assert weekly_atm["metrics"]["thetaCostPctOfReward"] <= 0.25, weekly_atm  # NEW gate is reachable
    weekly_atm_closer = assess({"spot": 25000, "strike": 25000, "type": "CE", "iv": 10, "days": 1, "premium": 40,
                                 "slUnderlying": 24975, "targetUnderlying": 25045, "lotSize": 75, "lots": 1, "horizonDays": 0.3})
    assert weekly_atm_closer["verdict"] == "Favorable", weekly_atm_closer  # Favorable is actually populated
    # Strike-spec parser.
    assert _parse_strikes("ATM-2..ATM+2") == [-2, -1, 0, 1, 2]
    assert _parse_strikes("ATM") == [0] and _parse_strikes("ATM+3") == [3]
    # Cost model: 1 lot NIFTY buy at 100 -> brokerage 20 + txn 2.63 + gst ~4.07 + stamp 0.23 + sebi ~0.01
    c = _fill_cost(100, 75, True, DEFAULT_COSTS)
    assert 26 < c < 28, c
    print("options_backtest self-checks passed.")
    print("verdict for JS self-test trade:", a["verdict"],
          "| rr=%.2f pop=%.3f thetaPct=%.4f" % (a["metrics"]["rr"], a["metrics"]["pop"],
                                                a["metrics"]["thetaPctOfPrem"]))
