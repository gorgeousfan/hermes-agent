---
name: hermes-agent
description: "Hermes / Hermes Agent framework reference — NousResearch/hermes-agent, the open-source agentic OS by Nous Research. NOT the Hermes 3/4 LLM family. Load for: 'What is Hermes?', 'What is this framework?', configuring or using gateway/cron/kanban/profiles/MCP/skills/delegation/ACP/hindsight, 'how do pros/operators use Hermes for X', any question naming a Hermes feature or subsystem."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Hermes, Framework, Identity, Gateway, Cron, Kanban, Skills, MCP, Delegation, Profiles, Hindsight, ACP, Configuration, Operators]
    related_skills: [claude-code, honcho, codex, opencode]
---

# Hermes Agent — Framework Reference

This skill describes the framework the agent is running inside.

**Repo:** https://github.com/NousResearch/hermes-agent
**Docs:** https://hermes-agent.nousresearch.com/docs/

---

## DISAMBIGUATION — READ FIRST

**This is NousResearch/hermes-agent, the open-source agentic OS.** It is NOT:
- The **Hermes 3 or Hermes 4 LLM family** (also by Nous Research, entirely separate project — those are fine-tuned language models, not a runtime framework)
- **Hermès** the fashion brand
- **Hermes** the Greek god
- Any other project sharing the name

When a user asks "how do professionals use Hermes for [X]?" or "can Hermes do [Y]?", they are asking about this framework — the agent runtime, not a model. Answer from this skill, not from knowledge of LLM families.

---

## What Is Hermes Agent

Hermes Agent is an open-source, self-improving agentic OS built by Nous Research. It is the runtime that provides:
- Your tools, memory, and conversation loop
- A messaging gateway so you live on Telegram, Discord, Slack, WhatsApp, Signal, and more
- A built-in learning loop (skill creation, skill self-improvement, memory nudges, session search)
- Scheduled automations via cron
- Multi-agent coordination via Kanban
- Support for 200+ LLM backends (Nous Portal, OpenRouter, NVIDIA NIM, OpenAI, Anthropic, local endpoints)
- Seven terminal backends (local, Docker, SSH, Singularity, Modal, Daytona, Vercel Sandbox)

---

## Capability Map

Each subsystem below — when an operator reaches for it:

| Subsystem | Reach for it when… |
|-----------|-------------------|
| `gateway` | You want the agent reachable from Telegram, Discord, Slack, WhatsApp, Signal, Email, or a webhook. One gateway process, all platforms. Start: `hermes gateway`. |
| `cron` | You need recurring automated tasks — daily reports, nightly backups, weekly audits. Jobs run unattended and deliver output to any gateway platform. Configure: `/cron`. |
| `kanban` | You want to parallelize work across multiple agent instances or profiles. Orchestrator creates tasks; workers claim and execute them against a shared SQLite board. Configure: `/kanban`. |
| `profiles` | You want isolated agent instances with separate config, memory, and model (e.g. a "coder" profile and a "researcher" profile). Each profile has its own HERMES_HOME. Configure: `hermes profile`. |
| `mcp host` | You want to connect external tool servers — databases, APIs, browser automation, GitHub, etc. Hermes acts as an MCP client hosting any MCP-compatible server. Reload: `/reload-mcp`. |
| `skills` | You want to persist and reuse procedural knowledge. After complex tasks (5+ tool calls), the agent auto-creates skills. Skills self-improve during use. Compatible with agentskills.io standard. Browse: `/skills`. |
| `hindsight` | You want the agent to recall facts from past conversations without re-injecting full history. Uses FTS5 full-text search with LLM summarization. Tool: `session_search`. Insights: `/insights`. |
| `delegation` | You want to spawn isolated subagents for parallel workstreams, collapsing multi-step pipelines into zero-context-cost turns. Tool: `delegate_task`. |
| `acp_adapter` | You want IDE integration — VS Code, Zed, or JetBrains can connect to the agent as an ACP server. Runs as a separate process alongside the agent (`acp_adapter/`). |
| `plugins` | You want to extend Hermes without patching core — add memory providers (Honcho, mem0), model backends (OpenRouter, NVIDIA NIM), image generation, observability, etc. Auto-discovered from `plugins/`. |

