"""Regression tests for #30186.

#30186: commit ea49b3862 ("tighten MEDIA extraction regex + silent
skip on file-not-found") replaced the permissive ``MEDIA:\\S+`` regex
with an explicit extension allowlist in three places. ``.md`` was
dropped from the text/document group, so agents could no longer
deliver Markdown files to users via the ``MEDIA:`` tag — only
``.txt`` and ``.csv`` worked. Markdown is a standard document format
routinely exchanged between agents and users and belongs alongside
``txt`` / ``csv``.

These tests pin ``.md`` into all three regex sites the issue calls out:

  1. ``BasePlatformAdapter.extract_media`` (``gateway/platforms/base.py``
     ~L2162) — final agent responses to users.
  2. The first ``_TOOL_MEDIA_RE`` in ``gateway/run.py`` (~L16522) —
     tool result history scan that builds ``_history_media_paths`` so
     the current turn doesn't re-emit paths already delivered.
  3. The second ``_TOOL_MEDIA_RE`` in ``gateway/run.py`` (~L16818) —
     the streaming-history fallback that re-extracts MEDIA tags from
     tool messages when the final response didn't include them.

The tests are intentionally regex-only / behavioural so they survive
unrelated refactors of the surrounding extraction loop.
"""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest

# ──────────────────────────────────────────────────────────────────────
# Minimal stubs so gateway.platforms.base imports cleanly without the
# real telegram extras installed.
# ──────────────────────────────────────────────────────────────────────
_tg = types.ModuleType("telegram")
_tg.constants = types.ModuleType("telegram.constants")
from unittest.mock import MagicMock  # noqa: E402

_ct = MagicMock()
_ct.SUPERGROUP = "supergroup"
_ct.GROUP = "group"
_ct.PRIVATE = "private"
_tg.constants.ChatType = _ct
sys.modules.setdefault("telegram", _tg)
sys.modules.setdefault("telegram.constants", _tg.constants)
sys.modules.setdefault("telegram.ext", types.ModuleType("telegram.ext"))

from gateway.platforms.base import BasePlatformAdapter  # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# 1. extract_media (gateway/platforms/base.py)
# ──────────────────────────────────────────────────────────────────────
class TestExtractMediaSupportsMd:
    """The user-facing extractor that runs over every final response."""

    def test_plain_md_path_is_extracted(self) -> None:
        """The exact failure mode in #30186: agent emits MEDIA:/foo.md
        and the gateway must hand the path to the adapter — not drop it."""
        media, cleaned = BasePlatformAdapter.extract_media(
            "Here is the report:\nMEDIA:/tmp/report.md"
        )
        assert media == [("/tmp/report.md", False)]
        # Tag must be scrubbed from the visible text so the user doesn't
        # see "MEDIA:/tmp/report.md" alongside the actual file delivery.
        assert "MEDIA:" not in cleaned
        assert "Here is the report" in cleaned

    def test_md_in_tilde_path_is_extracted_and_expanded(self) -> None:
        """``extract_media`` applies ``os.path.expanduser`` to the
        captured path; the contract here is only that the ``.md`` file
        survived the regex (we assert via the extension, not the exact
        expanded prefix, so the test is hermetic to ``$HOME``)."""
        media, _ = BasePlatformAdapter.extract_media(
            "MEDIA:~/Documents/spec.md"
        )
        assert len(media) == 1
        assert media[0][0].endswith("/Documents/spec.md")
        assert media[0][1] is False

    def test_md_in_quoted_path_with_spaces_is_extracted(self) -> None:
        """The extractor already accepts spaces inside quoted paths —
        make sure the new ``md`` entry doesn't regress that branch."""
        media, _ = BasePlatformAdapter.extract_media(
            "MEDIA: '/tmp/my notes/spec final.md'"
        )
        assert media == [("/tmp/my notes/spec final.md", False)]

    def test_md_with_audio_as_voice_directive_preserves_voice_flag(self) -> None:
        """Voice directive bookkeeping must not regress when ``.md`` is
        the file: the flag is per-tuple and ``.md`` files shouldn't
        accidentally disable it (the extractor sets the flag from the
        directive, not the extension)."""
        media, _ = BasePlatformAdapter.extract_media(
            "[[audio_as_voice]]\nMEDIA:/tmp/x.md"
        )
        # Voice flag is True because the directive is present, even
        # though ``.md`` is obviously not a voice file. The extractor's
        # contract is "tag presence → True"; the dispatch layer decides
        # whether to honour it.
        assert media == [("/tmp/x.md", True)]

    def test_md_alongside_other_documents_in_same_response(self) -> None:
        """Multi-MEDIA replies must extract every tag — proves the
        ``md`` entry doesn't accidentally short-circuit the alternation."""
        content = (
            "Here are the deliverables:\n"
            "MEDIA:/tmp/summary.md\n"
            "MEDIA:/tmp/data.csv\n"
            "MEDIA:/tmp/notes.txt\n"
            "MEDIA:/tmp/report.pdf"
        )
        media, _ = BasePlatformAdapter.extract_media(content)
        assert [m[0] for m in media] == [
            "/tmp/summary.md",
            "/tmp/data.csv",
            "/tmp/notes.txt",
            "/tmp/report.pdf",
        ]

    def test_unrelated_extension_still_rejected(self) -> None:
        """Sanity: the allowlist remains an allowlist. ``.xyz`` is not
        a real document format and must not be extracted; otherwise the
        tightening that ea49b3862 introduced would be undone."""
        media, _ = BasePlatformAdapter.extract_media("MEDIA:/tmp/foo.xyz")
        assert media == []

    def test_md_in_path_segment_does_not_match_without_extension(self) -> None:
        """Guard against false-positive: a path that merely *contains*
        ``md`` as a directory name (no ``.md`` extension) must NOT be
        extracted as a Markdown file."""
        media, _ = BasePlatformAdapter.extract_media("MEDIA:/tmp/md/")
        assert media == []


