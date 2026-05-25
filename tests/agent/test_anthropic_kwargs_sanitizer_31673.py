"""Regression tests for #31673 — Anthropic kwargs leak from Codex/Responses path.

The bug: a successful ``vision_analyze`` aux call landed on the main
Anthropic provider, then the very next streaming call to
``_anthropic_client.messages.stream(**api_kwargs)`` failed client-side with
``TypeError: Messages.stream() got an unexpected keyword argument
'instructions'``.

Without a deterministic repro, the fix is defensive: strip the known set of
Codex / OpenAI-Responses-only kwargs at both Anthropic call sites
(``_call_anthropic`` streaming and ``_anthropic_messages_create``
non-streaming) before they reach the SDK, and log a single warning the
first time a leak is observed so the upstream source can be tracked down
later without flooding the log.
"""

from __future__ import annotations

import inspect
import logging
from unittest.mock import MagicMock

import agent.anthropic_adapter as anthropic_adapter
from agent.anthropic_adapter import (
    _ANTHROPIC_KWARG_DENYLIST,
    sanitize_anthropic_messages_kwargs,
)


# ---------------------------------------------------------------------------
# Pure-helper behaviour
# ---------------------------------------------------------------------------


class TestSanitizeAnthropicMessagesKwargs:
    """``sanitize_anthropic_messages_kwargs`` strips Codex/Responses keys."""

    def test_strips_instructions_kwarg(self):
        """The exact kwarg from the issue's traceback gets dropped."""
        cleaned = sanitize_anthropic_messages_kwargs({
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1024,
            "instructions": "leaked from codex aux path",
        })
        assert "instructions" not in cleaned
        assert cleaned["model"] == "claude-sonnet-4-6"
        assert cleaned["max_tokens"] == 1024
        assert cleaned["messages"] == [{"role": "user", "content": "hi"}]

    def test_strips_full_codex_responses_keyset(self):
        """All known Codex/Responses-only keys are filtered."""
        leaky = {
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 4096,
            # OpenAI Responses / Codex leakage:
            "instructions": "you are helpful",
            "input": [{"role": "user", "content": []}],
            "store": False,
            "max_output_tokens": 8192,
            "parallel_tool_calls": True,
            "prompt_cache_key": "session-123",
            "reasoning": {"effort": "medium"},
            "include": ["reasoning.encrypted_content"],
        }
        cleaned = sanitize_anthropic_messages_kwargs(leaky)
        for offending in _ANTHROPIC_KWARG_DENYLIST:
            assert offending not in cleaned, offending

    def test_preserves_valid_anthropic_kwargs(self):
        """Anthropic-native kwargs pass through untouched."""
        original = {
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 16384,
            "system": "be brief",
            "tools": [{"name": "calc", "input_schema": {}}],
            "tool_choice": {"type": "auto"},
            "temperature": 0.7,
            "top_p": 0.9,
            "top_k": 40,
            "stop_sequences": ["END"],
            "metadata": {"user_id": "abc"},
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "high"},
            "extra_headers": {"anthropic-beta": "fast-mode-2026-04-22"},
            "extra_body": {"speed": "fast"},
            "service_tier": "priority",
        }
        cleaned = sanitize_anthropic_messages_kwargs(original)
        assert cleaned == original

    def test_returns_shallow_copy_when_filtering(self):
        """The original dict isn't mutated; the filtered one is a new dict."""
        original = {
            "model": "claude-sonnet-4-6",
            "messages": [],
            "max_tokens": 1024,
            "instructions": "leak",
        }
        cleaned = sanitize_anthropic_messages_kwargs(original)
        assert "instructions" in original
        assert cleaned is not original

    def test_returns_input_unchanged_when_no_leak(self):
        """No-op fast path: when nothing is leaking, return the same dict."""
        clean = {
            "model": "claude-opus-4-7",
            "messages": [],
            "max_tokens": 1024,
        }
        result = sanitize_anthropic_messages_kwargs(clean)
        assert result is clean

    def test_non_dict_input_returned_unchanged(self):
        """Defensive: caller passes a SimpleNamespace / None / list — pass through."""
        assert sanitize_anthropic_messages_kwargs(None) is None
        assert sanitize_anthropic_messages_kwargs([1, 2, 3]) == [1, 2, 3]
        sentinel = object()
        assert sanitize_anthropic_messages_kwargs(sentinel) is sentinel

    def test_warns_once_per_process_on_leak(self, caplog, monkeypatch):
        """Only the first leak in a process logs — silent on subsequent retries."""
        monkeypatch.setattr(anthropic_adapter, "_anthropic_kwargs_leak_warned", False)
        leaky = {
            "model": "m", "messages": [], "max_tokens": 1,
            "instructions": "leak", "input": [],
        }
        with caplog.at_level(logging.WARNING, logger="agent.anthropic_adapter"):
            sanitize_anthropic_messages_kwargs(leaky)
            sanitize_anthropic_messages_kwargs(leaky)
            sanitize_anthropic_messages_kwargs(leaky)
        warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
        assert len(warnings) == 1
        msg = warnings[0].getMessage()
        assert "instructions" in msg
        assert "input" in msg
        assert "31673" in msg

    def test_does_not_warn_when_clean(self, caplog, monkeypatch):
        """Happy path: no leak, no log noise."""
        monkeypatch.setattr(anthropic_adapter, "_anthropic_kwargs_leak_warned", False)
        with caplog.at_level(logging.WARNING, logger="agent.anthropic_adapter"):
            sanitize_anthropic_messages_kwargs({
                "model": "m", "messages": [], "max_tokens": 1,
            })
        warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
        assert warnings == []


