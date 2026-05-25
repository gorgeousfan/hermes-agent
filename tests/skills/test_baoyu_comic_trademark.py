"""
Regression tests for hermes-agent issue #28890.

The `skills/creative/baoyu-comic` skill previously shipped reference
templates that named trademarked manga characters (Doraemon / Nobita /
Gian / Shizuka) as the default cast for the ``ohmsha`` preset, which
caused Hermes-generated comics to redistribute those characters.

These tests guarantee that:
  - no shipped file in the skill mentions the previously-listed
    trademarked character names, AND
  - the top-level SKILL.md carries a written IP policy forbidding
    trademarked characters.

The negative-list inside the IP policy paragraph in SKILL.md is the
only allowed mention (it tells the model what NOT to do); everywhere
else the names must be absent.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "creative" / "baoyu-comic"

# Names / franchises that must not appear as default characters or
# reference templates anywhere in the skill.
FORBIDDEN_TERMS = [
    "Doraemon",
    "哆啦A梦",
    "哆啦 A 梦",
    "Nobita",
    "大雄",
    "Shizuka",
    "静香",
    "Gian",
    "胖虎",
    "Suneo",
    "小夫",
]


def _iter_skill_files() -> list[Path]:
    return [p for p in SKILL_DIR.rglob("*") if p.is_file() and p.suffix in {".md", ".txt"}]


def test_skill_dir_exists() -> None:
    assert SKILL_DIR.is_dir(), f"missing skill dir: {SKILL_DIR}"


def test_skill_md_has_ip_policy() -> None:
    src = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    # Header from the rewrite must be present.
    assert "Intellectual Property Policy" in src, (
        "SKILL.md must include an 'Intellectual Property Policy' section "
        "instructing the model to use only original characters (see #28890)."
    )
    assert "original characters" in src.lower()


@pytest.mark.parametrize("term", FORBIDDEN_TERMS)
def test_no_trademarked_character_outside_policy(term: str) -> None:
    """No skill file may mention the forbidden names except inside the
    SKILL.md IP-policy negative list."""
    offenders: list[tuple[Path, int, str]] = []
    for path in _iter_skill_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if term not in line:
                continue
            # The only allowed location is the IP-policy negative list
            # paragraph in SKILL.md, which says "Do NOT ... including
            # but not limited to ...". Permit those lines only.
            if path.name == "SKILL.md" and re.search(
                r"Do NOT|including but not limited to|trademarked", line
            ):
                continue
            offenders.append((path.relative_to(SKILL_DIR), lineno, line.strip()))
    assert not offenders, (
        f"Trademarked term {term!r} still present in baoyu-comic skill files: "
        + "; ".join(f"{p}:{ln}: {snippet}" for p, ln, snippet in offenders)
    )
