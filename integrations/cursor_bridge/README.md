# Cursor bridge (experimental)

Local OpenAI-compatible HTTP server that forwards chat completions to Cursor Agent SDK (`composer-2.5` by default).

## Install

```bash
uv pip install -e ".[cursor]"
export CURSOR_API_KEY="cursor_..."
```

## Run

```bash
python -m integrations.cursor_bridge
# Listens on http://127.0.0.1:18765/v1 by default
```

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `CURSOR_API_KEY` | (required) | Cursor API key; also used as Bearer token for bridge auth |
| `CURSOR_BRIDGE_HOST` | `127.0.0.1` | Bind address |
| `CURSOR_BRIDGE_PORT` | `18765` | Port |
| `CURSOR_BRIDGE_CWD` | process cwd | Local agent working directory |
| `CURSOR_BRIDGE_MODEL` | `composer-2.5` | Model id passed to SDK |
| `CURSOR_BRIDGE_CHAT_ONLY` | off | Prepend chat-only system guidance |

## Hermes config

```yaml
model:
  provider: custom
  base_url: http://127.0.0.1:18765/v1
  api_key: ${CURSOR_API_KEY}  # or paste key; must match bridge Bearer token
  api_mode: chat_completions
```

**Warning:** Each Hermes turn may start a full Cursor agent run (slow and costly). Prefer the `cursor_agent` tool for coding subtasks.
