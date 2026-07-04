"""Groq-backed "Ask AI" assistant behind the frontend's chat panel.

Architecture: a standard tool-calling model (NOT Groq's "compound" auto-tool system --
compound/compound-mini only support their own built-in web search and can't take custom
tools) gets four tools it can call as many times as a question needs. There is no
hardcoded cap on which or how many stocks it can reference:

  - lookup_stock(symbol_or_name): exact/fuzzy-resolves a ticker or company name to one
    stock's full computed context. Works for ANY stock in the universe, not just
    whichever one happens to be open in the app.
  - search_stocks(...): filtered/sorted slice of the whole ~1,800-stock universe
    (sector, ADX, primed-only, trend, sentiment) for discovery questions.
  - run_sql(select_query): read-only SQL over a DuckDB table of every stock's flattened
    fields, for open-ended questions the fixed search_stocks params can't express
    (aggregates, GROUP BY, custom logic).
  - web_search(query): only invoked when the model decides it actually needs live/web
    information; proxies to Groq's compound-mini (which *does* have built-in search) for
    just that sub-question, so the scarce web-search token budget isn't spent by default
    on every message.
"""
import difflib
import json
import os
import re
import time
from pathlib import Path

import duckdb
import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BREAKOUTS_PATH = DATA_DIR / "breakouts.json"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
CEREBRAS_API_URL = "https://api.cerebras.ai/v1/chat/completions"
NVIDIA_NIM_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

MAX_TOOL_ITERATIONS = 5


def _backend(label, url, api_key_env, model_env, default_model):
    """One entry in the fallback chain. Returns None (skipped) if its API key isn't
    configured -- so the chain degrades gracefully to whatever keys are actually set,
    rather than erroring out over providers you haven't signed up for yet."""
    api_key = os.environ.get(api_key_env)
    if not api_key:
        return None
    return {
        "label": label,
        "url": url,
        "api_key": api_key,
        "model": os.environ.get(model_env, default_model),
    }


def _build_main_chain():
    """Fallback order, ranked by reasoning quality (not just availability) since a weak
    model that gives up after one tool call is worse than a slower fallback: Groq's 70b
    first, then Gemini 2.5 Flash (verified solid at multi-step tool chaining). Next,
    Cerebras' gpt-oss-120b -- the larger cousin of the gpt-oss-20b we already run lower in
    this chain, so we trust the family's tool-calling behavior, and on a quota pool that's
    entirely separate from Groq/Gemini. Then NVIDIA NIM's deepseek-v4-flash -- same model
    as our native DeepSeek entry below, but served free (NIM's tier doesn't need a funded
    balance the way platform.deepseek.com does), on yet another separate quota. After that,
    Groq's smaller free models, which -- llama-3.1-8b-instant especially -- have been
    observed stopping after a single tool call instead of trying web_search next, and
    occasionally leaking raw tool-call syntax into the answer text. Native DeepSeek is last
    since it needs a funded balance, unlike the others -- kept as a harmless no-op fallback
    until that balance exists. Rebuilt on every call so .env changes take effect without
    restarting mid-debug."""
    candidates = [
        _backend("groq-70b", GROQ_API_URL, "GROQ_API_KEY", "GROQ_MODEL", "llama-3.3-70b-versatile"),
        _backend("gemini", GEMINI_API_URL, "GEMINI_API_KEY", "GEMINI_MODEL", "gemini-2.5-flash"),
        _backend("cerebras", CEREBRAS_API_URL, "CEREBRAS_API_KEY", "CEREBRAS_MODEL", "gpt-oss-120b"),
        _backend("nvidia-nim", NVIDIA_NIM_API_URL, "NVIDIA_NIM_API_KEY", "NVIDIA_NIM_MODEL", "deepseek-ai/deepseek-v4-flash"),
        _backend("groq-gptoss20b", GROQ_API_URL, "GROQ_API_KEY", "GROQ_MODEL_3", "openai/gpt-oss-20b"),
        _backend("groq-8b", GROQ_API_URL, "GROQ_API_KEY", "GROQ_MODEL_2", "llama-3.1-8b-instant"),
        _backend("deepseek", DEEPSEEK_API_URL, "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "deepseek-v4-flash"),
    ]
    return [c for c in candidates if c]


def _build_search_chain():
    # Web search specifically needs Groq's compound systems (built-in search tool). Order
    # matters here in a non-obvious way: groq/compound-mini's synthesis step runs on
    # llama-3.3-70b-versatile -- the SAME quota bucket as our main-chain groq-70b -- so it
    # fails right when 70b is already exhausted from regular conversation traffic. Plain
    # groq/compound uses llama-4-scout-17b + gpt-oss-120b instead, a genuinely separate
    # budget, so it goes first even though it's slower/heavier per Groq's own docs.
    # DeepSeek/Gemini aren't wired in here since they'd need a different search mechanism
    # (e.g. Gemini's own Google Search grounding) that isn't implemented yet.
    candidates = [
        _backend("groq-compound", GROQ_API_URL, "GROQ_API_KEY", "GROQ_SEARCH_MODEL", "groq/compound"),
        _backend("groq-compound-mini", GROQ_API_URL, "GROQ_API_KEY", "GROQ_SEARCH_MODEL_2", "groq/compound-mini"),
    ]
    return [c for c in candidates if c]


def configured_backends():
    """For the /api/health endpoint -- which backends are live vs. not configured."""
    main = _build_main_chain()
    search = [f"{b['label']} ({b['model']})" for b in _build_search_chain()]
    if os.environ.get("GEMINI_API_KEY"):
        search.append(f"gemini-native-search ({os.environ.get('GEMINI_SEARCH_MODEL', 'gemini-2.5-flash')})")
    return {
        "main_chain": [f"{b['label']} ({b['model']})" for b in main],
        "search_chain": search,
    }

_cache = {"mtime": None, "by_symbol": {}, "as_of": None, "con": None}

FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|attach|detach|copy|pragma|create|call|"
    r"export|import|install|load|vacuum|checkpoint)\b",
    re.IGNORECASE,
)