class TestAnthropicKwargDenylistContents:
    """The denylist captures the keys the issue's traceback names."""

    def test_includes_instructions_key_from_issue(self):
        """The exact kwarg the SDK rejected in #31673 is denied."""
        assert "instructions" in _ANTHROPIC_KWARG_DENYLIST

    def test_covers_codex_responses_request_shape(self):
        """All required-key kwargs from a Codex Responses request are denied."""
        # See agent/codex_responses_adapter.py — required = {"model",
        # "instructions", "input"} plus ``store=False``. ``model`` is shared
        # with Anthropic, the rest must never reach the Anthropic SDK.
        for key in ("instructions", "input", "store"):
            assert key in _ANTHROPIC_KWARG_DENYLIST


# ---------------------------------------------------------------------------
# Source-level guards: the call sites use the sanitizer
# ---------------------------------------------------------------------------


class TestStreamingCallSiteUsesSanitizer:
    """``_call_anthropic`` (chat_completion_helpers.py) sanitizes before SDK call."""

    def test_call_anthropic_calls_sanitizer_before_stream(self):
        """Source-level guard: streaming Anthropic call goes through the sanitizer.

        Pinning this in source guards against an accidental revert that
        re-introduces the #31673 crash without producing a runtime error
        in the Anthropic-free unit-test environment.
        """
        from agent.chat_completion_helpers import interruptible_streaming_api_call
        src = inspect.getsource(interruptible_streaming_api_call)
        assert "sanitize_anthropic_messages_kwargs" in src
        # Locate the actual call site (skip the docstring reference, which
        # also contains the literal ``messages.stream()`` for prose).  The
        # real call is fully qualified through ``_anthropic_client``.
        stream_idx = src.index("_anthropic_client.messages.stream(")
        # Sanitizer wrapper must appear inside the next 200 chars — i.e.
        # between ``stream(`` and its closing ``)`` over the multi-line
        # call, not anywhere else.  The buggy v0.14.0 shape
        # ``stream(**api_kwargs)`` would fail this guard.
        window = src[stream_idx:stream_idx + 200]
        assert "sanitize_anthropic_messages_kwargs(api_kwargs)" in window
        # And the legacy shape must be gone everywhere in the function.
        assert "messages.stream(**api_kwargs)" not in src


class TestNonStreamingCallSiteUsesSanitizer:
    """``_anthropic_messages_create`` (run_agent.py) sanitizes before SDK call."""

    def test_anthropic_messages_create_calls_sanitizer(self):
        """Source-level guard: non-streaming path also strips Codex kwargs."""
        from run_agent import AIAgent
        src = inspect.getsource(AIAgent._anthropic_messages_create)
        assert "sanitize_anthropic_messages_kwargs" in src
        assert "messages.create(" in src
        assert "sanitize_anthropic_messages_kwargs(api_kwargs)" in src


# ---------------------------------------------------------------------------
# Behavioural integration: exact #31673 traceback no longer fires
# ---------------------------------------------------------------------------


class _RaisingMessagesStream:
    """Minimal stand-in for ``client.messages.stream`` that mimics the
    Anthropic SDK's strict signature: it accepts only the documented
    Anthropic kwargs and raises ``TypeError`` on anything else — the
    exact failure mode from the #31673 traceback.
    """

    _VALID = frozenset({
        "model", "messages", "max_tokens", "system", "tools", "tool_choice",
        "temperature", "top_p", "top_k", "stop_sequences", "metadata",
        "stream", "thinking", "output_config", "extra_headers", "extra_query",
        "extra_body", "timeout", "service_tier",
    })

    def __init__(self):
        self.last_kwargs: dict | None = None

    def __call__(self, **kwargs):
        unexpected = set(kwargs) - self._VALID
        if unexpected:
            raise TypeError(
                "Messages.stream() got an unexpected keyword argument "
                f"{sorted(unexpected)[0]!r}"
            )
        self.last_kwargs = kwargs
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.response = None
        ctx.__iter__ = MagicMock(return_value=iter([]))
        ctx.get_final_message = MagicMock(return_value=None)
        return ctx


class TestEndToEndIssueScenario:
    """The exact crash from #31673 is suppressed — sanitized kwargs reach the SDK."""

    def test_leaked_instructions_kwarg_is_dropped_before_sdk(self):
        """A leaked ``instructions=`` kwarg no longer reaches ``Messages.stream``."""
        stream = _RaisingMessagesStream()
        leaky = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1024,
            "instructions": "leaked from codex aux path — would crash SDK",
        }
        cleaned = sanitize_anthropic_messages_kwargs(leaky)
        ctx = stream(**cleaned)
        ctx.__enter__()
        assert stream.last_kwargs is not None
        assert "instructions" not in stream.last_kwargs
        assert stream.last_kwargs["model"] == "claude-sonnet-4-6"

    def test_unsanitized_kwargs_reproduce_original_crash(self):
        """Confirms the test stand-in really mimics the #31673 failure."""
        stream = _RaisingMessagesStream()
        leaky = {
            "model": "claude-sonnet-4-6",
            "messages": [],
            "max_tokens": 1024,
            "instructions": "leak",
        }
        try:
            stream(**leaky)
        except TypeError as exc:
            assert "unexpected keyword argument 'instructions'" in str(exc)
        else:  # pragma: no cover — guard against the test stand-in regressing
            raise AssertionError(
                "Test stand-in failed to raise the #31673 TypeError"
            )
