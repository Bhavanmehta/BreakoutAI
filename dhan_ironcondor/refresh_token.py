"""
Mint a FRESH DhanHQ access token (valid 24h) with zero manual steps.

No browser, no OAuth consent, no app_secret. Uses the official DhanHQ SDK's
PIN + TOTP flow:  DhanLogin.generate_token(pin, totp)  ->  POST
https://auth.dhan.co/app/generateAccessToken?dhanClientId=..&pin=..&totp=..

The 6-digit TOTP is computed locally from your base32 TOTP secret via pyotp,
so the same secret regenerates a valid code forever -- the script can mint a
brand-new token on demand.

Reads from the environment (set these in set-dhan-env.local.ps1):
    DHAN_CLIENT_ID     stable, e.g. 1112605732
    DHAN_PIN           your Dhan login PIN
    DHAN_TOTP_SECRET   base32 secret from Dhan's "How to set up TOTP" (one-time)

On success: prints ONLY the raw access token to stdout (so a shell can capture
it), and all diagnostics to stderr. Exit 0 on success, non-zero on failure.

    python refresh_token.py                 # prints token
    $env:DHAN_ACCESS_TOKEN = (python refresh_token.py)   # capture in PowerShell
"""
import os
import sys

try:
    import pyotp
except ImportError:
    print("ERROR: pyotp not installed. Run: pip install pyotp", file=sys.stderr)
    sys.exit(3)

try:
    from dhanhq import DhanLogin
except ImportError as e:
    print(f"ERROR: could not import DhanLogin from dhanhq ({e}). "
          f"Upgrade: pip install -U 'dhanhq>=2.2'", file=sys.stderr)
    sys.exit(3)


# Response-shape is not rigidly documented, so pull the token defensively:
# try known keys, then any nested dict, then the longest JWT-ish string.
_TOKEN_KEYS = ("accessToken", "access_token", "accesstoken", "token", "jwt")


def _extract_token(resp):
    if isinstance(resp, str):
        return resp.strip() or None
    if not isinstance(resp, dict):
        return None
    for k in _TOKEN_KEYS:
        v = resp.get(k)
        if isinstance(v, str) and len(v) > 20:
            return v.strip()
    # nested (e.g. {"data": {"accessToken": ...}})
    for v in resp.values():
        if isinstance(v, dict):
            got = _extract_token(v)
            if got:
                return got
    # last resort: longest token-ish string in the payload
    best = None
    for v in resp.values():
        if isinstance(v, str) and len(v) > 40 and "." in v:
            if best is None or len(v) > len(best):
                best = v.strip()
    return best


def main() -> int:
    client_id = os.environ.get("DHAN_CLIENT_ID", "").strip()
    pin = os.environ.get("DHAN_PIN", "").strip()
    secret = os.environ.get("DHAN_TOTP_SECRET", "").strip().replace(" ", "")

    missing = [n for n, v in (("DHAN_CLIENT_ID", client_id),
                              ("DHAN_PIN", pin),
                              ("DHAN_TOTP_SECRET", secret)) if not v]
    if missing:
        print(f"ERROR: missing env var(s): {', '.join(missing)}. "
              f"Set them in set-dhan-env.local.ps1 and dot-source it.",
              file=sys.stderr)
        return 2

    try:
        totp = pyotp.TOTP(secret).now()
    except Exception as e:
        print(f"ERROR: could not compute TOTP from DHAN_TOTP_SECRET "
              f"(is it the base32 secret, not a 6-digit code?): {e}",
              file=sys.stderr)
        return 2

    try:
        resp = DhanLogin(client_id).generate_token(pin, totp)
    except Exception as e:
        print(f"ERROR: generate_token failed: {e}", file=sys.stderr)
        return 1

    token = _extract_token(resp)
    if not token:
        print(f"ERROR: no access token found in response: {resp!r}", file=sys.stderr)
        return 1

    print(f"[refresh_token] Fresh token minted (valid ~24h): ...{token[-6:]}",
          file=sys.stderr)
    print(token)   # ONLY the token on stdout
    return 0


if __name__ == "__main__":
    sys.exit(main())
