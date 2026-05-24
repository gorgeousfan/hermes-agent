"""Tests for agent.skill_utils.get_external_skills_dirs cache mtime invalidation."""
import os
from pathlib import Path

import pytest
import yaml

from agent import skill_utils


@pytest.fixture
def cleared_cache():
    skill_utils._external_dirs_cache_clear()
    yield
    skill_utils._external_dirs_cache_clear()


def _write_config(path, dirs):
    path.write_text(
        yaml.dump({"skills": {"external_dirs": [str(d) for d in dirs]}}),
        encoding="utf-8",
    )


class TestExternalDirsCache:
    def test_returns_empty_when_config_missing(self, tmp_path, monkeypatch, cleared_cache):
        monkeypatch.setattr(skill_utils, "get_config_path", lambda: tmp_path / "missing.yaml")
        assert skill_utils.get_external_skills_dirs() == []

    def test_caches_first_call_result(self, tmp_path, monkeypatch, cleared_cache):
        config = tmp_path / "config.yaml"
        ext = tmp_path / "ext"
        ext.mkdir()
        _write_config(config, [ext])
        monkeypatch.setattr(skill_utils, "get_config_path", lambda: config)
        monkeypatch.setattr(skill_utils, "get_skills_dir", lambda: tmp_path / "local")

        # Prime the cache.
        first = skill_utils.get_external_skills_dirs()

        # If the second call hits the cache, yaml_load must NOT be invoked again.
        def _explode(_content):
            raise AssertionError("yaml_load should not be called on cache hit")

        monkeypatch.setattr(skill_utils, "yaml_load", _explode)
        second = skill_utils.get_external_skills_dirs()
        assert first == [ext]
        assert second == [ext]

    def test_returns_copy_so_mutation_does_not_poison_cache(self, tmp_path, monkeypatch, cleared_cache):
        config = tmp_path / "config.yaml"
        ext = tmp_path / "ext"
        ext.mkdir()
        _write_config(config, [ext])
        monkeypatch.setattr(skill_utils, "get_config_path", lambda: config)
        monkeypatch.setattr(skill_utils, "get_skills_dir", lambda: tmp_path / "local")
        first = skill_utils.get_external_skills_dirs()
        poison = Path("/tmp/poisoned")
        first.append(poison)
        second = skill_utils.get_external_skills_dirs()
        assert poison not in second
        assert second == [ext]

    def test_cache_invalidates_when_mtime_changes(self, tmp_path, monkeypatch, cleared_cache):
        config = tmp_path / "config.yaml"
        ext_a = tmp_path / "ext_a"
        ext_b = tmp_path / "ext_b"
        ext_a.mkdir()
        ext_b.mkdir()
        monkeypatch.setattr(skill_utils, "get_config_path", lambda: config)
        monkeypatch.setattr(skill_utils, "get_skills_dir", lambda: tmp_path / "local")

        _write_config(config, [ext_a])
        first = skill_utils.get_external_skills_dirs()

        _write_config(config, [ext_b])
        os.utime(config, (config.stat().st_atime + 10, config.stat().st_mtime + 10))
        second = skill_utils.get_external_skills_dirs()

        assert first == [ext_a]
        assert second == [ext_b]

    def test_cache_clear_forces_reread(self, tmp_path, monkeypatch, cleared_cache):
        config = tmp_path / "config.yaml"
        ext = tmp_path / "ext"
        ext.mkdir()
        _write_config(config, [ext])
        monkeypatch.setattr(skill_utils, "get_config_path", lambda: config)
        monkeypatch.setattr(skill_utils, "get_skills_dir", lambda: tmp_path / "local")
        skill_utils.get_external_skills_dirs()
        assert skill_utils._EXTERNAL_DIRS_CACHE
        skill_utils._external_dirs_cache_clear()
        assert not skill_utils._EXTERNAL_DIRS_CACHE

    def test_corrupt_yaml_returns_empty(self, tmp_path, monkeypatch, cleared_cache):
        config = tmp_path / "config.yaml"
        config.write_text("::not:valid\n[unclosed")
        monkeypatch.setattr(skill_utils, "get_config_path", lambda: config)
        assert skill_utils.get_external_skills_dirs() == []

    def test_skips_nonexistent_dirs(self, tmp_path, monkeypatch, cleared_cache):
        config = tmp_path / "config.yaml"
        good = tmp_path / "good"
        good.mkdir()
        bad = tmp_path / "ghost"
        _write_config(config, [good, bad])
        monkeypatch.setattr(skill_utils, "get_config_path", lambda: config)
        monkeypatch.setattr(skill_utils, "get_skills_dir", lambda: tmp_path / "local")
        result = skill_utils.get_external_skills_dirs()
        assert good in result
        assert bad not in result
