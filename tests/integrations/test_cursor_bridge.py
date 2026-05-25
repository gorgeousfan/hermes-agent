"""Tests for integrations.cursor_bridge (no live Cursor API)."""

import json
from unittest.mock import patch

import pytest


class TestMessagesToPrompt:
    def test_flattens_roles(self):
        from integrations.cursor_bridge.adapter import messages_to_prompt

        prompt = messages_to_prompt(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hi"},
            ]
        )
        assert "[system]" in prompt
        assert "[user]" in prompt
        assert "Hi" in prompt

    def test_chat_only_prefix(self):
        from integrations.cursor_bridge.adapter import messages_to_prompt

        prompt = messages_to_prompt(
            [{"role": "user", "content": "ping"}],
            chat_only=True,
        )
        assert "chat model" in prompt.lower() or "plain language" in prompt.lower()


class TestRunChatCompletion:
    @patch("agent.cursor_sdk_client.cursor_sdk_available", return_value=False)
    def test_sdk_missing(self, _a):
        from integrations.cursor_bridge.adapter import run_chat_completion

        status, body = run_chat_completion({"messages": []})
        assert status == 503

    @patch("agent.cursor_sdk_client.cursor_sdk_available", return_value=True)
    @patch("agent.cursor_sdk_client._resolve_api_key", return_value=None)
    def test_auth_required(self, _k, _a):
        from integrations.cursor_bridge.adapter import run_chat_completion

        status, body = run_chat_completion({"messages": [{"role": "user", "content": "x"}]})
        assert status == 401

    @patch("agent.cursor_sdk_client.cursor_sdk_available", return_value=True)
    @patch("agent.cursor_sdk_client._resolve_api_key", return_value="key")
    def test_success(self, _k, _a):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        import sys

        mock_cursor = MagicMock()
        mock_cursor.Agent.prompt.return_value = SimpleNamespace(
            status="finished", result="hello from cursor"
        )
        mock_cursor.AgentOptions = MagicMock()
        mock_cursor.LocalAgentOptions = MagicMock()
        mock_cursor.CursorAgentError = type("CursorAgentError", (Exception,), {})

        with patch.dict(sys.modules, {"cursor_sdk": mock_cursor}):
            from integrations.cursor_bridge.adapter import run_chat_completion

            status, body = run_chat_completion(
                {"messages": [{"role": "user", "content": "say hi"}]}
            )
        assert status == 200
        assert body["choices"][0]["message"]["content"] == "hello from cursor"


class TestParseJsonBody:
    def test_invalid_json(self):
        from integrations.cursor_bridge.adapter import parse_json_body

        data, err = parse_json_body(b"{not json")
        assert data is None
        assert err
