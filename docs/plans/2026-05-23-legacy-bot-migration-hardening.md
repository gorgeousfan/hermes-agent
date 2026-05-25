# Legacy bot migration hardening deep dive

Date: 2026-05-23
Branch: `feat/legacy-bot-migration-hardening`

This document captures the technical design that came out of migrating a
legacy operations bot into Hermes. It is intentionally sanitized: no token
values, private channel IDs, credentials, organization names, or customer data
belong here.

## Goal

Move a long-running Slack/Telegram operations bot into Hermes without losing
alerting, scheduled summaries, delivery parity, or least-privilege controls.
The migration should favor Hermes primitives instead of carrying forward a
bespoke bot runtime.

## Legacy feature classes mapped to Hermes

| Legacy behavior | Hermes primitive | Why |
| --- | --- | --- |
| Daily Slack digest | Cron job with a pre-run collector script and LLM prompt | Collector is deterministic; summary remains model-generated. |
| Issue tracker watcher | `no_agent=True` cron script | Empty stdout means silent all-clear; non-empty stdout is the alert. |
| Standup prep | Prompt-only cron job with skills/toolsets | Mostly synthesis across existing sources. |
| End-of-day recap | Cron job with `context_from` and/or compact context script | Chains daily digest + watcher state into one recap. |
| Runbooks/triage procedures | Hermes skills | Procedural knowledge loads on demand and stays reusable. |
| Platform chat adapters | Hermes gateway | Avoids maintaining bespoke Slack/Telegram bot loops. |
| Deterministic watchdogs | `~/.hermes/scripts/*.py` + cron | Keeps side effects explicit and stdout-driven. |

## Runtime architecture

```text
Slack / Telegram / other gateway adapters
            │
            ▼
Hermes gateway + scheduler tick
            │
            ├── prompt-only cron jobs
            │       └── LLM synthesizes final delivery
            │
            ├── script + agent cron jobs
            │       ├── script collects compact ASCII-safe context
            │       └── LLM summarizes with attached skills/toolsets
            │
            └── no-agent watchdog jobs
                    ├── empty stdout → silent
                    ├── non-empty stdout → delivered verbatim
                    └── non-zero exit → error alert
```

The important migration invariant is that deterministic checks should not need
an LLM to decide whether to wake the user. They should emit nothing when there
is nothing to say.

## Key integration lessons

### 1. Windows hidden-gateway cron must not use `pythonw.exe` for child scripts

When the gateway is launched hidden from Windows Task Scheduler, `sys.executable`
can be `pythonw.exe`. Child cron scripts spawned with that interpreter can run
successfully while stdout/stderr disappears, which breaks both `no_agent=True`
watchdogs and script-output injection for agent-mode cron jobs.

Branch change:

- `cron/scheduler.py` now detects `pythonw.exe` on Windows and uses sibling
  `python.exe` for Python cron scripts when available.
- `tests/cron/test_cron_script.py` adds a regression test for the interpreter
  selection.

### 2. Slack App/Assistant DM surfaces need safe fallback and stronger gating

Slack can deliver inbound Socket Mode events from DM-like App/Assistant channel
IDs that are readable but not postable with `chat.postMessage`; Slack returns
`channel_not_found` on replies. During cutover, that made replies appear silent
or caused fallback attempts to open normal bot DMs.

Branch change:

- `gateway/platforms/slack.py` records DM channel → user mappings from inbound
  events.
- `send()` retries `channel_not_found` DM sends by opening the user's normal bot
  DM with `conversations.open()` and drops the invalid thread timestamp.
- The fallback refuses non-allowed users before opening a DM.
- `SLACK_RESTRICT_DM_CHANNELS=true` / `slack.restrict_dm_channels: true` lets
  `SLACK_ALLOWED_CHANNELS` apply to DMs as well as public/private channels.
- `tests/gateway/test_slack.py` covers fallback routing, non-allowed-user
  refusal, and DM channel restriction.

### 3. Private cutovers need both outbound and inbound controls

A cron delivery target like `slack:<home-dm>` controls where scheduled output is
sent, but the Slack app may still receive inbound events from other channels or
App Home surfaces. A safe cutover needs:

- `SLACK_ALLOWED_USERS`
- `SLACK_ALLOWED_CHANNELS`
- `SLACK_RESTRICT_DM_CHANNELS=true` when pinning to one DM/channel
- platform-specific smoke tests before recurring jobs are resumed

### 4. Large/non-ASCII collector output should be bounded and ASCII-safe

Hidden Windows scheduler output capture is less forgiving than direct script
execution. The digest collector should emit bounded, sanitized text rather than
large raw Slack JSON. The wrapper pattern is:

```python
out = raw_text.encode("ascii", "backslashreplace").decode("ascii")
sys.stdout.write(out[:MAX_BYTES])
```

### 5. End-of-day jobs should consume compact upstream context

The EOD recap should not re-query every source at delivery time. It should read
compact output from upstream jobs and produce a concise recap. This avoids
runaway tool usage and prevents fabrication when upstream credentials are
missing.

## Cutover workflow

1. Inventory the legacy bot and classify each feature by behavior, not by file.
2. Move reusable procedures into skills.
3. Move deterministic checks into scripts.
4. Create staged cron jobs with explicit delivery targets.
5. Align the gateway service `HERMES_HOME` with the CLI home that owns scripts,
   skills, cron jobs, and state.
6. Validate provider credentials with direct API smoke tests, then validate live
   Hermes adapter delivery.
7. Run local-only cron clones before enabling recurring delivery.
8. Resume jobs one at a time.
9. Stop the legacy bot only after parity is verified and rollback is known.

## Security model

- No secrets in repo docs, logs, skills, memory, or status reports.
- Prefer scoped platform tokens and explicit allowlists.
- Default to private delivery targets during cutover.
- Keep mutation-capable jobs off unless the user explicitly authorizes them.
- Treat Slack/Telegram test sends as delivery verification only, not as a reason
  to broaden app access.

## Branch contents

The initial Hermes branch contains generic hardening needed by the migration:

- Cron child-script interpreter fix for hidden Windows gateways.
- Slack non-postable DM fallback with allowlist enforcement.
- Slack DM/channel restriction knob for private cutovers.
- WhatsApp QR image output support for reliable QR pairing from gateway/PTY
  workflows.
- Documentation and regression tests for the above.

## Follow-up work

Potential upstreamable migration primitives:

1. A `hermes migrate bot` helper that scaffolds scripts, skills, and cron jobs
   from a manifest.
2. A safe delivery smoke-test command that creates a temporary no-agent cron job,
   runs it, verifies persisted output, and removes it.
3. A shared watcher-state library for `seen`, `ack`, `realert_after`, and
   quiet-hours behavior across migrated watchdog scripts.
4. A first-class compact-context convention for agent-mode cron chains.
