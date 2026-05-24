# Plugin Hook System Analysis — FR #28984 Phase 2

## 1. Hook Definitions

**File:** `hermes_cli/plugins.py`, lines 128–168 — `VALID_HOOKS: Set[str]`

```python
VALID_HOOKS = {
    "pre_tool_call",              # Can block tool execution
    "post_tool_call",             # Observational, fires after tool runs
    "transform_terminal_output",  # DEAD — no production call sites
    "transform_tool_result",      # Can replace tool result string
    "transform_llm_output",       # DEAD — no production call sites
    "pre_llm_call",               # DEAD — no production call sites
    "post_llm_call",              # DEAD — no production call sites
    "pre_api_request",            # DEAD — no production call sites
    "post_api_request",           # DEAD — no production call sites
    "on_session_start",           # DEAD — no production call sites
    "on_session_end",             # Called during CLI shutdown
    "on_session_finalize",        # Session boundary (CLI + gateway)
    "on_session_reset",           # /new or /reset in gateway
    "subagent_stop",              # After child agent finishes
    "pre_gateway_dispatch",       # Incoming message, before auth
    "pre_approval_request",       # Before approval prompt
    "post_approval_response",     # After user responds to approval
}
```

## 2. Dispatch Mechanism

**File:** `hermes_cli/plugins.py`

### Storage (line 775)
```python
self._hooks: Dict[str, List[Callable]] = {}
```

### Registration (lines 701–716)
```python
class PluginContext:
    def register_hook(self, hook_name: str, callback: Callable) -> None:
        if hook_name not in VALID_HOOKS:
            logger.warning("...")
        self._manager._hooks.setdefault(hook_name, []).append(callback)
```

### Invocation (lines 1296–1330, 1404–1409)
```python
class PluginManager:
    def invoke_hook(self, hook_name: str, **kwargs: Any) -> List[Any]:
        callbacks = self._hooks.get(hook_name, [])
        results = []
        for cb in callbacks:
            try:
                ret = cb(**kwargs)      # kwargs are passed as-is
                if ret is not None:
                    results.append(ret)
            except Exception as exc:
                logger.warning(...)
        return results

# Module-level convenience (line 1404):
def invoke_hook(hook_name: str, **kwargs: Any) -> List[Any]:
    return get_plugin_manager().invoke_hook(hook_name, **kwargs)
```

**Key design notes:**
- Callbacks receive `**kwargs` — ad-hoc, no typed payloads
- Each callback wrapped in its own `try/except`
- Non-`None` return values collected into a list
- Shell hooks (`agent/shell_hooks.py`) register additional callbacks on the same `PluginManager._hooks` dict, so they fire alongside Python plugin callbacks

---

## 3. ALL Production Call Sites

### 3.1 `pre_tool_call`

Called **indirectly** via `get_pre_tool_call_block_message()` which itself calls `invoke_hook("pre_tool_call", ...)`.

| File | Line | Context |
|------|------|---------|
| `hermes_cli/plugins.py` | 1428–1469 | **`get_pre_tool_call_block_message()`** — invokes hook, parses `{"action": "block", "message": "..."}` responses |
| `model_tools.py` | 787–794 | `handle_function_call()` — calls `get_pre_tool_call_block_message()` with `tool_name, args, task_id, session_id, tool_call_id` |

**Parameters passed:**
```python
invoke_hook("pre_tool_call",
    tool_name=tool_name,
    args=args if isinstance(args, dict) else {},
    task_id=task_id,
    session_id=session_id,
    tool_call_id=tool_call_id,
)
```

### 3.2 `post_tool_call`

| File | Line | Context |
|------|------|---------|
| `model_tools.py` | 849–862 | `handle_function_call()` — after tool dispatch completes |

**Parameters passed:**
```python
invoke_hook("post_tool_call",
    tool_name=function_name,
    args=function_args,
    result=result,
    task_id=task_id or "",
    session_id=session_id or "",
    tool_call_id=tool_call_id or "",
    duration_ms=duration_ms,
)
```

