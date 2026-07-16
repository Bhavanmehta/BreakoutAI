"""Vercel Python serverless function: GET /api/options_chain -- live index option-chain
data for the "Options Trade Assessor" (options_assessor.html), with a graceful
Mock fallback so the page NEVER hard-fails just because Dhan creds are missing/
expired/rate-limited on a given call.

Why this exists as its own endpoint (and not client-side fetch()):
Dhan's option-chain API needs an access-token + client-id sent as request HEADERS
on every call. The client-id is a fixed account id; the access-token expires ~24h
after issue and is now AUTO-MINTED on demand from three stable secrets (PIN + TOTP,
see get_dhan_token) and shared via Upstash -- no daily manual token paste. These
secrets must never reach the browser, so the assessor page cannot call api.dhan.co
directly. This function is the one place that holds/uses them.

Provider abstraction (this is the piece the original delivery was missing):
  DataProvider            -- interface: get_expiry_list(symbol), get_option_chain(symbol, expiry)
  LiveDhanProvider        -- real calls to api.dhan.co/v2/optionchain[/expirylist]
  MockDhanProvider        -- deterministic synthetic chain (pure Black-Scholes, no
                              network), used whenever Live fails for ANY reason
                              (creds missing, token expired, Dhan HTTP error, Dhan
                              rate-limit, network timeout) so the UI always renders
                              something rather than an error page.
Every response carries "source": "live" | "mock" (+ "demo_reason" when mock) so the
frontend can render an unmissable DEMO DATA badge -- this tool exists to size real
risk on real money, so silently showing synthetic numbers as if they were live
would be actively dangerous.

Dhan's option-chain endpoint is rate-limited to roughly 1 request per 3 seconds
per instrument; a short Upstash-backed cache (RATE_CACHE_TTL_SECONDS) absorbs
double-clicks/re-renders without hammering it. Cache is best-effort: if Upstash
isn't configured, every call just goes straight to Dhan (fine for a personal,
low-frequency tool).

Symbols: the 5 hardcoded indices in SYMBOL_MAP plus ANY NSE F&O stock (~210
underlyings, e.g. RELIANCE / HDFCBANK / DIXON). Stock symbols are resolved to
Dhan security-ids + lot sizes via Dhan's public scrip-master CSV, parsed once
and cached in Upstash for 24h (the raw CSV is ~27MB; the parsed map is ~10KB).
Verified live 2026-07-26: RELIANCE (NSE_EQ id 2885) returns real expiries.

Query params:
  GET /api/options_chain?action=expirylist&symbol=NIFTY
  GET /api/options_chain?action=chain&symbol=RELIANCE&expiry=2026-07-28
  GET /api/options_chain?action=symbols          -- all supported symbols + lot sizes

Self-contained (no sibling imports), same convention as every other api/*.py in
this repo -- Vercel's Python runtime bundles each file in isolation.

Local smoke test (needs backend/.env with DHAN_CLIENT_ID + DHAN_PIN + DHAN_TOTP_SECRET,
or a manual DHAN_ACCESS_TOKEN):
    python api/options_chain.py [SYMBOL] [EXPIRY]
"""
from __future__ import annotations
import json
import math
import os
import time
from abc import ABC, abstractmethod
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

DHAN_BASE = "https://api.dhan.co/v2"
RATE_CACHE_TTL_SECONDS = 15  # absorbs repeat clicks/re-renders without re-hitting Dhan's ~3s/instrument limit

# Dhan's UnderlyingScrip/UnderlyingSeg ids for the option-chain APIs. NIFTY is the
# one verified against a live call (2026-07-12); the others are the commonly
# documented Dhan index scrip ids but unverified end-to-end here -- if Dhan
# rejects one, LiveDhanProvider raises and the caller falls back to Mock (safe:
# never silently mis-map a symbol to the wrong chain).
SYMBOL_MAP = {
    "NIFTY": (13, "IDX_I"),
    "BANKNIFTY": (25, "IDX_I"),
    "FINNIFTY": (27, "IDX_I"),
    "MIDCPNIFTY": (442, "IDX_I"),
    "SENSEX": (51, "IDX_I"),
}

