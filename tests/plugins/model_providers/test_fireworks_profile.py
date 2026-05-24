"""Unit tests for the Fireworks provider profile.

Validates registration, discovery, and wire-shape contracts so Fireworks
requests are correctly shaped without going live.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fireworks_profile():
    """Resolve the registered Fireworks profile."""
    import model_tools  # noqa: F401 — triggers plugin discovery
    import providers

    profile = providers.get_provider_profile("fireworks")
    assert profile is not None, "fireworks provider profile must be registered"
    return profile


class TestFireworksProfileBasics:
    """Core profile metadata."""

    def test_name_and_aliases(self, fireworks_profile):
        assert fireworks_profile.name == "fireworks"
        assert "fireworks-ai" in fireworks_profile.aliases
        assert "fw" in fireworks_profile.aliases

    def test_display_name(self, fireworks_profile):
        assert fireworks_profile.display_name == "Fireworks AI"

    def test_base_url(self, fireworks_profile):
        assert fireworks_profile.base_url == "https://api.fireworks.ai/inference/v1"

    def test_env_vars(self, fireworks_profile):
        assert "FIREWORKS_API_KEY" in fireworks_profile.env_vars
        assert "FIREWORKS_BASE_URL" in fireworks_profile.env_vars

    def test_auth_type(self, fireworks_profile):
        assert fireworks_profile.auth_type == "api_key"

    def test_default_aux_model(self, fireworks_profile):
        assert fireworks_profile.default_aux_model == "accounts/fireworks/models/minimax-m2p5"

    def test_fallback_models(self, fireworks_profile):
        expected = (
            "accounts/fireworks/routers/kimi-k2p6-turbo",
            "accounts/fireworks/models/glm-5p1",
            "accounts/fireworks/models/minimax-m2p5",
        )
        assert fireworks_profile.fallback_models == expected

    def test_get_hostname(self, fireworks_profile):
        assert fireworks_profile.get_hostname() == "api.fireworks.ai"


class TestFireworksWireShape:
    """Fireworks uses plain OpenAI chat-completions — no extra_body quirks needed."""

    def test_prepare_messages_is_identity(self, fireworks_profile):
        msgs = [{"role": "user", "content": "hello"}]
        assert fireworks_profile.prepare_messages(msgs) == msgs

    def test_build_extra_body_empty(self, fireworks_profile):
        assert fireworks_profile.build_extra_body() == {}

    def test_build_api_kwargs_extras_empty(self, fireworks_profile):
        extra_body, top_level = fireworks_profile.build_api_kwargs_extras()
        assert extra_body == {}
        assert top_level == {}


class TestFireworksTransportKwargs:
    """End-to-end: the transport produces standard OpenAI kwargs."""

    def test_basic_kwargs(self, fireworks_profile):
        from agent.transports.chat_completions import ChatCompletionsTransport

        kwargs = ChatCompletionsTransport().build_kwargs(
            model="accounts/fireworks/routers/kimi-k2p6-turbo",
            messages=[{"role": "user", "content": "ping"}],
            tools=None,
            provider_profile=fireworks_profile,
            base_url="https://api.fireworks.ai/inference/v1",
            provider_name="fireworks",
        )
        assert kwargs["model"] == "accounts/fireworks/routers/kimi-k2p6-turbo"
        assert kwargs["messages"] == [{"role": "user", "content": "ping"}]
        assert "extra_body" not in kwargs

    def test_tools_pass_through(self, fireworks_profile):
        from agent.transports.chat_completions import ChatCompletionsTransport

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "test_tool",
                    "description": "A test tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        kwargs = ChatCompletionsTransport().build_kwargs(
            model="accounts/fireworks/models/glm-5p1",
            messages=[{"role": "user", "content": "call test_tool"}],
            tools=tools,
            provider_profile=fireworks_profile,
            base_url="https://api.fireworks.ai/inference/v1",
            provider_name="fireworks",
        )
        assert kwargs["tools"] == tools

    def test_temperature_passes_through(self, fireworks_profile):
        from agent.transports.chat_completions import ChatCompletionsTransport

        kwargs = ChatCompletionsTransport().build_kwargs(
            model="accounts/fireworks/models/minimax-m2p5",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            provider_profile=fireworks_profile,
            temperature=0.5,
            base_url="https://api.fireworks.ai/inference/v1",
            provider_name="fireworks",
        )
        assert kwargs["temperature"] == 0.5


class TestFireworksConsumerAPI:
    """Profile is readable from downstream consumers."""

    def test_aux_model_consumer(self, fireworks_profile):
        from agent.auxiliary_client import _get_aux_model_for_provider

        assert _get_aux_model_for_provider("fireworks") == "accounts/fireworks/models/minimax-m2p5"
