"""Live Fireworks smoke test.

Opt-in only:
    HERMES_LIVE_TESTS=1 pytest tests/run_agent/test_fireworks_live.py -q

Requires FIREWORKS_API_KEY in the process environment.
"""

from __future__ import annotations

import os

import pytest

LIVE = os.environ.get("HERMES_LIVE_TESTS") == "1"
FIREWORKS_KEY = os.environ.get("FIREWORKS_API_KEY", "")
LIVE_BASE_URL = "https://api.fireworks.ai/inference/v1"

pytestmark = [
    pytest.mark.skipif(not LIVE, reason="live-only: set HERMES_LIVE_TESTS=1"),
    pytest.mark.skipif(not FIREWORKS_KEY, reason="FIREWORKS_API_KEY not configured"),
    pytest.mark.integration,
]


def _make_live_client():
    from openai import OpenAI

    return OpenAI(api_key=FIREWORKS_KEY, base_url=LIVE_BASE_URL)


def test_fireworks_basic_chat():
    """A single-turn chat completion returns non-empty content."""
    client = _make_live_client()

    response = client.chat.completions.create(
        model="accounts/fireworks/routers/kimi-k2p6-turbo",
        messages=[{"role": "user", "content": "Say exactly the word 'pong' and nothing else."}],
        max_tokens=32,
        temperature=0.0,
        timeout=60,
    )

    content = response.choices[0].message.content
    assert content and "pong" in content.lower()


def test_fireworks_tool_call():
    """Fireworks model can emit a tool call."""
    client = _make_live_client()

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                    },
                    "required": ["city"],
                },
            },
        }
    ]

    response = client.chat.completions.create(
        model="accounts/fireworks/routers/kimi-k2p6-turbo",
        messages=[{"role": "user", "content": "What's the weather in San Francisco?"}],
        tools=tools,
        max_tokens=256,
        temperature=0.0,
        timeout=60,
    )

    tool_calls = response.choices[0].message.tool_calls
    assert tool_calls is not None and len(tool_calls) > 0
    assert tool_calls[0].function.name == "get_weather"