# Any NSE F&O *stock* is resolved dynamically: Dhan's public scrip-master CSV maps
# trading symbol -> NSE_EQ security id (the UnderlyingScrip for /optionchain) and
# option lot size. Parsed once, cached 24h in Upstash (+ warm-lambda memo) because
# the raw CSV is ~27MB but the parsed map is tiny.
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
FNO_MAP_CACHE_KEY = "optchain:fnomap:v1"
FNO_MAP_TTL_SECONDS = 24 * 3600

# Fallback index lot sizes if the scrip master is unreachable (SEBI revises these
# occasionally -- the live values from the master win whenever available).
INDEX_LOT_FALLBACK = {"NIFTY": 75, "BANKNIFTY": 35, "FINNIFTY": 65, "MIDCPNIFTY": 140, "SENSEX": 20}

# Fallback spot used by MockDhanProvider when no live/last-known spot is available
# at all (e.g. first-ever load, no query param). Rough, clearly-synthetic anchors --
# the mock chain is for UI/shape testing, never for sizing a real trade.
MOCK_DEFAULT_SPOT = {
    "NIFTY": 24200.0, "BANKNIFTY": 51500.0, "FINNIFTY": 23200.0,
    "MIDCPNIFTY": 12300.0, "SENSEX": 79500.0,
}
MOCK_DEFAULT_INTERVAL = {
    "NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50, "MIDCPNIFTY": 25, "SENSEX": 100,
}


class ProviderError(Exception):
    """Raised by a DataProvider when it cannot serve a real answer -- callers
    catch this and fall back to the next provider in the chain."""


class SymbolNotFoundError(ProviderError):
    """The symbol simply has no NSE-listed options (user error, not an outage).
    Must NOT fall back to mock data -- surfacing fake premiums for a symbol with
    no real chain would poison a trade assessment."""


# --- pure Black-Scholes (Python port of scripts/options_math.js, kept in sync
# by hand -- deliberately duplicated rather than shelling out to node, same
# self-containment rationale as the rest of api/*.py) --------------------------
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_d1_d2(spot: float, strike: float, iv_pct: float, t_years: float, r: float = 0.065):
    sigma = max(iv_pct, 0.01) / 100.0
    t = max(t_years, 1e-6)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    return d1, d2


def _bs_price(spot: float, strike: float, iv_pct: float, t_years: float, opt_type: str, r: float = 0.065) -> float:
    d1, d2 = _bs_d1_d2(spot, strike, iv_pct, t_years, r)
    t = max(t_years, 1e-6)
    if opt_type == "CE":
        return spot * _norm_cdf(d1) - strike * math.exp(-r * t) * _norm_cdf(d2)
    return strike * math.exp(-r * t) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def _bs_delta(spot: float, strike: float, iv_pct: float, t_years: float, opt_type: str, r: float = 0.065) -> float:
    d1, _ = _bs_d1_d2(spot, strike, iv_pct, t_years, r)
    return _norm_cdf(d1) if opt_type == "CE" else _norm_cdf(d1) - 1.0


# --- provider interface --------------------------------------------------------
class DataProvider(ABC):
    label: str

    @abstractmethod
    def get_expiry_list(self, symbol: str) -> list[str]: ...

    @abstractmethod
    def get_option_chain(self, symbol: str, expiry: str) -> dict: ...


