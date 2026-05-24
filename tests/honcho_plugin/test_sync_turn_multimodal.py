"""Regression tests for HonchoMemoryProvider.sync_turn with multimodal content.

See https://github.com/NousResearch/hermes-agent/issues/30252 — the OpenAI
vision-style ``list`` content shape used to crash ``sync_turn`` because
``sanitize_context`` only accepts strings, causing vision turns to be
silently dropped from Honcho memory.
"""

from __future__ import annotations

import threading

import pytest

from plugins.memory.honcho import HonchoMemoryProvider


# ---------------------------------------------------------------------------
# _flatten_content unit tests
# ---------------------------------------------------------------------------


def test_flatten_passthrough_string():
    assert HonchoMemoryProvider._flatten_content("hello") == "hello"


def test_flatten_passthrough_none():
    assert HonchoMemoryProvider._flatten_content(None) is None


def test_flatten_text_and_image_url():
    content = [
        {"type": "text", "text": "what colour is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]
    assert (
        HonchoMemoryProvider._flatten_content(content)
        == "what colour is this?\n[image]"
    )


def test_flatten_input_image_alias():
    content = [{"type": "input_image", "image": "..."}]
    assert HonchoMemoryProvider._flatten_content(content) == "[image]"


def test_flatten_unknown_type_becomes_placeholder():
    content = [{"type": "foo", "data": "..."}]
    assert HonchoMemoryProvider._flatten_content(content) == "[foo]"


def test_flatten_empty_list():
    assert HonchoMemoryProvider._flatten_content([]) == ""


def test_flatten_bare_strings_in_list():
    content = ["alpha", "beta"]
    assert HonchoMemoryProvider._flatten_content(content) == "alpha\nbeta"


def test_flatten_skips_empty_text_parts():
    content = [
        {"type": "text", "text": ""},
        {"type": "text", "text": "kept"},
    ]
    assert HonchoMemoryProvider._flatten_content(content) == "kept"


def test_flatten_skips_non_dict_non_str_items():
    content = [42, None, {"type": "text", "text": "ok"}]
    assert HonchoMemoryProvider._flatten_content(content) == "ok"


def test_flatten_skips_type_missing():
    content = [{"text": "no type field"}]
    # No "type" → entry produces no part.
    assert HonchoMemoryProvider._flatten_content(content) == ""


# ---------------------------------------------------------------------------
# sync_turn integration: list-shaped content must NOT raise, and the
# flattened text must reach the mocked Honcho session.
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def add_message(self, role, chunk):
        self.messages.append((role, chunk))


class _FakeManager:
    def __init__(self):
        self.session = _FakeSession()

    def get_or_create(self, _key):
        return self.session

    def _flush_session(self, _session):
        pass


def _make_provider(monkeypatch=None):
    """Build a minimally-wired HonchoMemoryProvider without running __init__."""
    p = HonchoMemoryProvider.__new__(HonchoMemoryProvider)
    p._cron_skipped = False
    p._manager = _FakeManager()
    p._session_key = "test-session"
    p._config = None
    p._sync_thread = None

    # _chunk_message normally lives on the class — fall back to a passthrough
    # if it isn't trivially callable on this lean instance.
    if not hasattr(p, "_chunk_message"):
        p._chunk_message = lambda text, _limit: [text] if text else []
    return p


def _run_sync_turn(provider, user_content, assistant_content):
    provider.sync_turn(user_content, assistant_content)
    if provider._sync_thread is not None:
        provider._sync_thread.join(timeout=5.0)


def test_sync_turn_accepts_multimodal_user_content():
    provider = _make_provider()
    user_content = [
        {"type": "text", "text": "what colour is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]

    _run_sync_turn(provider, user_content, "it is red")

    roles = [r for r, _ in provider._manager.session.messages]
    bodies = [c for _, c in provider._manager.session.messages]
    assert "user" in roles
    assert "assistant" in roles
    # Flattened user content must include both the text and the image marker.
    user_body = next(c for r, c in provider._manager.session.messages if r == "user")
    assert "what colour is this?" in user_body
    assert "[image]" in user_body
    # Assistant string content passes through unchanged.
    assert any("it is red" in b for b in bodies)


def test_sync_turn_does_not_raise_on_list_assistant_content():
    provider = _make_provider()
    assistant_content = [{"type": "text", "text": "ok"}]
    _run_sync_turn(provider, "hi", assistant_content)

    bodies = [c for _, c in provider._manager.session.messages]
    assert any("hi" == b for b in bodies)
    assert any("ok" == b for b in bodies)


def test_sync_turn_string_content_still_works():
    provider = _make_provider()
    _run_sync_turn(provider, "plain user", "plain assistant")

    msgs = provider._manager.session.messages
    assert ("user", "plain user") in msgs
    assert ("assistant", "plain assistant") in msgs


def test_flatten_input_text_canonicalized():
    content = [{"type": "input_text", "text": "hello"}]
    assert HonchoMemoryProvider._flatten_content(content) == "hello"


def test_flatten_output_text_canonicalized():
    content = [{"type": "output_text", "text": "assistant said"}]
    assert HonchoMemoryProvider._flatten_content(content) == "assistant said"


def test_flatten_non_string_text_value_does_not_raise():
    content = [{"type": "text", "text": 123}, {"type": "text", "text": None}]
    # Non-string text values are coerced (or skipped for None); join must not raise.
    out = HonchoMemoryProvider._flatten_content(content)
    assert "123" in out
