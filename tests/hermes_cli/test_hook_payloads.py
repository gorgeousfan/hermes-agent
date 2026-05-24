"""Tests for typed hook payloads (FR #28984 Phase 2).

Verifies that:
1. Each payload dataclass has the expected fields
2. payload_to_kwargs correctly converts to plain dict
3. Hook payload registry covers all active hooks
4. Round-trip: payload → kwargs → callback receives same values
5. Frozen payloads are immutable
"""

from dataclasses import FrozenInstanceError

import pytest

from hermes_cli.hook_payloads import (
    HOOK_PAYLOAD_TYPES,
    PostApiRequestPayload,
    PostApprovalResponsePayload,
    PostLlmCallPayload,
    PostToolCallPayload,
    PreApiRequestPayload,
    PreApprovalRequestPayload,
    PreGatewayDispatchPayload,
    PreLlmCallPayload,
    PreToolCallPayload,
    SessionEndPayload,
    SessionFinalizePayload,
    SessionResetPayload,
    SessionStartPayload,
    SubagentStopPayload,
    TransformToolResultPayload,
    payload_to_kwargs,
)


class TestPreToolCallPayload:
    def test_all_fields_present(self):
        p = PreToolCallPayload(
            tool_name="write_file",
            args={"path": "/tmp/x"},
            task_id="t1",
            session_id="s1",
            tool_call_id="tc1",
        )
        assert p.tool_name == "write_file"
        assert p.args == {"path": "/tmp/x"}
        assert p.task_id == "t1"
        assert p.session_id == "s1"
        assert p.tool_call_id == "tc1"

    def test_defaults(self):
        p = PreToolCallPayload(tool_name="test")
        assert p.args == {}
        assert p.task_id == ""
        assert p.session_id == ""
        assert p.tool_call_id == ""

    def test_frozen(self):
        p = PreToolCallPayload(tool_name="test")
        with pytest.raises(FrozenInstanceError):
            p.tool_name = "other"  # type: ignore[misc]

    def test_to_kwargs_round_trip(self):
        p = PreToolCallPayload(
            tool_name="read_file",
            args={"path": "/tmp/x"},
            task_id="t1",
            session_id="s1",
            tool_call_id="tc1",
        )
        kwargs = payload_to_kwargs(p)
        assert isinstance(kwargs, dict)
        assert kwargs["tool_name"] == "read_file"
        assert kwargs["args"] == {"path": "/tmp/x"}

        # Callback receives same values
        received = {}

        def cb(**kw):
            received.update(kw)

        cb(**kwargs)
        assert received["tool_name"] == "read_file"
        assert received["session_id"] == "s1"


class TestPostToolCallPayload:
    def test_all_fields(self):
        p = PostToolCallPayload(
            tool_name="terminal",
            args={"command": "ls"},
            result="file1\nfile2",
            task_id="t1",
            session_id="s1",
            tool_call_id="tc1",
            duration_ms=150,
        )
        assert p.duration_ms == 150
        assert p.result == "file1\nfile2"

    def test_defaults(self):
        p = PostToolCallPayload(tool_name="test")
        assert p.result is None
        assert p.duration_ms == 0


class TestTransformToolResultPayload:
    def test_same_fields_as_post(self):
        p = TransformToolResultPayload(
            tool_name="test",
            result="original",
            duration_ms=42,
        )
        assert p.result == "original"
        assert p.duration_ms == 42


class TestSessionPayloads:
    def test_session_start(self):
        p = SessionStartPayload(session_id="s1", model="gpt-4", platform="cli")
        kwargs = payload_to_kwargs(p)
        assert kwargs == {"session_id": "s1", "model": "gpt-4", "platform": "cli"}

    def test_session_end(self):
        p = SessionEndPayload(
            session_id="s1", platform="gateway",
            completed=True, interrupted=False, model="gpt-4"
        )
        assert p.completed is True
        assert p.interrupted is False

    def test_session_finalize(self):
        p = SessionFinalizePayload(session_id=None, platform="cli")
        assert p.session_id is None

    def test_session_reset(self):
        p = SessionResetPayload(session_id="new_sid", platform="telegram")
        assert p.session_id == "new_sid"


class TestApprovalPayloads:
    def test_pre_approval(self):
        p = PreApprovalRequestPayload(
            command="rm -rf /",
            description="Delete everything",
            pattern_key="terminal:rm",
            pattern_keys=["terminal:rm", "terminal:rmdir"],
            session_key="telegram:123",
            surface="gateway",
        )
        kwargs = payload_to_kwargs(p)
        assert kwargs["pattern_keys"] == ["terminal:rm", "terminal:rmdir"]
        assert kwargs["surface"] == "gateway"

    def test_post_approval(self):
        p = PostApprovalResponsePayload(
            command="rm -rf /",
            description="Delete everything",
            pattern_key="terminal:rm",
            pattern_keys=["terminal:rm"],
            session_key="telegram:123",
            surface="gateway",
            choice="deny",
        )
        assert p.choice == "deny"


class TestLlmPayloads:
    def test_pre_llm_call(self):
        p = PreLlmCallPayload(
            session_id="s1",
            user_message="hello",
            conversation_history=[{"role": "user", "content": "hi"}],
            is_first_turn=True,
            model="gpt-4",
            platform="cli",
            sender_id="user123",
        )
        assert p.is_first_turn is True
        assert p.sender_id == "user123"

    def test_post_llm_call(self):
        p = PostLlmCallPayload(
            session_id="s1",
            user_message="hello",
            assistant_response="Hello!",
            model="gpt-4",
            platform="cli",
        )
        assert p.assistant_response == "Hello!"


class TestSubagentStopPayload:
    def test_fields(self):
        p = SubagentStopPayload(
            parent_session_id="parent1",
            child_role="leaf",
            child_summary="done",
            child_status="success",
            duration_ms=3000,
        )
        kwargs = payload_to_kwargs(p)
        assert kwargs["parent_session_id"] == "parent1"
        assert kwargs["child_summary"] == "done"


class TestPayloadRegistry:
    def test_registry_covers_active_hooks(self):
        """Every hook with a payload class must be in VALID_HOOKS."""
        # These are the hooks we typed payloads for
        expected = {
            "pre_tool_call",
            "post_tool_call",
            "transform_tool_result",
            "on_session_start",
            "on_session_end",
            "on_session_finalize",
            "on_session_reset",
            "pre_approval_request",
            "post_approval_response",
            "pre_llm_call",
            "post_llm_call",
            "pre_api_request",
            "post_api_request",
            "subagent_stop",
        }
        assert set(HOOK_PAYLOAD_TYPES.keys()) == expected

    def test_pre_gateway_dispatch_excluded(self):
        """pre_gateway_dispatch passes live objects and should NOT have a payload."""
        assert "pre_gateway_dispatch" not in HOOK_PAYLOAD_TYPES


class TestPayloadToKwargs:
    def test_dict_passthrough(self):
        d = {"a": 1, "b": 2}
        assert payload_to_kwargs(d) == d

    def test_invalid_type(self):
        with pytest.raises(TypeError):
            payload_to_kwargs("not_a_payload")

    def test_empty_payload(self):
        p = PreGatewayDispatchPayload()
        assert payload_to_kwargs(p) == {}
