# Later To-Dos (parked ideas — not to work on right now)

Ideas we like but are explicitly deferring. Revisit when core scan/ledger work is stable.

---

## 1. "Ask BreakoutAI" — MCP query layer (idea #13)
**Status:** parked · **Full spec:** [`mcp-ask-breakoutai.md`](./mcp-ask-breakoutai.md)

Plain-English querying of our breakout ledger + scan JSON via an MCP server
(Claude / Cursor / VS Code). Read-only wrapper over data we already publish.
We'll explore this later.

---

## 2. Dual-model adversarial grade (idea #8)
**Status:** parked · not yet sure how it works in practice.

Idea: grade each call with two models and check for convergence
(e.g. Opus primary thesis + a cheaper skeptic pass). We *do* have multiple
models callable from the same API — Opus 4.8 (`claude-opus-4-8`),
Haiku 4.5 (`claude-haiku-4-5`), Sonnet 5 (`claude-sonnet-5`) — so infra isn't
the blocker; the open question is whether the adversarial grade actually adds
signal and how to wire it into the scan pipeline. **Explore later** before
committing to a design.
