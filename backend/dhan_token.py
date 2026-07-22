"""
Dhan access-token manager for the backend scan.

Dhan access tokens are valid ~24h, but token GENERATION is throttled by Dhan to
about once every 2 minutes. So we cache the minted token to disk and only
re-mint when the cached one is missing or within EXPIRY_MARGIN of its real JWT
`exp`. This keeps the daily CI scan to a single mint per run, and lets local /
offline runs reuse one token across many process invocations (bash, PowerShell,
Python) without tripping the throttle.

Minting uses the official DhanHQ PIN + TOTP flow (no browser, no OAuth, no
app_secret) — the same mechanism dhan_ironcondor/refresh_token.py uses. It needs
three values, read from the environment (GitHub secrets in CI):

    DHAN_CLIENT_ID     stable numeric client id, e.g. 1112605732
    DHAN_PIN           your Dhan login PIN
    DHAN_TOTP_SECRET   base32 TOTP secret (one-time, from Dhan's "set up TOTP")

A caller-supplied DHAN_ACCESS_TOKEN env var, if present and still valid,
overrides minting entirely (lets you inject a token without the PIN/TOTP
secret — e.g. a short-lived CI secret). Any token obtained this way is also
written to the disk cache so sibling processes can reuse it.
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

# Cache lives next to this module; gitignored (never commit a live token).
_CACHE_PATH = Path(__file__).resolve().parent / ".dhan_token_cache.json"
_EXPIRY_MARGIN_SEC = 60 * 60      # treat as expired if within 1h of JWT exp
_MINT_THROTTLE_SEC = 130          # Dhan allows a fresh token ~once / 2 min

_mem_token: str | None = None     # in-process memo, avoids re-reading disk


class DhanAuthError(RuntimeError):
    """Raised when a usable Dhan token can neither be reused nor minted."""


# --------------------------------------------------------------------------- #
# token inspection
# --------------------------------------------------------------------------- #
def _jwt_exp(token: str) -> int | None:
    """Pull the `exp` (unix seconds) claim out of a JWT without verifying it.
    Returns None if the token isn't a decodable JWT."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)          # pad base64
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        return int(exp) if exp is not None else None
    except Exception:
        return None


def _token_valid(token: str | None) -> bool:
    if not token or len(token) < 40:
        return False
    exp = _jwt_exp(token)
    if exp is None:
        return True   # not a JWT we can read; assume usable (API will 401 if not)
    return time.time() < exp - _EXPIRY_MARGIN_SEC


# --------------------------------------------------------------------------- #
# disk cache
# --------------------------------------------------------------------------- #
def _read_cache() -> dict:
    try:
        return json.loads(_CACHE_PATH.read_text())
    except Exception:
        return {}


def _write_cache(token: str) -> None:
    try:
        _CACHE_PATH.write_text(json.dumps({
            "token": token,
            "client_id": os.environ.get("DHAN_CLIENT_ID", "").strip(),
            "minted_at": int(time.time()),
            "exp": _jwt_exp(token),
        }))
    except Exception:
        pass   # cache is an optimisation, never fatal


# --------------------------------------------------------------------------- #
# minting (PIN + TOTP)
# --------------------------------------------------------------------------- #
_TOKEN_KEYS = ("accessToken", "access_token", "accesstoken", "token", "jwt")


def _extract_token(resp):
    """DhanLogin's response shape isn't rigidly documented; pull the token
    defensively (known keys, then nested dicts)."""
    if isinstance(resp, str):
        return resp.strip() or None
    if not isinstance(resp, dict):
        return None
    for k in _TOKEN_KEYS:
        v = resp.get(k)
        if isinstance(v, str) and len(v) > 20:
            return v.strip()
    for v in resp.values():
        if isinstance(v, dict):
            got = _extract_token(v)
            if got:
                return got
    return None


def _mint() -> str:
    client_id = os.environ.get("DHAN_CLIENT_ID", "").strip()
    pin = os.environ.get("DHAN_PIN", "").strip()
    secret = os.environ.get("DHAN_TOTP_SECRET", "").strip().replace(" ", "")
    missing = [n for n, v in (("DHAN_CLIENT_ID", client_id),
                              ("DHAN_PIN", pin),
                              ("DHAN_TOTP_SECRET", secret)) if not v]
    if missing:
        raise DhanAuthError(
            f"cannot mint Dhan token; missing env var(s): {', '.join(missing)}")
    try:
        import pyotp
        from dhanhq import DhanLogin
    except ImportError as e:
        raise DhanAuthError(f"missing dependency for Dhan token mint: {e}")

    try:
        totp = pyotp.TOTP(secret).now()
    except Exception as e:
        raise DhanAuthError(
            f"could not compute TOTP (is DHAN_TOTP_SECRET the base32 secret, "
            f"not a 6-digit code?): {e}")

    try:
        resp = DhanLogin(client_id).generate_token(pin, totp)
    except Exception as e:
        raise DhanAuthError(f"DhanLogin.generate_token failed: {e}")

    token = _extract_token(resp)
    if not token:
        raise DhanAuthError(f"no access token in mint response: {resp!r}")
    return token


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def get_access_token(force: bool = False) -> str:
    """Return a valid Dhan access token, reusing a cached one when possible and
    minting a fresh one only when necessary. Raises DhanAuthError if no token
    can be obtained."""
    global _mem_token

    # 1. explicit override wins (and gets cached for sibling processes)
    env_tok = os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
    if env_tok and not force and _token_valid(env_tok):
        if _read_cache().get("token") != env_tok:
            _write_cache(env_tok)
        _mem_token = env_tok
        return env_tok

    # 2. in-process memo
    if _mem_token and not force and _token_valid(_mem_token):
        return _mem_token

    # 3. disk cache
    cache = _read_cache()
    cached = cache.get("token")
    if cached and not force and _token_valid(cached):
        _mem_token = cached
        return cached

    # 4. mint fresh, honouring Dhan's ~2-minute generation throttle. If we're
    #    inside the throttle window but still hold a (near-expiry) token, reuse
    #    it rather than getting rejected.
    since_last = time.time() - cache.get("minted_at", 0)
    if since_last < _MINT_THROTTLE_SEC:
        if cached:
            _mem_token = cached
            return cached
        time.sleep(_MINT_THROTTLE_SEC - since_last)

    token = _mint()
    _write_cache(token)
    _mem_token = token
    return token


def make_client(token: str | None = None):
    """Return a ready-to-use dhanhq client. Handles both the newer
    dhanhq(DhanContext(...)) constructor and the older dhanhq(client_id, token)
    one, so we don't care which SDK version is installed."""
    client_id = os.environ.get("DHAN_CLIENT_ID", "").strip()
    token = token or get_access_token()
    import dhanhq as _pkg
    try:
        from dhanhq import DhanContext
        return _pkg.dhanhq(DhanContext(client_id, token))
    except Exception:
        return _pkg.dhanhq(client_id, token)


if __name__ == "__main__":
    import sys
    try:
        t = get_access_token(force="--force" in sys.argv)
        exp = _jwt_exp(t)
        left = int((exp - time.time()) / 60) if exp else None
        print(f"[dhan_token] OK: ...{t[-6:]}  "
              f"({'expires in ~%dh%dm' % (left // 60, left % 60) if left else 'exp unknown'})")
    except DhanAuthError as e:
        print(f"[dhan_token] FAILED: {e}", file=sys.stderr)
        sys.exit(1)