### 3.3 `transform_tool_result`

| File | Line | Context |
|------|------|---------|
| `model_tools.py` | 870–885 | `handle_function_call()` — after `post_tool_call`, before result is appended to conversation |

**Parameters passed:**
```python
invoke_hook("transform_tool_result",
    tool_name=function_name,
    args=function_args,
    result=result,
    task_id=task_id or "",
    session_id=session_id or "",
    tool_call_id=tool_call_id or "",
    duration_ms=duration_ms,
)
```

### 3.4 `on_session_finalize`

| File | Line | Context |
|------|------|---------|
| `cli.py` | 776–777 | CLI shutdown (`shutdown_cli` function) |
| `cli.py` | 6007–6011 | `HermesCLI._notify_session_boundary()` — `/new` or `/reset` |
| `gateway/run.py` | 3226–3231 | `GatewayRunner._finalize_shutdown_agents()` — shutdown |
| `gateway/run.py` | 4368–4375 | `GatewayRunner` session expiry sweeper |
| `gateway/run.py` | 9048–9051 | `GatewayRunner.handle_new_session()` — `/new` or `/reset` |

**Parameters (CLI shutdown):**
```python
invoke_hook("on_session_finalize",
    session_id=_active_agent_ref.session_id if _active_agent_ref else None,
    platform="cli",
)
```

**Parameters (CLI _notify_session_boundary):**
```python
invoke_hook(event_type,          # "on_session_finalize" or "on_session_reset"
    session_id=self.agent.session_id if self.agent else None,
    platform=getattr(self, "platform", None) or "cli",
)
```

**Parameters (gateway shutdown):**
```python
_invoke_hook("on_session_finalize",
    session_id=getattr(agent, "session_id", None),
    platform="gateway",
)
```

**Parameters (gateway expiry):**
```python
_invoke_hook("on_session_finalize",
    session_id=entry.session_id,
    platform=_platform,
)
```

**Parameters (gateway /new or /reset):**
```python
_invoke_hook("on_session_finalize",
    session_id=_old_sid,
    platform=source.platform.value if source.platform else "",
)
```

### 3.5 `on_session_end`

| File | Line | Context |
|------|------|---------|
| `cli.py` | 14185–14192 | CLI shutdown — fires when agent was mid-turn |

**Parameters:**
```python
_invoke_hook("on_session_end",
    session_id=self.agent.session_id,
    completed=False,
    interrupted=True,
    model=getattr(self.agent, 'model', None),
    platform=getattr(self.agent, 'platform', None) or "cli",
)
```

### 3.6 `on_session_reset`

| File | Line | Context |
|------|------|---------|
| `gateway/run.py` | 9118–9121 | `GatewayRunner.handle_new_session()` — after new session created |

**Parameters:**
```python
_invoke_hook("on_session_reset",
    session_id=_new_sid,
    platform=source.platform.value if source.platform else "",
)
```

### 3.7 `subagent_stop`

| File | Line | Context |
|------|------|---------|
| `tools/delegate_tool.py` | 2268–2275 | After each child subagent completes |

**Parameters:**
```python
_invoke_hook("subagent_stop",
    parent_session_id=_parent_session_id,
    child_role=child_role,
    child_summary=entry.get("summary"),
    child_status=entry.get("status"),
    duration_ms=int((entry.get("duration_seconds") or 0) * 1000),
)
```

### 3.8 `pre_gateway_dispatch`

| File | Line | Context |
|------|------|---------|
| `gateway/run.py` | 6449–6454 | Incoming MessageEvent, before auth/pairing |

**Parameters:**
```python
_invoke_hook("pre_gateway_dispatch",
    event=event,        # MessageEvent object
    gateway=self,       # GatewayRunner
    session_store=self.session_store,
)
```

### 3.9 `pre_approval_request`

