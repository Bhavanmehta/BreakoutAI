"""AI bull/bear/verdict pipeline for individual stocks -- the "Lite" design validated in
scratchpad/model_compare_out.txt: feed the LLM a pre-built plain-text data digest (from
data/breakouts.json, already computed locally by find_breakouts.py/signals.py -- no
tool-calling loop, unlike the original TauricResearch TradingAgents repo this idea is
adapted from) and get back a single structured JSON verdict in one shot.

Two tiers, both free-tier by default (confirmed choice -- see HANDOFF.md/session notes):

  - Lite pass: 1 call per stock (bull_case + bear_case + verdict + confidence together --
    NOT 3-4 separate round-trips; a single well-structured prompt gets all of it in one
    response, which is what actually keeps a 50-stock scan fast/cheap). Routed through a
    fast free-tier chain: Cerebras -> Gemini 2.5 Flash -> Groq -> NVIDIA NIM -> DeepSeek.
    Ordered by *latency* (this pass runs ~50x/scan), unlike ask_ai.py's main chain which is
    ordered by tool-calling reasoning quality -- a different job with different priorities.
  - Deep pass: same digest, richer prompt, routed through a slower reasoning-tier free
    model. Only meant to be called for a handful of shortlisted stocks (e.g. the top 5-10
    by Lite verdict/conviction), never the full universe -- see analyze_stock(deep=True).
    NOTE: the specific reasoning model here is a placeholder default (DEEP_MODEL_ENV below)
    -- confirm/swap it once you've eyeballed a few Deep-pass outputs; it's isolated in one
    place (_build_deep_chain) so changing it later doesn't touch the Lite pass at all.
"""
import concurrent.futures
import json
import re
import time

from ask_ai import (  # reuse the fallback/retry plumbing + live web search (Deep pass only)
    _backend, _chat_completion, _BackendFailure, tool_web_search,
)

LITE_SYSTEM = """You are a stock analysis debate engine. Given structured technical/fundamental \
data for one stock, produce:
1. bull_case: the strongest 2-3 sentence argument FOR this being a good breakout trade right now.
2. bear_case: the strongest 2-3 sentence argument AGAINST it / key risks.
3. verdict: one of "BUY", "WATCH", or "AVOID".
4. confidence: an integer 0-100.
5. reasoning: one sentence that weighs both sides and explains the verdict.

This is educational analysis, not investment advice -- do not add disclaimers, they're shown \
separately by the app. Respond with ONLY valid JSON, no markdown fences, no commentary, in \
exactly this shape:
{"bull_case": "...", "bear_case": "...", "verdict": "BUY|WATCH|AVOID", "confidence": <int 0-100>, "reasoning": "..."}
"""

DEEP_SYSTEM = LITE_SYSTEM.replace(
    "Given structured technical/fundamental",
    "This stock passed an initial screen and is a shortlist candidate. Given structured technical/fundamental",
).replace(
    "one sentence that weighs both sides and explains the verdict.",
    "2-3 sentences that weighs both sides in more depth than a quick screen would, "
    "explicitly naming the single biggest risk that could invalidate the setup.",
) + (
    "\n\nYou may also be given a 'Recent live web search results' section after the structured "
    "data below -- that's supplementary real-time context (recent headlines, sector mood, "
    "broader market conditions) to sanity-check your call against, not a replacement for the "
    "structured technical/fundamental fields, which remain the primary source of truth for "
    "prices/levels/indicators. If it's absent or empty, just ignore it and use the structured "
    "data alone -- never say you searched the web if this section wasn't provided."
)


def build_digest(s):
    """Plain-text digest of one stock record from data/breakouts.json. Every field is
    pulled with .get()+defaults since not every stock has every sub-object populated
    (e.g. `analog`, `levels.support`, `entry` can be sparse for thinly-traded names) --
    unlike the scratch model_compare.py prototype this is based on, this must survive
    being run across the whole ~1,800-stock universe, not just one hand-picked example."""
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


CEREBRAS_API_URL = "https://api.cerebras.ai/v1/chat/completions"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
NVIDIA_NIM_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"


