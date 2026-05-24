"""Typed hook payloads for the plugin system (FR #28984 Phase 2).

Each hook type has a corresponding dataclass that declares all fields the hook
*should* receive.  When a new field is added to the payload, every call site
that constructs it is forced to supply the value — preventing the "caller
missed the new parameter" class of bugs (e.g. #28961, #28296).

Design constraints
------------------
* **Zero breaking change**: Existing plugin callbacks still receive ``**kwargs``
  — the dataclass is unpacked to a plain dict before dispatch.  Plugins that
  already destructure kwargs continue to work unchanged.
* **Single construction point**: Each payload is built once, close to the
  event source.  All downstream hook invocations reuse the same payload.
* **Opt-in**: Call sites migrate gradually.  Unmigrated call sites continue to
  pass raw kwargs (``invoke_hook("pre_tool_call", tool_name=...)``) which still
  works — the payload is only constructed when the helper function is used.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Tool call payloads
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PreToolCallPayload:
    """Payload for ``pre_tool_call`` hook.

    Plugins may return ``{"action": "block", "message": "..."}`` to prevent
    tool execution.
    """

    tool_name: str
    args: Dict[str, Any] = field(default_factory=dict)
    task_id: str = ""
    session_id: str = ""
    tool_call_id: str = ""


@dataclass(frozen=True)
class PostToolCallPayload:
    """Payload for ``post_tool_call`` hook.

    Observational — fired after the tool has executed.  Return values are
    collected but not acted upon.
    """

    tool_name: str
    args: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    task_id: str = ""
    session_id: str = ""
    tool_call_id: str = ""
    duration_ms: int = 0


@dataclass(frozen=True)
class TransformToolResultPayload:
    """Payload for ``transform_tool_result`` hook.

    Plugins may return a string to replace the tool result.
    """

    tool_name: str
    args: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    task_id: str = ""
    session_id: str = ""
    tool_call_id: str = ""
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Session lifecycle payloads
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionStartPayload:
    """Payload for ``on_session_start`` hook."""

    session_id: str = ""
    model: str = ""
    platform: str = ""


@dataclass(frozen=True)
class SessionEndPayload:
    """Payload for ``on_session_end`` hook."""

    session_id: str = ""
    platform: str = ""
    completed: bool = False
    interrupted: bool = False
    model: str = ""


@dataclass(frozen=True)
class SessionFinalizePayload:
    """Payload for ``on_session_finalize`` hook."""

    session_id: Optional[str] = None
    platform: str = ""


@dataclass(frozen=True)
class SessionResetPayload:
    """Payload for ``on_session_reset`` hook."""

    session_id: str = ""
    platform: str = ""


# ---------------------------------------------------------------------------
# Approval payloads
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PreApprovalRequestPayload:
    """Payload for ``pre_approval_request`` hook."""

    command: str = ""
    description: str = ""
    pattern_key: str = ""
    pattern_keys: List[str] = field(default_factory=list)
    session_key: str = ""
    surface: str = ""  # "cli" | "gateway"


@dataclass(frozen=True)
class PostApprovalResponsePayload:
    """Payload for ``post_approval_response`` hook."""

    command: str = ""
    description: str = ""
    pattern_key: str = ""
    pattern_keys: List[str] = field(default_factory=list)
    session_key: str = ""
    surface: str = ""  # "cli" | "gateway"
    choice: str = ""   # "once" | "session" | "always" | "deny" | "timeout"


# ---------------------------------------------------------------------------
# LLM call payloads (for currently-dead hooks, typed for future activation)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PreLlmCallPayload:
    """Payload for ``pre_llm_call`` hook."""

    session_id: str = ""
    user_message: str = ""
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    is_first_turn: bool = False
    model: str = ""
    platform: str = ""
    sender_id: str = ""


@dataclass(frozen=True)
class PostLlmCallPayload:
    """Payload for ``post_llm_call`` hook.

    Fired once per turn after the tool-calling loop completes.
    """

    session_id: str = ""
    user_message: str = ""
    assistant_response: str = ""
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    model: str = ""
    platform: str = ""


# ---------------------------------------------------------------------------
# API request payloads (for currently-dead hooks, typed for future activation)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PreApiRequestPayload:
    """Payload for ``pre_api_request`` hook."""

    session_id: str = ""
    model: str = ""
    provider: str = ""
    messages_count: int = 0


@dataclass(frozen=True)
class PostApiRequestPayload:
    """Payload for ``post_api_request`` hook."""

    session_id: str = ""
    model: str = ""
    provider: str = ""
    status_code: int = 0
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Sub-agent payload
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SubagentStopPayload:
    """Payload for ``subagent_stop`` hook."""

    parent_session_id: str = ""
    child_role: str = ""
    child_summary: Optional[str] = None
    child_status: Optional[str] = None
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Gateway dispatch payload
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PreGatewayDispatchPayload:
    """Payload for ``pre_gateway_dispatch`` hook.

    Plugins may return a dict to influence flow:
    - ``{"action": "skip"}`` — drop message
    - ``{"action": "rewrite", "text": "..."}`` — replace event text
    - ``{"action": "allow"}`` or ``None`` — normal dispatch

    Note: ``event``, ``gateway``, and ``session_store`` are live objects and
    cannot be frozen — they are excluded from the dataclass and passed through
    separately.
    """

    # This payload intentionally excludes the live-object kwargs.
    # Those are still passed as raw kwargs alongside the payload dict.
    pass


# ---------------------------------------------------------------------------
# Payload registry — maps hook names to their payload dataclass
# ---------------------------------------------------------------------------

HOOK_PAYLOAD_TYPES: Dict[str, type] = {
    "pre_tool_call": PreToolCallPayload,
    "post_tool_call": PostToolCallPayload,
    "transform_tool_result": TransformToolResultPayload,
    "on_session_start": SessionStartPayload,
    "on_session_end": SessionEndPayload,
    "on_session_finalize": SessionFinalizePayload,
    "on_session_reset": SessionResetPayload,
    "pre_approval_request": PreApprovalRequestPayload,
    "post_approval_response": PostApprovalResponsePayload,
    "pre_llm_call": PreLlmCallPayload,
    "post_llm_call": PostLlmCallPayload,
    "pre_api_request": PreApiRequestPayload,
    "post_api_request": PostApiRequestPayload,
    "subagent_stop": SubagentStopPayload,
    # "pre_gateway_dispatch" excluded — passes live objects, not plain data
}


def payload_to_kwargs(payload: Any) -> Dict[str, Any]:
    """Convert a frozen payload dataclass to a plain kwargs dict.

    This is the bridge that preserves backward compatibility: plugin callbacks
    still receive ``cb(**kwargs)`` with plain dicts — they don't need to know
    about the dataclass layer.
    """
    if hasattr(payload, "__dataclass_fields__"):
        return asdict(payload)
    if isinstance(payload, dict):
        return payload
    raise TypeError(f"Expected dataclass or dict, got {type(payload)}")