SQL_SCHEMA_DESC = (
    "Table `stocks` (~1,800 rows, one per NSE/BSE stock) columns: symbol, name, sector "
    "(full 'Broad Sector · Industry' string), sector_group (JUST the broad sector, e.g. "
    "'Consumer Cyclical' -- use THIS, not `sector`, for any 'which sector' question so it "
    "matches the app's Sector Radar panel instead of splintering into per-industry rows), "
    "industry, price, change_pct, in_uptrend (bool), adx_value, adx_label, ema_8, "
    "ema_8_position, ema_21, ema_21_position, ema_50, ema_50_position, ema_200, "
    "ema_200_position (position is 'ABOVE'/'BELOW' the price), resistance_level, "
    "distance_to_resistance_pct, times_tested, base_depth_pct, volatility_ratio, "
    "volatility_state, breakout_today (bool), sentiment ('Bullish'/'Bearish'/'Neutral'), "
    "readiness_score ('high'/'medium'/'low'), readiness_label, readiness_watch (bool, true "
    "= primed), readiness_reliable (bool), past_breakouts_count, followthrough_rate (0-1), "
    "avg_fwd_return_20d_pct, analog_date, analog_similarity (0-1), analog_fwd_20d_pct, "
    "analog_worked (bool), promoter_pct, fii_pct, dii_pct, public_pct, pattern_name, "
    "pattern_confidence, pattern_direction."
)

SYSTEM_PROMPT = """You are "Ask AI", the assistant embedded in BreakoutAI, an educational \
pre-breakout radar for NSE/BSE Indian equities covering roughly 1,800 stocks. You help retail \
traders understand technical setups -- you do not decide what anyone should trade.

You have tools to look up REAL computed data for any stock in our universe, not just the one \
currently open in the app, plus a tool to search the live web. Ground rules:

1. Never state a specific number about a stock's price, indicators, ownership, or history unless \
you actually retrieved it via lookup_stock / search_stocks / run_sql in this conversation. Our \
dataset does NOT include fundamentals like earnings/EPS/P/E/revenue -- if lookup_stock comes back \
without what the user asked for (earnings, news, company background, anything not in the technical/ \
ownership snapshot), you MUST call web_search before answering. Do not conclude "not in our data" \
and stop -- that is only acceptable after web_search also fails to find it.
2. Tag every claim: "[Our data]" for anything from a tool result grounded in our dataset, "[Web]" \
for anything from the web_search tool or your own general knowledge. Never blend the two without \
tagging -- the reader needs to know what to independently verify.
3. {open_hint} If the user asks about a *different* stock, call lookup_stock for it -- you are not \
limited to the one that happens to be open.
4. Never give direct, personalized buy/sell/hold instructions ("you should buy X now"). Explain what \
the setup means and its risks; end with a short reminder that this is educational, not investment \
advice, and to consult a SEBI-registered advisor before acting.
5. Named chart patterns (Cup & Handle, etc.) are shown for education only -- this app's own \
reliability testing found they do NOT predict follow-through better than "no pattern", so don't \
oversell a pattern match as a strong signal if asked about it.
6. Be concise -- a few sentences to a short paragraph, like an analyst answering a quick question, \
not an essay.
"""