| File | Line | Context | Surface |
|------|------|---------|---------|
| `tools/approval.py` | 1208–1216 | Gateway approval path | `"gateway"` |
| `tools/approval.py` | 1346–1354 | CLI approval path | `"cli"` |

**Parameters (gateway):**
```python
_fire_approval_hook("pre_approval_request",
    command=command,
    description=combined_desc,
    pattern_key=primary_key,
    pattern_keys=list(all_keys),
    session_key=session_key,
    surface="gateway",
)
```

**Parameters (CLI):**
```python
_fire_approval_hook("pre_approval_request",
    command=command,
    description=combined_desc,
    pattern_key=primary_key,
    pattern_keys=list(all_keys),
    session_key=session_key,
    surface="cli",
)
```

### 3.10 `post_approval_response`

| File | Line | Context | Surface |
|------|------|---------|---------|
| `tools/approval.py` | 1290–1299 | Gateway approval path (after user responds) | `"gateway"` |
| `tools/approval.py` | 1358–1367 | CLI approval path (after user chooses) | `"cli"` |

**Parameters (gateway):**
```python
_fire_approval_hook("post_approval_response",
    command=command,
    description=combined_desc,
    pattern_key=primary_key,
    pattern_keys=list(all_keys),
    session_key=session_key,
    surface="gateway",
    choice=_outcome,     # "once" | "session" | "always" | "deny" | "timeout"
)
```

**Parameters (CLI):**
```python
_fire_approval_hook("post_approval_response",
    command=command,
    description=combined_desc,
    pattern_key=primary_key,
    pattern_keys=list(all_keys),
    session_key=session_key,
    surface="cli",
    choice=choice,       # "once" | "session" | "always" | "deny"
)
```

---

## 4. DEAD Hooks (defined but NEVER called from production code)

These hooks exist in `VALID_HOOKS` and some plugins register handlers for them, but **no production code calls `invoke_hook()` with these names**:

| Hook Name | Plugin Consumers (examples) |
|-----------|---------------------------|
| `pre_api_request` | `plugins/observability/langfuse/__init__.py` — subscribed |
| `post_api_request` | `plugins/observability/langfuse/__init__.py` — subscribed |
| `pre_llm_call` | `plugins/observability/langfuse/__init__.py` — subscribed |
| `post_llm_call` | `plugins/observability/langfuse/__init__.py` — subscribed |
| `on_session_start` | _none found_ |
| `transform_llm_output` | Tests only (`tests/test_transform_llm_output_hook.py`) |
| `transform_terminal_output` | _none found_ |

**Note:** `post_api_request` is called by the langfuse plugin itself during `on_post_llm_call` to trigger post-processing, but it's **not** called by any production agent/gateway code.

---

## 5. Key Observations for Phase 2 Dataclass Design

### Parameter patterns across hooks:

| Group | Common Fields |
|-------|---------------|
| **Tool hooks** (`pre_tool_call`, `post_tool_call`, `transform_tool_result`) | `tool_name`, `args`, `task_id`, `session_id`, `tool_call_id` — always passed together |
| **Session hooks** (`on_session_finalize`, `on_session_end`, `on_session_reset`) | `session_id`, `platform` — sometimes with `completed`, `interrupted`, `model` |
| **Approval hooks** (`pre_approval_request`, `post_approval_response`) | `command`, `description`, `pattern_key`, `pattern_keys`, `session_key`, `surface` — `post` adds `choice` |
| **Gateway hook** (`pre_gateway_dispatch`) | `event`, `gateway`, `session_store` — all objects |

### Consistency issues to fix:
1. **`session_id` vs `parent_session_id`**: `subagent_stop` uses `parent_session_id` instead of `session_id`
2. **`tool_call_id` is "":** Several call sites pass `tool_call_id or ""`, losing `None` semantics
3. **`duration_ms` type**: int from `model_tools.py`, but could be float — no validation
4. **`platform` varies**: `"cli"` string, `"gateway"` string, or `source.platform.value` (enum member)
5. **Several hooks defined but never called** — candidates for deprecation or removal
