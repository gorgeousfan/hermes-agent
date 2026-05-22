"""Regression guard: preserve thinking blocks on DeepSeek's /anthropic endpoint.

DeepSeek's ``api.deepseek.com/anthropic`` route speaks the Anthropic Messages
protocol but, when thinking mode is enabled, requires ``thinking`` blocks from
prior assistant turns to round-trip on subsequent requests.  The generic
third-party path strips them (signatures are Anthropic-proprietary and other
proxies cannot validate them), so without a DeepSeek-specific carve-out the
next tool-call turn fails with HTTP 400::

    The content[].thinking in the thinking mode must be passed back to the
    API.

DeepSeek v4-pro signs thinking blocks with UUIDs (e.g.
``53f4dd1d-...``) that are NOT Anthropic cryptographic signatures.
On the DeepSeek /anthropic path we preserve ALL thinking blocks as-is —
DeepSeek creates and can validate its own signatures.  This differs from
Kimi's /coding endpoint which requires only unsigned blocks.

See hermes-agent#16748.
"""

from __future__ import annotations

import pytest


class TestDeepSeekAnthropicPreservesThinking:
    """convert_messages_to_anthropic must replay DeepSeek thinking blocks."""

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://api.deepseek.com/anthropic",
            "https://api.deepseek.com/anthropic/",
            "https://api.deepseek.com/anthropic/v1",
            "https://API.DeepSeek.com/anthropic",
        ],
    )
    def test_unsigned_thinking_block_survives_replay(self, base_url: str) -> None:
        """Unsigned thinking (synthesised from reasoning_content) must be preserved."""
        from agent.anthropic_adapter import convert_messages_to_anthropic

        messages = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "reasoning_content": "planning the tool call",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "skill_view", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ]
        _system, converted = convert_messages_to_anthropic(
            messages, base_url=base_url
        )

        assistant_msg = next(m for m in converted if m["role"] == "assistant")
        thinking_blocks = [
            b for b in assistant_msg["content"]
            if isinstance(b, dict) and b.get("type") == "thinking"
        ]
        assert len(thinking_blocks) == 1, (
            f"DeepSeek /anthropic ({base_url}) must preserve unsigned thinking "
            "blocks synthesised from reasoning_content — upstream rejects "
            "replayed tool-call messages without them."
        )
        assert thinking_blocks[0]["thinking"] == "planning the tool call"
        # Synthesised block — never has a signature
        assert "signature" not in thinking_blocks[0]

    def test_unsigned_thinking_preserved_on_non_latest_assistant_turn(self) -> None:
        """DeepSeek validates history across every prior assistant turn, not just last."""
        from agent.anthropic_adapter import convert_messages_to_anthropic

        messages = [
            {"role": "user", "content": "q1"},
            {
                "role": "assistant",
                "reasoning_content": "r1",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "f", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
            {"role": "user", "content": "q2"},
            {
                "role": "assistant",
                "reasoning_content": "r2",
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "f", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_2", "content": "ok"},
        ]
        _system, converted = convert_messages_to_anthropic(
            messages, base_url="https://api.deepseek.com/anthropic"
        )

        assistants = [m for m in converted if m["role"] == "assistant"]
        assert len(assistants) == 2
        for assistant, expected in zip(assistants, ("r1", "r2")):
            thinking = [
                b for b in assistant["content"]
                if isinstance(b, dict) and b.get("type") == "thinking"
            ]
            assert len(thinking) == 1
            assert thinking[0]["thinking"] == expected

    def test_signed_deepseek_thinking_block_is_preserved(self) -> None:
        """DeepSeek-signed thinking blocks must be preserved for round-tripping.

        DeepSeek v4-pro signs its thinking blocks with UUIDs (e.g.
        ``53f4dd1d-...``) that are NOT Anthropic cryptographic signatures.
        These blocks must survive replay — DeepSeek creates and validates its
        own signatures, and rejecting them triggers HTTP 400:
        "The content[].thinking in the thinking mode must be passed back."
        """
        from agent.anthropic_adapter import convert_messages_to_anthropic

        messages = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "deepseek-signed reasoning",
                        "signature": "53f4dd1d-54b6-484b-b43c-170a86447d66",
                    },
                    {"type": "text", "text": "hello"},
                ],
            },
            {"role": "user", "content": "again"},
        ]
        _system, converted = convert_messages_to_anthropic(
            messages, base_url="https://api.deepseek.com/anthropic"
        )

        assistant_msg = next(m for m in converted if m["role"] == "assistant")
        thinking_blocks = [
            b for b in assistant_msg["content"]
            if isinstance(b, dict) and b.get("type") == "thinking"
        ]
        assert len(thinking_blocks) == 1, (
            "DeepSeek-signed thinking blocks must be preserved on the "
            "/anthropic endpoint — DeepSeek creates and validates its own "
            "UUID signatures and requires them to round-trip."
        )
        assert thinking_blocks[0]["thinking"] == "deepseek-signed reasoning"
        assert thinking_blocks[0]["signature"] == "53f4dd1d-54b6-484b-b43c-170a86447d66"

    def test_cache_control_stripped_from_thinking_block(self) -> None:
        """cache_control must still be stripped even when the block is preserved.

        DeepSeek's compatibility matrix lists cache_control on thinking blocks
        as ignored — cache markers interfere with signature validation on
        upstreams that do check them, so Hermes strips them everywhere.
        """
        from agent.anthropic_adapter import convert_messages_to_anthropic

        messages = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "reasoning_content": "r1",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "f", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ]
        # Inject cache_control on the synthesised thinking block after-the-fact
        # by running conversion once, mutating, then re-running would be
        # indirect.  Instead check the simpler invariant: no thinking block in
        # the converted output carries cache_control.
        _system, converted = convert_messages_to_anthropic(
            messages, base_url="https://api.deepseek.com/anthropic"
        )
        for m in converted:
            if not isinstance(m.get("content"), list):
                continue
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") in {"thinking", "redacted_thinking"}:
                    assert "cache_control" not in b

    def test_openai_compat_deepseek_base_is_not_matched(self) -> None:
        """The OpenAI-compatible ``api.deepseek.com`` base must NOT trigger the
        DeepSeek /anthropic branch — it never reaches this adapter, but the
        detector should still fail closed so an accidental misuse doesn't
        quietly send signed Anthropic blocks to an OpenAI endpoint.
        """
        from agent.anthropic_adapter import _is_deepseek_anthropic_endpoint

        assert _is_deepseek_anthropic_endpoint("https://api.deepseek.com") is False
        assert _is_deepseek_anthropic_endpoint("https://api.deepseek.com/v1") is False
        assert _is_deepseek_anthropic_endpoint("https://api.deepseek.com/anthropic") is True
        assert _is_deepseek_anthropic_endpoint("https://api.deepseek.com/anthropic/v1") is True

    def test_non_deepseek_third_party_still_strips_all_thinking(self) -> None:
        """MiniMax and other third-party Anthropic endpoints must keep the
        generic strip-all behaviour (they reject unsigned blocks outright).
        """
        from agent.anthropic_adapter import convert_messages_to_anthropic

        messages = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "reasoning_content": "r1",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "f", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ]
        _system, converted = convert_messages_to_anthropic(
            messages, base_url="https://api.minimax.io/anthropic"
        )
        assistant_msg = next(m for m in converted if m["role"] == "assistant")
        thinking_blocks = [
            b for b in assistant_msg["content"]
            if isinstance(b, dict) and b.get("type") == "thinking"
        ]
        assert thinking_blocks == [], (
            "Non-DeepSeek third-party endpoints must keep the generic "
            "strip-all-thinking behaviour — unsigned blocks get rejected."
        )
