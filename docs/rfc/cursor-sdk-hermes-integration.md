# RFC: Cursor SDK (Composer 2.5) integration for Hermes Agent

## Summary

Integrate [Cursor Agent SDK](https://cursor.com/docs/sdk/python) (`cursor-sdk`) so Hermes can delegate focused coding work to Composer 2.5, with an optional local OpenAI-compatible bridge for advanced users who want Composer as the inference backend.

## Motivation

- Hermes excels at long-running orchestration (memory, skills, messaging, multi-backend terminals).
- Cursor's agent runtime excels at IDE-grade repo editing with Composer models.
- Users should combine both without nested confusion or undocumented cookie proxies.

## Proposed design (two phases)

### Phase 1 — `cursor_agent` tool (this RFC's primary deliverable)

- New Hermes tool that spawns a **local** Cursor SDK agent (`composer-2.5` by default).
- Input: `goal`, optional `context`, `cwd`, `resume_agent_id`.
- Output: structured JSON summary (status, agent_id, run_id, summary, error).
- Parent Hermes agent keeps its normal LLM and tool surface.

### Phase 2 — `integrations/cursor_bridge` (experimental)

- Sidecar HTTP server: `POST /v1/chat/completions`, `GET /v1/models`, `GET /health`.
- Hermes `provider: custom` points at the bridge.
- Documented as **high cost / high latency** — not recommended for default use.

## Non-goals

- Built-in `cursor` entry in `PROVIDER_REGISTRY` (custom provider + docs is enough).
- Cookie/session scraping proxies.
- Adding `cursor-sdk` to default `[all]` install (proprietary license, large wheels).

## Dependencies and licensing

- Optional extra: `hermes-agent[cursor]` → `cursor-sdk>=0.1.5,<0.2`
- Lazy install key: `tools.cursor` in `tools/lazy_deps.py`
- Users supply `CURSOR_API_KEY` from Cursor Dashboard → Integrations

## Security

- `cursor_agent` grants Cursor the same class of access as the Cursor IDE agent (files, shell, MCP on the target `cwd`).
- Bridge binds to `127.0.0.1` by default; require `CURSOR_API_KEY` when exposed beyond loopback.
- Never log API keys; redact errors returned to the model.

## Open questions for maintainers

1. Should `cursor_agent` join `hermes-acp` / `hermes-api-server` toolsets by default?
2. Is a bundled `plugins/model-providers/cursor-bridge/` profile desirable after the bridge stabilizes?
3. Any concern about shipping proprietary `cursor-sdk` as an optional extra under MIT Hermes?

## Implementation PRs

1. `feat(tools): add cursor_agent tool via Cursor SDK`
2. `feat(integrations): experimental Cursor OpenAI bridge for custom provider`
