"""Test profile install/update preserves local skills via additive merge.

Covers: https://github.com/NousResearch/hermes-agent/issues/25120

_copy_dist_payload must NOT delete locally-installed skills when
installing or updating a profile distribution.  The additive merge
strategy overwrites only files present in the distribution source,
leaving all other local files intact.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manifest(name: str = "test-dist", version: str = "1.0.0"):
    """Create a minimal DistributionManifest."""
    from hermes_cli.profile_distribution import DistributionManifest
    return DistributionManifest(
        name=name,
        version=version,
        description="Test distribution",
        distribution_owned=["skills"],
    )


def _make_staged_tree(staged: Path, skills: list[str] = None):
    """Create a staged distribution source tree."""
    skills = skills or ["core-skill"]
    skills_dir = staged / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    for name in skills:
        sk = skills_dir / name
        sk.mkdir()
        (sk / "SKILL.md").write_text(f"---\nname: {name}\n---\nContent for {name}")
    (staged / "distribution.yaml").write_text(f"name: test-dist\nversion: 1.0.0\n")


def _make_target_tree(target: Path, local_skills: list[str] = None):
    """Create an existing target profile with local skills."""
    local_skills = local_skills or ["my-custom-skill"]
    skills_dir = target / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    for name in local_skills:
        sk = skills_dir / name
        sk.mkdir()
        (sk / "SKILL.md").write_text(f"---\nname: {name}\n---\nLocal content")
    # Minimal config to simulate an existing profile
    (target / "config.yaml").write_text("model:\n  default: gpt-5.4\n")


# ---------------------------------------------------------------------------
# Tests: _additive_copytree
# ---------------------------------------------------------------------------

class TestAdditiveCopytree:
    """Verify _additive_copytree preserves local files."""

    def test_copies_new_files(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        (src / "new.txt").write_text("hello")
        
        from hermes_cli.profile_distribution import _additive_copytree
        _additive_copytree(src, dst)
        
        assert (dst / "new.txt").read_text() == "hello"

    def test_preserves_existing_local_files(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        
        (src / "dist-skill").mkdir()
        (src / "dist-skill" / "SKILL.md").write_text("dist content")
        
        (dst / "local-skill").mkdir()
        (dst / "local-skill" / "SKILL.md").write_text("local content")
        
        from hermes_cli.profile_distribution import _additive_copytree
        _additive_copytree(src, dst)
        
        # Dist file added
        assert (dst / "dist-skill" / "SKILL.md").read_text() == "dist content"
        # Local file preserved!
        assert (dst / "local-skill" / "SKILL.md").read_text() == "local content"

    def test_overwrites_existing_dist_files(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        
        (src / "core-skill").mkdir()
        (src / "core-skill" / "SKILL.md").write_text("v2 content")
        
        (dst / "core-skill").mkdir()
        (dst / "core-skill" / "SKILL.md").write_text("v1 content")
        
        from hermes_cli.profile_distribution import _additive_copytree
        _additive_copytree(src, dst)
        
        assert (dst / "core-skill" / "SKILL.md").read_text() == "v2 content"

    def test_nested_directories_merge(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        
        (src / "skills" / "devops" / "tool-a").mkdir(parents=True)
        (src / "skills" / "devops" / "tool-a" / "SKILL.md").write_text("new tool")
        
        (dst / "skills" / "devops" / "tool-b").mkdir(parents=True)
        (dst / "skills" / "devops" / "tool-b" / "SKILL.md").write_text("old tool")
        
        from hermes_cli.profile_distribution import _additive_copytree
        _additive_copytree(src, dst)
        
        assert (dst / "skills" / "devops" / "tool-a" / "SKILL.md").read_text() == "new tool"
        assert (dst / "skills" / "devops" / "tool-b" / "SKILL.md").read_text() == "old tool"

    def test_ignores_excluded_files(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        
        (src / "auth.json").write_text("stolen!")
        (src / ".env").write_text("SECRET=val")
        (src / "good.txt").write_text("good")
        
        from hermes_cli.profile_distribution import _additive_copytree
        _additive_copytree(src, dst, ignore=lambda d, names: [n for n in names if n in {"auth.json", ".env"}])
        
        assert (dst / "good.txt").exists()
        assert not (dst / "auth.json").exists()
        assert not (dst / ".env").exists()


# ---------------------------------------------------------------------------
# Tests: _copy_dist_payload integration
# ---------------------------------------------------------------------------

class TestCopyDistPayload:
    """Verify _copy_dist_payload preserves local skills."""

    def test_install_preserves_local_skills(self, tmp_path):
        """Installing a distribution must not delete locally-installed skills."""
        from hermes_cli.profile_distribution import _copy_dist_payload

        staged = tmp_path / "staged"
        target = tmp_path / "target"
        staged.mkdir()
        target.mkdir()

        _make_staged_tree(staged, skills=["core-skill"])
        _make_target_tree(target, local_skills=["my-custom-skill"])
        manifest = _make_manifest()

        _copy_dist_payload(staged, target, manifest, preserve_config=True)

        # Distribution skill present
        assert (target / "skills" / "core-skill" / "SKILL.md").exists()
        # Local skill preserved!
        assert (target / "skills" / "my-custom-skill" / "SKILL.md").exists()
        assert (target / "skills" / "my-custom-skill" / "SKILL.md").read_text() == "---\nname: my-custom-skill\n---\nLocal content"

    def test_update_overwrites_dist_skills_not_local(self, tmp_path):
        """Updating should overwrite distribution skills but keep local ones."""
        from hermes_cli.profile_distribution import _copy_dist_payload

        staged = tmp_path / "staged"
        target = tmp_path / "target"
        staged.mkdir()
        target.mkdir()

        _make_staged_tree(staged, skills=["core-skill"])
        _make_target_tree(target, local_skills=["my-custom-skill", "core-skill"])
        
        # Simulate an older version of core-skill already installed
        (target / "skills" / "core-skill" / "SKILL.md").write_text("---\nname: core-skill\n---\nOld version")
        
        manifest = _make_manifest()
        _copy_dist_payload(staged, target, manifest, preserve_config=True)

        # core-skill updated
        assert (target / "skills" / "core-skill" / "SKILL.md").read_text() == "---\nname: core-skill\n---\nContent for core-skill"
        # Local skill still there
        assert (target / "skills" / "my-custom-skill" / "SKILL.md").read_text() == "---\nname: my-custom-skill\n---\nLocal content"

    def test_fresh_install_creates_dirs(self, tmp_path):
        """Fresh install to empty target should work like before."""
        from hermes_cli.profile_distribution import _copy_dist_payload

        staged = tmp_path / "staged"
        target = tmp_path / "target"
        staged.mkdir()

        _make_staged_tree(staged, skills=["core-skill"])
        manifest = _make_manifest()

        _copy_dist_payload(staged, target, manifest, preserve_config=False)

        assert (target / "skills" / "core-skill" / "SKILL.md").exists()
