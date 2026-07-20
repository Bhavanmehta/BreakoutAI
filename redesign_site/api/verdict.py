"""Vercel Python serverless function: POST /api/verdict -- on-demand "Deep Dive" AI verdict
for a single stock, Redis-cached.

The Lite pass (bull/bear/verdict/confidence) is already computed for every gated,
high-conviction stock once a day by backend/run_scan.py and baked straight into
data/breakouts.json (or data/us/breakouts.json) -- cheap enough (free-tier, fast models)
to just run at scan time. The Deep pass is the opposite: a slower reasoning-tier model,
richer prompt, and only meant to run when a user actually asks for it by clicking
"Deep Dive" on one stock in The Read panel -- so this lives here as its own endpoint
rather than in the batch scan.

Self-contained (digest builder + fallback-chain plumbing duplicated from
backend/ai_verdict.py / backend/ask_ai.py, not imported) for the same reason documented
in api/watchlist.py: Vercel's Python runtime bundles each api/*.py file in isolation and
does NOT pick up sibling modules, confirmed via a real deploy.

Request body (JSON): {"symbol": "TCS", "market": "IN", "as_of": "2026-07-06", "stock": {...}}
-- "stock" is the exact stock record the frontend already has in memory (from the
combined_breakout_scanner_platform.html fetch of data/breakouts.json), so this endpoint
never needs its own copy of that ~1,800-stock file (which, per that file's own docs, only
lives on the orphan `data` branch in production anyway, not bundled with this function).
"as_of" is the scan's as_of_date, used (with market+symbol) as the Redis cache key so a
Deep verdict is computed at most once per stock per day, no matter how many times the
button gets clicked.

Response: the same shape as backend/ai_verdict.py's analyze_stock(deep=True) --
{bull_case, bear_case, verdict, confidence, reasoning, backend, latency_sec, digest,
pass: "deep"} on success, plus "cached": true/false, or {"error": "..."} on failure
(every configured backend failed / rate-limited / returned unparseable output).

Needs UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN (cache) plus at least one of
GROQ_API_KEY / CEREBRAS_API_KEY / GEMINI_API_KEY / NVIDIA_NIM_API_KEY / DEEPSEEK_API_KEY
(model) set as Vercel project env vars. Missing cache config degrades to "just compute,
don't cache" rather than failing the request -- a slow response beats a broken one.

Local smoke test (needs backend/.env populated):
    python api/verdict.py [SYMBOL]
"""
from __future__ import annotations
import concurrent.futures
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import requests

CACHE_TTL_SECONDS = 3 * 24 * 60 * 60  # 3 days -- well past same-day reuse, auto-cleans stale keys

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
CEREBRAS_API_URL = "https://api.cerebras.ai/v1/chat/completions"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
NVIDIA_NIM_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

DEEP_SYSTEM = """You are a stock analysis debate engine. This stock passed an initial screen \
and is a shortlist candidate. Given structured technical/fundamental data for one stock, produce:
1. bull_case: the strongest 2-3 sentence argument FOR this being a good breakout trade right now.
2. bear_case: the strongest 2-3 sentence argument AGAINST it / key risks.
3. verdict: one of "BUY", "WATCH", or "AVOID".
4. confidence: an integer 0-100.
5. reasoning: 2-3 sentences that weighs both sides in more depth than a quick screen would, \
explicitly naming the single biggest risk that could invalidate the setup.

This is educational analysis, not investment advice -- do not add disclaimers, they're shown \
separately by the app. Respond with ONLY valid JSON, no markdown fences, no commentary, in \
exactly this shape:
{"bull_case": "...", "bear_case": "...", "verdict": "BUY|WATCH|AVOID", "confidence": <int 0-100>, "reasoning": "..."}
""" + (
    "\n\nYou may also be given a 'Recent live web search results' section after the structured "
    "data below -- that's supplementary real-time context (recent headlines, sector mood, "
    "broader market conditions) to sanity-check your call against, not a replacement for the "
    "structured technical/fundamental fields, which remain the primary source of truth for "
    "prices/levels/indicators. If it's absent or empty, just ignore it and use the structured "
    "data alone -- never say you searched the web if this section wasn't provided."
)

_JSON_FENCE_RE = re.compile(r"^```(json)?|```$", re.MULTILINE)


