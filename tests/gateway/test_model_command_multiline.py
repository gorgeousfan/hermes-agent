"""Regression tests for gateway ``/model`` multi-line input handling.

When a user sends a single message that begins with ``/model X`` and then
contains a newline followed by additional text (e.g. via Element/Matrix's
``Shift+Enter``), the gateway used to forward the entire remainder into the
model name parser, which then replied with the misleading
``"Model names cannot contain spaces"`` error. The fix is to detect the
newline up front and return a clear, accurate single-line-command message
instead. Refs issue #22716.
"""

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource


def _make_runner():
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._voice_mode = {}
    runner._session_model_overrides = {}
    return runner


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(
            platform=Platform.TELEGRAM, chat_id="12345", chat_type="dm"
        ),
    )


@pytest.mark.asyncio
async def test_multiline_model_command_returns_clear_error():
    """``/model X\\nfollow-up`` returns a single-line-command guidance reply."""
    event = _make_event("/model ollama-cloud/glm-5.1\nBonjour test, repond OK")

    result = await _make_runner()._handle_model_command(event)

    assert result is not None
    # The misleading historic error must NOT appear:
    assert "cannot contain spaces" not in result.lower()
    # The new error explains the actual constraint:
    assert "single-line command" in result.lower()
    # And nudges the user toward the right two-message split, surfacing
    # the model arg they originally asked for so they can resend it as-is.
    assert "/model ollama-cloud/glm-5.1" in result


@pytest.mark.asyncio
async def test_multiline_model_command_preserves_global_flag_in_first_line():
    """Multi-line input with ``--global`` on the first line is still rejected.

    The user's intent on the first line — including any flags — is echoed
    back verbatim in the suggested command, so they can copy-paste it as a
    follow-up single-line message.
    """
    event = _make_event(
        "/model openai/gpt-5.5 --global\nUse this for the rest of the day"
    )

    result = await _make_runner()._handle_model_command(event)

    assert result is not None
    assert "single-line command" in result.lower()
    assert "/model openai/gpt-5.5 --global" in result


@pytest.mark.asyncio
async def test_trailing_newline_only_is_not_multiline():
    """``/model X\\n`` (stray trailing newline, no follow-up text) is benign.

    Chat clients commonly append a stray trailing newline; we should NOT
    treat that as a multi-line command and refuse the switch. The model
    name on the first line is well-formed, so the switch must proceed.
    """
    event = _make_event("/model openai/gpt-5.5\n")

    # With no follow-up text after the newline, get_command_remainder() is
    # empty → the early return is skipped → the regular switch path runs.
    # We don't fully exercise the switch here (no config/network) but we do
    # assert that the response is NOT the new multi-line guidance error.
    result = await _make_runner()._handle_model_command(event)

    assert result is None or "single-line command" not in result.lower()


@pytest.mark.asyncio
async def test_multiline_model_command_empty_first_line_falls_back_to_generic_hint():
    """``/model\\nQuestion`` — empty model arg — points at the bare command."""
    event = _make_event("/model\nWhat is the capital of France?")

    result = await _make_runner()._handle_model_command(event)

    assert result is not None
    assert "single-line command" in result.lower()
    # Empty first line means we don't echo a partial model name; we
    # fall back to the generic placeholder.
    assert "/model <name>" in result
