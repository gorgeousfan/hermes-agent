# Path Divergence Analysis — Phase 3 (FR #28984)

## Summary of Findings

### 1. /model Switch Handler

**Shared implementation** — All three paths (CLI, Gateway, TUI) converge on the same core functions:

| Path | Entry Point | Switch Logic | Runtime Swap |
|------|-------------|-------------|--------------|
| CLI `/model` | `cli.py:7099` `_handle_model_command()` | `hermes_cli/model_switch.py:615` `switch_model()` | `agent/agent_runtime_helpers.py:1285` `switch_model()` |
| Gateway `/model` | `gateway/run.py:9873` `_handle_model_command()` | same | same |
| TUI `/model` | `tui_gateway/server.py:1071` `_apply_model_switch()` | same | same |

**Config fields consumed by switch_model()** (hermes_cli/model_switch.py):
- `model_aliases` (config.yaml top-level) — line 205
- `model.aliases` (config.yaml model section) — line 221
- None of the `fallback_*` or `credential_pool_*` fields are read here.

**Config fields consumed by agent_runtime_helpers.switch_model()** (line 1285):
- `custom_providers` (re-read from live config for context_length override) — line 1400-1406
- Clears `_config_context_length` — line 1324

---

### 2. Fallback Activation Path

**Primary implementation**: `agent/chat_completion_helpers.py:673` `try_activate_fallback()`
**Called from**: `run_agent.py:3076` (AIAgent._try_activate_fallback)

**Config fields consumed at startup** (different paths):

| Entry Point | fallback_model/fallback_providers consumed? | File:Line |
|-------------|---------------------------------------------|-----------|
| CLI HermesCLI.__init__ | ✅ Yes, reads from CLI_CONFIG, passes to AIAgent | `cli.py:2854-2859`, `cli.py:4610` |
| Gateway AgentRunner.__init__ | ✅ Yes, reads via `_load_fallback_model()` | `gateway/run.py:1453`, `gateway/run.py:2819-2837` |
| TUI `_make_agent()` | **❌ NO — fallback_model not passed** | `tui_gateway/server.py:1906-1930` |
| TUI `_rebuild_agent_kwargs()` | ⚠️ Copies from existing agent's `_fallback_model` attribute | `tui_gateway/server.py:1851` |