# ──────────────────────────────────────────────────────────────────────
# 2 + 3. _TOOL_MEDIA_RE (both copies in gateway/run.py)
# ──────────────────────────────────────────────────────────────────────
# The regex appears twice inside ``gateway/run.py`` — first to build the
# history-dedupe set (line ~16522), second to re-extract MEDIA tags from
# tool messages when the final response is missing them (line ~16818).
# Both copies are identical and built inline (not exported), so we
# reconstruct the same string and assert on it directly. That keeps the
# test fully regex-focused without standing up a full GatewayRunner.
_TOOL_MEDIA_RE_PATTERN = (
    r'MEDIA:((?:/|~\/)\S+\.(?:png|jpe?g|gif|webp|'
    r'mp4|mov|avi|mkv|webm|ogg|opus|mp3|wav|m4a|'
    r'flac|epub|pdf|zip|rar|7z|docx?|xlsx?|pptx?|'
    r'txt|csv|md|apk|ipa))'
)
_TOOL_MEDIA_RE = re.compile(_TOOL_MEDIA_RE_PATTERN, re.IGNORECASE)


def _gateway_run_source() -> str:
    """Cache the gateway/run.py source so we can grep it for the two
    regex sites without importing the entire module (which pulls in
    heavy optional deps in CI)."""
    repo_root = Path(__file__).resolve().parents[2]
    return (repo_root / "gateway" / "run.py").read_text(encoding="utf-8")


class TestToolMediaRegexSupportsMd:
    """The history-side scan that gateway/run.py performs over tool
    messages must also extract ``.md`` paths — otherwise the dedupe
    set misses Markdown deliveries from prior turns and the streaming
    fallback never re-emits them."""

    def test_md_path_is_matched(self) -> None:
        match = _TOOL_MEDIA_RE.search("MEDIA:/tmp/spec.md")
        assert match is not None
        assert match.group(1) == "/tmp/spec.md"

    def test_uppercase_md_path_is_matched(self) -> None:
        match = _TOOL_MEDIA_RE.search("MEDIA:/tmp/README.MD")
        assert match is not None
        assert match.group(1) == "/tmp/README.MD"

    def test_tilde_md_path_is_matched(self) -> None:
        match = _TOOL_MEDIA_RE.search("MEDIA:~/docs/draft.md")
        assert match is not None
        assert match.group(1) == "~/docs/draft.md"

    def test_multiple_md_paths_in_tool_message_all_matched(self) -> None:
        """The dedupe loop calls ``finditer``; missing even one path
        would let the streaming fallback re-emit it as a fresh
        delivery on the next turn."""
        tool_content = (
            "Wrote two files:\n"
            "MEDIA:/tmp/part1.md\n"
            "MEDIA:/tmp/part2.md\n"
        )
        paths = [m.group(1) for m in _TOOL_MEDIA_RE.finditer(tool_content)]
        assert paths == ["/tmp/part1.md", "/tmp/part2.md"]

    def test_md_extracted_alongside_other_doc_extensions(self) -> None:
        tool_content = (
            "MEDIA:/tmp/notes.md\nMEDIA:/tmp/data.csv\nMEDIA:/tmp/log.txt"
        )
        paths = [m.group(1) for m in _TOOL_MEDIA_RE.finditer(tool_content)]
        assert paths == [
            "/tmp/notes.md",
            "/tmp/data.csv",
            "/tmp/log.txt",
        ]

    def test_unrelated_extension_still_rejected(self) -> None:
        """Same allowlist guarantee as the extract_media path."""
        assert _TOOL_MEDIA_RE.search("MEDIA:/tmp/foo.xyz") is None


# ──────────────────────────────────────────────────────────────────────
# Source-level guarantee: both copies in gateway/run.py include `md`
# ──────────────────────────────────────────────────────────────────────
class TestGatewayRunSourceContainsMdInBothRegexes:
    """Belt-and-braces guard. If a future cleanup factors the regex
    out into a constant, these checks will still hold (the source will
    contain the new symbol). If someone reverts ``md`` from either
    inline copy, this test fires."""

    def test_both_tool_media_regex_copies_include_md(self) -> None:
        src = _gateway_run_source()
        # The exact allowlist tail. There must be exactly two matches —
        # one per _TOOL_MEDIA_RE site — and each must include ``md``.
        hits = re.findall(r"txt\|csv\|md\|apk\|ipa", src)
        assert len(hits) == 2, (
            f"Expected exactly 2 _TOOL_MEDIA_RE copies with md, got {len(hits)}. "
            "If you refactored the regex into a single constant, "
            "update this assertion accordingly — but make sure md is still there."
        )

    def test_no_pre_30186_allowlist_tail_remains(self) -> None:
        """Regression-guard the other direction: the old ``txt|csv|apk|ipa``
        tail without ``md`` must NOT survive anywhere in the file."""
        src = _gateway_run_source()
        assert "txt|csv|apk|ipa" not in src, (
            "Pre-#30186 regex tail without md is still present in gateway/run.py"
        )


class TestPlatformsBaseSourceContainsMd:
    """Same source-level guarantee for the extract_media regex in
    ``gateway/platforms/base.py``."""

    def test_extract_media_pattern_includes_md(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        src = (repo_root / "gateway" / "platforms" / "base.py").read_text(
            encoding="utf-8"
        )
        assert "txt|csv|md|apk|ipa" in src
        assert "txt|csv|apk|ipa" not in src
