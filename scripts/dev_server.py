"""Local dev server: static files + /api/quotes, so the index ticker and live
price overlay work on localhost like they do on Vercel.

Usage (from repo root):  python scripts/dev_server.py   ->  http://localhost:8000
"""
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from api.quotes import MAX_SYMBOLS, fetch_quotes  # noqa: E402
from api.index_ohlc import fetch_index_ohlc  # noqa: E402
from api.options_chain import ConfigError, ProviderError, SymbolNotFoundError, _load_env_file, fetch_expiry_list, fetch_option_chain, fetch_symbols  # noqa: E402
from api.options_backtest import LocalOnlyError, dispatch as backtest_dispatch  # noqa: E402
from api.options_platform import build_platform_view  # noqa: E402

_load_env_file()  # so DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN etc. from backend/.env are visible to LiveDhanProvider in local dev


class Handler(SimpleHTTPRequestHandler):
    def _send_json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/api/quotes":
            market = (qs.get("market") or ["IN"])[0].strip().upper()
            symbols = [s.strip().upper() for s in (qs.get("symbols") or [""])[0].split(",") if s.strip()][:MAX_SYMBOLS]
            suffix = ".NS" if market != "US" else ""
            self._send_json(200, {"quotes": fetch_quotes([s if s.startswith("^") else f"{s}{suffix}" for s in symbols])})
            return
        if parsed.path == "/api/index_ohlc":
            symbol = (qs.get("symbol") or [""])[0].strip().upper()
            if not symbol:
                self._send_json(400, {"error": "symbol query param is required"})
                return
            data = fetch_index_ohlc(symbol)
            if data is None:
                self._send_json(502, {"error": "could not fetch index history"})
                return
            self._send_json(200, data)
            return
        if parsed.path == "/api/options_chain":
            action = (qs.get("action") or [""])[0].strip().lower()
            symbol = (qs.get("symbol") or ["NIFTY"])[0].strip().upper()
            try:
                if action == "expirylist":
                    self._send_json(200, fetch_expiry_list(symbol))
                    return
                if action == "chain":
                    expiry = (qs.get("expiry") or [""])[0].strip()
                    if not expiry:
                        self._send_json(400, {"error": "expiry query param is required for action=chain"})
                        return
                    self._send_json(200, fetch_option_chain(symbol, expiry))
                    return
                if action == "symbols":
                    self._send_json(200, fetch_symbols())
                    return
                self._send_json(400, {"error": "action query param must be 'expirylist', 'chain' or 'symbols'"})
                return
            except ConfigError as exc:
                self._send_json(500, {"error": str(exc)})
                return
            except ProviderError as exc:
                self._send_json(502, {"error": str(exc)})
                return
        if parsed.path == "/api/options_platform":
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
            except ProviderError as exc:
                self._send_json(502, {"error": str(exc)})
            return
        if parsed.path == "/api/options_backtest":
            try:
                self._send_json(200, backtest_dispatch(qs))
            except LocalOnlyError as exc:
                self._send_json(501, {"error": str(exc)})
            except SymbolNotFoundError as exc:
                self._send_json(404, {"error": str(exc)})
            except (ValueError, TypeError) as exc:
                self._send_json(400, {"error": str(exc)})
            except ConfigError as exc:
                self._send_json(500, {"error": str(exc)})
            except ProviderError as exc:
                self._send_json(502, {"error": str(exc)})
            return
        return super().do_GET()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    os.chdir(ROOT)
    print(f"Serving BreakoutAI at http://localhost:{port} (static + /api/quotes)")
    ThreadingHTTPServer(("", port), Handler).serve_forever()
