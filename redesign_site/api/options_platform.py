"""Unified Options Platform engine: GET /api/options_platform?symbol=NIFTY[&expiry=...]

One endpoint that turns a live option chain into a decision view for BOTH sides
of the book, consumed by options_platform.html (the "Options Cockpit"):

  chain (api/options_chain.py, live Dhan w/ mock fallback)
    -> features   (PCR near/far, OI concentration, max pain, walls, ATM IV,
                   IV skew, expected move, aggregate delta-OI)
    -> regime     (rule-based: TRENDING_BULL/BEAR, HIGH/LOW_VOLATILITY,
                   GAMMA_PINNING + OI state matrix)  [taxonomy borrowed from the
                   AI-trader repo research -- concepts only, code is ours]
    -> buy view   (directional bias + scored long-premium candidates,
                   suppressed in regimes where buying premium bleeds)
    -> sell view  (entry gate + prospective iron condor / butterfly, REUSING
                   dhan_ironcondor's strategy.py + black76.py -- not a rewrite)

Analysis/paper only: this module places NO orders and imports nothing from
dhan_ironcondor/execution.py. Margin is the flat paper estimate from the condor
config and is labelled "estimated" in the payload.

Local-first (scripts/dev_server.py). The Vercel-style `handler` class at the
bottom exists for parity with sibling api/*.py files, but this module imports
api.options_chain + dhan_ironcondor/, so unlike its siblings it is NOT
self-contained for isolated serverless bundling.

Caveat: dhan_ironcondor's modules import each other by bare name
(`from black76 import delta`), so we register them in sys.modules under those
names ("config", "black76", "strategy"). Nothing else in the dev-server process
imports those module names today; revisit if that changes.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import math
import statistics
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.options_chain import (  # noqa: E402
    SYMBOL_MAP, ConfigError, ProviderError, SymbolNotFoundError,
    fetch_expiry_list, fetch_option_chain, _load_env_file,
)
from api.index_ohlc import fetch_index_ohlc  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")

# Yahoo symbols for prev-close lookup. Only mappings verified elsewhere in this
# repo are listed (see api/index_ohlc.py usage); unlisted indices degrade to
# prev_close=null rather than risking a wrong symbol.
YAHOO_INDEX = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN"}

# --- regime thresholds (v1 rule-based; tune here, not inline) ------------------
HIGH_VOL_ATM_IV = 18.0          # % IV
HIGH_VOL_EXP_MOVE_PCT = 1.5     # ATM straddle as % of spot
HIGH_VOL_DAY_MOVE_PCT = 1.2     # |move vs prev close| %
TREND_MOVE_PCT = 0.6            # directional day move threshold %
WEAK_TREND_MOVE_PCT = 0.25      # low-confidence fallback threshold %
LOW_VOL_ATM_IV = 12.0
LOW_VOL_EXP_MOVE_PCT = 0.45
PIN_MAX_PAIN_PCT = 0.25         # |spot-max_pain| as % of spot
PIN_MAX_DTE = 1                 # calendar days to expiry
PIN_MIN_CONCENTRATION = 0.10    # top strike's share of total chain OI
OI_STATE_MIN_MOVE_PCT = 0.10    # below this the tape is "flat" for the OI matrix
OI_STATE_MIN_DOI_FRAC = 0.01    # |sum(dOI)| must exceed 1% of total OI
BUY_DELTA_LO, BUY_DELTA_HI, BUY_DELTA_SWEET = 0.30, 0.65, 0.45

_IC = None  # lazily-loaded (config, black76, strategy) modules from dhan_ironcondor/


def _ic_modules():
    """Load dhan_ironcondor's pure modules once, preserving their bare-name
    imports (see module docstring caveat). Never touches execution.py."""
    global _IC
    if _IC is None:
        loaded = {}
        for name in ("config", "black76", "strategy"):
            spec = importlib.util.spec_from_file_location(
                name, ROOT / "dhan_ironcondor" / f"{name}.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod  # strategy.py does `from black76 import ...`
            spec.loader.exec_module(mod)
            loaded[name] = mod
        _IC = (loaded["config"], loaded["black76"], loaded["strategy"])
    return _IC


# --- small helpers --------------------------------------------------------------
def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN guard


def _rnd(x, n=2):
    return None if x is None else round(x, n)


def _mid_spread_pct(leg: dict):
    bid, ask = _f(leg.get("bid")), _f(leg.get("ask"))
    if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    return (ask - bid) / mid * 100.0 if mid > 0 else None


def _prev_close(symbol: str) -> float | None:
    ysym = YAHOO_INDEX.get(symbol)
    if ysym is None:
        ysym = f"{symbol}.NS" if symbol not in SYMBOL_MAP else None
    if ysym is None:
        return None
    data = fetch_index_ohlc(ysym)
    bars = (data or {}).get("bars") or []
    if len(bars) < 2:
        return None
    # If the latest daily bar is today's (still-forming or just-closed) bar,
    # prev close is the bar before it; otherwise the latest bar IS the prev day.
    today = dt.datetime.now(IST).strftime("%Y-%m-%d")
    last = bars[-1]
    return _f(last[4]) if last[0] != today else _f(bars[-2][4])


# --- features -------------------------------------------------------------------
def _compute_features(strikes: list[dict], spot: float, expiry: str) -> dict:
    ks = [s["strike"] for s in strikes]
    by_k = {s["strike"]: s for s in strikes}
    diffs = [b - a for a, b in zip(ks, ks[1:]) if b > a]
    interval = statistics.median(diffs) if diffs else None
    atm = min(ks, key=lambda k: abs(k - spot))

    def oi(leg):
        return _f(leg.get("oi")) or 0.0

    ce_oi_total = sum(oi(s["ce"]) for s in strikes)
    pe_oi_total = sum(oi(s["pe"]) for s in strikes)
    near = [s for s in strikes if interval and abs(s["strike"] - atm) <= 2 * interval]
    ce_oi_near = sum(oi(s["ce"]) for s in near)
    pe_oi_near = sum(oi(s["pe"]) for s in near)

    total_oi = ce_oi_total + pe_oi_total
    per_strike_oi = {k: oi(v["ce"]) + oi(v["pe"]) for k, v in by_k.items()}
    concentration = (max(per_strike_oi.values()) / total_oi) if total_oi > 0 else None

    # Max pain: strike K minimizing total intrinsic payout owed by writers at expiry.
    max_pain = None
    if total_oi > 0:
        def payout(K):
            return sum(oi(by_k[k]["ce"]) * max(0.0, K - k) + oi(by_k[k]["pe"]) * max(0.0, k - K)
                       for k in ks)
        max_pain = min(ks, key=payout)

    call_wall = max(ks, key=lambda k: oi(by_k[k]["ce"])) if ce_oi_total > 0 else None
    put_wall = max(ks, key=lambda k: oi(by_k[k]["pe"])) if pe_oi_total > 0 else None

    atm_row = by_k[atm]
    ivs = [v for v in (_f(atm_row["ce"].get("iv")), _f(atm_row["pe"].get("iv"))) if v]
    atm_iv = sum(ivs) / len(ivs) if ivs else None

    iv_skew = None
    if interval:
        put_k = min(ks, key=lambda k: abs(k - (atm - 2 * interval)))
        call_k = min(ks, key=lambda k: abs(k - (atm + 2 * interval)))
        pv, cv = _f(by_k[put_k]["pe"].get("iv")), _f(by_k[call_k]["ce"].get("iv"))
        if pv and cv:
            iv_skew = pv - cv

    straddle = None
    ce_ltp, pe_ltp = _f(atm_row["ce"].get("ltp")), _f(atm_row["pe"].get("ltp"))
    if ce_ltp and pe_ltp:
        straddle = ce_ltp + pe_ltp

    # Aggregate OI change -- only when the provider passed prev_oi through
    # (live Dhan only; mock has none). None => OI state UNKNOWN, never guessed.
    d_oi = 0.0
    have_prev = False
    for s in strikes:
        for side in ("ce", "pe"):
            cur, prev = _f(s[side].get("oi")), _f(s[side].get("prev_oi"))
            if cur is not None and prev is not None:
                d_oi += cur - prev
                have_prev = True

    return {
        "interval": interval, "atm_strike": atm,
        "pcr_near": _rnd(pe_oi_near / ce_oi_near, 3) if ce_oi_near > 0 else None,
        "pcr_far": _rnd(pe_oi_total / ce_oi_total, 3) if ce_oi_total > 0 else None,
        "oi_concentration": _rnd(concentration, 3),
        "max_pain": max_pain, "call_wall": call_wall, "put_wall": put_wall,
        "atm_iv": _rnd(atm_iv), "iv_skew": _rnd(iv_skew),
        "expected_move_pts": _rnd(straddle),
        "expected_move_pct": _rnd(straddle / spot * 100.0, 2) if straddle else None,
        "delta_oi_total": _rnd(d_oi, 0) if have_prev else None,
        "_total_oi": total_oi,
    }


# --- regime ----------------------------------------------------------------------
def _classify_regime(feat: dict, move_pct: float | None, dte: int) -> dict:
    reasons, label, conf = [], None, 0.0
    atm_iv, exp_pct = feat.get("atm_iv"), feat.get("expected_move_pct")

    if (atm_iv is not None and atm_iv >= HIGH_VOL_ATM_IV) or \
       (exp_pct is not None and exp_pct >= HIGH_VOL_EXP_MOVE_PCT) or \
       (move_pct is not None and abs(move_pct) >= HIGH_VOL_DAY_MOVE_PCT):
        label, conf = "HIGH_VOLATILITY", 0.75
        reasons.append(f"vol elevated: ATM IV {atm_iv}, expected move {exp_pct}%, day move {_rnd(move_pct)}%")
    elif move_pct is not None and move_pct >= TREND_MOVE_PCT:
        label, conf = "TRENDING_BULL", 0.7
        reasons.append(f"spot +{_rnd(move_pct)}% vs prev close (>= {TREND_MOVE_PCT}%)")
    elif move_pct is not None and move_pct <= -TREND_MOVE_PCT:
        label, conf = "TRENDING_BEAR", 0.7
        reasons.append(f"spot {_rnd(move_pct)}% vs prev close (<= -{TREND_MOVE_PCT}%)")
    elif (feat.get("max_pain") is not None and dte <= PIN_MAX_DTE
          and feat.get("oi_concentration") is not None
          and feat["oi_concentration"] >= PIN_MIN_CONCENTRATION
          and abs(feat["atm_strike"] - feat["max_pain"]) / feat["atm_strike"] * 100.0 <= PIN_MAX_PAIN_PCT):
        label, conf = "GAMMA_PINNING", 0.65
        reasons.append(f"expiry in {dte}d, max pain {feat['max_pain']} near spot, "
                       f"OI concentration {feat['oi_concentration']}")
    elif (atm_iv is not None and atm_iv <= LOW_VOL_ATM_IV) or \
         (exp_pct is not None and exp_pct <= LOW_VOL_EXP_MOVE_PCT):
        label, conf = "LOW_VOLATILITY", 0.65
        reasons.append(f"vol subdued: ATM IV {atm_iv}, expected move {exp_pct}%")
    elif move_pct is not None and abs(move_pct) >= WEAK_TREND_MOVE_PCT:
        label = "TRENDING_BULL" if move_pct > 0 else "TRENDING_BEAR"
        conf = 0.4
        reasons.append(f"weak drift {_rnd(move_pct)}% vs prev close (low confidence)")
    else:
        label, conf = "LOW_VOLATILITY", 0.35
        reasons.append("no threshold met; defaulting to rangebound (low confidence)")

    # OI state matrix (price direction x aggregate dOI) -- honest UNKNOWN when
    # prev-day OI or prev close is unavailable.
    d_oi, total_oi = feat.get("delta_oi_total"), feat.get("_total_oi") or 0.0
    if d_oi is None or move_pct is None:
        oi_state = "UNKNOWN"
        reasons.append("OI state unknown: prev-day OI or prev close unavailable")
    elif abs(move_pct) < OI_STATE_MIN_MOVE_PCT or abs(d_oi) < OI_STATE_MIN_DOI_FRAC * total_oi:
        oi_state = "NEUTRAL"
    elif move_pct > 0:
        oi_state = "LONG_BUILD_UP" if d_oi > 0 else "SHORT_COVERING"
    else:
        oi_state = "SHORT_BUILD_UP" if d_oi > 0 else "LONG_UNWINDING"

    if dte <= 0:
        reasons.append("expiry day (0DTE): theta decay is extreme and pin risk is high -- "
                       "premium selling favored; long premium is a scalp, not a hold")

    return {"label": label, "oi_state": oi_state, "confidence": conf, "reasons": reasons}


# --- buy view ---------------------------------------------------------------------
def _buy_view(strikes: list[dict], feat: dict, regime: dict) -> dict:
    label = regime["label"]
    bias = {"TRENDING_BULL": "CALLS", "TRENDING_BEAR": "PUTS"}.get(label, "NEUTRAL")
    suppressed = label in ("LOW_VOLATILITY", "GAMMA_PINNING")
    atm_iv = feat.get("atm_iv")

    def candidates(side: str) -> list[dict]:
        out = []
        for s in strikes:
            leg = s[side]
            ltp, delta = _f(leg.get("ltp")), _f(leg.get("delta"))
            if not ltp or delta is None:
                continue
            d = abs(delta)
            if not (BUY_DELTA_LO <= d <= BUY_DELTA_HI):
                continue
            spread = _mid_spread_pct(leg)
            iv, oi_v, vol = _f(leg.get("iv")), _f(leg.get("oi")), _f(leg.get("volume"))
            liq = 40.0 if spread is not None and spread <= 1.0 else \
                max(0.0, 40.0 - (spread - 1.0) * 10.0) if spread is not None else 10.0
            fit = max(0.0, 30.0 - abs(d - BUY_DELTA_SWEET) / 0.20 * 30.0)
            ivs = 15.0 if iv is None or atm_iv is None else \
                (30.0 if iv <= atm_iv + 2.0 else max(0.0, 30.0 - (iv - atm_iv - 2.0) * 5.0))
            why = [f"delta {d:.2f}"]
            why.append(f"spread {spread:.1f}%" if spread is not None else "no bid/ask")
            if iv is not None and atm_iv is not None and iv > atm_iv + 2.0:
                why.append(f"IV {iv:.1f} rich vs ATM {atm_iv:.1f}")
            out.append({"type": side.upper(), "strike": s["strike"], "ltp": _rnd(ltp),
                        "delta": _rnd(delta, 3), "iv": _rnd(iv), "oi": oi_v, "volume": vol,
                        "spread_pct": _rnd(spread, 2), "score": _rnd(liq + fit + ivs, 0),
                        "why": ", ".join(why)})
        out.sort(key=lambda c: -(c["score"] or 0))
        return out[:3]

    if bias == "CALLS":
        cands = candidates("ce")
    elif bias == "PUTS":
        cands = candidates("pe")
    else:
        cands = (candidates("ce") + candidates("pe"))
        cands.sort(key=lambda c: -(c["score"] or 0))
        cands = cands[:4]

    if suppressed:
        verdict = (f"{label.replace('_', ' ').title()}: long premium bleeds theta here -- "
                   "buying suppressed; premium selling is the higher-odds side today.")
    elif label == "HIGH_VOLATILITY":
        verdict = ("High volatility: directional longs can work but premium is expensive -- "
                   "size down, prefer tighter-delta strikes, beware IV crush.")
    elif bias != "NEUTRAL":
        verdict = f"{label.replace('_', ' ').title()}: favor {bias.lower()} on pullbacks; candidates below."
    else:
        verdict = "No directional edge from regime; both sides shown, wait for a trigger."

    return {"bias": bias, "suppressed": suppressed, "verdict": verdict, "candidates": cands}


# --- sell view ----------------------------------------------------------------------
# Condor ladder shown side-by-side: (label, target short delta). Wing is the
# config default for all three; the frontend slider overrides both live.
CONDOR_PRESETS = (("Wide", 0.10), ("Balanced", 0.20), ("Tight", 0.30))


def _sell_view(strikes, spot, expiry, symbol, lot_size, regime, move_pct, source, feat) -> dict:
    cfg, b76, strat = _ic_modules()
    atm_iv = feat.get("atm_iv")
    interval = feat.get("interval")
    notes = ["F approximated by cash spot (condor book prices off NIFTY FUT)",
             f"wing width {cfg.WING_WIDTH} pts (NIFTY-tuned)",
             f"POP is Black-76 model estimate at ATM IV {atm_iv}% -- not a guarantee",
             "margin is the flat paper estimate, NOT broker-confirmed"]

    gate_reasons, gate_ok = [], True
    if source != "live":
        gate_ok = False
        gate_reasons.append("demo/mock data -- never act on synthetic premiums")
    if regime["label"] in ("HIGH_VOLATILITY", "TRENDING_BULL", "TRENDING_BEAR") \
            and regime["confidence"] >= 0.6:
        gate_ok = False
        gate_reasons.append(f"regime {regime['label']} -- selling premium into vol/trend blocked")
    if move_pct is not None:
        ok, reason = strat.late_entry_ok(spot, spot / (1 + move_pct / 100.0),
                                         cfg.LATE_ENTRY_MAX_MOVE_PCT)
        gate_reasons.append(reason)
        gate_ok = gate_ok and ok
    else:
        gate_reasons.append("prev close unavailable -- move-vs-prev-close check skipped")
    if gate_ok:
        gate_reasons.insert(0, "all gates passed")

    if symbol not in SYMBOL_MAP:
        return {"gate": {"ok": False, "reasons": ["sell engine is index-tuned in v1; "
                                                  "stock condors not supported"]},
                "presets": [], "butterfly": {"ok": False, "reason": "index-only in v1"},
                "slim_chain": [], "chain_meta": None, "notes": notes}

    calls, puts = {}, {}
    for s in strikes:
        for side, book in (("ce", calls), ("pe", puts)):
            ltp, iv = _f(s[side].get("ltp")), _f(s[side].get("iv"))
            if ltp and iv and ltp > 0 and iv > 0:
                book[s["strike"]] = strat.ChainLeg(s["strike"], ltp, iv / 100.0)
    chain = strat.OptionChain(calls=calls, puts=puts)

    now = dt.datetime.now(IST)
    T = b76.time_to_expiry(now, dt.date.fromisoformat(expiry))
    lot = int(lot_size or cfg.LOT_SIZE)
    if not lot_size:
        notes.append(f"live lot size unavailable -- using config LOT_SIZE={cfg.LOT_SIZE} (VERIFY)")

    def pop(be_lo, be_hi):
        """Black-76 lognormal P(be_lo < F_T < be_hi) at a single ATM-IV vol.
        P(F_T < K) = N(-d2(K)); POP is the difference across the two breakevens."""
        sig = (atm_iv or 0.0) / 100.0
        if not (spot and be_lo and be_hi) or sig <= 0 or T <= 0:
            return None
        root = sig * math.sqrt(T)
        def p_below(K):
            return b76.N(-((math.log(spot / K) - 0.5 * sig * sig * T) / root))
        return _rnd(max(0.0, min(1.0, p_below(be_hi) - p_below(be_lo))), 3)

    def pack(spec, why, name, target_delta, wing):
        base = {"name": name, "short_delta_target": target_delta, "wing": wing}
        if spec is None:
            return {**base, "ok": False, "reason": why}
        credit = spec.net_credit
        max_loss_pts = wing - credit
        be_lo, be_hi = spec.short_put_k - credit, spec.short_call_k + credit
        return {**base, "ok": True,
                "short_call_k": spec.short_call_k, "long_call_k": spec.long_call_k,
                "short_put_k": spec.short_put_k, "long_put_k": spec.long_put_k,
                "net_credit_pts": _rnd(credit), "credit_rupees": _rnd(credit * lot, 0),
                "max_loss_pts": _rnd(max_loss_pts), "max_loss_rupees": _rnd(max_loss_pts * lot, 0),
                "breakeven_low": _rnd(be_lo), "breakeven_high": _rnd(be_hi),
                "pop": pop(be_lo, be_hi),
                "short_call_delta": _rnd(spec.short_call_delta, 3),
                "short_put_delta": _rnd(spec.short_put_delta, 3),
                "lots": 1, "margin_rupees": cfg.MARGIN_PER_LOT_PAPER,
                "margin_source": "estimated",
                "reward_risk": _rnd(credit / max_loss_pts, 2) if max_loss_pts > 0 else None}

    # Ladder of delta-anchored condors (spot for both OR bounds -> centered on
    # spot); min_credit=0 so a thin preset still renders for comparison -- the
    # gate above, not the credit floor, governs whether to act.
    presets = []
    for name, td in CONDOR_PRESETS:
        spec, why = strat.select_condor_at(chain, spot, spot, spot, T,
                                           cfg.RISK_FREE_RATE, td, cfg.WING_WIDTH, min_credit=0)
        presets.append(pack(spec, why, name, td, cfg.WING_WIDTH))
    fly_spec, fly_why = strat.select_butterfly(chain, spot, spot, spot, T, cfg.RISK_FREE_RATE)

    # Slim chain (strikes within ~8% of spot with quotes) for the client-side
    # delta/wing slider -- it recomputes strikes/credit/POP without a round-trip.
    slim = []
    for s in strikes:
        if abs(s["strike"] - spot) > 0.08 * spot:
            continue
        ce_ltp, ce_d = _f(s["ce"].get("ltp")), _f(s["ce"].get("delta"))
        pe_ltp, pe_d = _f(s["pe"].get("ltp")), _f(s["pe"].get("delta"))
        if ce_ltp is None and pe_ltp is None:
            continue
        slim.append({"k": s["strike"], "ce_ltp": _rnd(ce_ltp), "ce_delta": _rnd(ce_d, 3),
                     "pe_ltp": _rnd(pe_ltp), "pe_delta": _rnd(pe_d, 3)})

    meta = {"spot": spot, "T": T, "atm_iv": atm_iv, "interval": interval,
            "wing_default": cfg.WING_WIDTH, "lot": lot, "min_credit": cfg.MIN_CREDIT_PTS}
    return {"gate": {"ok": gate_ok, "reasons": gate_reasons},
            "presets": presets,
            "butterfly": pack(fly_spec, fly_why, "Butterfly", None, cfg.WING_WIDTH),
            "slim_chain": slim, "chain_meta": meta, "notes": notes}


# --- orchestration --------------------------------------------------------------------
def build_platform_view(symbol: str, expiry: str | None = None) -> dict:
    symbol = symbol.upper()
    el = fetch_expiry_list(symbol)
    expiries = el.get("expiries") or []
    if not expiry:
        expiry = expiries[0] if expiries else None
    if not expiry:
        raise ProviderError("no expiries available")

    ch = fetch_option_chain(symbol, expiry)
    spot, strikes = _f(ch.get("spot")), ch.get("strikes") or []
    if not spot or not strikes:
        raise ProviderError("chain response missing spot/strikes")
    source = ch.get("source", "mock")
    lot_size = ch.get("lot_size") or el.get("lot_size")

    prev_close = _prev_close(symbol)
    move_pct = _rnd((spot - prev_close) / prev_close * 100.0, 2) if prev_close else None

    now = dt.datetime.now(IST)
    dte = (dt.date.fromisoformat(expiry) - now.date()).days
    feat = _compute_features(strikes, spot, expiry)
    regime = _classify_regime(feat, move_pct, dte)
    buy = _buy_view(strikes, feat, regime)
    sell = _sell_view(strikes, spot, expiry, symbol, lot_size, regime, move_pct, source, feat)

    out = {"source": source, "symbol": symbol, "expiry": expiry, "expiries": expiries,
           "spot": spot, "lot_size": lot_size, "prev_close": _rnd(prev_close),
           "move_pct": move_pct, "dte": dte, "expiry_day": dte <= 0,
           "generated_at": now.isoformat(timespec="seconds"),
           "mode": "paper",
           "features": {k: v for k, v in feat.items() if not k.startswith("_")},
           "regime": regime, "buy": buy, "sell": sell}
    if ch.get("demo_reason"):
        out["demo_reason"] = ch["demo_reason"]
    return out


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        symbol = (qs.get("symbol") or ["NIFTY"])[0].strip().upper()
        expiry = (qs.get("expiry") or [""])[0].strip() or None
        try:
            self._send_json(200, build_platform_view(symbol, expiry))
        except SymbolNotFoundError as exc:
            self._send_json(404, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except ConfigError as exc:
            self._send_json(500, {"error": str(exc)})
        except Exception as exc:  # ProviderError et al
            self._send_json(502, {"error": str(exc)})


if __name__ == "__main__":
    _load_env_file()
    sym = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    view = build_platform_view(sym, sys.argv[2] if len(sys.argv) > 2 else None)
    print(json.dumps(view, indent=2, default=str))