# --- digest builder (mirrors backend/ai_verdict.py's build_digest) --------------------
def _build_digest(s: dict) -> str:
    ema = s.get("ema_stack") or {}

    def ema_line(key, label):
        e = ema.get(key) or {}
        if e.get("value") is None:
            return None
        return f"{e.get('position', '?')} {label}ema({e['value']})"

    ema_parts = [p for p in (ema_line("ema8", "8"), ema_line("ema21", "21"),
                              ema_line("ema50", "50"), ema_line("ema200", "200")) if p]

    adx = s.get("adx") or {}
    trend = s.get("trend") or {}
    pattern = s.get("pattern") or {}
    volume = s.get("volume") or {}
    volatility = s.get("volatility") or {}
    breakout = s.get("breakout") or {}
    resistance = s.get("resistance") or {}
    support = (s.get("levels") or {}).get("support") or {}
    history = s.get("history") or {}
    entry = s.get("entry") or {}
    holdings = s.get("holdings") or {}
    readiness = s.get("readiness") or {}

    lines = [
        f"Stock: {s.get('name')} ({s.get('symbol')}), {s.get('sector')}",
        f"Price: Rs {s.get('price')} ({s.get('change_pct'):+.1f}% today)" if s.get("change_pct") is not None
        else f"Price: Rs {s.get('price')}",
        f"Trend: {trend.get('label', 'Unknown')}; ADX {adx.get('value', 'n/a')} ({adx.get('label', 'n/a')})",
    ]
    if ema_parts:
        lines.append("EMAs: price is " + ", ".join(ema_parts))
    if pattern.get("name"):
        lines.append(f"Pattern: {pattern['name']} (confidence {pattern.get('confidence', 'n/a')}) - {pattern.get('description', '')}")
    if volume.get("ratio") is not None:
        lines.append(f"Volume: {volume['ratio']}x average (surge={volume.get('surge', False)})")
    if volatility.get("state"):
        lines.append(f"Volatility: {volatility['state']} (contraction ratio {volatility.get('contraction_ratio', 'n/a')})")
    lines.append(f"Breakout today: {breakout.get('today', False)}, sentiment {breakout.get('sentiment', 'Neutral')}")
    if resistance.get("level") is not None:
        lines.append(f"Resistance: Rs {resistance['level']} ({resistance.get('distance_pct', 'n/a')}% away, {resistance.get('touches', 0)} touches)")
    if support.get("level") is not None:
        lines.append(f"Support: Rs {support['level']} ({support.get('distance_pct', 'n/a')}% away)")
    if history.get("past_breakouts"):
        rate = history.get("followthrough_rate")
        rate_pct = f"{rate*100:.0f}%" if isinstance(rate, (int, float)) else "n/a"
        lines.append(
            f"Historical breakout follow-through: {rate_pct} of past {history['past_breakouts']} breakouts "
            f"(avg 20d fwd return {history.get('avg_fwd_return_20d_pct', 'n/a')}%)"
        )
    if entry.get("suggested_entry"):
        lines.append(f"Suggested entry: {entry['suggested_entry']}, stop-loss: {entry.get('stop_loss', 'n/a')}")
    if holdings:
        lines.append(f"Holdings: promoter {holdings.get('promoter', 'n/a')}%, FII {holdings.get('fii', 'n/a')}%, DII {holdings.get('dii', 'n/a')}%")
    if readiness.get("conviction") is not None:
        lines.append(f"Rule-based conviction score: {readiness['conviction']}/100 ({readiness.get('score', 'n/a')}) -- {readiness.get('label', '')}")

    return "\n".join(lines)


# --- fallback-chain plumbing (mirrors backend/ask_ai.py, trimmed: no tools) -----------
def _backend(label, url, api_key_env, model_env, default_model):
    api_key = os.environ.get(api_key_env)
    if not api_key:
        return None
    return {"label": label, "url": url, "api_key": api_key, "model": os.environ.get(model_env, default_model)}


