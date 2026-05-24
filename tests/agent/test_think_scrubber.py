"""Tests for StreamingThinkScrubber.

These tests lock in the contract the scrubber must satisfy so downstream
consumers (ACP, api_server, TTS, CLI, gateway) never see reasoning
blocks leaking through the stream_delta_callback.  The scenarios map
directly to the MiniMax-M2.7 / DeepSeek / Qwen3 streaming patterns that
break the older per-delta regex strip.

``feed`` / ``flush`` return ``(visible, reasoning)`` — ``visible`` is
what reaches the chat-completions ``delta.content`` channel,
``reasoning`` is what was scrubbed out of ``<think>…</think>`` blocks
and routed via ``_fire_reasoning_delta`` to downstream reasoning
consumers (the new ``delta.reasoning_content`` SSE channel).  Each
test asserts BOTH halves of that tuple so a regression on either side
(reasoning swallowed silently, or visible content leaked into
reasoning) fails fast.
"""

from __future__ import annotations

import pytest

from agent.think_scrubber import StreamingThinkScrubber


def _drive(scrubber: StreamingThinkScrubber, deltas: list[str]) -> tuple[str, str]:
    """Feed a sequence of deltas, then flush, and return concatenated
    ``(visible, reasoning)`` across all calls."""
    visible_parts: list[str] = []
    reasoning_parts: list[str] = []
    for d in deltas:
        v, r = scrubber.feed(d)
        visible_parts.append(v)
        reasoning_parts.append(r)
    v, r = scrubber.flush()
    visible_parts.append(v)
    reasoning_parts.append(r)
    return "".join(visible_parts), "".join(reasoning_parts)


class TestClosedPairs:
    """Closed <tag>...</tag> pairs are always stripped, regardless of boundary."""

    def test_closed_pair_single_delta(self) -> None:
        s = StreamingThinkScrubber()
        assert _drive(s, ["<think>reasoning</think>Hello world"]) == (
            "Hello world",
            "reasoning",
        )

    def test_closed_pair_surrounded_by_content(self) -> None:
        s = StreamingThinkScrubber()
        assert _drive(s, ["Hello <think>note</think> world"]) == (
            "Hello  world",
            "note",
        )

    @pytest.mark.parametrize(
        "tag",
        ["think", "thinking", "reasoning", "thought", "REASONING_SCRATCHPAD"],
    )
    def test_all_tag_variants(self, tag: str) -> None:
        s = StreamingThinkScrubber()
        delta = f"<{tag}>x</{tag}>Hello"
        assert _drive(s, [delta]) == ("Hello", "x")

    def test_case_insensitive_pair(self) -> None:
        s = StreamingThinkScrubber()
        assert _drive(s, ["<THINK>x</Think>Hello"]) == ("Hello", "x")


class TestUnterminatedOpen:
    """Unterminated open tag discards all subsequent content to end of stream,
    surfacing it as reasoning so a downstream ``delta.reasoning_content``
    consumer can still display the chain-of-thought."""

    def test_open_at_stream_start(self) -> None:
        s = StreamingThinkScrubber()
        assert _drive(s, ["<think>reasoning text with no close"]) == (
            "",
            "reasoning text with no close",
        )

    def test_open_after_newline(self) -> None:
        s = StreamingThinkScrubber()
        # 'Hello\n' is a block boundary for the <think> that follows
        assert _drive(s, ["Hello\n<think>reasoning"]) == (
            "Hello\n",
            "reasoning",
        )

    def test_open_after_newline_then_whitespace(self) -> None:
        s = StreamingThinkScrubber()
        assert _drive(s, ["Hello\n  <think>reasoning"]) == (
            "Hello\n  ",
            "reasoning",
        )

    def test_prose_mentioning_tag_not_stripped(self) -> None:
        """Mid-line '<think>' in prose is preserved (no boundary)."""
        s = StreamingThinkScrubber()
        text = "Use the <think> element for reasoning"
        assert _drive(s, [text]) == (text, "")


class TestOrphanClose:
    """Orphan close tags (no prior open) are stripped without boundary check.
    They never produce reasoning — there was no matching open block."""

    def test_orphan_close_alone(self) -> None:
        s = StreamingThinkScrubber()
        assert _drive(s, ["Hello</think>world"]) == ("Helloworld", "")

    def test_orphan_close_with_trailing_space_consumed(self) -> None:
        """Matches _strip_think_blocks case 3 \\s* behaviour."""
        s = StreamingThinkScrubber()
        assert _drive(s, ["Hello</think> world"]) == ("Helloworld", "")

    def test_multiple_orphan_closes(self) -> None:
        s = StreamingThinkScrubber()
        assert _drive(s, ["A</think>B</thinking>C"]) == ("ABC", "")


class TestPartialTagsAcrossDeltas:
    """Partial tags at delta boundaries must be held back, not emitted raw."""

    def test_split_open_tag_held_back(self) -> None:
        """'<' arrives alone, 'think>' completes it on next delta."""
        s = StreamingThinkScrubber()
        # At stream start, last_emitted_ended_newline=True, so <think> at 0 is boundary
        assert _drive(s, ["<", "think>reasoning</think>done"]) == (
            "done",
            "reasoning",
        )

    def test_split_open_tag_not_at_boundary(self) -> None:
        """Mid-line split '<' + 'think>X</think>' is a closed pair.

        Closed pairs are always stripped (matching
        ``_strip_think_blocks`` case 1), even without a block
        boundary — a closed pair is an intentional bounded construct.
        """
        s = StreamingThinkScrubber()
        assert _drive(s, ["word<", "think>prose</think>more"]) == (
            "wordmore",
            "prose",
        )

    def test_split_close_tag_held_back(self) -> None:
        """Close tag split across deltas still closes the block."""
        s = StreamingThinkScrubber()
        assert _drive(s, ["<think>reasoning<", "/think>after"]) == (
            "after",
            "reasoning",
        )

    def test_split_close_tag_deep(self) -> None:
        """Close tag can be split anywhere."""
        s = StreamingThinkScrubber()
        assert _drive(s, ["<think>reasoning</th", "ink>after"]) == (
            "after",
            "reasoning",
        )


