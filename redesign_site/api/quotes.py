"""Vercel Python serverless function: GET /api/quotes?symbols=A,B,C&market=IN|US

Lightweight live-price overlay for the frontend's few-minutes polling (see
combined_breakout_scanner_platform.html's pollLiveQuotes()). Returns just
price/prev-close/change% -- never readiness/ADX/resistance/etc, which are
anchored to the last *completed* daily close (see CLAUDE.md's breakout
definition) and would be conceptually wrong to recompute mid-session on a
still-forming bar. The daily backend pipeline (run_scan.py) stays the source
of truth for all of that; this only makes the price/day-move feel current
between scans.

Deliberately NOT backed by the yfinance library -- that pulls in pandas/numpy,
working against the "zero dependencies beyond requests" shape this repo's
Vercel functions use (see api/watchlist.py; Vercel bundles each api/*.py off
the root requirements.txt, so a heavy import here would bloat every function's
cold start). Instead this hits Yahoo's own public v8 chart-meta endpoint
directly -- confirmed live (2026-07-06) to work unauthenticated, unlike the
v7 batch quote endpoint which now 401s without a crumb/cookie. The tradeoff:
v8 is per-symbol, not batchable, so up to MAX_SYMBOLS requests are fanned out
with a thread pool to keep total latency to a few seconds.

Unofficial endpoint: Yahoo doesn't publish or guarantee it, and has grown
stricter over time. Fails soft per-symbol -- whatever Yahoo won't return for
is just omitted from the response, so a hiccup skips that symbol (or that
whole poll) rather than breaking the page. The frontend simply keeps showing
the last good price until the next successful poll.

Usage: GET /api/quotes?symbols=TCS,APOLLO&market=IN
       GET /api/quotes?symbols=AAPL,MSFT&market=US
"""
from __future__ import annotations
import json
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import requests

MAX_SYMBOLS = 90  # matches the frontend's MAX_RESULTS (80) + the selected detail stock
MAX_WORKERS = 20
REQUEST_TIMEOUT = 6
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def _fetch_one(session: requests.Session, yahoo_symbol: str):
    """(bare_symbol, {price, prev_close, change_pct}) or (bare_symbol, None) on any failure."""
    bare = yahoo_symbol[:-3] if yahoo_symbol.endswith(".NS") else yahoo_symbol
    try:
        resp = session.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}",
            params={"interval": "1d", "range": "1d"}, headers=_HEADERS, timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            return bare, None
        meta = resp.json()["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose")
        if price is None or not prev:
            return bare, None
        return bare, {"price": price, "prev_close": prev, "change_pct": round((price - prev) / prev * 100, 2)}
    except Exception:
        return bare, None


def fetch_quotes(yahoo_symbols: list[str]) -> dict[str, dict]:
    if not yahoo_symbols:
        return {}
    with requests.Session() as session, ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = pool.map(lambda sym: _fetch_one(session, sym), yahoo_symbols)
        return {bare: quote for bare, quote in results if quote is not None}


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
        raw_symbols = (qs.get("symbols") or [""])[0]
        market = (qs.get("market") or ["IN"])[0].strip().upper()
        symbols = [s.strip().upper() for s in raw_symbols.split(",") if s.strip()][:MAX_SYMBOLS]
        if not symbols:
            self._send_json(400, {"error": "symbols query param is required"})
            return
        suffix = ".NS" if market != "US" else ""
        # Index symbols (^NSEI, ^GSPC, ...) already carry Yahoo's own notation -- no exchange suffix.
        yahoo_symbols = [s if s.startswith("^") else f"{s}{suffix}" for s in symbols]
        try:
            self._send_json(200, {"quotes": fetch_quotes(yahoo_symbols)})
        except Exception as exc:
            self._send_json(502, {"error": str(exc)})


if __name__ == "__main__":
    print("Smoke test -- live quotes for a few symbols in each market...\n")
    print(" IN:", fetch_quotes(["TCS.NS", "APOLLO.NS", "NOTAREALTICKERXX.NS"]))
    print(" US:", fetch_quotes(["AAPL", "MSFT"]))
