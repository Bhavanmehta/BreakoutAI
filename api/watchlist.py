"""Vercel Python serverless function: GET/POST/DELETE /api/watchlist.

The personal "My Watchlist" feature's only live backend endpoint. Gated by a single
shared secret (WATCHLIST_SECRET env var) rather than real per-user accounts: there's
only one real user, so this just needs to keep randoms from writing to the store if
they find the URL, not protect a multi-tenant system. The frontend sends the secret
as an X-Watchlist-Secret header, cached in localStorage after a one-time prompt.

Storage is one Upstash Redis hash, key "watchlist", field = symbol, value = a JSON
string {"symbol", "date_added", "entry_price"}. A hash (not one big JSON blob) means
add/remove is a single atomic HSET/HDEL, no read-modify-write race. Name/current
price are deliberately NOT stored -- the frontend joins them from the already-loaded
data/breakouts.json at render time. Talks to Upstash over its plain REST API (POST
the command as a JSON array to the base URL) so this has zero dependencies beyond
`requests` (see root requirements.txt).

Everything lives in this one file, including the storage helpers, rather than
importing a sibling api/_watchlist_store.py -- confirmed via a real deploy that
Vercel's Python runtime bundles each api/*.py file in isolation and does NOT pick up
sibling modules in the same directory (ModuleNotFoundError at import time), so a
single self-contained file is the reliable shape here, matching Vercel's own
single-file handler example.

Deploys automatically -- Vercel detects any api/*.py file as a Python serverless
function (BaseHTTPRequestHandler-based `handler` class is Vercel's own convention).
Needs UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN / WATCHLIST_SECRET set as
Vercel project env vars (Project Settings -> Environment Variables) -- Vercel injects
these into os.environ directly in production, no .env loading needed there (that's
only for the local smoke-test below).

Local smoke test (no Vercel/Node needed -- substitute for `vercel dev`, which isn't
available in this environment): needs backend/.env populated, then:
    python api/watchlist.py
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

IST = timezone(timedelta(hours=5, minutes=30))
HASH_KEY = "watchlist"


class ConfigError(Exception):
    """UPSTASH_REDIS_REST_URL/TOKEN not set."""


def _upstash(*command: str):
    url = os.environ.get("UPSTASH_REDIS_REST_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    if not url or not token:
        raise ConfigError("UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN not set")
    resp = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json=list(command), timeout=10)
    resp.raise_for_status()
    body = resp.json()
    if body.get("error"):
        raise RuntimeError(f"Upstash error: {body['error']}")
    return body.get("result")


def list_items() -> list[dict]:
    """[{symbol, date_added, entry_price}, ...], newest-added first."""
    raw = _upstash("HGETALL", HASH_KEY) or []
    items = [json.loads(raw[i + 1]) for i in range(0, len(raw), 2)]
    items.sort(key=lambda it: it["date_added"], reverse=True)
    return items


def add_item(symbol: str, date_added: str, entry_price: float) -> dict:
    item = {"symbol": symbol, "date_added": date_added, "entry_price": entry_price}
    _upstash("HSET", HASH_KEY, symbol, json.dumps(item))
    return item


def remove_item(symbol: str) -> None:
    _upstash("HDEL", HASH_KEY, symbol)


class handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Watchlist-Secret")

    def _send_json(self, status: int, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        expected = os.environ.get("WATCHLIST_SECRET")
        if not expected:
            return False
        return self.headers.get("X-Watchlist-Secret") == expected

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if not os.environ.get("WATCHLIST_SECRET"):
            self._send_json(500, {"error": "WATCHLIST_SECRET not configured on the server"})
            return
        if not self._authorized():
            self._send_json(401, {"error": "invalid or missing X-Watchlist-Secret"})
            return
        try:
            self._send_json(200, {"items": list_items()})
        except ConfigError as exc:
            self._send_json(500, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def do_POST(self):
        if not os.environ.get("WATCHLIST_SECRET"):
            self._send_json(500, {"error": "WATCHLIST_SECRET not configured on the server"})
            return
        if not self._authorized():
            self._send_json(401, {"error": "invalid or missing X-Watchlist-Secret"})
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            symbol = str(body.get("symbol") or "").strip().upper()
            entry_price = float(body.get("entry_price"))
            if not symbol:
                raise ValueError("symbol is required")
            today = datetime.now(IST).strftime("%Y-%m-%d")
            item = add_item(symbol, today, entry_price)
            self._send_json(200, item)
        except (ValueError, TypeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except ConfigError as exc:
            self._send_json(500, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def do_DELETE(self):
        if not os.environ.get("WATCHLIST_SECRET"):
            self._send_json(500, {"error": "WATCHLIST_SECRET not configured on the server"})
            return
        if not self._authorized():
            self._send_json(401, {"error": "invalid or missing X-Watchlist-Secret"})
            return
        try:
            symbol = (parse_qs(urlparse(self.path).query).get("symbol") or [""])[0].strip().upper()
            if not symbol:
                raise ValueError("symbol query param is required")
            remove_item(symbol)
            self._send_json(200, {"removed": symbol})
        except (ValueError, TypeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except ConfigError as exc:
            self._send_json(500, {"error": str(exc)})
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
            val = val[1:-1]  # Upstash's own copy-paste button wraps values in double quotes
        os.environ.setdefault(key.strip(), val)


if __name__ == "__main__":
    _load_env_file()
    print("Smoke test -- add/list/remove a throwaway entry against the real Upstash store.\n")
    try:
        print("Adding _TEST_...")
        print(" ", add_item("_TEST_", "2026-01-01", 100.0))
        print("Listing (should include _TEST_)...")
        items = list_items()
        print(" ", items)
        assert any(it["symbol"] == "_TEST_" for it in items), "round-trip add->list failed"
        print("Removing _TEST_...")
        remove_item("_TEST_")
        items = list_items()
        assert not any(it["symbol"] == "_TEST_" for it in items), "remove failed"
        print(" OK -- add/list/remove all round-tripped cleanly.")
    except ConfigError as e:
        print("SKIPPED --", e, "-- set UPSTASH_REDIS_REST_URL/TOKEN in backend/.env first.")
    except Exception as e:
        print("FAILED --", repr(e))
