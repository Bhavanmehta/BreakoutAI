---
name: wrap-session
description: Consolidate an in-progress BreakoutAI work session into HANDOFF.md and the memory system before the user compacts/closes the chat, so a fresh session can resume with full context and no re-derivation.
---

# Wrap session

Run this when the user wants to close out or compact the current chat and pick up
cleanly later (they may say "wrap this up", "save progress", "let's compact", etc.).
Do not wait to be asked for each individual step below — do all of them.

## 1. Take stock first

- `git status` (repo root) — list every modified/new/untracked file. Never assume
  something is committed; this project's rule is **commit on a branch, never `main`**,
  and commits only happen when explicitly requested — so most sessions end with
  uncommitted work, and that's expected, not a problem to fix.
- Re-read the conversation for: what was the actual goal, what was tried, what broke
  and how it was fixed, what the concrete results/numbers were, what's still open or
  was explicitly deferred by the user.
- If there are multiple independent threads of work in-flight (e.g. a backend research
  script and an unrelated frontend feature that don't touch each other's files), treat
  them as separate sections — don't blend them into one narrative.

## 2. Rewrite HANDOFF.md (repo root)

This file is the primary resume mechanism — a fresh session reads it first. Keep past
sessions' sections intact (append/rewrite the current session's section, don't delete
history unless it's now redundant with memory). Structure:

- A short **TL;DR** at the top naming every independent open thread and whether each is
  committed or not.
- One section per session/thread, titled `## Session N — <short description>`, containing:
  - **Why** — the actual motivating question or ask, in the user's terms.
  - **What was built/found** — concrete enough that another agent doesn't need to
    re-read every file; name the actual files/functions touched.
  - **Bugs hit and fixes** — anything non-obvious that cost real debugging time; a
    fresh session should not rediscover the same bug.
  - **Results** — real numbers, not vague summaries ("41.1% hit rate, n=19,747,
    p<0.001", not "looked promising").
  - **Next steps** — concrete, ordered, and honest about what's a decision left to the
    user vs. a task ready to execute.
  - **Exact re-run commands** for anything expensive (whole-market backtests, scrapes)
    so the next session doesn't have to reconstruct them.
- A shared **Pending/next (older)** section for longer-lived TODOs that span sessions.
- A **Key files touched, by thread** section so a diff/review pass is easy later.

## 3. Update the memory system

Memory lives at `C:\Users\bhava\.claude\projects\c--Users-bhava-OneDrive-Documents-GitHub-BreakoutAI\memory\`.

- Update (don't blindly overwrite) any existing memory file whose topic this session
  touched — e.g. append a new dated results block to a research-tracking memory rather
  than replacing prior findings.
- Create new memory files only for genuinely new, durable facts/feedback/decisions —
  not for ephemeral task state (that belongs in HANDOFF.md, not memory).
- Always update `session-handoff-pointer.md` last so it accurately reflects the
  *current* set of open threads and where in HANDOFF.md to find each one — this is
  the one memory file basically guaranteed to be read at the start of the next session.
- Update `MEMORY.md`'s index line for any file you touched (one line, under ~150 chars).
- Follow the project's existing memory conventions (frontmatter with `name`/
  `description`/`metadata.type`, linking related memories with `[[name]]`) — check an
  existing memory file for the exact format rather than guessing.

## 4. Report back

Tell the user, concretely:
- What got written/updated (HANDOFF.md + which memory files).
- What is and isn't committed, and a reminder that nothing gets committed without
  being asked.
- That it's safe to compact/close now, and what a fresh session will see first.
- If asked "what should my first message be", give a short, concrete, ready-to-paste
  message naming HANDOFF.md and which thread(s) to pick up — don't just say "read
  HANDOFF.md", name the actual decision or task that's next.