def _build_deep_chain():
    """Same ordering rationale as backend/ai_verdict.py's _build_deep_chain: a reasoning-
    tier model first (only ever called for one user-clicked stock at a time, so its
    slower latency is a non-issue here), falling through to the fast Lite-pass chain if
    no reasoning model is configured/reachable -- degrades to Lite quality rather than
    hard-failing the request."""
    candidates = [
        _backend("groq-deep", GROQ_API_URL, "GROQ_API_KEY", "DEEP_MODEL", "deepseek-r1-distill-llama-70b"),
        _backend("cerebras", CEREBRAS_API_URL, "CEREBRAS_API_KEY", "CEREBRAS_MODEL", "gpt-oss-120b"),
        _backend("gemini", GEMINI_API_URL, "GEMINI_API_KEY", "GEMINI_MODEL", "gemini-2.5-flash"),
        _backend("groq-70b", GROQ_API_URL, "GROQ_API_KEY", "GROQ_MODEL", "llama-3.3-70b-versatile"),
        _backend("nvidia-nim", NVIDIA_NIM_API_URL, "NVIDIA_NIM_API_KEY", "NVIDIA_NIM_MODEL", "deepseek-ai/deepseek-v4-flash"),
        _backend("deepseek", DEEPSEEK_API_URL, "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "deepseek-chat"),
    ]
    return [c for c in candidates if c]


# --- live web search (mirrors backend/ask_ai.py's tool_web_search, trimmed: no tool-calling
# loop needed here, this is called directly rather than offered to a model as a function) ---
def _build_search_chain():
    """Same rationale as ask_ai.py's _build_search_chain: web search specifically needs
    Groq's compound systems (built-in search tool), NOT the plain chat models in
    _build_deep_chain above."""
    candidates = [
        _backend("groq-compound", GROQ_API_URL, "GROQ_API_KEY", "GROQ_SEARCH_MODEL", "groq/compound"),
        _backend("groq-compound-mini", GROQ_API_URL, "GROQ_API_KEY", "GROQ_SEARCH_MODEL_2", "groq/compound-mini"),
    ]
    return [c for c in candidates if c]


def _extract_sources(message):
    """Best-effort extraction of URLs from Groq compound's executed_tools metadata."""
    urls = []
    for tool in (message.get("executed_tools") or []):
        urls.extend(re.findall(r'https?://[^\s"\'\\]+', json.dumps(tool)))
    seen, out = set(), []
    for u in urls:
        u = u.rstrip(".,)")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:6]


GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


