# Weaviate Engram — Hermes Memory Provider Plugin: Implementation Plan

Status: **draft / for review**
Author: planning pass, not yet implemented
Target location once approved: `plugins/memory/engram/`

---

## 1. Goal

Ship a Hermes memory provider plugin named **`weaviate_engram`** (folder name and `memory.provider` key) that uses [Weaviate Engram](https://weaviate.io/blog/engram-deep-dive) (Weaviate's managed memory service, currently in preview) as the long-term memory backend. Tool names stay short — `engram_search`, `engram_store`, `engram_fetch` — to keep the model's token budget tight.

When the user runs `hermes memory setup` they should be able to pick `weaviate_engram` from the provider list, paste an Engram API key, and from that point on Hermes should:

- recall relevant memories before each turn (prefetch),
- persist each completed turn to Engram in the background,
- expose explicit `engram_*` tools the model can call (`store`, `search`, `fetch`),
- mirror built-in `MEMORY.md` / `USER.md` writes to Engram so memory works the same way regardless of which write path the agent uses,
- ingest the full conversation on session end so Engram's server-side pipelines can do their extract/reconcile pass over the whole conversation.

There is **no `forget` tool** by design — Engram treats forgetting as a first-class server-side concern ("purposeful forgetting"), not a client-issued delete. See §2 for what that means in practice and §6 for how we expose it (or don't).

> **Naming note:** the folder name must be `weaviate_engram` (underscore), not `weaviate-engram`. Hermes's plugin loader builds the import path as `plugins.memory.<name>` (`plugins/memory/__init__.py:196`) and uses it for `sys.modules` keying — a dash would block any relative imports inside the plugin (e.g. `from .client import EngramClient`). The display name in `plugin.yaml` is still "Weaviate Engram".

This is the *first* non‑Nous‑built provider that pushes data into Weaviate, so the plugin should also serve as a reference for anyone wiring Weaviate-backed agents into Hermes.

---

## 2. Background — what we're integrating

### Hermes memory provider model (verified from source)

- ABC: `agent/memory_provider.py:42` — `class MemoryProvider(ABC)`. Required methods: `name`, `is_available`, `initialize`, `get_tool_schemas`. Everything else is overridable with a sensible default (`agent/memory_provider.py:83-279`).
- Manager: `agent/memory_manager.py` enforces **one external provider** (built-in always runs alongside it). Tests at `tests/agent/test_memory_provider.py:153-163` lock this in.
- Discovery: `plugins/memory/__init__.py:67-98` scans `plugins/memory/<name>/` for an `__init__.py` that defines either `register(ctx)` (preferred) or a `MemoryProvider` subclass.
- Activation: user picks via `memory.provider: engram` in `config.yaml`. The CLI helper `discover_plugin_cli_commands()` (`plugins/memory/__init__.py:323`) only loads CLI commands for the active provider.
- Profile isolation rule: every storage path must use the `hermes_home` kwarg passed into `initialize()` — never hardcode `~/.hermes`. (Docstring: `agent/memory_provider.py:67-70`.)

The cleanest reference implementation is **Supermemory** (`plugins/memory/supermemory/__init__.py`). It's a SaaS provider with API-key auth, a Python SDK, semantic search + explicit tools + session ingest — structurally identical to what we want. We'll model the Engram plugin on it.

### Weaviate Engram (verified from the deep-dive blog and search results)

- **Hosting:** Weaviate Cloud only, currently in **preview**. No self-host story yet.
- **Auth:** project-scoped API key.
- **Python SDK:** `pip install weaviate-engram` — a standalone package (NOT the `weaviate-client[agents]` extra). Imports as `from engram import EngramClient`.
- **API surface** (verified against `weaviate-engram==0.6.0`):
  ```python
  from engram import EngramClient

  client = EngramClient(api_key="YOUR_API_KEY")
  # Optional: EngramClient(api_key=..., base_url="...", timeout=30.0, headers=...)

  run = client.memories.add(
      "User prefers concise responses and dark mode",
      user_id="alice@example.com",
  )
  print(run.run_id)  # e.g. "run_abc123"

  results = client.memories.search(
      query="What does the user prefer?",
      user_id="alice@example.com",
  )
  for memory in results:
      print(memory.content)
  ```
  - `EngramClient(*, api_key, base_url="https://api.engram.weaviate.io", timeout=30.0)` — only the API key is required; defaults talk to Engram's public endpoint.
  - `.memories.add(input_data, *, user_id, group=None, properties=None) -> Run` — first arg can be a plain string (per the example above). Pipelines run async server-side, so the call returns immediately with a `Run(run_id, status, error)` handle.
  - `.memories.search(*, query, user_id=None, group=None, retrieval_config=None, properties=None, topics=None) -> SearchResults` — list-like iterable of `Memory(id, content, topic, group, created_at, updated_at, user_id, tags, score, properties, project_id)`.
  - `.memories.delete(memory_id, *, user_id=None, group=None) -> None` and `.memories.get(memory_id, ...) -> Memory` **do exist** in the SDK, but the plugin deliberately does not expose them as agent tools — see "Purposeful forgetting" below.
- **Purposeful forgetting:** Engram treats deletion and expiry as first-class operations handled by the server-side pipelines. Although the SDK *does* expose `.memories.delete` and `.memories.get`, the plugin deliberately does not surface them as agent tools — Engram's design intent is that "forgetting" is achieved by writing a *correcting* memory rather than issuing a delete:
  - We do **not** ship an `engram_forget` tool.
  - "Forgetting" is done via `engram_store` with content like "Correction: the user no longer works at X; they joined Y" — Engram's reconcile step supersedes the old memory.
  - If/when Engram exposes a programmatic surface for expiry policies or "mark stale" hints, we can add an `engram_expire` tool in a later phase. The negative test in §8 locks this design choice in.
- **Scoping:** three models exposed — project-wide, user-scoped (Weaviate multi-tenancy under the hood, strict), property-scoped (soft, e.g. `conversation_id`). We use **user-scoped** as default to mirror Hermes's per-profile isolation. Property-scoping is an opt-in Phase 2 feature, see §11.4 for what it would buy us.
- **Pipelines:** configured **server-side** on the Engram project (starter templates: "personalization", "continual learning"; custom Extract→Transform→Commit also supported). The plugin doesn't manage pipelines — it just ships raw turns; pipeline config lives in the Engram console. This is a real advantage vs Supermemory: less of the "what's worth remembering" logic in the client.

---

## 3. Architecture overview

```
┌──────────────────────────────────────────────────────────────┐
│ Hermes Agent                                                 │
│                                                              │
│   MemoryManager                                              │
│   ├── builtin   (MEMORY.md / USER.md, FTS5)                  │
│   └── engram    (this plugin) ────────┐                      │
│                                       ▼                      │
│   prefetch()  ──── _client.memories.search(query, user_id)   │
│   sync_turn() ──── _client.memories.add(messages, user_id)   │
│   on_session_end() ─ _client.memories.add(full convo, ...)   │
│   handle_tool_call(engram_store|search|forget|fetch)         │
│   on_memory_write() ─ mirror MEMORY.md add into Engram       │
└──────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
                          ┌─────────────────────────┐
                          │ Weaviate Cloud / Engram │
                          │  server-side pipelines: │
                          │  extract → reconcile →  │
                          │  commit → Weaviate      │
                          └─────────────────────────┘
```

Map to Supermemory equivalents (so the diff is small and reviewable):

| Concern | Supermemory uses | Engram will use |
|---|---|---|
| SaaS auth | `SUPERMEMORY_API_KEY` env | `ENGRAM_API_KEY` env (optional `ENGRAM_BASE_URL`) |
| Python install | `pip install supermemory` | `pip install weaviate-engram` |
| Import | `from supermemory import Supermemory` | `from engram import EngramClient` |
| Tenant/scope | `container_tag` | `user_id` (Engram multi-tenancy) |
| Ingest call | `client.documents.add(content, container_tags=[…])` | `client.memories.add(text, user_id=…)` |
| Search call | `client.search.memories(q=…, container_tag=…)` | `client.memories.search(query=…, user_id=…)` |
| Session ingest | POST `/v4/conversations` | `client.memories.add(formatted_transcript, user_id=…)` |
| Forget | `client.memories.forget(id=…, container_tag=…)` | **Not exposed as a tool** (SDK has `.delete` but we don't surface it — purposeful forgetting design) |
| Profile recall | `client.profile(q=…)` | `.memories.search(query="user facts", user_id=…)` |
| Background work | daemon threads | same pattern |

---

## 4. File layout

```
plugins/memory/weaviate_engram/
├── __init__.py        # WeaviateEngramMemoryProvider + register(ctx)
├── client.py          # Thin wrapper around weaviate-client[agents]
├── plugin.yaml        # name, version, description, pip_dependencies
├── README.md          # User-facing setup and config docs
└── cli.py             # Optional: `hermes weaviate_engram doctor` / `status` (Phase 3)
```

And tests:
```
tests/plugins/memory/test_weaviate_engram_provider.py
```

`plugin.yaml`:
```yaml
name: weaviate_engram
version: 0.1.0
description: "Weaviate Engram managed memory — semantic recall, server-side extract/reconcile pipelines, purposeful forgetting."
pip_dependencies:
  - "weaviate-engram>=0.6"
```

Honcho is the precedent for splitting `client.py` out (`plugins/memory/honcho/{client.py,session.py}`). Worth doing here too because the Engram preview API may shift and we want the wire layer isolated.

---

## 5. Configuration schema

`get_config_schema()` returns (in order shown by the setup wizard):

| Key | Secret | Required | Default | Notes |
|---|---|---|---|---|
| `api_key` | yes | yes | – | env var `ENGRAM_API_KEY`. Link: weaviate.io/engram. |
| `user_id_template` | no | no | `{identity}` | Same template trick Supermemory uses. Default scopes by Hermes profile; users can set to a static value for shared memory. |
| `auto_recall` | no | no | `true` | Inject memory context before turns. |
| `auto_capture` | no | no | `true` | Persist each completed turn. |
| `capture_session_end` | no | no | `true` | Ship full conversation on session end (Phase 2). |
| `mirror_builtin_writes` | no | no | `true` | Reflect `MEMORY.md`/`USER.md` writes into Engram (Phase 2). |
| `max_recall_results` | no | no | `10` | Bounded 1..20. |
| `min_capture_chars` | no | no | `10` | Skip trivial turns. |
| `api_timeout` | no | no | `10.0` | Seconds; bounded 0.5..60. The SDK's own default is 30s. |
| `pipeline_hint` | no | no | `""` | Free-form note injected into `system_prompt_block()`. |

Non-secret config persists to `$HERMES_HOME/weaviate_engram.json` via `save_config()` — same pattern as Supermemory.

Env-var overrides (always win over the JSON):

- `ENGRAM_API_KEY` (secret, written to `.env`)
- `ENGRAM_BASE_URL` — optional, defaults to `https://api.engram.weaviate.io` (the SDK's default); set for staging or self-hosted endpoints.
- `WEAVIATE_ENGRAM_USER_ID` — pin a single user_id, useful for testing.

---

## 6. Method-by-method plan

Source contract: `agent/memory_provider.py`. The class will subclass `MemoryProvider` directly.

### `name` → `"weaviate_engram"`

### `is_available() -> bool`
- Cheap, no network. Return `True` iff `WEAVIATE_API_KEY` AND `WEAVIATE_URL` are set AND `import weaviate` succeeds AND the agents extra is importable (probe for the memories surface — e.g. `from weaviate.agents.memories import MemoriesClient`, exact path TBD per §11.1).
- Modelled after `supermemory/__init__.py:454-462`.

### `initialize(session_id, **kwargs)`
- Read `hermes_home` (required), `agent_identity`, `agent_context`, `user_id` kwargs.
- Load `$HERMES_HOME/weaviate_engram.json`; merge env-var overrides.
- Resolve `user_id` from template — replace `{identity}` with `agent_identity` (default `"default"`), sanitize to safe charset. Allow `kwargs["user_id"]` (gateway sessions) to override the template entirely.
- Decide `_write_enabled`: `False` when `agent_context` in `{"cron", "flush", "subagent"}` (same gate Supermemory uses at `:511`). Subagents should not pollute the user's memory with delegated-task chatter.
- Construct `_EngramClient(api_key, cluster_url, timeout, user_id)`. Swallow exceptions, set `_active = False` if anything fails (matches existing convention).
- Initialize thread handles: `_sync_thread`, `_prefetch_thread`, `_write_thread` (all `Optional[Thread]`), plus `_prefetch_lock`, `_prefetch_result`, `_turn_count = 0`.

### `system_prompt_block() -> str`
- Return a short block when `_active`:
  ```
  # Engram (Weaviate)
  Long-term memory active. User scope: <user_id>.
  Use engram_search/engram_store/engram_forget/engram_fetch for explicit memory operations.
  ```
- Include `pipeline_hint` if set.
- Empty string when inactive (so an unconfigured plugin doesn't pollute the prompt).

### `prefetch(query, *, session_id="") -> str`
- Guards: `_active`, `_auto_recall`, `_client`, non-empty query.
- Call `_client.search_memories(query, limit=max_recall_results, user_id=…)`.
- Format using a small helper that mirrors `supermemory._format_prefetch_context`. Wrap the entire block in `<engram-context>…</engram-context>` fence — `MemoryManager`'s `StreamingContextScrubber` recognises any fenced tag (`agent/memory_manager.py:62-225`) so this prevents the model from echoing recalled memory as if it were user input.
- **Latency budget:** must return fast. For the first turn, allow a bounded synchronous wait (the call is supposed to be sub-second on Engram). For subsequent turns, return the cached `_prefetch_result` populated by `queue_prefetch()`.

### `queue_prefetch(query, *, session_id="") -> None`
- Spawn a daemon thread that calls `.memories.search`, writes the formatted result to `_prefetch_result` under `_prefetch_lock`. This is the latency-hiding pattern Honcho uses (`plugins/memory/honcho/__init__.py:643-647`).

### `sync_turn(user_content, assistant_content, *, session_id="") -> None`
- Guards: active, write enabled, capture enabled, non-trivial content (reuse Supermemory's `_clean_text_for_capture`, `_is_trivial_message`, `_MIN_CAPTURE_LENGTH` patterns).
- **Strip Hermes's own `<engram-context>` fences** out of `assistant_content` before sending — otherwise we'd re-ingest our own injected memories on every turn. (Supermemory does this at `:567-568` with regex.)
- Spawn daemon thread that calls:
  ```python
  client.memories.add(
      [{"role": "user", "content": clean_user},
       {"role": "assistant", "content": clean_assistant}],
      user_id=self._user_id,
  )
  ```
- Join previous sync thread with short timeout before launching new one (prevents thread pile-up).
- Swallow + log exceptions; never raise out of `sync_turn` — the manager protects us anyway but defense-in-depth.

### `on_session_end(messages) -> None`
- When `capture_session_end` is on, batch the full conversation and send it as one `client.memories.add(messages, user_id=…)` call. Engram's pipelines can then do cross-turn extraction in one pass.
- This call *may* block (we're at session boundary; latency is acceptable per the ABC contract — `agent/memory_provider.py:153`).
- Join outstanding `_sync_thread` (timeout ~10s) before finishing so nothing is dropped.

### `on_memory_write(action, target, content, metadata=None) -> None`
- When `mirror_builtin_writes` and `action == "add"`, fire-and-forget into Engram via a daemon thread:
  ```python
  client.memories.add(
      [{"role": "system", "content": f"[{target}.md] {content}"}],
      user_id=self._user_id,
  )
  ```
  Including the metadata blob (write_origin, session_id, …) as part of the message text so Engram's extractor can see provenance.
- For `action == "replace"`: send the replacement as a *correcting* memory ("Correction to a previously stored memory: <new content>"). Engram's reconcile pipeline supersedes the old one — this is the same mechanism the agent uses via `engram_store`.
- For `action == "remove"`: send a correcting memory phrased as a retraction ("The user no longer wishes the following to be remembered: <content>"). We don't pretend to hard-delete; Engram handles this server-side via purposeful forgetting.

### `on_session_switch(new_session_id, *, parent_session_id="", reset=False, **kwargs)`
- Update `self._session_id`. If `reset=True`, clear `_prefetch_result` and turn counter — same hygiene as Honcho.
- `user_id` does **not** change here (it's scoped to the Hermes profile, not the session).

### `on_pre_compress(messages) -> str`
- Cheap pass: call `.memories.search` with a one-liner summarising the topic of the messages about to be compressed, and return the top 3 results as text. This becomes part of the compression summary prompt, so insights survive context squeeze.
- Optional for v1 — can ship as no-op default and add in Phase 2.

### `get_tool_schemas()` and `handle_tool_call()`

Three tools — deliberately **no `engram_forget`** (see §2 "Purposeful forgetting"):

- `engram_search { query: str, limit?: int (1..20) }` → top-N memories with similarity scores.
- `engram_store { content: str, metadata?: object }` → explicit save; calls `.memories.add` with a single `{"role": "user", "content": …}` message. **This is also the forgetting tool** — the agent corrects/supersedes memories by writing a new, correcting one. The tool description must spell this out so the model knows: *"To 'forget' or correct a memory, store a new memory that explicitly states the correction. Engram's reconcile pipeline supersedes older memories with newer correcting ones."*
- `engram_fetch { query?: str }` → "what do you know about me?" / profile-shaped recall. In preview: implement as `.memories.search(query or "user facts", limit=10)`. If `.memories.fetch` exists with a dedicated profile endpoint, prefer it.

Each tool returns a JSON string. Errors via `tools.registry.tool_error(...)` (same as Supermemory).

### `shutdown()`
- Join each background thread with a 5s timeout. Identical to `supermemory/__init__.py:639-644`.

### `save_config(values, hermes_home)`
- Write non-secret subset to `$HERMES_HOME/engram.json` (merge with existing).

### `register(ctx)`
- `ctx.register_memory_provider(EngramMemoryProvider())` — exactly as in every existing plugin.

---

## 7. Threading & latency

Constraints from the ABC docstrings:
- `is_available()` — **no** network calls.
- `prefetch()` — must be fast (cache hot path); the actual API call goes on a background thread.
- `sync_turn()` — must be non-blocking (daemon thread).
- `on_session_end()` — may block.
- `shutdown()` — should drain.

Engram is designed for sub-second `add` (it's fire-and-forget — the run completes async server-side) and sub-second `search`, so per-turn budget should be easy to hit. We'll set `api_timeout` to 5s default with a sub-second target for `search`. All threads are daemons named `engram-sync`, `engram-prefetch`, `engram-write` for debuggability.

Resource bounds we'll explicitly cap so a runaway agent can't hammer the API:
- One in-flight `_sync_thread` at a time (join before launching new).
- One in-flight `_prefetch_thread` at a time.
- `max_recall_results` clamped 1..20.
- `api_timeout` clamped 0.5..15.
- Message size: clamp single-message content to 32 KB before sending (Engram preview presumably has its own limits; verify and align).

---

## 8. Tests

`tests/plugins/memory/test_weaviate_engram_provider.py` — mirror the structure of `tests/plugins/memory/test_supermemory_provider.py` (16 KB, comprehensive). The contract test base lives at `tests/agent/test_memory_provider.py:15-78` (`FakeMemoryProvider`); we'll reuse the `MemoryManager` integration patterns there.

What to cover (no real network — mock `_EngramClient`):
1. `is_available()` false without env, true when both `WEAVIATE_API_KEY` and `WEAVIATE_URL` are set AND `weaviate.agents.memories` (or whatever the import path turns out to be) is importable.
2. `initialize()` populates `_user_id` correctly under the template (`{identity}` → `agent_identity`).
3. `initialize()` with `agent_context="subagent"` sets `_write_enabled=False` and no writes happen on sync.
4. `prefetch` empty when `_auto_recall=False`, otherwise calls the client and returns fenced text containing the formatted results.
5. `sync_turn` calls `.memories.add` once, off the main thread, with the cleaned messages and resolved `user_id`. Verify `<engram-context>` fences are stripped from `assistant_content` before send.
6. `sync_turn` no-ops on trivial messages (`"ok"`, `"thanks"`).
7. `on_session_end` sends one batch call with the full message list and joins the sync thread.
8. `on_memory_write("add", "memory", …)` mirrors into Engram as-is; `"replace"` mirrors as a "Correction:" message; `"remove"` mirrors as a retraction message. None of them attempt to call a delete API.
9. Tool: `engram_search` returns formatted results; `engram_store` returns `{saved: True, id: …}`; `engram_fetch` returns a profile-ish blob.
10. **Negative test:** `get_tool_schemas()` does NOT include any tool named `engram_forget`, `engram_delete`, or similar (lock in the design choice; prevents accidental reintroduction).
11. Tool errors: each tool returns `tool_error(...)` (parseable JSON) on client exception — agent shouldn't crash.
12. `shutdown()` joins all threads.
13. `save_config()` writes `weaviate_engram.json` with only non-secret keys; assert `api_key` never appears in the saved JSON.
14. Smoke test through `MemoryManager`: register provider, `initialize_all`, run one synthetic turn, assert tool schemas wired, assert `sync_all` doesn't raise.

If we have an Engram preview key in CI secrets later, add a marked-`integration` test that hits the live API end-to-end (gated by env var). Until then everything is mocked.

---

## 9. Milestones

**Phase 1 — Walking skeleton (mergeable)**
- File layout, `plugin.yaml`, README.
- `WeaviateEngramMemoryProvider` implementing the four required methods + `sync_turn` + `prefetch` + `system_prompt_block`.
- `client.py` wrapping `weaviate-client[agents]` — narrow surface: `add_memory`, `search_memories`, constructor.
- `engram_search` + `engram_store` + `engram_fetch` tools.
- Unit tests for the above with a mocked client (including the negative test that no forget tool is exposed).
- `hermes memory setup` flow verified manually.
- `README.md` (with a clear "no forget tool, here's why" section) + a one-paragraph addition to the main repo README's provider table.

**Phase 2 — Full surface**
- `on_session_end` full-conversation ingest.
- `on_memory_write` mirror (including correction/retraction phrasing for replace/remove).
- `on_session_switch` state reset.
- `queue_prefetch` + cached `prefetch` for sub-turn latency.
- `on_pre_compress` extraction.
- Optional property-scoped tagging (`scope_properties: true` opt-in — see §11.4).
- Expanded test suite.

**Phase 3 — Polish**
- `cli.py` with `hermes weaviate_engram status` / `hermes weaviate_engram doctor` (probe connection, dump current user_id, list recent runs).
- Optional integration test job (env-gated).
- Multi-`user_id` mode analogous to Supermemory's `enable_custom_container_tags` if there's demand.
- Honcho-style background sync queue with retry if Engram adds rate-limit or transient-failure semantics that warrant it.
- If Engram later exposes a programmatic expiry/decay-policy surface, add an `engram_expire` tool then.

---

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Exact import path from `weaviate-client[agents]` isn't documented publicly.** | Wrap behind `client.py`. Probe a couple of likely import paths in a single try/except in `is_available()`. Lock in once we have preview access. |
| **Preview API changes between now and GA.** | Keep `client.py` small (50–100 LOC). Mock it in tests. Pin `weaviate-client` version range in `plugin.yaml`. |
| **No client-side forget contradicts user/agent expectations.** | Clear README section on purposeful forgetting. The `engram_store` tool description tells the model how to "forget" (write a correcting memory). Negative test prevents regression. |
| **User runs the plugin without `WEAVIATE_URL`.** | `is_available()` checks both env vars; setup wizard requires `cluster_url`; doctor command in Phase 3 validates the round-trip. |
| **`sync_turn` storms on long sessions.** | Single in-flight thread; trivial-message skip; min-length filter. |
| **Re-ingesting our own recalled memories as "new" turns.** | Strip `<engram-context>` fences before send (same trick Supermemory uses for its own fences). |
| **Subagents polluting the user's memory.** | Honor `agent_context in {"subagent","cron","flush"}` → `_write_enabled = False`. |
| **Secrets accidentally in `engram.json`.** | `save_config` only receives non-secret values from the wizard (the manager splits them by the schema's `secret: True` flag). Add an assert in tests that `api_key` never appears in the saved JSON. |

---

## 11. Open questions to resolve before coding

**Resolved (incorporated above):**
- ~~SDK package~~ → `pip install weaviate-engram`, imported as `from engram import EngramClient`. Verified against `weaviate-engram==0.6.0`.
- ~~Constructor signature~~ → `EngramClient(*, api_key, base_url="https://api.engram.weaviate.io", timeout=30.0, headers=None)`. Only `api_key` required; no cluster URL.
- ~~`add` / `search` shape~~ → `add(text, *, user_id) -> Run(run_id, status, error)`; `search(*, query, user_id) -> SearchResults` (iterable of `Memory(id, content, topic, score, ...)`).
- ~~Delete API~~ → SDK exposes `.memories.delete` and `.memories.get`, but we don't surface them as agent tools (purposeful forgetting design). Locked in by the negative test in §8.
- ~~Plugin naming~~ → `weaviate_engram`.
- ~~Property-scoped on top of user-scoped~~ **Decided: Phase 2 opt-in behind `scope_properties: true` in `weaviate_engram.json`.** SDK confirmed: `.memories.add` accepts a `properties: dict[str, str]` kwarg and `.memories.search` accepts the same — Phase 2 wiring is straightforward.

**Still open:**

1. **Pipeline templates** — should the plugin *recommend* a starter template ("personalization") in the README and the setup wizard, or stay agnostic? Recommendation: README mentions the named templates from the blog as suggestions, but the wizard stays agnostic — pipelines are configured in the Engram console, not from Hermes.
2. **`user_id` charset / length limits** — verify before we lock in the sanitizer. The SDK types it as `str | None` without published constraints; current sanitizer restricts to `[A-Za-z0-9_-]` to be safe.
3. **Engram `group` / `topic` parameters** — both `.memories.add` and `.memories.search` accept `group` and `topics` parameters that we currently ignore. Worth considering in Phase 2: map `agent_workspace` → `group` so different Hermes workspaces have soft-isolated memory scopes within the same `user_id`.

---

## 12. References

**Hermes (verified file:line)**
- ABC: `agent/memory_provider.py:42`
- Manager: `agent/memory_manager.py`
- Loader: `plugins/memory/__init__.py:67-98, 160-285`
- Closest template: `plugins/memory/supermemory/__init__.py` (entire file)
- Contract tests: `tests/agent/test_memory_provider.py:15-78`
- Supermemory provider-specific tests: `tests/plugins/memory/test_supermemory_provider.py`
- Plugin guide: https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin

**Engram**
- Deep-dive blog: https://weaviate.io/blog/engram-deep-dive
- Internal-use-case post: https://weaviate.io/blog/engram-internal-use-case
- Weaviate Python client (likely SDK host): https://pypi.org/project/weaviate-client/
- Weaviate docs entry: https://docs.weaviate.io/weaviate
