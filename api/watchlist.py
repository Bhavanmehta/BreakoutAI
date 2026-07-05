"""Vercel Python serverless function: GET/POST/DELETE /api/watchlist.

The personal "My Watchlist" feature's only live backend endpoint -- see
api/_watchlist_store.py for the actual Upstash Redis storage. Gated by a single
shared secret (WATCHLIST_SECRET env var) rather than real per-user accounts: there's
only one real user, so this just needs to keep randoms from writing to the store if
they find the URL, not protect a multi-tenant system. The frontend sends the secret
as an X-Watchlist-Secret header, cached in localStorage after a one-time prompt.

Deploys automatically -- Vercel detects any api/*.py file as a Python serverless
function (BaseHTTPRequestHandler-based `handler` class is Vercel's own convention,
confirmed via Vercel's docs). Needs UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN
/ WATCHLIST_SECRET set as Vercel project env vars (Project Settings -> Environment
Variables) -- Vercel injects these into os.environ directly, no .env loading needed
here (that's only for the local smoke-test script).
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from _watchlist_store import ConfigError, add_item, list_items, remove_item

IST = timezone(timedelta(hours=5, minutes=30))


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
