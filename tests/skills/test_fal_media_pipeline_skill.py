from __future__ import annotations

from pathlib import Path

from agent.skill_utils import parse_frontmatter


SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / "optional-skills"
    / "creative"
    / "fal-media-pipeline"
    / "SKILL.md"
)


def test_fal_media_pipeline_skill_frontmatter():
    content = SKILL_PATH.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(content)

    assert frontmatter["name"] == "fal-media-pipeline"
    assert frontmatter["description"] == "Create images, edits, and videos with FAL."
    assert frontmatter["author"] == "Hermes Agent"
    assert body.strip()

    hermes_meta = frontmatter["metadata"]["hermes"]
    assert hermes_meta["requires_tools"] == [
        "image_generate",
        "image_edit",
        "video_generate",
    ]
    assert "FAL" in hermes_meta["tags"]


def test_fal_media_pipeline_skill_is_public_and_toolchain_first():
    content = SKILL_PATH.read_text(encoding="utf-8").lower()

    assert "curl" not in content
    assert "/home/" not in content
    assert "obsidian" not in content
    assert "cassie" not in content
    assert "cgic" not in content
    assert "pew" not in content
    assert "image_generate" in content
    assert "image_edit" in content
    assert "video_generate" in content