**Divergence (Bug #28753)**: The TUI gateway's `_make_agent()` does not read `fallback_providers`/`fallback_model` from config.yaml. A fresh TUI agent has no fallback chain. The `_rebuild_agent_kwargs` function copies whatever the existing agent has, but if the first agent was created without one, it's never populated.

---

### 3. Gateway Restart Path

**File**: `gateway/restart.py` (20 lines)

Only reads `agent.restart_drain_timeout` from `DEFAULT_CONFIG`. No divergence here — very simple, single-purpose module.

---

### 4. Credential Pool Activation

**Config field `credential_pool_strategies`**:

- **Only consumed by**: `agent/credential_pool.py:370-383` `get_pool_strategy()`
- Reads config **directly from filesystem** (via `_load_config_safe()` at line 372), not passed through AIAgent constructor
- Called once at `CredentialPool.__init__()` time (line 394) — strategy is **frozen for the pool's lifetime**
- Fallback activation calls `resolve_provider_client()` in `agent/auxiliary_client.py:3068`, which may create a new credential pool via credential sources

**Divergence (Bug #28023)**: The credential pool strategy is read once at pool construction and never refreshed. If the user changes `credential_pool_strategies` while an agent is running, existing pools don't pick it up. Fallback activation goes through `resolve_provider_client()` which uses `agent/credential_sources.py` to build pools — this path may or may not create new pools with updated strategies.

---

### 5. Session Lifecycle Handlers

**session:start emission**:
- `gateway/run.py:7902-7918` — Emitted for both new sessions AND auto-reset sessions
- Config checked: `privacy.redact_pii` (line 7928-7932)

**session:end emission**:
- `gateway/run.py:9068-9073` — **Only emitted in the `/new`/`/reset` command handler**
- `gateway/run.py:7890-8000` — Auto-reset/idle-expiry path **does NOT emit session:end**

**Divergence (Bug #28746)**: Auto-reset sessions get `session:start` (line 7913) but the preceding `session:end` is never emitted. The auto-reset path (line 7892-8000) starts at session retrieval and jumps directly to creating a new session context without firing `session:end` for the old one.

**Per-session model overrides cleared on auto-reset**:
- `gateway/run.py:7897` — `_session_model_overrides.pop(session_key, None)`
- `gateway/run.py:7898` — `_set_session_reasoning_override(session_key, None)`
- This means a `/model` switch done before an idle reset is lost

---

### 6. Detailed Config Field Divergence Matrix

| Config Field | CLI Startup | Gateway Startup | TUI Startup | /model Switch | Fallback | Auto-reset |
|---|---|---|---|---|---|---|
| `fallback_providers` / `fallback_model` | ✅ `cli.py:2855` | ✅ `run.py:2832` | ❌ `server.py:1906` | ❌ Not consumed | ✅ `chat_completion_helpers.py:673` | ❌ |
| `credential_pool_strategies` | ✅ `pool.py:376` (runtime) | ✅ `pool.py:376` (runtime) | ✅ `pool.py:376` (runtime) | ❌ Not re-read* | ❌ Not re-read* | ❌ |
| `model.context_length` | ✅ At agent init | ✅ At agent init | ✅ At agent init | ✅ Cleared & re-resolved (`agent_runtime_helpers.py:1324`) | ✅ Cleared & re-resolved (`chat_completion_helpers.py:800`) | ❌ |
| `custom_providers` (for context_length) | ✅ At agent init | ✅ At agent init | ✅ At agent init | ✅ Re-read live config (`agent_runtime_helpers.py:1402`) | ❌ Reads from config in chain | ❌ |
| `session:end` emission | N/A | ✅ On /new, /reset (`run.py:9069`) | N/A | N/A | N/A | ❌ **Not emitted** `run.py:7890-8000` |
| Token counters | ✅ At init | ✅ At init | ✅ At init | ❌ `reset_session_state()` zeros all (`run_agent.py:527-557`) | ❌ No reset | ❌ No reset |
| `privacy.redact_pii` | N/A | ✅ Re-read per-message (`run.py:7928-7932`) | N/A | N/A | N/A | ✅ Same re-read path |

*\* Strategy is read once at pool construction, not re-read during fallback*

---

### 7. Key Code Locations

| Component | File | Lines | Description |
|-----------|------|-------|-------------|
| CLI fallback_model init | `cli.py` | 2854-2859 | Reads `fallback_providers`/`fallback_model` from CLI_CONFIG |
| CLI agent build | `cli.py` | 4578-4624 | Passes `fallback_model=self._fallback_model` to AIAgent (line 4610) |
| Gateway fallback_model init | `gateway/run.py` | 1453 | `self._fallback_model = self._load_fallback_model()` |
| Gateway `_load_fallback_model` | `gateway/run.py` | 2819-2837 | Reads config.yaml directly |
| Gateway agent build (cached) | `gateway/run.py` | 16310-16325 | Passes `fallback_model=self._fallback_model` |
| Gateway agent build (ephemeral) | `gateway/run.py` | 11445-11458 | Passes `fallback_model=self._fallback_model` |
| Gateway `_try_resolve_fallback_provider` | `gateway/run.py` | 898-942 | Resolves fallback credentials for gateway startup auth failure |
| TUI `_make_agent` | `tui_gateway/server.py` | 1880-1930 | **Does NOT pass fallback_model** |
| TUI `_rebuild_agent_kwargs` | `tui_gateway/server.py` | 1825-1852 | Copies `fallback_model` from existing agent |
| TUI `_apply_model_switch` | `tui_gateway/server.py` | 1071-1156 | Model switch handler |
| AIAgent `switch_model` | `run_agent.py` | 599-602 | Forwarder to `agent_runtime_helpers.switch_model` |
| AIAgent init | `run_agent.py` | 326-483 | Full constructor, accepts `fallback_model` at line 408 |
| `agent_init.init_agent` | `agent/agent_init.py` | 133, 696-746 | Builds fallback chain from parameter |
| `agent_runtime_helpers.switch_model` | `agent/agent_runtime_helpers.py` | 1285-1479 | In-place model swap + runtime update |
| `chat_completion_helpers.try_activate_fallback` | `agent/chat_completion_helpers.py` | 673-850+ | Fallback activation: client swap, no counter reset |
| `credential_pool.get_pool_strategy` | `agent/credential_pool.py` | 370-383 | Reads `credential_pool_strategies` from config |
| AIAgent `reset_session_state` | `run_agent.py` | 527-564 | Zeros token counters — called at startup, NOT on fallback |
| Gateway session:end | `gateway/run.py` | 9068-9073 | Emitted on `/new`/`/reset` only |
| Gateway auto-reset path | `gateway/run.py` | 7890-8000 | No session:end emission |
| Gateway session:start (auto-reset) | `gateway/run.py` | 7902-7918 | Emitted on auto-reset but without prior session:end |

---

### 8. Recommended Phase 3 Test Scenarios

1. **TUI fallback_model parity**: Test that a TUI gateway agent receives the `fallback_providers` from config.yaml on fresh creation
2. **session:end auto-reset**: Verify that idle-expiry/auto-reset path emits `session:end` before `session:start`
3. **Token counters across model switch**: Verify per-model token usage isn't lost during `/model` switch
4. **Credential pool strategy refresh after config change**: Verify strategy is re-read when pool is rebuilt
5. **Fallback credential pool strategy**: Verify `credential_pool_strategies` is honored when fallback creates new pools
6. **Gateway /model override persistence**: Verify `/model --global` properly persists to config.yaml
7. **Fallback chain after primary provider switch**: Verify fallback entries targeting old primary are pruned (line 1472-1476 of `agent_runtime_helpers.py`)
