# Weaviate Engram Memory Provider

Long-term memory backed by [Weaviate Engram](https://weaviate.io/blog/engram-deep-dive) — Weaviate's managed memory service with server-side extract / reconcile / commit pipelines.

## Requirements

- `pip install weaviate-engram` (already declared in `plugin.yaml`'s `pip_dependencies`)
- An Engram API key from [weaviate.io/engram](https://weaviate.io/engram)

## Setup

```bash
hermes memory setup    # select "weaviate_engram"
```

Or manually:

```bash
hermes config set memory.provider weaviate_engram
echo 'ENGRAM_API_KEY=...' >> ~/.hermes/.env
```

That's it — no cluster URL needed. The SDK talks to Engram's public endpoint
(`https://api.engram.weaviate.io`) by default; override with `ENGRAM_BASE_URL`
if you're pointing at a staging or self-hosted deployment.

## Config

Config file: `$HERMES_HOME/weaviate_engram.json`

| Key | Default | Description |
|-----|---------|-------------|
| `user_id_template` | `{identity}` | Template for the Engram `user_id`. `{identity}` is replaced with the Hermes profile name (`coder`, `default`, …). Set to a literal value for shared memory across profiles. |
| `auto_recall` | `true` | Inject relevant memory context before each turn. |
| `auto_capture` | `true` | Store each completed user/assistant turn after the response. |
| `max_recall_results` | `10` | Max recalled items, bounded 1..20. |
| `min_capture_chars` | `10` | Skip trivial turns shorter than this. |
| `api_timeout` | `10.0` | Engram request timeout in seconds, bounded 0.5..60. |
| `pipeline_hint` | `""` | Optional note injected into the system prompt so the model knows which Engram pipeline is active (purely informational; pipelines are configured on the Engram side). |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `ENGRAM_API_KEY` | Engram API key (required, secret). |
| `ENGRAM_BASE_URL` | Optional. Overrides the default `https://api.engram.weaviate.io` endpoint. |
| `WEAVIATE_ENGRAM_USER_ID` | Pin a single `user_id`, useful for testing. Overrides `user_id_template`. |

## Tools

| Tool | Description |
|------|-------------|
| `engram_search` | Search memories by semantic similarity. |
| `engram_store` | Store an explicit memory. **Also the forgetting mechanism — see below.** |
| `engram_fetch` | Profile-shaped recall ("what do you know about me?"). |

## Purposeful forgetting (no `engram_forget`)

Engram is designed around *purposeful forgetting* — deletion and expiry are first-class server-side operations, handled by the same pipelines that extract and reconcile memories. Although the `engram` SDK exposes a `.memories.delete` method, this plugin deliberately does not surface it as an agent tool.

**To correct or "forget" a memory, store a new memory that explicitly states the correction.** Engram's reconcile pipeline supersedes the older memory.

Example:
```
engram_store(content="Correction: the user moved from Berlin to Lisbon in 2026.")
```

## Behavior

When enabled, Hermes will:

- prefetch relevant memory context before each turn (`auto_recall`),
- store each completed conversation turn after the response (`auto_capture`),
- expose explicit `engram_search`, `engram_store`, and `engram_fetch` tools.

Phase 2 additions (not in v1): full-conversation session-end ingest, built-in memory mirror, on-session-switch state reset, queued background prefetch, on-pre-compress extraction, and optional property-scoped search.

## Support

- [Weaviate Engram](https://weaviate.io/engram)
- [`weaviate-engram` on PyPI](https://pypi.org/project/weaviate-engram/)
- [Weaviate Docs](https://docs.weaviate.io)
- [Weaviate Slack](https://weaviate.io/slack)
