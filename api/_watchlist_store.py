"""Upstash Redis REST helpers behind the personal watchlist API (api/watchlist.py).

One Redis hash, key "watchlist", field = symbol, value = a JSON string
{"symbol", "date_added", "entry_price"}. A hash (not one big JSON blob) means
add/remove is a single atomic HSET/HDEL -- no read-modify-write race between
requests. Name/current price are deliberately NOT stored here -- the frontend joins
them from the already-loaded data/breakouts.json at render time, same
graceful-degradation convention as holdings/sector (a symbol that falls out of the
scanned universe just shows no current price instead of erroring).

Talks to Upstash over its plain REST API (POST the command as a JSON array to the
base URL) rather than a Redis client library, so this has zero new dependencies
beyond `requests` (already in backend/requirements.txt) and works unmodified in
Vercel's Python runtime.

Needs UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN -- either exported, or in
backend/.env (gitignored, see backend/.env.example) for local smoke-testing.

Smoke test (no Vercel/Node needed -- this is the substitute for `vercel dev`, which
isn't available in this environment):
    python api/_watchlist_store.py
"""
from __future__ import annotations
import json
import os
from pathlib import Path

import requests

HASH_KEY = "watchlist"


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
    # HGETALL returns a flat [field, value, field, value, ...] list.
    items = [json.loads(raw[i + 1]) for i in range(0, len(raw), 2)]
    items.sort(key=lambda it: it["date_added"], reverse=True)
    return items


def add_item(symbol: str, date_added: str, entry_price: float) -> dict:
    item = {"symbol": symbol, "date_added": date_added, "entry_price": entry_price}
    _upstash("HSET", HASH_KEY, symbol, json.dumps(item))
    return item


def remove_item(symbol: str) -> None:
    _upstash("HDEL", HASH_KEY, symbol)


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
