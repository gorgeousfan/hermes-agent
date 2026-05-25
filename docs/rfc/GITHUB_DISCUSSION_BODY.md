## RFC: Cursor SDK (Composer 2.5) integration

**Summary:** Add optional Cursor Agent SDK support to Hermes in two phases:

1. **`cursor_agent` tool** — Hermes delegates bounded coding tasks to Composer via `cursor-sdk` (local `cwd` by default). Parent keeps memory/skills/messaging.
2. **`integrations/cursor_bridge` (experimental)** — Local OpenAI-compatible HTTP shim so advanced users can point `provider: custom` at Composer. Documented as costly/slow; not default.

**Why not a built-in provider?** Maintainer docs already recommend custom OpenAI-compatible endpoints for simple cases. The SDK is an agent runtime, not chat-completions-native.

**Licensing:** `cursor-sdk` is proprietary — optional extra `hermes-agent[cursor]` only, lazy-install key `tools.cursor`, tool hidden without `CURSOR_API_KEY`.

**Security:** Same surface as Cursor IDE agent for the target repo (files, shell, MCP). Bridge binds `127.0.0.1` by default.

**Implementation:** See `docs/rfc/cursor-sdk-hermes-integration.md` and branch `feat/cursor-agent-tool`.

### Open questions

1. Should `cursor` toolset be included in `hermes-cli` default tools or stay opt-in via `hermes tools`?
2. Interest in a bundled `plugins/model-providers/cursor-bridge` profile after the bridge stabilizes?
3. Any concern shipping proprietary `cursor-sdk` as an optional extra under MIT Hermes?

Feedback welcome before we open the PR.