class TestTheMiniMaxScenario:
    """The exact pattern run_agent per-delta regex strip breaks."""

    def test_minimax_split_open(self) -> None:
        """delta1='<think>', delta2='Let me check', delta3='</think>done'."""
        s = StreamingThinkScrubber()
        assert _drive(
            s, ["<think>", "Let me check their config", "</think>", "done"]
        ) == (
            "done",
            "Let me check their config",
        )

    def test_minimax_split_open_with_trailing_content(self) -> None:
        """Reasoning then closes and hands off to final content."""
        s = StreamingThinkScrubber()
        assert _drive(
            s,
            [
                "<think>",
                "The user wants to know if thinking is on",
                "</think>",
                "\n\nshow_reasoning: false — thinking is OFF.",
            ],
        ) == (
            "\n\nshow_reasoning: false — thinking is OFF.",
            "The user wants to know if thinking is on",
        )

    def test_minimax_unterminated_reasoning_at_end(self) -> None:
        """Unclosed reasoning at stream end is surfaced as reasoning,
        never leaked into visible output."""
        s = StreamingThinkScrubber()
        assert _drive(
            s, ["<think>", "The user wants", " to know something"]
        ) == (
            "",
            "The user wants to know something",
        )


class TestResetAndReentry:
    def test_reset_clears_in_block_state(self) -> None:
        s = StreamingThinkScrubber()
        s.feed("<think>hanging")
        assert s._in_block is True
        s.reset()
        assert s._in_block is False
        # After reset, a new turn works cleanly
        assert _drive(s, ["Hello world"]) == ("Hello world", "")

    def test_reset_clears_buffered_partial_tag(self) -> None:
        s = StreamingThinkScrubber()
        s.feed("word<")
        assert s._buf == "<"
        s.reset()
        assert s._buf == ""
        assert _drive(s, ["fresh content"]) == ("fresh content", "")


class TestFlushBehaviour:
    def test_flush_surfaces_unterminated_block_as_reasoning(self) -> None:
        """Unterminated reasoning at stream end goes out via the
        reasoning channel rather than being silently discarded.

        For this input the closing-tag prefix buffer is empty when
        flush runs (the model emitted no '<' that could start a
        close), so both flush halves are empty — the reasoning was
        already surfaced during the preceding ``feed`` call."""
        s = StreamingThinkScrubber()
        assert s.feed("<think>reasoning with no close") == (
            "",
            "reasoning with no close",
        )
        assert s.flush() == ("", "")

    def test_flush_surfaces_partial_close_held_back_as_reasoning(self) -> None:
        """If the model dies mid-close-tag, the partial close-tag
        prefix that was held back during streaming is surfaced as
        reasoning on flush (it was still inside the unterminated
        block, just waiting for the rest of the close tag)."""
        s = StreamingThinkScrubber()
        # '<think>foo</th' — '</th' is held back as a partial close prefix.
        assert s.feed("<think>foo</th") == ("", "foo")
        # Stream ends without the rest of '</think>' ever arriving.
        # The held-back '</th' is the trailing reasoning, not visible prose.
        assert s.flush() == ("", "</th")

    def test_flush_emits_innocent_partial_tag_tail(self) -> None:
        """If held-back tail turned out not to be a real tag, emit it."""
        s = StreamingThinkScrubber()
        s.feed("word<")  # '<' could be a tag prefix
        # Stream ends with only '<' held back — emit it as prose.
        assert s.flush() == ("<", "")

    def test_flush_on_empty_scrubber(self) -> None:
        s = StreamingThinkScrubber()
        assert s.flush() == ("", "")


class TestRealisticStreaming:
    """Character-by-character streaming must work as well as larger chunks."""

    def test_char_by_char_closed_pair(self) -> None:
        s = StreamingThinkScrubber()
        deltas = list("<think>x</think>Hello world")
        assert _drive(s, deltas) == ("Hello world", "x")

    def test_char_by_char_orphan_close(self) -> None:
        s = StreamingThinkScrubber()
        deltas = list("Hello</think>world")
        assert _drive(s, deltas) == ("Helloworld", "")

    def test_reasoning_then_real_response_first_word_preserved(self) -> None:
        """Regression: the first word of the final response must NOT be eaten.

        Stefan's screenshot bug — 'Let me check' was being rendered as
        ' me check'.  The scrubber must not consume any character of
        post-close content.
        """
        s = StreamingThinkScrubber()
        deltas = [
            "<think>",
            "User wants to know things",
            "</think>",
            "Let me check their config.",
        ]
        assert _drive(s, deltas) == (
            "Let me check their config.",
            "User wants to know things",
        )

    def test_no_tag_passthrough_is_identical(self) -> None:
        """Streams without any reasoning tags pass through byte-for-byte."""
        s = StreamingThinkScrubber()
        deltas = ["Hello ", "world ", "how ", "are ", "you?"]
        assert _drive(s, deltas) == ("Hello world how are you?", "")