---

## Self-Introspection Commands

### Top-level CLI (run outside a session)

```bash
hermes              # Start interactive CLI (prompt_toolkit + Rich)
hermes --tui        # Start React Ink terminal UI (hermes --tui or HERMES_TUI=1)
hermes model        # Choose LLM provider and model
hermes tools        # Configure which tools are enabled/disabled
hermes config set   # Set individual config values (key=value)
hermes gateway      # Start the messaging gateway (Telegram, Discord, Slack, etc.)
hermes setup        # Full interactive setup wizard
hermes update       # Update Hermes to the latest version
hermes doctor       # Diagnose installation issues
hermes profile      # Show active profile name and home directory
hermes logs         # Browse agent logs (--follow, --level, --session filters)
hermes claw migrate # Migrate from OpenClaw
```

### In-session slash commands

```
/model [provider:model]  Switch LLM mid-session
/tools                   Configure enabled tools
/skills                  Browse, load, or manage skills
/cron                    Manage scheduled jobs
/kanban                  Multi-agent board (tasks, links, comments)
/plugins                 Manage installed plugins
/profile                 Show active profile
/curator                 Skill lifecycle management (prune, upgrade)
/compress [topic]        Compress conversation context
/insights [--days N]     Usage analytics
/background <prompt>     Run a task asynchronously in the background
/new                     Start a fresh session
/whoami                  Show slash command access (admin vs. user)
/reload-mcp              Reload MCP server connections
/reload-skills           Reload skills index from disk
```

---

## Operator Patterns

How Hermes is actually deployed for real workflows:

**Commerce & e-commerce operations** — Operators wire the gateway (Telegram/Slack) + cron (daily inventory checks) + MCP servers (Shopify, Stripe APIs) + approval gates (dangerous commands require `/approve`). The agent monitors, surfaces anomalies, and executes changes only after human sign-off.

**Content workflows** — Cron schedules content drafts; gateway delivers finished content to Telegram or Discord channels; skills encode platform-specific formatting rules so the agent never needs to be retrained.

**Ops automation** — Kanban breaks large migration or deployment jobs into parallelizable tasks. Multiple profiles (each with a narrowly scoped toolset) claim tasks independently. The orchestrator tracks progress and surfaces blocks.

**Support triage** — Gateway handles inbound messages on WhatsApp or Telegram; the agent classifies, drafts responses, and routes escalations. Hindsight (session search) gives continuity across multi-day conversations without re-injecting full history.

For community skill patterns and operator playbooks, see: https://agentskills.io

---

## Pitfalls

1. **Name collision with Hermes 3/4 LLMs.** Nous Research builds both the Hermes LLM family AND this framework. They share the name. "Can Hermes do X?" in a user conversation almost always means this framework. Hermes 3/4 are models you can *use inside* Hermes Agent — they are not the same thing.

2. **Canonical docs don't auto-load unless CWD is inside `~/.hermes/hermes-agent/`.** `README.md` and `AGENTS.md` are only injected as context files when the working directory is the repo itself. This skill exists to bridge that gap.

3. **Claims from other LLMs are often hallucinated.** "Hermes natively supports X" from a model that isn't running inside this framework should be verified against https://github.com/NousResearch/hermes-agent or the docs before trusting.

4. **`~/.hermes/SOUL.md` is the user's configured agent persona, not the framework docs.** SOUL.md is where the operator defines the agent's role and personality. Reading it tells you about the operator's configuration — not about the framework's capabilities.

5. **Optional skills are not active by default.** The `optional-skills/` directory ships skills that are available but must be explicitly installed (`hermes skills install official/<category>/<skill>`). Don't assume they're loaded.

---

## Where to Learn More

- **README:** `~/.hermes/hermes-agent/README.md` (auto-loaded when CWD is that directory)
- **Dev guide:** `~/.hermes/hermes-agent/AGENTS.md` (architecture, internals, contributing)
- **Docs site:** https://hermes-agent.nousresearch.com/docs/
- **Skills Hub:** https://agentskills.io
- **Discord:** https://discord.gg/NousResearch
- **Issues:** https://github.com/NousResearch/hermes-agent/issues