class LiveDhanProvider(DataProvider):
    label = "live"

    def __init__(self):
        # client-id is a stable account id; accept either casing (legacy .env used
        # DHAN_Client_ID; the auto-mint creds use canonical DHAN_CLIENT_ID).
        self.client_id = _env("DHAN_CLIENT_ID") or _env("DHAN_Client_ID")
        # Access token is auto-minted from PIN+TOTP and shared via Upstash; falls
        # back to a manually-pasted token if the mint creds are absent.
        try:
            self.token = get_dhan_token()
        except ProviderError:
            self.token = _env("DHAN_ACCESS_TOKEN") or _env("DHAN_Access_TOKEN")
        if not self.client_id or not self.token:
            raise ProviderError(
                "Dhan not configured -- set DHAN_CLIENT_ID + DHAN_PIN + DHAN_TOTP_SECRET "
                "(auto-mint) or a manual DHAN_ACCESS_TOKEN")

    def _headers(self):
        return {
            "access-token": self.token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _underlying(self, symbol: str):
        scrip, seg, _lot = _resolve_symbol(symbol)
        return scrip, seg

    def _post(self, path: str, body: dict, _retried: bool = False) -> dict:
        try:
            resp = requests.post(f"{DHAN_BASE}{path}", headers=self._headers(), json=body, timeout=15)
        except requests.exceptions.RequestException as exc:
            raise ProviderError(f"network error calling Dhan: {exc}") from exc
        if resp.status_code in (401, 403) and not _retried:
            # Token was rejected (expired / invalidated early). Force one fresh
            # mint and retry before giving up and falling back to Mock.
            try:
                self.token = get_dhan_token(force_refresh=True)
            except ProviderError:
                pass
            else:
                return self._post(path, body, _retried=True)
        if resp.status_code == 429:
            raise ProviderError("Dhan rate-limited this request (429) -- wait a few seconds and retry")
        if not resp.ok:
            raise ProviderError(f"Dhan HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(f"Dhan returned non-JSON response: {exc}") from exc
        if data.get("status") != "success":
            raise ProviderError(f"Dhan error response: {json.dumps(data)[:300]}")
        return data

    def get_expiry_list(self, symbol: str) -> list[str]:
        scrip, seg = self._underlying(symbol)
        data = self._post("/optionchain/expirylist", {"UnderlyingScrip": scrip, "UnderlyingSeg": seg})
        expiries = data.get("data") or []
        if not expiries:
            raise ProviderError("Dhan returned an empty expiry list")
        return expiries

    def get_option_chain(self, symbol: str, expiry: str) -> dict:
        scrip, seg = self._underlying(symbol)
        data = self._post("/optionchain", {"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": expiry})
        payload = data.get("data") or {}
        spot = payload.get("last_price")
        oc = payload.get("oc") or {}
        if spot is None or not oc:
            raise ProviderError("Dhan option-chain response missing last_price/oc")

        def leg(raw: dict) -> dict:
            greeks = raw.get("greeks") or {}
            return {
                "ltp": raw.get("last_price"),
                "bid": raw.get("top_bid_price"),
                "ask": raw.get("top_ask_price"),
                "oi": raw.get("oi"),
                "volume": raw.get("volume"),
                "iv": raw.get("implied_volatility"),
                "prev_oi": raw.get("previous_oi"),
                "prev_volume": raw.get("previous_volume"),
                "delta": greeks.get("delta"),
                "theta": greeks.get("theta"),
                "gamma": greeks.get("gamma"),
                "vega": greeks.get("vega"),
            }

        strikes = []
        for k, v in oc.items():
            try:
                strike = float(k)
            except (TypeError, ValueError):
                continue
            strikes.append({"strike": strike, "ce": leg(v.get("ce") or {}), "pe": leg(v.get("pe") or {})})
        strikes.sort(key=lambda s: s["strike"])
        if not strikes:
            raise ProviderError("Dhan option-chain response had no usable strikes")
        return {"spot": spot, "strikes": strikes}


class MockDhanProvider(DataProvider):
    label = "mock"

    def get_expiry_list(self, symbol: str) -> list[str]:
        # Next 4 Thursdays (NSE's weekly-expiry weekday), starting from today/tomorrow.
        import datetime
        today = datetime.date.today()
        out = []
        d = today
        while len(out) < 4:
            d += datetime.timedelta(days=1)
            if d.weekday() == 3:  # Thursday
                out.append(d.isoformat())
        return out

    def get_option_chain(self, symbol: str, expiry: str) -> dict:
        import datetime
        sym = symbol.upper()
        spot = MOCK_DEFAULT_SPOT.get(sym)
        interval = MOCK_DEFAULT_INTERVAL.get(sym)
        if spot is None:  # unknown symbol => generic stock-ish anchor, clearly synthetic
            spot, interval = 1000.0, 10
        iv_pct = 13.0
        try:
            exp_date = datetime.date.fromisoformat(expiry)
            days = max((exp_date - datetime.date.today()).days, 1)
        except (ValueError, TypeError):
            days = 3
        t_years = days / 365.0
        atm = round(spot / interval) * interval
        strikes = []
        for i in range(-8, 9):
            k = atm + i * interval
            if k <= 0:
                continue
            ce_px = _bs_price(spot, k, iv_pct, t_years, "CE")
            pe_px = _bs_price(spot, k, iv_pct, t_years, "PE")
            ce_delta = _bs_delta(spot, k, iv_pct, t_years, "CE")
            pe_delta = _bs_delta(spot, k, iv_pct, t_years, "PE")
            strikes.append({
                "strike": float(k),
                "ce": {"ltp": round(ce_px, 2), "bid": round(ce_px * 0.98, 2), "ask": round(ce_px * 1.02, 2),
                       "oi": None, "volume": None, "iv": iv_pct, "delta": round(ce_delta, 4),
                       "theta": None, "gamma": None, "vega": None},
                "pe": {"ltp": round(pe_px, 2), "bid": round(pe_px * 0.98, 2), "ask": round(pe_px * 1.02, 2),
                       "oi": None, "volume": None, "iv": iv_pct, "delta": round(pe_delta, 4),
                       "theta": None, "gamma": None, "vega": None},
            })
        return {"spot": spot, "strikes": strikes}


# --- env / cache helpers (mirrors api/trades.py & api/verdict.py conventions) --
def _env(name: str) -> str | None:
    val = os.environ.get(name)
    if val and len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1]
    return val


class ConfigError(Exception):
    """UPSTASH_REDIS_REST_URL/TOKEN not set."""


def _upstash(*command: str):
    url = _env("UPSTASH_REDIS_REST_URL")
    token = _env("UPSTASH_REDIS_REST_TOKEN")
    if not url or not token:
        raise ConfigError("UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN not set")
    resp = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json=list(command), timeout=10)
    resp.raise_for_status()
    body = resp.json()
    if body.get("error"):
        raise RuntimeError(f"Upstash error: {body['error']}")
    return body.get("result")


