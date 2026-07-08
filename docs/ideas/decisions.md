# Competitor Ideas Review — Decisions

> Triage of the competitor-ideas review (`docs/competitor-ideas-review.html`).
> Captured from the review session. This file is the source of truth (the viewer's
> in-browser selections are not persisted).

## ✅ Adopt (12)
| # | Idea | Notes |
|---|------|-------|
| 1 | Two-column signals (confirming / risk) | |
| 2 | RSS-backed conviction score | |
| 3 | Make-or-break line per call | |
| 4 | Explicit gates (breadth / volume / trend) | |
| 5 | Setup-type routing (VCP, base breakout, momentum) | |
| 6 | Health badges | |
| 9 | ⭐ Hindsight Loop (learn from resolved calls) | Top pick — we already have resolved-call ground truth |
| 10 | Attribution | |
| 11 | Benchmark + expectancy | |
| 12 | Baskets | |
| 14 | News pulse | |
| 15 | Insider-flow flag | |

## 🕒 Parked → Later To-Dos (2)
See `docs/ideas/later-todos.md`.
| # | Idea | Why parked |
|---|------|-----------|
| 8 | Dual-model adversarial grade | Unsure how it'd work in practice — explore feasibility later |
| 13 | "Ask BreakoutAI" (MCP query layer) | Good idea, not now — spec captured in `mcp-ask-breakoutai.md` |

## ❌ Skip (1)
| # | Idea | Reason |
|---|------|--------|
| 7 | Cost / freshness stamp & point-in-time replay | Not worth the overhead |

---

## Suggested build order (agreed)
1. **First sprint:** #9 Hindsight Loop + #11 Benchmark + expectancy — highest leverage, lowest risk, directly counters Delvantic; we already hold the data.
2. Card-UX cluster: #1 / #2 / #3 / #4.
3. Remainder of the Adopt pile.
