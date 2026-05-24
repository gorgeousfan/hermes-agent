---
name: orion-state
description: "Read-only visibility into Orion paper-trading node state via SSH. Surfaces open positions, today's fires/blocks with reasons, watchlist, news, macros, and Monte Carlo risk model output for Phoebe to answer 'what's Orion up to?' questions."
version: "1.0.0"
author: "punchtaylor"
license: "MIT"
---

# Orion State Skill

Read-only visibility into the Orion paper-trading node (`192.168.1.200`) via SSH. Phoebe consumes Orion's existing on-disk state — Orion code stays untouched. No MQTT subscriptions required; this skill exists because Orion does not publish portfolio/decision state to MQTT today.

## When to Use

- User asks about Orion's current portfolio, recent decisions, P&L, watchlist, recent news, macro indicators
- Morning briefing extension — overnight Orion activity + risk model state
- Any read-only "what is Orion doing right now" query

## When NOT to Use

- **Anything that modifies Orion state.** This skill is read-only by design. To control Orion, use the (future) `orion-config` or `orion-alpaca` skills.
- Real-time tick data. Skill reads cached/logged state, which lags markets by seconds to minutes.

## Prerequisites

- Passwordless SSH from Helios → `punchtaylor@192.168.1.200` (verified deployed in mesh as of 2026-05-12 per `reference_tailscale.md` and `config.md`)
- Python 3 on Helios (already available — the script uses stdlib only, no external dependencies)

## Interface

| Command | Description | Example |
|---------|-------------|---------|
| `summary` | One-line overview: open positions, today's decision count, last trade-log entry | `summary` |
| `positions` | Open positions (from `orion_pending_open.json`) | `positions` |
| `today` | Today's fires + blocks grouped by reason | `today` |
| `watchlist` | Count + sample of `cheap_tradable_universe.json` | `watchlist` |
| `news [-n N]` | Recent N headlines from news cache (default 8) | `news -n 5` |
| `macros` | BDI / BTC / Copper / DXY / NatGas / Oil cache dashboard | `macros` |
| `risk` | Monte Carlo model summary (equity, p_win 0.55 scenario stats) | `risk` |
| `morning` | Composite: positions + today + risk + macros (for morning briefing) | `morning` |

## Usage Pattern

```bash
# One-line status check
python3 ~/.hermes/skills/orion-state/scripts/orion_state.py summary

# What did Orion do today?
python3 ~/.hermes/skills/orion-state/scripts/orion_state.py today

# Risk model state
python3 ~/.hermes/skills/orion-state/scripts/orion_state.py risk

# Morning briefing block
python3 ~/.hermes/skills/orion-state/scripts/orion_state.py morning
```

Output is plain text, conversational, ready for Phoebe to read aloud or include in a text response.

## Underlying State Files (on Orion)

| File | Used By |
|------|---------|
| `~/orion_pending_open.json` | `positions`, `summary`, `morning` |
| `~/orion_trades.log` | `today`, `summary` (pipe-delimited, result codes encode block reasons) |
| `~/orion_decisions.jsonl` | `summary` (decision count) |
| `~/cheap_tradable_universe.json` | `watchlist` |
| `~/news_cache.json` | `news`, `morning` |
| `~/bdi_cache.json`, `~/btc_cache.json`, etc | `macros`, `morning` |
| `~/monte_carlo_latest.json` | `risk`, `morning` |

## Error Handling

- **SSH timeout / connection failure:** Returns empty content; output says "unreadable" or "unavailable" for that section. No crash.
- **Missing file:** Same as SSH failure — graceful degradation.
- **JSON parse errors:** Caught; section shows "(unreadable)" rather than dumping a traceback.
- **No network to Orion:** All subcommands degrade gracefully — partial output is better than no output.

## Morning Briefing Integration

The `morning` subcommand is designed to be called from Phoebe's existing morning routine. Output block is concise, ready to drop into a larger briefing message:

```
=== Orion Overnight Brief ===

Open positions (N): ...
Orion today (YYYY-MM-DD): ...
Risk model: ...
Macro indicators: ...
```

To integrate: invoke `python3 ~/.hermes/skills/orion-state/scripts/orion_state.py morning` from wherever Phoebe assembles her morning brief, include the stdout in her message.

## What This Skill Does NOT Do

- **Doesn't query Alpaca directly.** Portfolio truth from Alpaca API is a separate (future) skill; this skill consumes Orion's cached state. Acceptable for v1 since the trade-log is append-only and reasonably fresh.
- **Doesn't compute P&L.** Orion's logs show trades but P&L computation requires Alpaca side or a P&L cache file (not present today). Future enhancement.
- **Doesn't modify anything.** Strictly read.

## Status

- ✅ v1 written 2026-05-17
- ⏳ Empirical test pending (run all subcommands, verify output shape)
- ⏳ Morning briefing integration pending (requires Phoebe-side hook)
- ⏳ Commit to `punchtaylor/hermes-agent` fork pending

## Future Phases (read-only first, control later)

Per the Phoebe-controls-Orion roadmap discussed 2026-05-17:

- **Phase 2:** `orion-alpaca` — Phoebe places/exits trades via Alpaca API directly, in parallel to Orion. Reads from this skill to avoid duplication.
- **Phase 3:** `orion-config` — Phoebe tunes Orion's threshold parameters via SSH config-block edits (same pattern as `ha-ssh-config`). No Orion logic changes, just dials.
- **Phase 4** (requires Orion-code approval): MQTT command topic on Orion side for richer real-time control.

All future phases consume this read-skill's data. Building eyes first is leverage for everything that comes after.
