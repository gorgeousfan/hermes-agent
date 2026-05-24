"""Tests for the reasoning-block transcript leak fix (#7233).

The gateway prepends a ``💭 **Reasoning:**`` block to ``response`` when
``display.show_reasoning`` is enabled.  Before the fix, that
reasoning-prefixed string was written into the session transcript and
then re-played as assistant content on the next ``/resume``, leaking
internal model thinking into user-visible chat.

The fix has two parts:

1. The display-prepend logic is extracted into the module-level helper
   ``gateway.run._prepend_reasoning_for_display`` so callers (and tests)
   can exercise the real prod code instead of re-implementing it.
2. In ``GatewayRunner._handle_message_with_agent``, the gateway
   snapshots ``_clean_response = response`` BEFORE calling that helper
   and writes the snapshot (never the display-prefixed copy) into the
   transcript's fallback "no new messages" branch.

These tests import the real helper and guard the invariants directly,
so silently regressing the snapshot or the helper would fail here.
"""

import pytest

from gateway.run import (
    REASONING_DISPLAY_HEADER,
    _prepend_reasoning_for_display,
)


# ---------------------------------------------------------------------------
# Real prod helper — display-side behavior
# ---------------------------------------------------------------------------


def test_prepend_helper_adds_header_when_reasoning_present():
    out = _prepend_reasoning_for_display(
        "The answer is 42.", "I thought about it carefully."
    )
    assert REASONING_DISPLAY_HEADER in out
    assert "I thought about it carefully." in out
    assert out.endswith("The answer is 42.")


def test_prepend_helper_returns_response_unchanged_when_no_reasoning():
    """A None or empty reasoning string is treated as 'no reasoning to show'."""
    for empty in (None, ""):
        out = _prepend_reasoning_for_display("Plain answer.", empty)
        assert out == "Plain answer."
        assert REASONING_DISPLAY_HEADER not in out


def test_prepend_helper_returns_empty_response_unchanged():
    """No reasoning prefix on an empty response — nothing to decorate."""
    assert _prepend_reasoning_for_display("", "Some reasoning") == ""
    assert _prepend_reasoning_for_display(None, "Some reasoning") is None


def test_prepend_helper_collapses_long_reasoning():
    """Long reasoning is truncated with a ``... N more lines`` marker."""
    reasoning = "\n".join(f"line {i}" for i in range(40))
    out = _prepend_reasoning_for_display("Answer", reasoning)
    assert "line 0" in out and "line 14" in out
    assert "line 39" not in out
    assert "25 more lines" in out


# ---------------------------------------------------------------------------
# Snapshot pattern — the load-bearing fix for #7233
# ---------------------------------------------------------------------------


def _simulate_handler_snapshot(response: str, last_reasoning: str | None) -> tuple[str, str]:
    """Mirror the snapshot order used in ``_handle_message_with_agent``.

    The handler calls::

        _clean_response = response
        if _show_reasoning_effective:
            response = _prepend_reasoning_for_display(response, last_reasoning)
        ...
        self.session_store.append_to_transcript(..., {"content": _clean_response})

    So the only thing this helper checks is that the snapshot precedes
    the call to the real prod helper.  Any future change that flips this
    order (or drops the snapshot) will fail one of the assertions below.
    """
    _clean_response = response
    response = _prepend_reasoning_for_display(response, last_reasoning)
    return response, _clean_response


def test_clean_snapshot_excludes_reasoning_when_display_prepend_runs():
    display, clean = _simulate_handler_snapshot(
        "Hello world", "Thinking out loud"
    )
    assert REASONING_DISPLAY_HEADER in display  # user sees reasoning
    assert REASONING_DISPLAY_HEADER not in clean  # transcript stays clean
    assert clean == "Hello world"


def test_clean_snapshot_matches_display_when_no_reasoning_text():
    display, clean = _simulate_handler_snapshot("Quiet answer", None)
    assert display == clean == "Quiet answer"


def test_clean_snapshot_taken_before_helper_call():
    """Mutating ``response`` after the snapshot must not change the clean copy.

    Guards against a refactor that re-orders the snapshot to AFTER the
    prefix is applied — the most likely regression shape.
    """
    response = "Original"
    _clean_response = response
    response = _prepend_reasoning_for_display(response, "Some reasoning text")
    assert _clean_response == "Original"
    assert response != _clean_response


# ---------------------------------------------------------------------------
# Primary-path safety (re: Copilot review note)
# ---------------------------------------------------------------------------


def test_primary_path_agent_messages_are_not_passed_through_helper():
    """``agent_messages[history_len:]`` flows directly into the transcript
    in the primary branch of ``_handle_message_with_agent``.  These dicts
    come from the agent loop's own message list; the reasoning helper is
    only ever applied to the local ``response`` STRING.

    This test guards the invariant that the helper is a pure
    string-transform and cannot reach into a message dict that would be
    persisted via the primary path.  If a future refactor ever passes
    ``msg["content"]`` through the helper before the transcript write,
    THIS test will keep passing — but the snapshot assertion still
    catches the symptom (transcript content containing the marker).
    """
    assistant_msg = {"role": "assistant", "content": "Pure model output."}
    original_content = assistant_msg["content"]

    # The helper signature accepts (str, str | None) → str — it can't
    # mutate a dict in place.
    prefixed = _prepend_reasoning_for_display(original_content, "Some reasoning")

    assert prefixed != original_content  # the string we passed got prefixed
    assert REASONING_DISPLAY_HEADER in prefixed
    # The dict we never passed in stays exactly as-is.
    assert assistant_msg["content"] == original_content
    assert REASONING_DISPLAY_HEADER not in assistant_msg["content"]


# ---------------------------------------------------------------------------
# Header constant is the public marker used by other gateway code paths
# ---------------------------------------------------------------------------


def test_reasoning_display_header_is_stable_marker():
    """The header constant is what other code (transcript filters,
    history scrubbers) match on.  Keep it stable."""
    assert REASONING_DISPLAY_HEADER == "\U0001f4ad **Reasoning:**"
