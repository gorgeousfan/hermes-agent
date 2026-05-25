from unittest.mock import patch

from agent.chat_completion_helpers import (
    _api_payload_for_stale_timeout,
    _estimate_payload_tokens,
)
from run_agent import AIAgent


def test_stale_payload_uses_responses_input_and_instructions():
    payload = _api_payload_for_stale_timeout(
        {
            "model": "gpt-5.5",
            "instructions": "system prompt",
            "input": [{"role": "user", "content": "hello"}],
        }
    )

    assert payload == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "hello"},
    ]


def test_stale_payload_prefers_chat_messages_when_present():
    messages = [{"role": "user", "content": "chat path"}]

    assert _api_payload_for_stale_timeout(
        {"messages": messages, "input": [{"role": "user", "content": "responses path"}]}
    ) is messages


def test_codex_responses_payload_gets_large_context_timeout_bump(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with patch.object(AIAgent, "__init__", lambda self, **kw: None):
        agent = AIAgent()

    setattr(agent, "provider", "openai-codex")
    setattr(agent, "model", "gpt-5.5")
    setattr(agent, "base_url", "https://chatgpt.com/backend-api/codex")
    setattr(agent, "_base_url", agent.base_url)

    payload = _api_payload_for_stale_timeout(
        {
            "instructions": "system",
            "input": [{"role": "user", "content": "x" * 240_000}],
        }
    )

    assert _estimate_payload_tokens(payload) > 50_000
    assert agent._compute_non_stream_stale_timeout(payload) == 450.0
