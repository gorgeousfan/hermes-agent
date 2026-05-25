"""Tests for agent.cursor_sdk_client (mocked — no live Cursor API)."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


class TestBuildCursorPrompt:
    def test_includes_goal_and_context(self):
        from agent.cursor_sdk_client import build_cursor_prompt

        prompt = build_cursor_prompt("fix tests", context="use pytest")
        assert "fix tests" in prompt
        assert "pytest" in prompt
        assert "structured summary" in prompt.lower() or "summary" in prompt.lower()


class TestRunCursorAgent:
    @patch("agent.cursor_sdk_client.cursor_sdk_available", return_value=False)
    def test_missing_sdk(self, _avail):
        from agent.cursor_sdk_client import run_cursor_agent

        out = run_cursor_agent(goal="hello")
        assert out["status"] == "error"
        assert out["error_type"] == "dependency"

    @patch("agent.cursor_sdk_client.cursor_sdk_available", return_value=True)
    @patch("agent.cursor_sdk_client._resolve_api_key", return_value=None)
    def test_missing_api_key(self, _key, _avail):
        from agent.cursor_sdk_client import run_cursor_agent

        out = run_cursor_agent(goal="hello")
        assert out["status"] == "error"
        assert out["error_type"] == "auth"

    @patch("agent.cursor_sdk_client.cursor_sdk_available", return_value=True)
    @patch("agent.cursor_sdk_client._resolve_api_key", return_value="cursor_test")
    @patch("agent.cursor_sdk_client._load_sdk")
    def test_successful_run(self, mock_load, _key, _avail):
        from agent.cursor_sdk_client import run_cursor_agent

        mock_agent = MagicMock()
        mock_agent.agent_id = "agent-abc"
        mock_run = MagicMock()
        mock_run.id = "run-xyz"
        mock_run.messages.return_value = []
        mock_result = SimpleNamespace(status="finished", result="done summary")
        mock_run.wait.return_value = mock_result

        mock_agent.send.return_value = mock_run
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_agent
        mock_ctx.__exit__.return_value = False

        MockAgent = MagicMock()
        MockAgent.create.return_value = mock_ctx
        CursorAgentError = type("CursorAgentError", (Exception,), {})
        mock_load.return_value = (
            MockAgent,
            MagicMock(),
            CursorAgentError,
            MagicMock(),
            None,
        )

        out = run_cursor_agent(goal="add readme", cwd="/tmp/repo")
        assert out["status"] == "finished"
        assert out["agent_id"] == "agent-abc"
        assert out["run_id"] == "run-xyz"
        assert "done summary" in out["summary"]


class TestProbe:
    @patch("agent.cursor_sdk_client.cursor_sdk_available", return_value=False)
    def test_probe_no_sdk(self, _a):
        from agent.cursor_sdk_client import probe_cursor_api_key

        assert probe_cursor_api_key()["ok"] is False
