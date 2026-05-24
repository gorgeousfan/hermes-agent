---
name: session-goal-paste
description: "Generate structured GOAL PASTE from last CLI session context for starting a new session with full state handoff."
version: 1.2.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [workflow, session-management, handoff, productivity]
    related_skills: [plan, writing-plans]
---

# Session Goal Paste

## Overview

The user frequently asks for a **GOAL PASTE** — a condensed, structured text block extracted from the most recent CLI session's context. This paste is fed into a new CLI session to re-establish state without re-reading the entire conversation history.

**Use case:** After a long CLI session completes (or gets interrupted), the user needs to hand off all context — what was done, what remains, known blockers — into a fresh session in ~20 lines. The goal paste is NOT a full summary; it's a **machine-readable handoff** that a new agent instance can consume instantly.

## When to Use

- User says: "look at last non-empty session running in cli, I need a new goal paste"
- User says: "give me a goal paste for this set of runs"
- User starts a conversation with "fresh session" or asks for session state handoff
- User asks "what was the last session doing and what's next?"
- User says "this is for the [specific topic] part" — the paste may need to be scoped to a sub-concern

## Core Structure

The goal paste uses a **header + status + TODO** format:

```
── [PROJECT NAME] — FRESH SESSION GOAL PROMPT ──
Path: /path/to/project
Model: [model details]
HW: [hardware specs if relevant]

Phase [N] STATUS: ✅ / 🟡 / ❌
  - [Key accomplishment 1]
  - [Key accomplishment 2]
  - [Known remaining diff / artifact]

NEXT: [PHASE N.F] [DESCRIPTIVE NAME]

TODO:
1. [Action item with file path or command]
2. [Next action item]
3. [Verification step]

KNOWN BLOCKERS (skip):
  - [Blocker 1 → deferred to later phase]
  - [Blocker 2 → deferred to later phase]

DELIVERABLE: "Phase N.F complete: [one-line result statement]"
```

**Sizing rule:** If the paste exceeds ~25 lines, or if the user has previously said "too big", use the vagua/overnight variant instead.

## Vagua / Overnight Variant (shorter, more autonomous)

When the user says "too big", "make it vagua", "overnight", or the session will run without supervision, switch to this format.

The vagua variant has TWO outputs:
1. A **overnight-map.md** written to `.hermes/mind-palace/overnight-map.md` — contains full verbose state with verified/unverified claim tables, workstream descriptions, and data-not-to-re-derive
2. A **short goal paste** (~10 lines) that points to the map and lists workstreams as labels A/B/C

### Overnight-map.md structure

Write this file to disk before composing the goal paste:

```
# [Project Name] — Overnight Navigation Map

**Purpose:** Entry doc for long-running autonomous sessions.

## Quick Trunk Reference
[table of key docs and their paths]

## Where We Are
[phase status with VERIFIED vs CLAIMED sections]

**VERIFIED CLAIMS — real output from actual binaries:**
[table of streams with test results]

**Active blockers:**
[P0 — root blocker description]

## Workstreams (pick one per session)
A — [highest impact]
B — [next]

## Data You Should Not Re-Derive
[3-5 hard-won facts]

## Fallback
If all streams look blocked, write a plan.md.
```
```

### Goal paste format (vagua)

```
── [PROJECT] — GOAL PASTE ──
Path: [path] | Model: [model]
Overnight nav: .hermes/mind-palace/overnight-map.md

[What's been verified, what's the root blocker. 2-3 lines max.]

Pick one:
A — [stream A]
B — [stream B]
C — [stream C]

Don't re-derive: [3-5 facts].
```

## Devil's Advocate Verification Step

**IMPORTANT:** Before composing a goal paste, VERIFY instead of TRUST.

The preceding session may have made claims that weren't actually saved or run. This happens frequently in long sessions. Always:

1. **Check binary existence**: `ls -lt <binary_name> 2>/dev/null` — if a tool was claimed built but the binary doesn't exist, mark it UNVERIFIED
2. **Check file existence**: `wc -l <source_file>` — verify new files were actually saved
3. **Run the test if uncertain**: `timeout 30 ./<test_binary> 2>&1 | tail -10` — quick smoke test
4. **Check for red flags**: grep for `FIXME|TODO|HACK` in critical source files
5. **Separate claims into sections**: `VERIFIED` (I ran it or confirmed file state) vs `CLAIMED-UNVERIFIED` (session said it but I haven't verified)

**Pitfall:** A session can claim "all 5 streams complete" but only 3 have actual binaries on disk. Always verify file artifacts before repeating claims.

## Scoping

If the user says "the other session is doing work rn, this is for the [X] part", the goal paste should focus ONLY on [X] — don't try to summarize the full project state. The paste becomes a **task-specific handoff** rather than a project-wide status report.

## Progressive Refinement

The user may ask for a goal paste, then later say "again look at last non-empty session" — meaning they want a NEW paste with updated context. Don't reuse the old one; re-query session state completely.

## What to Include

- **When a new paper/resource was added during the session**, include it in the paste under the NEXT section so the next session knows to read it.
- **Known float artifacts** — call them out explicitly so the next agent doesn't re-debug them.

## Pitfalls

- **Don't write a narrative summary.** The user already lived it. The paste is for a *new* agent that has no context. Be terse and factual.
- **Don't claim things are done that weren't verified.** If the session said "debugged but not built", mark it 🟡.
- **Include HW specs only when relevant** (GPU type, VRAM, CUDA version for GPU work).
- **Always verify file state** — sessions can claim things that weren't actually saved to disk.
- **The user doesn't want to see the search process** — just produce the paste directly.