def _cache_get(key: str) -> dict | None:
    try:
        raw = _upstash("GET", key)
        return json.loads(raw) if raw else None
    except Exception:
        return None  # cache is a best-effort speed-up, never a hard dependency


def _cache_set(key: str, value: dict) -> None:
    try:
        _upstash("SET", key, json.dumps(value), "EX", str(RATE_CACHE_TTL_SECONDS))
    except Exception:
        pass


# --- Dhan access-token auto-mint (PIN + TOTP), Redis-cached -------------------
# Dhan expires the option-chain access token ~24h after issue. Rather than a
# human pasting a fresh token into env every day, we mint one on demand from
# three STABLE secrets (DHAN_CLIENT_ID / DHAN_PIN / DHAN_TOTP_SECRET) and cache
# it in Upstash so every warm lambda AND every other endpoint (options_backtest,
# the refresh cron) share one token until it nears expiry.
#
# Replicates dhanhq.DhanLogin.generate_token() with raw HTTP + a stdlib RFC-6238
# TOTP (no dhanhq/pyotp dependency -- keeps this serverless bundle lean, same
# self-containment rationale as the rest of api/*.py):
#   POST https://auth.dhan.co/app/generateAccessToken?dhanClientId=..&pin=..&totp=..
DHAN_AUTH_URL = "https://auth.dhan.co/app/generateAccessToken"
DHAN_TOKEN_CACHE_KEY = "dhan:access_token:v1"
DHAN_TOKEN_REFRESH_BUFFER = 3600  # re-mint when <1h of validity remains
DHAN_TOKEN_FALLBACK_TTL = 20 * 3600  # cache lifetime if the JWT carries no exp
_DHAN_TOKEN_MEM: tuple[str, float] | None = None  # (token, exp_epoch) warm-lambda memo

_DHAN_TOKEN_KEYS = ("accessToken", "access_token", "accesstoken", "token", "jwt")


