"""Vercel Python serverless function: GET/POST/DELETE /api/trades.

Optional cloud sync for the "Options Trade Assessor" — saved option-trade
assessments. Deliberately a near-clone of api/watchlist.py: same one-user shared
secret model, same single-file Upstash-over-REST shape, same handler conventions.
The Assessor's PRIMARY save is browser localStorage ("saved locally"); this
endpoint is the optional durable mirror, only reached when the same WATCHLIST_SECRET
is configured, so a user with no backend still gets the full local experience.

Gated by the same shared secret (WATCHLIST_SECRET env var) sent as an
X-Watchlist-Secret header — there's only one real user, so this just keeps randoms
from writing to the store, not a multi-tenant auth system. The secret is reused
(not a new TRADES_SECRET) so the owner configures one secret for the whole site.

Storage is one Upstash Redis hash, key "trades", field = the trade's id (a
"{market}:{symbol}:{strike}{opt_type}:{saved_at}" string, unique per saved
assessment so re-saving the same setup later keeps both), value = a JSON string of
the full assessment record. A hash (not one big JSON blob) means save/delete is a
single atomic HSET/HDEL, no read-modify-write race.

Everything lives in this one file (storage helpers included) rather than importing
a sibling api/_trades_store.py — Vercel's Python runtime bundles each api/*.py file
in isolation and does NOT pick up sibling modules (ModuleNotFoundError at import
time), so a single self-contained file is the reliable shape here.

Deploys automatically — Vercel detects any api/*.py as a Python serverless function
(BaseHTTPRequestHandler-based `handler` class is Vercel's convention). Needs
UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN / WATCHLIST_SECRET set as Vercel
project env vars; injected into os.environ in production (no .env loading there).

Local smoke test (substitute for `vercel dev`): needs backend/.env populated, then:
    python api/trades.py
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
HASH_KEY = "trades"


class ConfigError(Exception):
    """UPSTASH_REDIS_REST_URL/TOKEN not set."""


def _env(name: str) -> str | None:
    val = os.environ.get(name)
    if val and len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1]  # Upstash's own copy-paste button wraps values in quotes; tolerate pasting that verbatim
    return val


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


def _trade_id(rec: dict) -> str:
    """Stable-ish field key. Includes saved_at so the same setup saved at two
    different times keeps both rows rather than overwriting."""
    return f"{rec['market']}:{rec['symbol']}:{rec['strike']}{rec['opt_type']}:{rec['saved_at']}"


def list_items() -> list[dict]:
    """[{id, symbol, market, ...}, ...], newest-saved first."""
    raw = _upstash("HGETALL", HASH_KEY) or []
    items = []
    for i in range(0, len(raw), 2):
        rec = json.loads(raw[i + 1])
        rec.setdefault("id", raw[i])
        items.append(rec)
    items.sort(key=lambda it: it.get("saved_at", ""), reverse=True)
    return items


def add_item(rec: dict) -> dict:
    """Store one assessment record. `rec` must already carry symbol/market/strike/
    opt_type; saved_at + id are stamped here if absent."""
    rec.setdefault("saved_at", datetime.now(IST).isoformat())
    rec["id"] = _trade_id(rec)
    _upstash("HSET", HASH_KEY, rec["id"], json.dumps(rec))
    return rec


def remove_item(trade_id: str) -> None:
    _upstash("HDEL", HASH_KEY, trade_id)


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
            market = str(body.get("market") or "IN").strip().upper()
            opt_type = str(body.get("opt_type") or body.get("type") or "").strip().upper()
            if not symbol:
                raise ValueError("symbol is required")
            if market not in ("IN", "US"):
                raise ValueError("market must be 'IN' or 'US'")
            if opt_type not in ("CE", "PE"):
                raise ValueError("opt_type must be 'CE' or 'PE'")
            # Coerce the numeric fields we know about; keep everything else as sent
            # so the record stays a faithful snapshot of what the UI assessed.
            rec = dict(body)
            rec["symbol"] = symbol
            rec["market"] = market
            rec["opt_type"] = opt_type
            rec.pop("type", None)
            for k in ("spot", "strike", "iv", "days", "premium", "sl", "target", "lot_size", "lots"):
                if rec.get(k) is not None:
                    rec[k] = float(rec[k])
            rec = add_item(rec)
            self._send_json(200, rec)
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
            qs = parse_qs(urlparse(self.path).query)
            trade_id = (qs.get("id") or [""])[0].strip()
            if not trade_id:
                raise ValueError("id query param is required")
            remove_item(trade_id)
            self._send_json(200, {"removed": trade_id})
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
    print("Smoke test -- save/list/remove a throwaway trade against the real Upstash store.\n")
    try:
        rec = add_item({
            "symbol": "_TEST_", "market": "IN", "opt_type": "CE",
            "spot": 25000.0, "strike": 25100.0, "iv": 13.0, "days": 3.0,
            "premium": 90.0, "sl": 24850.0, "target": 25350.0,
            "verdict": "Marginal", "saved_at": "2026-01-01T00:00:00+05:30",
        })
        print("Saved:", rec["id"])
        items = list_items()
        mine = [it for it in items if it["symbol"] == "_TEST_"]
        assert mine and mine[0]["id"] == rec["id"], "save/list round-trip failed"
        print("Listed OK, removing...")
        remove_item(rec["id"])
        items = list_items()
        assert not any(it["symbol"] == "_TEST_" for it in items), "remove failed"
        print(" OK -- save/list/remove all round-tripped cleanly.")
    except ConfigError as e:
        print("SKIPPED --", e, "-- set UPSTASH_REDIS_REST_URL/TOKEN in backend/.env first.")
    except Exception as e:
        print("FAILED --", repr(e))