def _gemini_native_search(query, api_key):
    """Gemini's own Google Search grounding tool -- a separate API/quota from the OpenAI-
    compat chat completions endpoint used everywhere else in this file."""
    resp = requests.post(
        GEMINI_INTERACTIONS_URL,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json={
            "model": os.environ.get("GEMINI_SEARCH_MODEL", "gemini-2.5-flash"),
            "input": query,
            "tools": [{"type": "google_search"}],
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Gemini search HTTP {resp.status_code}: {resp.text[:300]}")
    text_parts, sources = [], []
    for step in resp.json().get("steps", []):
        if step.get("type") != "model_output":
            continue
        for part in step.get("content", []):
            if part.get("type") == "text":
                text_parts.append(part.get("text", ""))
                for ann in part.get("annotations", []):
                    u = ann.get("url")
                    if u and u not in sources:
                        sources.append(u)
    return {"answer": "".join(text_parts).strip(), "sources": sources[:6]}


def _web_search(query):
    """Groq-compound first, Gemini native grounding as fallback -- same order/rationale as
    ask_ai.py's tool_web_search. Never raises; returns {"error": ...} on total failure so
    callers can just skip it."""
    chain = _build_search_chain()
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not chain and not gemini_key:
        return {"error": "web search unavailable -- configure GROQ_API_KEY and/or GEMINI_API_KEY."}

    errors = []
    if chain:
        try:
            message, _label = _chat_completion(
                [
                    {"role": "system", "content": "Answer the user's query using web search. Be factual and concise."},
                    {"role": "user", "content": query},
                ],
                chain,
            )
            return {"answer": (message.get("content") or "").strip(), "sources": _extract_sources(message)}
        except Exception as exc:
            errors.append(f"groq: {exc}")

    if gemini_key:
        try:
            return _gemini_native_search(query, gemini_key)
        except Exception as exc:
            errors.append(f"gemini-native-search: {exc}")

    return {"error": "web_search failed on every backend: " + "; ".join(errors)}


def _web_query_stock(s):
    return f"{s.get('name')} {s.get('symbol')} share price news today India NSE BSE -- latest developments this week"


def _web_query_sector_macro(s):
    sector = s.get("sector") or ""
    sector_part = f"{sector} sector, " if sector else ""
    return (f"{sector_part}Indian stock market today: Nifty/Sensex sentiment, sector news, "
            "RBI or macro developments this week")


def _gather_web_context(s):
    """Best-effort LIVE web search -- Deep pass only, never the batched Lite pass computed
    in backend/run_scan.py (that runs ~1x per stock across the whole ~1,800-stock universe
    every scan, where even one search call per stock would burn through free-tier search
    quota almost immediately). Two queries (stock-specific + combined sector/macro) run
    concurrently to bound added latency on top of the Deep pass's own reasoning-model call.
    Never raises and never blocks the verdict: a failed/unavailable/empty query is just
    dropped from the digest rather than injecting an error into the LLM prompt."""
    queries = [("Stock", _web_query_stock(s)), ("Sector & market", _web_query_sector_macro(s))]

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(queries)) as ex:
        futures = {ex.submit(_web_search, q): label for label, q in queries}
        done, _pending = concurrent.futures.wait(futures, timeout=30)
        for fut in done:
            label = futures[fut]
            try:
                results[label] = fut.result()
            except Exception:
                continue

    lines, all_sources = [], []
    for label, _q in queries:
        r = results.get(label)
        if not r or r.get("error") or not r.get("answer"):
            continue
        lines.append(f"[{label}] {r['answer']}")
        all_sources.extend(r.get("sources") or [])
    if not lines:
        return None, []
    seen = set()
    sources = [u for u in all_sources if not (u in seen or seen.add(u))][:8]
    return "\n".join(lines), sources


class _BackendFailure(Exception):
    def __init__(self, label, detail, is_quota=False):
        super().__init__(detail)
        self.label = label
        self.detail = detail
        self.is_quota = is_quota


def _one_completion(backend, messages):
    payload = {"model": backend["model"], "messages": messages, "temperature": 0.3}
    last_exc = None
    for attempt in range(3):
        try:
            resp = requests.post(
                backend["url"],
                headers={"Authorization": f"Bearer {backend['api_key']}", "Content-Type": "application/json"},
                json=payload,
                timeout=55,
            )
            break
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    else:
        raise _BackendFailure(backend["label"], f"network error: {last_exc}")

    if not resp.ok:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if isinstance(body, list):  # Gemini wraps error bodies as [{"error": {...}}]
            body = body[0] if body else {}
        err = body.get("error", {}) if isinstance(body, dict) else {}
        if isinstance(err, str):
            err = {"message": err}
        raise _BackendFailure(
            backend["label"],
            err.get("message") or f"HTTP {resp.status_code}: {resp.text[:300]}",
            is_quota=resp.status_code == 429 or err.get("code") in ("rate_limit_exceeded", "request_too_large"),
        )
    return resp.json()["choices"][0]["message"]


def _chat_completion(messages, backends):
    if not backends:
        raise RuntimeError(
            "No free-tier AI backend configured (need at least one of GROQ_API_KEY, "
            "CEREBRAS_API_KEY, GEMINI_API_KEY, NVIDIA_NIM_API_KEY, DEEPSEEK_API_KEY)."
        )
    failures = []
    for backend in backends:
        try:
            return _one_completion(backend, messages), backend["label"]
        except _BackendFailure as exc:
            failures.append(f"{exc.label}: {exc.detail}")
            continue
    raise RuntimeError("Every configured AI backend failed or is rate-limited right now:\n- " + "\n- ".join(failures))


def _parse_verdict_json(content):
    cleaned = _JSON_FENCE_RE.sub("", (content or "").strip()).strip()
    parsed = json.loads(cleaned)
    verdict = str(parsed.get("verdict", "")).upper()
    if verdict not in ("BUY", "WATCH", "AVOID"):
        raise ValueError(f"unexpected verdict value: {parsed.get('verdict')!r}")
    try:
        confidence = int(parsed.get("confidence"))
    except (TypeError, ValueError):
        raise ValueError(f"non-integer confidence: {parsed.get('confidence')!r}")
    return {
        "bull_case": str(parsed.get("bull_case", "")).strip(),
        "bear_case": str(parsed.get("bear_case", "")).strip(),
        "verdict": verdict,
        "confidence": max(0, min(100, confidence)),
        "reasoning": str(parsed.get("reasoning", "")).strip(),
    }


def compute_deep_verdict(stock: dict) -> dict:
    """Same return shape as backend/ai_verdict.py's analyze_stock(deep=True): the parsed
    verdict dict plus metadata on success, or {"error": ...} on failure. Never raises."""
    digest = _build_digest(stock)
    chain = _build_deep_chain()
    if not chain:
        return {"error": "No free-tier AI backend configured (need at least one of "
                          "GROQ_API_KEY, CEREBRAS_API_KEY, GEMINI_API_KEY, NVIDIA_NIM_API_KEY, DEEPSEEK_API_KEY)."}

    t0 = time.time()  # covers web search + LLM call -- the full wait the frontend shows a spinner for

    web_context, web_sources = None, []
    try:
        web_context, web_sources = _gather_web_context(stock)
    except Exception:
        pass  # live web search is a bonus on top of the digest, never blocks the verdict

    user_content = f"Analyze this stock:\n\n{digest}"
    if web_context:
        user_content += "\n\nRecent live web search results (supplementary real-time context):\n" + web_context

    messages = [
        {"role": "system", "content": DEEP_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    try:
        message, label = _chat_completion(messages, chain)
    except RuntimeError as exc:
        return {"error": str(exc), "digest": digest}
    latency = time.time() - t0

    try:
        parsed = _parse_verdict_json(message.get("content"))
    except (json.JSONDecodeError, ValueError) as exc:
        return {"error": f"{label} returned unparseable output: {exc}", "raw": message.get("content"), "digest": digest}

    parsed["backend"] = label
    parsed["latency_sec"] = round(latency, 2)
    parsed["digest"] = digest
    parsed["pass"] = "deep"
    return parsed


# --- Upstash Redis cache (mirrors api/watchlist.py's _upstash helper) -----------------
class ConfigError(Exception):
    """UPSTASH_REDIS_REST_URL/TOKEN not set."""


def _env(name: str) -> str | None:
    val = os.environ.get(name)
    if val and len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1]
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


def _cache_key(market: str, symbol: str, as_of: str) -> str:
    return f"verdict:deep:{market}:{symbol}:{as_of}"


def get_cached(market: str, symbol: str, as_of: str) -> dict | None:
    raw = _upstash("GET", _cache_key(market, symbol, as_of))
    return json.loads(raw) if raw else None


def set_cached(market: str, symbol: str, as_of: str, verdict: dict) -> None:
    _upstash("SET", _cache_key(market, symbol, as_of), json.dumps(verdict), "EX", str(CACHE_TTL_SECONDS))


def get_deep_verdict(symbol: str, market: str, as_of: str, stock: dict) -> dict:
    """Cache-first: a Deep verdict is computed at most once per stock per day, no matter
    how many times the frontend's Deep Dive button gets clicked. Cache lookup/write
    failures (Upstash not configured, or a transient error) are logged into the response
    but never block computing/returning a fresh verdict -- a slow, uncached response
    beats a broken one."""
    cache_note = None
    try:
        cached = get_cached(market, symbol, as_of)
        if cached is not None:
            cached = dict(cached)
            cached["cached"] = True
            return cached
    except ConfigError as exc:
        cache_note = str(exc)
    except Exception as exc:
        cache_note = f"cache read failed: {exc}"

    result = compute_deep_verdict(stock)
    result["cached"] = False
    if cache_note:
        result["cache_note"] = cache_note
    elif not result.get("error"):
        try:
            set_cached(market, symbol, as_of, result)
        except Exception as exc:
            result["cache_note"] = f"cache write failed: {exc}"
    return result


class handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
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

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            symbol = str(body.get("symbol") or "").strip().upper()
            market = str(body.get("market") or "IN").strip().upper()
            as_of = str(body.get("as_of") or "").strip()
            stock = body.get("stock")
            if not symbol:
                raise ValueError("symbol is required")
            if market not in ("IN", "US"):
                raise ValueError("market must be 'IN' or 'US'")
            if not as_of:
                raise ValueError("as_of is required (the scan's as_of_date, for cache keying)")
            if not isinstance(stock, dict) or not stock:
                raise ValueError("stock (the full stock record) is required")
            result = get_deep_verdict(symbol, market, as_of, stock)
            self._send_json(200, result)
        except (ValueError, TypeError) as exc:
            self._send_json(400, {"error": str(exc)})
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
            val = val[1:-1]
        os.environ.setdefault(key.strip(), val)


if __name__ == "__main__":
    import sys

    _load_env_file()
    data_path = Path(__file__).resolve().parent.parent / "data" / "breakouts.json"
    d = json.loads(data_path.read_text(encoding="utf-8"))
    by_symbol = {x["symbol"]: x for x in d["stocks"]}
    sym = sys.argv[1] if len(sys.argv) > 1 else "SPANDANA"
    s = by_symbol.get(sym) or d["stocks"][0]
    as_of = d.get("as_of_date", "TEST")

    print(f"=== Deep verdict for {s['symbol']} (as_of={as_of}) -- first call (should compute) ===")
    print(json.dumps(get_deep_verdict(s["symbol"], "IN", as_of, s), indent=2))
    print()
    print("=== second call (should be cached, if Upstash is configured) ===")
    print(json.dumps(get_deep_verdict(s["symbol"], "IN", as_of, s), indent=2))
