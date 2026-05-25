"""Tests for tools.cursor_tool."""

import json
import os
from unittest.mock import patch

import pytest


class TestCursorToolRegistration:
    def test_schema_registered(self):
        import tools.cursor_tool  # noqa: F401 — registers with registry
        from tools.registry import registry

        entry = registry.get_entry("cursor_agent")
        assert entry is not None
        assert entry.toolset == "cursor"
        assert "goal" in entry.schema["parameters"]["properties"]


class TestCheckRequirements:
    def test_false_without_key(self, monkeypatch):
        monkeypatch.delenv("CURSOR_API_KEY", raising=False)
        from tools.cursor_tool import check_cursor_agent_requirements

        assert check_cursor_agent_requirements() is False

    @patch("agent.cursor_sdk_client.cursor_sdk_available", return_value=True)
    def test_true_with_key_and_sdk(self, _sdk, monkeypatch):
        monkeypatch.setenv("CURSOR_API_KEY", "cursor_test")
        from tools.cursor_tool import check_cursor_agent_requirements

        assert check_cursor_agent_requirements() is True


class TestHandler:
    @patch("agent.cursor_sdk_client.run_cursor_agent")
    def test_returns_json(self, mock_run):
        mock_run.return_value = {"status": "finished", "summary": "ok"}
        from tools.cursor_tool import cursor_agent

        raw = cursor_agent(goal="test goal")
        data = json.loads(raw)
        assert data["status"] == "finished"
        mock_run.assert_called_once()