def _load_breakouts():
    mtime = BREAKOUTS_PATH.stat().st_mtime
    if _cache["mtime"] == mtime:
        return
    data = json.loads(BREAKOUTS_PATH.read_text(encoding="utf-8"))
    stocks = data.get("stocks", [])
    _cache["by_symbol"] = {s["symbol"]: s for s in stocks}
    _cache["as_of"] = data.get("as_of_date")
    _cache["con"] = _build_duckdb(stocks)
    _cache["mtime"] = mtime


def _flatten(s):
    ema = s.get("ema_stack") or {}

    def ema_field(period, key):
        for v in ema.values():
            if v.get("period") == period:
                return v.get(key)
        return None

    holdings = s.get("holdings") or {}
    analog = s.get("analog") or {}
    history = s.get("history") or {}
    resistance = s.get("resistance") or {}
    readiness = s.get("readiness") or {}
    pattern = s.get("pattern") or {}
    volatility = s.get("volatility") or {}
    sector_full = s.get("sector") or ""
    return {
        "symbol": s.get("symbol"),
        "name": s.get("name"),
        "sector": sector_full,
        # The broad sector group before " · industry" -- mirrors the frontend's own
        # sectorGroup() so "which sector has the most primed names" matches what the
        # Sector Radar panel shows, instead of splintering into per-industry rows.
        "sector_group": (sector_full.split(" · ")[0].strip() or "Unclassified") if sector_full else "Unclassified",
        "industry": s.get("industry"),
        "price": s.get("price"),
        "change_pct": s.get("change_pct"),
        "in_uptrend": (s.get("trend") or {}).get("in_uptrend"),
        "adx_value": (s.get("adx") or {}).get("value"),
        "adx_label": (s.get("adx") or {}).get("label"),
        "ema_8": ema_field(8, "value"), "ema_8_position": ema_field(8, "position"),
        "ema_21": ema_field(21, "value"), "ema_21_position": ema_field(21, "position"),
        "ema_50": ema_field(50, "value"), "ema_50_position": ema_field(50, "position"),
        "ema_200": ema_field(200, "value"), "ema_200_position": ema_field(200, "position"),
        "resistance_level": resistance.get("level"),
        "distance_to_resistance_pct": resistance.get("distance_pct"),
        "times_tested": resistance.get("times_tested"),
        "base_depth_pct": resistance.get("base_depth"),
        "volatility_ratio": volatility.get("contraction_ratio"),
        "volatility_state": volatility.get("state"),
        "breakout_today": (s.get("breakout") or {}).get("today"),
        "sentiment": (s.get("breakout") or {}).get("sentiment"),
        "readiness_score": readiness.get("score"),
        "readiness_label": readiness.get("label"),
        "readiness_watch": readiness.get("watch"),
        "readiness_reliable": readiness.get("reliable"),
        "past_breakouts_count": history.get("past_breakouts"),
        "followthrough_rate": history.get("followthrough_rate"),
        "avg_fwd_return_20d_pct": history.get("avg_fwd_return_20d_pct"),
        "analog_date": analog.get("date"),
        "analog_similarity": analog.get("similarity"),
        "analog_fwd_20d_pct": analog.get("fwd_20d_pct"),
        "analog_worked": analog.get("worked"),
        "promoter_pct": holdings.get("promoter"),
        "fii_pct": holdings.get("fii"),
        "dii_pct": holdings.get("dii"),
        "public_pct": holdings.get("public"),
        "pattern_name": pattern.get("name"),
        "pattern_confidence": pattern.get("confidence"),
        "pattern_direction": pattern.get("direction"),
    }


def _build_duckdb(stocks):
    df = pd.DataFrame([_flatten(s) for s in stocks])
    con = duckdb.connect(":memory:")
    con.register("stocks", df)
    return con


def stock_context(symbol):
    """Full curated context for one stock -- what lookup_stock hands back to the model."""
    _load_breakouts()
    s = _cache["by_symbol"].get((symbol or "").upper())
    if not s:
        return None
    ctx = _flatten(s)
    ctx["data_as_of"] = _cache["as_of"]
    return ctx


