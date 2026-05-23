"""Tests for the memory_disabled guard: dead-write prevention and stale-guidance fix.

When memory_enabled is False but user_profile_enabled is True, the MemoryStore
object is still created (agent_init.py uses OR logic). Without the dispatch guard
the memory tool silently succeeds — writing data that is never injected into the
prompt. These tests verify both the dispatch guard and the MEMORY_GUIDANCE gate.
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from agent.system_prompt import build_system_prompt_parts, MEMORY_GUIDANCE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(
    *,
    memory_enabled: bool = True,
    user_profile_enabled: bool = True,
    memory_store=None,
    valid_tool_names=None,
):
    """Create a minimal mock agent with the attributes the SUT reads."""
    agent = MagicMock()
    agent._memory_enabled = memory_enabled
    agent._user_profile_enabled = user_profile_enabled
    agent._memory_store = memory_store
    agent.valid_tool_names = valid_tool_names if valid_tool_names is not None else set()
    # Defaults for build_system_prompt_parts
    agent.tools = []
    agent._memory_manager = None
    agent._context_engine = None
    agent._user_profile_store = None
    agent.session_id = "test-session"
    agent.gateway_session_id = None
    agent.model_name = "test-model"
    agent.provider_name = "test-provider"
    agent.cron_mode = False
    agent._kanban_show = False
    agent._disabled_toolsets = set()
    agent._enabled_toolsets = None
    agent._skill_list = []
    agent._channel_name = "test"
    agent._platform = "cli"
    agent._interactive = True
    agent._thinking_mode = False
    agent._image_gen_available = False
    agent._video_gen_available = False
    agent._video_available = False
    agent._tts_available = False
    agent._spotify_available = False
    agent._homeassistant_available = False
    agent._kanban_available = False
    agent._feishu_available = False
    agent._yuanbao_available = False
    agent._discord_available = False
    agent._computer_use_available = False
    agent._session_max_turns = None
    agent._current_turn = 0
    agent._max_agent_loops = 50
    agent._delegate_max_spawn_depth = 1
    agent._delegate_max_concurrent_children = 3
    agent._background_processes = {}
    agent._kanban_worker_guidance = ""
    agent._todo_list = None
    agent.load_soul_identity = False
    agent.skip_context_files = True
    return agent


# ===========================================================================
# Dispatch guard tests — agent/agent_runtime_helpers.py (invoke_tool path)
# ===========================================================================


class TestInvokeToolDeadWriteGuard:
    """Verify the guard in invoke_tool blocks dead writes (concurrent path)."""

    def test_memory_target_rejected_when_disabled(self):
        """invoke_tool: memory(target='memory') error when disabled."""
        from agent.agent_runtime_helpers import invoke_tool

        agent = _make_agent(memory_enabled=False, user_profile_enabled=True)
        agent._memory_manager = None

        result = invoke_tool(
            agent=agent,
            function_name="memory",
            function_args={
                "action": "add",
                "target": "memory",
                "content": "Should be blocked",
            },
            effective_task_id="test-task",
        )
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert "disabled" in parsed["error"]
        assert "memory_enabled" in parsed["error"]

    def test_memory_target_allowed_when_enabled(self):
        """invoke_tool: memory(target='memory') works when enabled."""
        from agent.agent_runtime_helpers import invoke_tool

        mock_store = MagicMock()
        mock_store.add.return_value = json.dumps({"success": True})

        agent = _make_agent(
            memory_enabled=True,
            user_profile_enabled=True,
            memory_store=mock_store,
        )
        agent._memory_manager = None

        result = invoke_tool(
            agent=agent,
            function_name="memory",
            function_args={
                "action": "add",
                "target": "memory",
                "content": "Should work",
            },
            effective_task_id="test-task",
        )
        mock_store.add.assert_called_once()

    def test_user_target_allowed_when_memory_disabled(self):
        """invoke_tool: memory(target='user') works even when memory_enabled=False."""
        from agent.agent_runtime_helpers import invoke_tool

        mock_store = MagicMock()
        mock_store.add.return_value = json.dumps({"success": True})

        agent = _make_agent(
            memory_enabled=False,
            user_profile_enabled=True,
            memory_store=mock_store,
        )
        agent._memory_manager = None

        result = invoke_tool(
            agent=agent,
            function_name="memory",
            function_args={
                "action": "add",
                "target": "user",
                "content": "User prefers Korean",
            },
            effective_task_id="test-task",
        )
        mock_store.add.assert_called_once()

    def test_fully_disabled_also_rejected(self):
        """invoke_tool: memory(target='memory') rejected when both disabled."""
        from agent.agent_runtime_helpers import invoke_tool

        agent = _make_agent(
            memory_enabled=False,
            user_profile_enabled=False,
            memory_store=None,
        )
        agent._memory_manager = None

        result = invoke_tool(
            agent=agent,
            function_name="memory",
            function_args={
                "action": "add",
                "target": "memory",
                "content": "This should fail",
            },
            effective_task_id="test-task",
        )
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert "disabled" in parsed["error"]

    def test_default_target_is_memory(self):
        """invoke_tool: missing target defaults to 'memory' and is blocked."""
        from agent.agent_runtime_helpers import invoke_tool

        agent = _make_agent(memory_enabled=False, user_profile_enabled=True)
        agent._memory_manager = None

        result = invoke_tool(
            agent=agent,
            function_name="memory",
            function_args={
                "action": "add",
                # No "target" key — should default to "memory"
                "content": "Should be blocked",
            },
            effective_task_id="test-task",
        )
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert "disabled" in parsed["error"]


# ===========================================================================
# MEMORY_GUIDANCE injection tests — agent/system_prompt.py
# ===========================================================================


class TestMemoryGuidanceGate:
    """MEMORY_GUIDANCE should only be injected when memory_enabled is True."""

    def test_guidance_injected_when_enabled(self):
        agent = _make_agent(memory_enabled=True, valid_tool_names={"memory"})
        parts = build_system_prompt_parts(agent)
        full_prompt = "\n".join(str(v) for v in parts.values())
        assert MEMORY_GUIDANCE in full_prompt

    def test_guidance_not_injected_when_disabled(self):
        agent = _make_agent(memory_enabled=False, valid_tool_names={"memory"})
        parts = build_system_prompt_parts(agent)
        full_prompt = "\n".join(str(v) for v in parts.values())
        assert MEMORY_GUIDANCE not in full_prompt

    def test_guidance_not_injected_when_tool_not_in_valid_names(self):
        """If 'memory' is not in valid_tool_names, guidance is absent."""
        agent = _make_agent(memory_enabled=True, valid_tool_names={"terminal"})
        parts = build_system_prompt_parts(agent)
        full_prompt = "\n".join(str(v) for v in parts.values())
        assert MEMORY_GUIDANCE not in full_prompt
