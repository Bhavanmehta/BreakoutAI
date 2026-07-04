"""Local dev API server for the frontend's "Ask AI" panel.

Exists purely so the Groq API key never has to sit in client-side JS — the browser calls
this local server, which calls Groq. Dev-only (CORS-open, no auth, no rate limiting);
not meant to be deployed as-is.

Run:  cd backend; python chat_server.py     (defaults to http://localhost:8010)
Needs GROQ_API_KEY — either export it, or create backend/.env (gitignored) with:
    GROQ_API_KEY=gsk_...
Get a free key at https://console.groq.com/keys (note: this is different from the
chat.groq.com playground UI — that's a hosted chat product, not an API key source).
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import ask_ai

PORT = int(os.environ.get("PORT", "8010"))


def _load_env_file():
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, status, obj):
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
        if self.path == "/api/health":
            backends = ask_ai.configured_backends()
            self._send_json(200, {
                "ok": bool(backends["main_chain"]),
                "main_chain": backends["main_chain"],
                "search_chain": backends["search_chain"],
            })
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/ask":
            self._send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            result = ask_ai.ask(
                symbol=body.get("symbol"),
                question=body.get("question", ""),
                history=body.get("history"),
            )
            self._send_json(200, result)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def log_message(self, fmt, *args):
        print("[chat_server]", fmt % args)


if __name__ == "__main__":
    _load_env_file()
    if not os.environ.get("GROQ_API_KEY"):
        print(
            "WARNING: GROQ_API_KEY not set. Create backend/.env with GROQ_API_KEY=gsk_... "
            "(free key: https://console.groq.com/keys) or export it before running.\n"
        )
    print(f"Ask AI backend listening on http://localhost:{PORT}  (POST /api/ask, GET /api/health)")
    ThreadingHTTPServer(("localhost", PORT), Handler).serve_forever()