def _normalize_name(n):
    n = (n or "").upper()
    for junk in (" LIMITED", " LTD.", " LTD", " INDUSTRIES", " COMPANY", " CO.", " CORP"):
        n = n.replace(junk, " ")
    return re.sub(r"\s+", " ", n).strip()


def resolve_symbol(query):
    """Exact or fuzzy-match a ticker/company name to a symbol in our universe."""
    _load_breakouts()
    q = (query or "").strip().upper()
    if not q:
        return None
    if q in _cache["by_symbol"]:
        return q

    symbols = list(_cache["by_symbol"].keys())
    close = difflib.get_close_matches(q, symbols, n=1, cutoff=0.72)
    if close:
        return close[0]

    norm_q = _normalize_name(q)
    best_symbol, best_score = None, 0.0
    for sym, stock in _cache["by_symbol"].items():
        name_norm = _normalize_name(stock.get("name"))
        if norm_q and (norm_q in name_norm or name_norm.startswith(norm_q)):
            return sym
        score = difflib.SequenceMatcher(None, norm_q, name_norm).ratio()
        if score > best_score:
            best_symbol, best_score = sym, score
    return best_symbol if best_score > 0.6 else None


def tool_lookup_stock(symbol_or_name):
    sym = resolve_symbol(symbol_or_name)
    if not sym:
        return {"error": f"No stock matching '{symbol_or_name}' found in our ~1,800-stock universe."}
    return stock_context(sym)


def tool_search_stocks(sector=None, min_adx=None, max_adx=None, primed_only=None,
                        in_uptrend=None, sentiment=None, sort_by="readiness", limit=20):
    _load_breakouts()
    limit = max(1, min(int(limit or 20), 50))
    rows = [_flatten(s) for s in _cache["by_symbol"].values()]

    def keep(r):
        if sector and sector.lower() not in (r["sector_group"] or "").lower() \
                and sector.lower() not in (r["sector"] or "").lower():
            return False
        if min_adx is not None and (r["adx_value"] or 0) < min_adx:
            return False
        if max_adx is not None and (r["adx_value"] or 0) > max_adx:
            return False
        if primed_only and not r["readiness_watch"]:
            return False
        if in_uptrend is not None and bool(r["in_uptrend"]) != bool(in_uptrend):
            return False
        if sentiment and (r["sentiment"] or "").lower() != sentiment.lower():
            return False
        return True

    rows = [r for r in rows if keep(r)]
    rank = {"high": 0, "medium": 1, "low": 2}
    if sort_by == "adx":
        rows.sort(key=lambda r: -(r["adx_value"] or 0))
    elif sort_by == "change":
        rows.sort(key=lambda r: -(r["change_pct"] if r["change_pct"] is not None else -999))
    elif sort_by == "proximity":
        rows.sort(key=lambda r: abs(r["distance_to_resistance_pct"]) if r["distance_to_resistance_pct"] is not None else 9999)
    else:
        rows.sort(key=lambda r: rank.get(r["readiness_score"], 9))

    trimmed = rows[:limit]
    fields = ("symbol", "name", "sector_group", "price", "change_pct", "adx_value",
              "readiness_score", "readiness_label", "distance_to_resistance_pct", "sentiment")
    return {
        "match_count": len(rows),
        "returned": len(trimmed),
        "stocks": [{k: r[k] for k in fields} for r in trimmed],
    }


def tool_run_sql(query):
    _load_breakouts()
    q = (query or "").strip().rstrip(";")
    if ";" in q:
        return {"error": "Only a single statement is allowed (no semicolons)."}
    if not re.match(r"^\s*(select|with)\b", q, re.IGNORECASE):
        return {"error": "Only SELECT (or WITH ... SELECT) queries are allowed."}
    if FORBIDDEN_SQL.search(q):
        return {"error": "Query contains a disallowed keyword."}
    try:
        df = _cache["con"].execute(q).fetchdf()
    except Exception as exc:
        return {"error": f"SQL error: {exc}"}
    truncated = len(df) > 50
    df = df.head(50)
    return {"row_count": len(df), "truncated": truncated, "rows": json.loads(df.to_json(orient="records"))}


