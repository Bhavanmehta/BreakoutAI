"""Vercel Cron endpoint: GET/POST /api/refresh_dhan_token -- proactively mint a
fresh DhanHQ access token and cache it in Upstash so the option-chain and
backtest endpoints always have a valid token without any manual paste.

The lazy mint in api/options_chain.get_dhan_token() already refreshes on demand;
this cron just keeps the SHARED Upstash token warm (re-minting before expiry) so
the first visitor of a quiet day never eats mint latency and the token never
lapses. Scheduled daily in vercel.json "crons" (token lives ~24h).

Self-contained (no sibling imports) because Vercel bundles each api/*.py in
isolation -- the mint + cache logic here MUST stay in sync with
api/options_chain.get_dhan_token() (same DHAN_AUTH_URL / cache key / exp math).

Secrets (all STABLE -- set once, never rotated by hand):
    DHAN_CLIENT_ID     stable account id, e.g. 1112605732
    DHAN_PIN           Dhan login PIN
    DHAN_TOTP_SECRET   base32 TOTP secret from Dhan's "Enable TOTP" (one-time)

Optional auth: if CRON_SECRET is set, callers must send
`Authorization: Bearer $CRON_SECRET` (Vercel Cron sends this automatically).

Local smoke test (needs backend/.env with the 3 secrets above):
    python api/refresh_dhan_token.py
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import struct
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import requests

DHAN_AUTH_URL = "https://auth.dhan.co/app/generateAccessToken"
DHAN_TOKEN_CACHE_KEY = "dhan:access_token:v1"
DHAN_TOKEN_REFRESH_BUFFER = 3600        # cache expires this long before the JWT does
DHAN_TOKEN_FALLBACK_TTL = 20 * 3600     # used only if the JWT carries no exp claim
_TOKEN_KEYS = ("accessToken", "access_token", "accesstoken", "token", "jwt")


def _env(name: str) -> str | None:
    val = os.environ.get(name)
    if val and len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1]
    return val


def _totp_now(secret: str) -> str:
    """RFC-6238 TOTP (SHA-1, 30s step, 6 digits) from a base32 secret."""
    s = secret.strip().replace(" ", "").upper()
    s += "=" * (-len(s) % 8)
    key = base64.b32decode(s)
    counter = struct.pack(">Q", int(time.time()) // 30)
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def _extract_token(resp) -> str | None:
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
    best = None
    for v in resp.values():
        if isinstance(v, str) and len(v) > 40 and "." in v:
            if best is None or len(v) > len(best):
                best = v.strip()
    return best


def _jwt_exp(token: str) -> float | None:
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        return float(exp) if exp else None
    except Exception:
        return None


def _upstash(*command: str):
    url = _env("UPSTASH_REDIS_REST_URL")
    token = _env("UPSTASH_REDIS_REST_TOKEN")
    if not url or not token:
        raise RuntimeError("UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN not set")
    resp = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json=list(command), timeout=10)
    resp.raise_for_status()
    body = resp.json()
    if body.get("error"):
        raise RuntimeError(f"Upstash error: {body['error']}")
    return body.get("result")


def _mint() -> str:
    client_id = _env("DHAN_CLIENT_ID") or _env("DHAN_Client_ID")
    pin = _env("DHAN_PIN")
    secret = _env("DHAN_TOTP_SECRET")
    if not (client_id and pin and secret):
        raise RuntimeError("need DHAN_CLIENT_ID + DHAN_PIN + DHAN_TOTP_SECRET to mint a token")
    totp = _totp_now(secret)
    resp = requests.post(
        DHAN_AUTH_URL,
        params={"dhanClientId": client_id, "pin": pin, "totp": totp},
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(f"Dhan token mint HTTP {resp.status_code}: {resp.text[:300]}")
    token = _extract_token(resp.json())
    if not token:
        raise RuntimeError(f"no access token in Dhan mint response: {resp.text[:300]}")
    return token


def refresh() -> dict:
    """Mint a fresh token and write it to the shared Upstash cache. Returns a
    small status dict (never the raw token -- only a masked tail)."""
    token = _mint()
    now = time.time()
    exp = _jwt_exp(token) or (now + DHAN_TOKEN_FALLBACK_TTL)
    ttl = int(max(exp - now - DHAN_TOKEN_REFRESH_BUFFER, 60))
    _upstash("SET", DHAN_TOKEN_CACHE_KEY, json.dumps({"token": token, "exp": exp}), "EX", str(ttl))
    return {
        "status": "ok",
        "token_tail": token[-6:],
        "expires_at": int(exp),
        "expires_in_hours": round((exp - now) / 3600, 1),
        "cache_ttl_seconds": ttl,
    }


def _authorized(headers) -> bool:
    secret = _env("CRON_SECRET")
    if not secret:
        return True  # no secret configured -> open (fine for a personal tool)
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    return auth == f"Bearer {secret}"


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _run(self):
        if not _authorized(self.headers):
            self._send(401, {"status": "error", "error": "unauthorized"})
            return
        try:
            self._send(200, refresh())
        except Exception as exc:
            self._send(500, {"status": "error", "error": str(exc)})

    def do_GET(self):
        self._run()

    def do_POST(self):
        self._run()


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
    _load_env_file()
    print(json.dumps(refresh(), indent=2))
