---
name: atlas-briefing
description: "Read-only access to Atlas's morning briefing (weather + NOAA alerts + Orion overnight performance + last-conversation summary) via SSH. Lets Phoebe-Hermes consume the legacy pre-Helios morning brief that Atlas builds daily."
version: "1.0.0"
author: "punchtaylor"
license: "MIT"
---

# Atlas Briefing Skill

Pull-from-Atlas access to the daily morning briefing that `phoebe_atlas.py` already builds. Mirrors the `orion-state` skill pattern: SSH to Atlas, read the persisted brief file, surface content to Phoebe.

This skill exists because Atlas's morning brief currently delivers to MQTT (Luna TTS) + Telegram only — Phoebe-Hermes on Helios is not on either of those wires. Atlas was patched 2026-05-19 to additionally persist the brief to `~/morning_briefing_YYYY-MM-DD.md` + `~/morning_briefing_latest.md` so this skill can read it.

## When to Use

- Phoebe's morning routine — pull today's brief and surface it to user (via iMessage cron, voice readback, or chat response)
- User asks "what's the morning brief?" or "what did Atlas say this morning?"
- Cross-referencing the Atlas-built morning summary with other Phoebe context

## When NOT to Use

- Real-time conversational chat — this is a once-a-day artifact
- Anything that modifies the brief (read-only; Atlas owns the build)

## Prerequisites

- Passwordless SSH from Helios → `punchtaylor@192.168.1.212` (deployed 2026-05-12 mesh)
- Atlas patched (2026-05-19) to persist briefing to `~/morning_briefing_*.md` files. Older briefs (pre-patch) won't have persisted files.

## Interface

| Command | Description | Example |
|---------|-------------|---------|
| `today` | Today's brief (defaults to `~/morning_briefing_latest.md`) | `today` |
| `date YYYY-MM-DD` | Brief from a specific date | `date 2026-05-19` |
| `summary` | One-line status: when last brief was generated, char count | `summary` |

## Usage Pattern

```bash
# Today's morning brief content
python3 ~/.hermes/skills/phoebebot-migration/atlas-briefing/scripts/atlas_briefing.py today

# Specific date
python3 ~/.hermes/skills/phoebebot-migration/atlas-briefing/scripts/atlas_briefing.py date 2026-05-19

# Quick health check
python3 ~/.hermes/skills/phoebebot-migration/atlas-briefing/scripts/atlas_briefing.py summary
```

## Brief Content

What Atlas's `_build_morning_briefing()` assembles:
- Greeting (user's name from `_load_facts()`)
- Weather (Open-Meteo, Indianapolis: temp + condition + hi/lo)
- NOAA alerts in last 24h (count + most recent)
- Orion trading performance (via `_orion_request("PERFORMANCE")`) — with Alpaca open-position fallback when Orion is unreachable
- Last meaningful conversation summary (from `episodes` table)

## Error Handling

- **No brief file for today:** Returns "Atlas has not built a morning briefing today" — happens before first-activity trigger or if Atlas was down at brief time
- **SSH timeout / connection failure:** "Atlas unreachable" — graceful degradation
- **File read errors:** Caught; returns informative message rather than crashing

## Integration

The natural wire-up is Hermes-Phoebe's existing 8am cron job (`Morning X Digest`, `0 8 * * *`). Either:
- Extend that cron's prompt to also include `atlas_briefing.py today` output for a single consolidated morning brief, OR
- Add a separate cron job at 8:05am that delivers the Atlas brief alongside (after the X digest)

Decision deferred to the operator — see `project_pre_helios_integration_audit.md` for context.

## Status

- v1 written 2026-05-19 — closes Sunday task #2 from `task_sunday_orion_integration.md`
- Atlas-side persistence patch shipped same day in `phoebe_atlas.py` `_deliver()` (lines ~3024)
- Hermes-side cron wire-up: pending operator decision (consolidate vs separate)