def _extract_sources(message):
    """Best-effort extraction of URLs from Groq compound's executed_tools metadata.
    The exact shape isn't fully documented publicly, so this stays defensive: it just
    regexes any URL out of whatever fields are present rather than assuming a schema."""
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
    """Gemini's own Google Search grounding tool -- a genuinely separate API (the
    Interactions API, not the OpenAI-compat chat completions endpoint the rest of this
    file uses) and a separate quota from everything else in the fallback chain. Response
    shape (verified against a live call): `steps` is a list of typed entries; the answer
    text + citations live in the `model_output` step's `content` list."""
    resp = requests.post(
        GEMINI_INTERACTIONS_URL,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json={
            "model": os.environ.get("GEMINI_SEARCH_MODEL", "gemini-2.5-flash"),
            "input": query,
            "tools": [{"type": "google_search"}],
        },
        timeout=45,
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


def tool_web_search(query):
    chain = _build_search_chain()
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not chain and not gemini_key:
        return {"error": "web_search is unavailable -- configure GROQ_API_KEY and/or GEMINI_API_KEY."}

    errors = []
    if chain:
        try:
            message, _label = _chat_completion(
                messages=[
                    {"role": "system", "content": "Answer the user's query using web search. Be factual and concise."},
                    {"role": "user", "content": query},
                ],
                backends=chain,
            )
            return {"answer": _clean_answer(message.get("content")), "sources": _extract_sources(message)}
        except Exception as exc:
            errors.append(f"groq: {exc}")

    if gemini_key:
        try:
            return _gemini_native_search(query, gemini_key)
        except Exception as exc:
            errors.append(f"gemini-native-search: {exc}")

    return {"error": "web_search failed on every backend: " + "; ".join(errors)}


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_stock",
            "description": (
                "Resolve a company name or ticker (exact or approximate) to one stock's full "
                "computed technical/fundamental snapshot from our dataset of ~1,800 NSE/BSE "
                "stocks. Use this whenever the user names ANY stock, whether or not it's the "
                "one currently open in the app."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol_or_name": {
                        "type": "string",
                        "description": "Ticker or company name, e.g. 'CGPOWER' or 'CG Power' or 'Reliance'.",
                    }
                },
                "required": ["symbol_or_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_stocks",
            "description": (
                "Filter/sort the whole stock universe by common criteria. Use for questions "
                "like 'which IT stocks are primed' or 'strongest trends today'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sector": {"type": "string"},
                    "min_adx": {"type": "number"},
                    "max_adx": {"type": "number"},
                    "primed_only": {"type": "boolean"},
                    "in_uptrend": {"type": "boolean"},
                    "sentiment": {"type": "string", "enum": ["Bullish", "Bearish", "Neutral"]},
                    "sort_by": {"type": "string", "enum": ["readiness", "adx", "change", "proximity"]},
                    "limit": {"type": "integer", "description": "Max rows to return (default 20, max 50)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": (
                "Run a read-only SELECT query over the full stock universe for questions "
                "search_stocks can't express (aggregates, GROUP BY, custom logic). "
                + SQL_SCHEMA_DESC
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "A single SELECT (or WITH ... SELECT) statement."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the live web for information not in our dataset (company background, "
                "recent news, macro context). Only call this when lookup_stock/search_stocks/"
                "run_sql can't answer the question -- it draws on a much smaller shared rate budget."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]


def _one_completion(backend, messages, tools):
    """Single attempt against one backend, with retry-on-transient-network-error. Raises
    _BackendFailure (never lets a raw exception escape) so the caller can decide whether
    to fall through to the next backend in the chain."""
    payload = {"model": backend["model"], "messages": messages, "temperature": 0.3}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    last_exc = None
    for attempt in range(3):
        try:
            resp = requests.post(
                backend["url"],
                headers={"Authorization": f"Bearer {backend['api_key']}", "Content-Type": "application/json"},
                json=payload,
                timeout=45,
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
        if isinstance(err, str):  # some providers put a plain string here
            err = {"message": err}
        raise _BackendFailure(
            backend["label"],
            err.get("message") or f"HTTP {resp.status_code}: {resp.text[:300]}",
            is_quota=resp.status_code == 429 or err.get("code") in ("rate_limit_exceeded", "request_too_large"),
        )
    return resp.json()["choices"][0]["message"]


class _BackendFailure(Exception):
    def __init__(self, label, detail, is_quota=False):
        super().__init__(detail)
        self.label = label
        self.detail = detail
        self.is_quota = is_quota


def _chat_completion(messages, backends, tools=None):
    """Try each backend in order, falling through to the next on ANY failure (quota,
    auth, outage, network) so one provider being down or rate-limited doesn't block the
    whole feature. Returns (message, label_of_backend_that_answered)."""
    if not backends:
        raise RuntimeError(
            "No AI backend is configured. Set at least one of GROQ_API_KEY, DEEPSEEK_API_KEY, "
            "or GEMINI_API_KEY in backend/.env."
        )
    failures = []
    for backend in backends:
        try:
            return _one_completion(backend, messages, tools), backend["label"]
        except _BackendFailure as exc:
            failures.append(f"{exc.label}: {exc.detail}")
            continue
    raise RuntimeError(
        "Every configured AI backend failed or is rate-limited right now:\n- " + "\n- ".join(failures)
    )


def _dispatch_tool(name, args):
    try:
        if name == "lookup_stock":
            return tool_lookup_stock(args.get("symbol_or_name", ""))
        if name == "search_stocks":
            return tool_search_stocks(**args)
        if name == "run_sql":
            return tool_run_sql(args.get("query", ""))
        if name == "web_search":
            return tool_web_search(args.get("query", ""))
        return {"error": f"unknown tool '{name}'"}
    except Exception as exc:
        return {"error": str(exc)}


_LEAKED_TOOL_SYNTAX = re.compile(r"<function=[^>]*>.*?(</function>|$)", re.DOTALL)


def _clean_answer(text):
    """Weaker models (observed: llama-3.1-8b-instant) occasionally echo their own raw
    tool-call syntax into the final answer text instead of just returning prose. Strip it
    defensively regardless of which backend produced the answer."""
    return _LEAKED_TOOL_SYNTAX.sub("", text or "").strip()


def ask(symbol, question, history=None):
    question = (question or "").strip()[:2000]
    if not question:
        raise ValueError("question is required")

    main_chain = _build_main_chain()
    if not main_chain:
        raise RuntimeError(
            "No AI backend is configured. Set at least one of GROQ_API_KEY, DEEPSEEK_API_KEY, "
            "or GEMINI_API_KEY in backend/.env. Free keys: https://console.groq.com/keys, "
            "https://platform.deepseek.com/api_keys, https://aistudio.google.com/apikey"
        )

    _load_breakouts()
    open_hint = (
        f"The user currently has {symbol} open in the app (use this as the default subject "
        f"if a question doesn't name a stock)."
        if symbol else
        "No stock is currently open in the app."
    )
    system_prompt = SYSTEM_PROMPT.format(open_hint=open_hint)

    messages = [{"role": "system", "content": system_prompt}]
    for turn in (history or [])[-6:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": str(turn["content"])[:2000]})
    messages.append({"role": "user", "content": question})

    all_sources, tools_used, backends_used = [], [], []

    def record(label):
        if label not in backends_used:
            backends_used.append(label)

    for _ in range(MAX_TOOL_ITERATIONS):
        message, label = _chat_completion(messages, main_chain, tools=TOOLS)
        record(label)
        tool_calls = message.get("tool_calls")
        if not tool_calls:
            return {
                "answer": _clean_answer(message.get("content")),
                "sources": all_sources[:6],
                "tools_used": tools_used,
                "backends_used": backends_used,
            }

        messages.append({
            "role": "assistant",
            "content": message.get("content"),
            "tool_calls": tool_calls,
        })
        for tc in tool_calls:
            fn = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = _dispatch_tool(fn, args)
            tools_used.append({"tool": fn, "args": args})
            if fn == "web_search" and isinstance(result, dict):
                all_sources.extend(result.get("sources", []))
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": fn,
                "content": json.dumps(result, default=str)[:4000],
            })

    # Exceeded MAX_TOOL_ITERATIONS: force a final answer without further tool calls.
    message, label = _chat_completion(messages, main_chain)
    record(label)
    return {
        "answer": _clean_answer(message.get("content")),
        "sources": all_sources[:6],
        "tools_used": tools_used,
        "backends_used": backends_used,
    }


if __name__ == "__main__":
    # Self-test: context builder + resolver only (no network / API key needed).
    _load_breakouts()
    sample = next(iter(_cache["by_symbol"]), None)
    if sample:
        print("sample symbol:", sample)
        print(json.dumps(stock_context(sample), indent=2)[:600])
        print("resolve 'reliance industries' ->", resolve_symbol("reliance industries"))
        print("resolve by partial symbol 'CGPOWR' ->", resolve_symbol("CGPOWR"))
    else:
        print("No stocks in data/breakouts.json to test with.")
