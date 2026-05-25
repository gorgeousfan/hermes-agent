---
title: "Cursor Agent SDK"
sidebar_label: "Cursor SDK"
description: "Delegate coding work to Composer via Cursor SDK and optional OpenAI bridge"
---

# Cursor Agent SDK integration

Hermes can delegate focused repository work to **Cursor's agent runtime** (Composer 2.5 by default) using the official [Cursor Python SDK](https://cursor.com/docs/sdk/python). This keeps Hermes orchestration (memory, skills, messaging, cron) while Cursor handles IDE-grade editing in a target directory.

## Install (optional)

The SDK is **not** included in the default install (proprietary package, large wheels).

```bash
uv pip install -e ".[cursor]"
```

Add to `~/.hermes/.env`:

```bash
CURSOR_API_KEY=cursor_...   # https://cursor.com/dashboard/integrations
```

Verify:

```bash
hermes doctor
```

## `cursor_agent` tool (recommended)

Use when you want Cursor Composer to implement or refactor code, not when you need Hermes subagents (`delegate_task`).

| Parameter | Description |
|-----------|-------------|
| `goal` | Required task description |
| `context` | Optional constraints, paths, conventions |
| `cwd` | Repo directory (default: process cwd) |
| `model` | Default `composer-2.5` |
| `resume_agent_id` | Continue a prior Cursor agent in the same parent turn |
| `cloud_repo_url` | Optional — run on Cursor cloud instead of local `cwd` |

Returns JSON: `status`, `agent_id`, `run_id`, `summary`, and `error` on failure.

The tool is in the **`cursor`** toolset (included by `hermes-acp` and `hermes-api-server`). Enable globally via `hermes tools` if needed.

### vs `delegate_task`

| | `cursor_agent` | `delegate_task` |
|--|----------------|-----------------|
| Runtime | Cursor SDK (Composer) | Hermes `AIAgent` subagent |
| Tools | Cursor-native | Hermes tool registry |
| Best for | Repo editing with Composer | Parallel Hermes workers |

## Experimental: OpenAI bridge (Composer as LLM)

**Not recommended for most users.** Each Hermes chat turn may spawn a full Cursor agent run (slow, costly).

### 1. Start the bridge

```bash
export CURSOR_API_KEY=cursor_...
python -m integrations.cursor_bridge
# http://127.0.0.1:18765/v1
```

Optional: `CURSOR_BRIDGE_CHAT_ONLY=1` adds guidance to answer in chat-only mode.

### 2. Point Hermes custom provider at the bridge

```yaml
model:
  provider: custom
  base_url: http://127.0.0.1:18765/v1
  api_key: ${CURSOR_API_KEY}
  api_mode: chat_completions
```

Bearer auth on bridge requests must match `CURSOR_API_KEY`.

See [integrations/cursor_bridge/README.md](https://github.com/NousResearch/hermes-agent/blob/main/integrations/cursor_bridge/README.md) for environment variables.

## RFC and upstream contribution

Design notes: [docs/rfc/cursor-sdk-hermes-integration.md](https://github.com/NousResearch/hermes-agent/blob/main/docs/rfc/cursor-sdk-hermes-integration.md).

## Security

`cursor_agent` and the bridge grant Cursor the same class of access as the Cursor IDE agent for the chosen `cwd` (files, shell, MCP). Use on trusted repositories only.
