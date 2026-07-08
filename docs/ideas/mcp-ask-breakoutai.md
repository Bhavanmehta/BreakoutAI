# Idea #13 — "Ask BreakoutAI" (MCP query layer)

> **Status:** Maybe → parked for later implementation
> **Source:** Signal8.ai (their live headline feature)
> **Effort:** Medium · **IP:** Clear (MCP is an open Anthropic protocol; our data + server are entirely ours)
> **Our naming:** "Ask BreakoutAI" (do **not** reuse Signal8's wording/branding)

---

## What it is
Expose the existing BreakoutAI data (DuckDB ledger + published scan JSON) through a **Model Context Protocol (MCP) server**, so a user can query their breakouts in **plain English** from Claude, ChatGPT, Cursor, or VS Code.

Example: *"Which US tight-VCP calls this week are still below their pivot?"* →
*"2 open: RIGL (pivot $30.20, now $31.40 — just above) and XYZ still 1.2% under. Both A-grade, breadth gate passed."*

## Why it's worth doing
- It's Signal8's single most modern/differentiating feature.
- We already have the hard part: the **DuckDB ledger** + the daily **scan/performance JSON**. This is a thin read-only wrapper over data we already publish.
- Low lift, high signal; strong story ("your breakouts, queryable in plain English").

---

## Implementation sketch (later)

### 1. Server
- Build a small **MCP server** (Python — matches our stack; the `mcp` SDK supports stdio + HTTP transports).
- Read-only. No write tools. No secrets exposed.

### 2. Data sources to wrap
| Source | Already exists | Exposed as |
|---|---|---|
| `duckdb` breakout ledger (`DUCKDB_PATH`) | ✅ | SQL-backed query tool |
| `us/performance.json`, India `performance.json` | ✅ | resolved won/lost/open records |
| daily scan output (`export_ohlc.py` products) | ✅ | latest picks per market |

### 3. Tools to expose (read-only)
- `list_calls(market, date_range, status)` → calls with tier/conviction/status
- `get_performance(market, window)` → win-rate, expectancy, benchmark delta
- `query_ledger(sql)` → **parameterised, allow-listed** read-only SQL against a *view* (never raw tables; block DDL/DML)
- `explain_call(symbol, date)` → the card's confirming/risk signals + make-or-break

### 4. Safety / guardrails
- Read-only connection; open DuckDB with `access_mode=READ_ONLY`.
- SQL tool runs against a **restricted view**, statement-timeout, row cap (reuse existing `MAX_RESULTS`).
- No API keys or file paths returned in responses.
- Rate-limit per client.

### 5. Packaging / distribution
- Ship an `mcp.json` / install snippet so users add it to Claude Desktop / Cursor config.
- Optional hosted HTTP transport later; start with local stdio for our own dogfooding.

---

## Open questions to resolve before building
- [ ] Local-only (power users) vs hosted (everyone)? Start local.
- [ ] Auth model if hosted (per-user token?).
- [ ] Which subset of the ledger is safe to expose publicly vs members-only.
- [ ] Do we also want a same-data **"Ask BreakoutAI" chat box on the site** (idea shares the backend query layer)?

## Dependencies / relation to other ideas
- Pairs naturally with **#11 benchmark + expectancy** (query surface for the track record).
- The same query layer can power a website chat widget, not just external IDEs.

---
*Parked from the competitor-ideas review (`docs/competitor-ideas-review.html`). Revisit when core scan/ledger work is stable.*