def _build_lite_chain():
    """Ordered by *speed* (this runs ~1x per stock across the whole scan universe), not
    reasoning depth -- confirmed fastest-with-good-quality in scratchpad/model_compare_out.txt
    was Cerebras, then Gemini 2.5 Flash, then Groq. NVIDIA NIM/DeepSeek are further
    fallbacks (DeepSeek native needs a funded balance, kept last as a harmless no-op until
    one exists, matching ask_ai.py's rationale)."""
    candidates = [
        _backend("cerebras", CEREBRAS_API_URL, "CEREBRAS_API_KEY", "CEREBRAS_MODEL", "gpt-oss-120b"),
        _backend("gemini", GEMINI_API_URL, "GEMINI_API_KEY", "GEMINI_MODEL", "gemini-2.5-flash"),
        _backend("groq-70b", GROQ_API_URL, "GROQ_API_KEY", "GROQ_MODEL", "llama-3.3-70b-versatile"),
        _backend("nvidia-nim", NVIDIA_NIM_API_URL, "NVIDIA_NIM_API_KEY", "NVIDIA_NIM_MODEL", "deepseek-ai/deepseek-v4-flash"),
        _backend("deepseek", DEEPSEEK_API_URL, "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "deepseek-chat"),
    ]
    return [c for c in candidates if c]


# TODO(confirm): placeholder reasoning-tier default for the Deep pass -- swap the model
# name (and/or env var) here once you've compared a few real Deep-pass outputs. Kept as a
# free-tier reasoning model; only ever called for a handful of shortlisted stocks so its
# slower latency (observed 10-48s in the comparison test) is a non-issue at that volume.
DEEP_MODEL_ENV = "DEEP_MODEL"
DEEP_MODEL_DEFAULT = "deepseek-r1-distill-llama-70b"  # served free on Groq at time of writing


def _build_deep_chain():
    candidates = [
        _backend("groq-deep", GROQ_API_URL, "GROQ_API_KEY", DEEP_MODEL_ENV, DEEP_MODEL_DEFAULT),
    ]
    chain = [c for c in candidates if c]
    # Falls through to the same fast chain if no reasoning model configured/reachable,
    # so Deep pass degrades to Lite quality rather than hard-failing.
    return chain + _build_lite_chain()


def _web_query_stock(s):
    return f"{s.get('name')} {s.get('symbol')} share price news today India NSE BSE -- latest developments this week"


def _web_query_sector_macro(s):
    sector = s.get("sector") or ""
    sector_part = f"{sector} sector, " if sector else ""
    return (f"{sector_part}Indian stock market today: Nifty/Sensex sentiment, sector news, "
            "RBI or macro developments this week")


def _gather_web_context(s):
    """Best-effort LIVE web search -- Deep pass ONLY (see analyze_stock). A stock-specific
    query and a combined sector+macro query run concurrently through the same
    Groq-compound/Gemini search chain ask_ai.py's chat panel already uses (tool_web_search
    there). Deliberately NOT wired into the Lite pass: that runs ~1x per stock across the
    whole ~1,800-stock universe every scan, where even one search call per stock would burn
    through free-tier search quota almost immediately -- see the module docstring and the
    session notes this decision came out of. Kept to 2 queries here (not 3) to bound the
    added latency/quota cost on top of the Deep pass's own reasoning-model call. Never
    raises and never blocks the verdict: any failed/unavailable/empty query is just dropped
    from the digest rather than injecting an error into the LLM prompt."""
    queries = [("Stock", _web_query_stock(s)), ("Sector & market", _web_query_sector_macro(s))]

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(queries)) as ex:
        futures = {ex.submit(tool_web_search, q): label for label, q in queries}
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


_JSON_FENCE_RE = re.compile(r"^```(json)?|```$", re.MULTILINE)


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


def analyze_stock(s, deep=False):
    """s: one stock dict from data/breakouts.json (as loaded, not pre-flattened).
    Returns the parsed verdict dict plus metadata (backend used, latency, digest) on
    success, or {"error": ...} if every backend in the chain failed/produced unparseable
    output. Never raises -- callers looping over 50+ stocks shouldn't need a try/except
    per call."""
    digest = build_digest(s)
    chain = _build_deep_chain() if deep else _build_lite_chain()
    system_prompt = DEEP_SYSTEM if deep else LITE_SYSTEM

    if not chain:
        return {"error": "No free-tier AI backend configured (need at least one of "
                          "CEREBRAS_API_KEY, GEMINI_API_KEY, GROQ_API_KEY, NVIDIA_NIM_API_KEY, DEEPSEEK_API_KEY)."}

    t0 = time.time()  # covers web search + LLM call -- the full wait the frontend shows a spinner for

    web_context, web_sources = None, []
    if deep:
        try:
            web_context, web_sources = _gather_web_context(s)
        except Exception:
            pass  # live web search is a bonus on top of the digest, never blocks the verdict

    user_content = f"Analyze this stock:\n\n{digest}"
    if web_context:
        user_content += "\n\nRecent live web search results (supplementary real-time context):\n" + web_context

    messages = [
        {"role": "system", "content": system_prompt},
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
    parsed["pass"] = "deep" if deep else "lite"
    if deep:
        parsed["web_sources"] = web_sources
    return parsed


if __name__ == "__main__":
    import sys
    from pathlib import Path

    data_path = Path(__file__).resolve().parent.parent / "data" / "breakouts.json"
    d = json.loads(data_path.read_text(encoding="utf-8"))
    by_symbol = {x["symbol"]: x for x in d["stocks"]}
    sym = sys.argv[1] if len(sys.argv) > 1 else "SPANDANA"
    s = by_symbol.get(sym) or d["stocks"][0]

    print("=== DIGEST ===")
    print(build_digest(s))
    print()
    print("=== LITE PASS ===")
    print(json.dumps(analyze_stock(s, deep=False), indent=2))