def _totp_now(secret: str) -> str:
    """RFC-6238 TOTP (SHA-1, 30s step, 6 digits) from a base32 secret -- the
    same 6-digit code Google Authenticator / pyotp produce, using only stdlib."""
    import base64
    import hashlib
    import hmac
    import struct
    s = secret.strip().replace(" ", "").upper()
    s += "=" * (-len(s) % 8)  # base32 wants length to be a multiple of 8
    key = base64.b32decode(s)
    counter = struct.pack(">Q", int(time.time()) // 30)
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def _extract_dhan_token(resp) -> str | None:
    """Pull the access token out of Dhan's mint response defensively (the exact
    shape isn't rigidly documented): known keys, then any nested dict, then the
    longest JWT-ish string."""
    if isinstance(resp, str):
        return resp.strip() or None
    if not isinstance(resp, dict):
        return None
    for k in _DHAN_TOKEN_KEYS:
        v = resp.get(k)
        if isinstance(v, str) and len(v) > 20:
            return v.strip()
    for v in resp.values():
        if isinstance(v, dict):
            got = _extract_dhan_token(v)
            if got:
                return got
    best = None
    for v in resp.values():
        if isinstance(v, str) and len(v) > 40 and "." in v:
            if best is None or len(v) > len(best):
                best = v.strip()
    return best


def _jwt_exp(token: str) -> float | None:
    """Best-effort: read the `exp` (epoch seconds) claim out of a JWT so we can
    cache the token until just before it actually expires. None if not a JWT."""
    import base64
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        return float(exp) if exp else None
    except Exception:
        return None


def _mint_dhan_token() -> str:
    """Mint a fresh Dhan access token from the 3 stable secrets (PIN + TOTP).
    Raises ProviderError on any failure so callers can fall back."""
    client_id = _env("DHAN_CLIENT_ID") or _env("DHAN_Client_ID")
    pin = _env("DHAN_PIN")
    secret = _env("DHAN_TOTP_SECRET")
    if not (client_id and pin and secret):
        raise ProviderError(
            "cannot auto-mint Dhan token -- need DHAN_CLIENT_ID + DHAN_PIN + DHAN_TOTP_SECRET")
    try:
        totp = _totp_now(secret)
    except Exception as exc:
        raise ProviderError(
            f"could not compute TOTP from DHAN_TOTP_SECRET (is it the base32 secret?): {exc}") from exc
    try:
        resp = requests.post(
            DHAN_AUTH_URL,
            params={"dhanClientId": client_id, "pin": pin, "totp": totp},
            timeout=15,
        )
    except requests.exceptions.RequestException as exc:
        raise ProviderError(f"network error minting Dhan token: {exc}") from exc
    if not resp.ok:
        raise ProviderError(f"Dhan token mint HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise ProviderError(f"Dhan token mint returned non-JSON: {exc}") from exc
    token = _extract_dhan_token(data)
    if not token:
        raise ProviderError(f"no access token in Dhan mint response: {json.dumps(data)[:300]}")
    return token


def _cache_set_token(token: str, exp: float) -> None:
    ttl = int(max(exp - time.time() - DHAN_TOKEN_REFRESH_BUFFER, 60))
    try:
        _upstash("SET", DHAN_TOKEN_CACHE_KEY, json.dumps({"token": token, "exp": exp}), "EX", str(ttl))
    except Exception:
        pass  # cache is a best-effort speed-up, never a hard dependency


def get_dhan_token(force_refresh: bool = False) -> str:
    """Return a valid Dhan access token, minting + caching as needed.

    Preference order (each falls through to the next on failure/expiry):
      1. warm-lambda memo (this process)
      2. Upstash cache shared across lambdas + endpoints
      3. mint a fresh one (PIN + TOTP) and write it to memo + Upstash
      4. a manually-pasted DHAN_ACCESS_TOKEN from env (legacy escape hatch)

    Raises ProviderError only when there is no usable token at all."""
    global _DHAN_TOKEN_MEM
    now = time.time()

    if not force_refresh:
        if _DHAN_TOKEN_MEM and _DHAN_TOKEN_MEM[1] - DHAN_TOKEN_REFRESH_BUFFER > now:
            return _DHAN_TOKEN_MEM[0]
        cached = _cache_get(DHAN_TOKEN_CACHE_KEY)
        if cached and cached.get("token") and cached.get("exp", 0) - DHAN_TOKEN_REFRESH_BUFFER > now:
            _DHAN_TOKEN_MEM = (cached["token"], cached["exp"])
            return cached["token"]

    try:
        token = _mint_dhan_token()
        exp = _jwt_exp(token) or (now + DHAN_TOKEN_FALLBACK_TTL)
        _DHAN_TOKEN_MEM = (token, exp)
        _cache_set_token(token, exp)
        return token
    except ProviderError:
        static = _env("DHAN_ACCESS_TOKEN") or _env("DHAN_Access_TOKEN")
        if static:
            return static  # may be stale -> Dhan call fails -> Mock fallback (safe)
        raise


# --- F&O symbol resolver (any NSE stock with options, not just indices) --------
_FNO_MEM: dict | None = None  # warm-lambda memo so we hit Upstash once per instance


def _build_fno_map() -> dict:
    """Parse Dhan's scrip master into {"stocks": {SYM: {"id", "lot"}}, "index_lots": {...}}.
    Streams the ~27MB CSV; the result is ~10KB. Raises ProviderError on any failure."""
    import csv
    try:
        resp = requests.get(SCRIP_MASTER_URL, timeout=90, stream=True)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise ProviderError(f"could not download Dhan scrip master: {exc}") from exc
    # The CSV response carries no charset header, so iter_lines(decode_unicode=True)
    # would yield bytes; decode explicitly instead.
    lines = (ln.decode("utf-8", "replace") if isinstance(ln, bytes) else ln
             for ln in resp.iter_lines())
    reader = csv.DictReader(lines)
    stock_lots: dict[str, int] = {}
    index_lots: dict[str, int] = {}
    eq_ids: dict[str, int] = {}
    for row in reader:
        if row.get("SEM_EXM_EXCH_ID") != "NSE":
            continue
        inst = row.get("SEM_INSTRUMENT_NAME")
        if inst in ("OPTSTK", "OPTIDX"):
            sym = (row.get("SEM_TRADING_SYMBOL") or "").split("-")[0].strip()
            tgt = stock_lots if inst == "OPTSTK" else index_lots
            if sym and sym not in tgt:
                try:
                    tgt[sym] = int(float(row.get("SEM_LOT_UNITS") or 0)) or 1
                except (TypeError, ValueError):
                    pass
        elif row.get("SEM_SEGMENT") == "E" and (row.get("SEM_SERIES") or "").strip() == "EQ":
            sym = (row.get("SEM_TRADING_SYMBOL") or "").strip()
            if sym and sym not in eq_ids:
                try:
                    eq_ids[sym] = int(row.get("SEM_SMST_SECURITY_ID") or 0)
                except (TypeError, ValueError):
                    pass
    stocks = {s: {"id": eq_ids[s], "lot": lot}
              for s, lot in stock_lots.items() if eq_ids.get(s)}
    if not stocks:
        raise ProviderError("scrip master parsed but yielded no F&O stock map")
    return {"stocks": stocks, "index_lots": index_lots}


def _get_fno_map() -> dict:
    global _FNO_MEM
    if _FNO_MEM is not None:
        return _FNO_MEM
    try:
        raw = _upstash("GET", FNO_MAP_CACHE_KEY)
        if raw:
            _FNO_MEM = json.loads(raw)
            return _FNO_MEM
    except Exception:
        pass  # cache optional -- fall through to a fresh build
    fno = _build_fno_map()
    try:
        _upstash("SET", FNO_MAP_CACHE_KEY, json.dumps(fno), "EX", str(FNO_MAP_TTL_SECONDS))
    except Exception:
        pass
    _FNO_MEM = fno
    return fno


def _resolve_symbol(symbol: str) -> tuple[int, str, int | None]:
    """-> (UnderlyingScrip, UnderlyingSeg, lot_size|None). Indices from SYMBOL_MAP,
    anything else looked up in the NSE F&O stock map (verified live: RELIANCE ->
    (2885, NSE_EQ), expirylist returns real dates)."""
    sym = symbol.upper()
    if sym in SYMBOL_MAP:
        scrip, seg = SYMBOL_MAP[sym]
        lot = INDEX_LOT_FALLBACK.get(sym)
        try:
            lot = int(_get_fno_map().get("index_lots", {}).get(sym) or lot)
        except Exception:
            pass  # fallback lot is fine
        return scrip, seg, lot
    try:
        info = _get_fno_map()["stocks"].get(sym)
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f"could not load NSE F&O symbol map: {exc}") from exc
    if not info:
        raise SymbolNotFoundError(
            f"{sym!r} has no NSE-listed options (not an F&O underlying). "
            "Supported: NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY/SENSEX + ~210 F&O stocks.")
    return int(info["id"]), "NSE_EQ", int(info["lot"])


def _lot_for(symbol: str) -> int | None:
    try:
        return _resolve_symbol(symbol)[2]
    except Exception:
        return None


# --- orchestration: try Live, fall back to Mock, always tag the source --------
def fetch_symbols() -> dict:
    """Every supported underlying + lot size, indices first (feeds the symbol picker)."""
    try:
        fno = _get_fno_map()
        idx = [{"symbol": s, "lot": int(fno.get("index_lots", {}).get(s) or INDEX_LOT_FALLBACK[s])}
               for s in SYMBOL_MAP]
        stocks = [{"symbol": s, "lot": int(v["lot"])} for s, v in sorted(fno["stocks"].items())]
        return {"source": "live", "symbols": idx + stocks}
    except Exception as exc:
        idx = [{"symbol": s, "lot": INDEX_LOT_FALLBACK[s]} for s in SYMBOL_MAP]
        return {"source": "mock", "demo_reason": str(exc), "symbols": idx}


def fetch_expiry_list(symbol: str) -> dict:
    cache_key = f"optchain:expiry:{symbol.upper()}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        expiries = LiveDhanProvider().get_expiry_list(symbol)
        result = {"source": "live", "symbol": symbol.upper(), "expiries": expiries}
    except SymbolNotFoundError:
        raise  # unknown symbol is a user error -- never mask it with mock data
    except ProviderError as exc:
        expiries = MockDhanProvider().get_expiry_list(symbol)
        result = {"source": "mock", "demo_reason": str(exc), "symbol": symbol.upper(), "expiries": expiries}

    result["lot_size"] = _lot_for(symbol)
    _cache_set(cache_key, result)
    return result


def fetch_option_chain(symbol: str, expiry: str) -> dict:
    cache_key = f"optchain:chain:{symbol.upper()}:{expiry}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        chain = LiveDhanProvider().get_option_chain(symbol, expiry)
        result = {"source": "live", "symbol": symbol.upper(), "expiry": expiry, **chain}
    except SymbolNotFoundError:
        raise  # unknown symbol is a user error -- never mask it with mock data
    except ProviderError as exc:
        chain = MockDhanProvider().get_option_chain(symbol, expiry)
        result = {"source": "mock", "demo_reason": str(exc), "symbol": symbol.upper(), "expiry": expiry, **chain}

    result["lot_size"] = _lot_for(symbol)
    _cache_set(cache_key, result)
    return result


class handler(BaseHTTPRequestHandler):
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
        action = (qs.get("action") or [""])[0].strip().lower()
        symbol = (qs.get("symbol") or ["NIFTY"])[0].strip().upper()
        try:
            if action == "expirylist":
                self._send_json(200, fetch_expiry_list(symbol))
                return
            if action == "chain":
                expiry = (qs.get("expiry") or [""])[0].strip()
                if not expiry:
                    raise ValueError("expiry query param is required for action=chain")
                self._send_json(200, fetch_option_chain(symbol, expiry))
                return
            if action == "symbols":
                self._send_json(200, fetch_symbols())
                return
            raise ValueError("action must be 'expirylist', 'chain' or 'symbols'")
        except SymbolNotFoundError as exc:
            self._send_json(404, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})


def _load_env_file():
    env_path = Path(__file__).resolve().parent.parent / "backend" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ.setdefault(key.strip(), val)


if __name__ == "__main__":
    import sys

    _load_env_file()
    sym = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    print(f"=== expiry list for {sym} ===")
    el = fetch_expiry_list(sym)
    print(json.dumps({k: v for k, v in el.items() if k != "expiries"} | {"expiries": el["expiries"][:5]}, indent=2))

    expiry = sys.argv[2] if len(sys.argv) > 2 else el["expiries"][0]
    print(f"\n=== option chain for {sym} {expiry} ===")
    ch = fetch_option_chain(sym, expiry)
    print(f"source={ch['source']} spot={ch['spot']} strikes={len(ch['strikes'])}"
          + (f" demo_reason={ch.get('demo_reason')}" if ch.get("demo_reason") else ""))
    atm = min(ch["strikes"], key=lambda s: abs(s["strike"] - ch["spot"]))
    print("ATM strike:", json.dumps(atm, indent=2))
