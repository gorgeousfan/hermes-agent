"""Tests for save_config_value() in cli.py — atomic write behavior."""

import yaml
from unittest.mock import MagicMock

import pytest


class TestSaveConfigValueAtomic:
    """save_config_value() must use atomic round-trip YAML updates."""

    @pytest.fixture
    def config_env(self, tmp_path, monkeypatch):
        """Isolated config environment with a writable config.yaml."""
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        config_path.write_text(yaml.dump({
            "model": {"default": "test-model", "provider": "openrouter"},
            "display": {"skin": "default"},
        }))
        monkeypatch.setattr("cli._hermes_home", hermes_home)
        return config_path

    def test_calls_roundtrip_yaml_update(self, config_env, monkeypatch):
        """save_config_value must preserve user-edited YAML structure."""
        mock_update = MagicMock()
        monkeypatch.setattr("utils.atomic_roundtrip_yaml_update", mock_update)

        from cli import save_config_value
        save_config_value("display.skin", "mono")

        mock_update.assert_called_once_with(config_env, "display.skin", "mono")

    def test_preserves_existing_keys(self, config_env):
        """Writing a new key must not clobber existing config entries."""
        from cli import save_config_value
        save_config_value("agent.max_turns", 50)

        result = yaml.safe_load(config_env.read_text())
        assert result["model"]["default"] == "test-model"
        assert result["model"]["provider"] == "openrouter"
        assert result["display"]["skin"] == "default"
        assert result["agent"]["max_turns"] == 50

    def test_creates_nested_keys(self, config_env):
        """Dot-separated paths create intermediate dicts as needed."""
        from cli import save_config_value
        save_config_value("auxiliary.compression.model", "google/gemini-3-flash-preview")

        result = yaml.safe_load(config_env.read_text())
        assert result["auxiliary"]["compression"]["model"] == "google/gemini-3-flash-preview"

    def test_overwrites_existing_value(self, config_env):
        """Updating an existing key replaces the value."""
        from cli import save_config_value
        save_config_value("display.skin", "ares")

        result = yaml.safe_load(config_env.read_text())
        assert result["display"]["skin"] == "ares"

    def test_preserves_env_ref_templates_in_unrelated_fields(self, config_env):
        """The /model --global persistence path must not inline env-backed secrets."""
        config_env.write_text(yaml.dump({
            "custom_providers": [{
                "name": "tuzi",
                "api_key": "${TU_ZI_API_KEY}",
                "model": "claude-opus-4-6",
            }],
            "model": {"default": "test-model", "provider": "openrouter"},
        }))

        from cli import save_config_value
        save_config_value("model.default", "doubao-pro")

        result = yaml.safe_load(config_env.read_text())
        assert result["model"]["default"] == "doubao-pro"
        assert result["custom_providers"][0]["api_key"] == "${TU_ZI_API_KEY}"

    def test_preserves_comments_after_config_mutation(self, config_env):
        """CLI config writes should not strip existing user comments."""
        config_env.write_text(
            "# user selected model\n"
            "model:\n"
            "  # keep this provider note\n"
            "  provider: openrouter\n"
            "display:\n"
            "  skin: default  # inline skin note\n",
            encoding="utf-8",
        )

        from cli import save_config_value
        save_config_value("display.skin", "mono")

        text = config_env.read_text(encoding="utf-8")
        result = yaml.safe_load(text)
        assert result["display"]["skin"] == "mono"
        assert "# user selected model" in text
        assert "# keep this provider note" in text
        assert "# inline skin note" in text

    def test_preserves_readable_unicode_after_config_mutation(self, config_env):
        """Non-ASCII prompts should remain readable instead of \\u-escaped."""
        config_env.write_text(
            "agent:\n"
            "  system_prompt: 你好，保持中文输出\n"
            "display:\n"
            "  skin: default\n",
            encoding="utf-8",
        )

        from cli import save_config_value
        save_config_value("display.skin", "mono")

        text = config_env.read_text(encoding="utf-8")
        result = yaml.safe_load(text)
        assert result["agent"]["system_prompt"] == "你好，保持中文输出"
        assert "你好，保持中文输出" in text
        assert "\\u4f60" not in text

    def test_file_not_truncated_on_error(self, config_env, monkeypatch):
        """If atomic_yaml_write raises, the original file is untouched."""
        original_content = config_env.read_text()

        def exploding_write(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("utils.atomic_roundtrip_yaml_update", exploding_write)

        from cli import save_config_value
        result = save_config_value("display.skin", "broken")

        assert result is False
        assert config_env.read_text() == original_content


# ---------------------------------------------------------------------------
# save_config_value_detailed -- structured success/failure for callers that
# must surface the failure reason to the user (issue #27660).
# ---------------------------------------------------------------------------


class TestSaveConfigValueDetailed:
    """Pin the tuple-return contract of the detailed variant."""

    @pytest.fixture
    def config_env(self, tmp_path, monkeypatch):
        from pathlib import Path
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        config_path.write_text(yaml.dump({"display": {"skin": "default"}}))
        monkeypatch.setattr("cli._hermes_home", hermes_home)
        return config_path

    def test_success_returns_true_none(self, config_env):
        from cli import save_config_value_detailed
        ok, err = save_config_value_detailed("display.skin", "mono")
        assert ok is True
        assert err is None

    def test_ruamel_missing_returns_actionable_error(self, config_env, monkeypatch):
        """When ruamel.yaml import fails, the error string must include the
        pip-install hint so users hit by #27660 can self-heal without
        reading source.
        """
        from utils import MissingYamlRoundtripDependency

        def exploding_import(*_args, **_kwargs):
            raise MissingYamlRoundtripDependency(
                ImportError("No module named 'ruamel'")
            )

        monkeypatch.setattr("utils.atomic_roundtrip_yaml_update", exploding_import)

        from cli import save_config_value_detailed
        ok, err = save_config_value_detailed("approvals.destructive_slash_confirm", False)
        assert ok is False
        assert err is not None
        assert "ruamel.yaml" in err
        assert "pip install ruamel.yaml" in err
        assert "No module named 'ruamel'" in err

    def test_generic_oserror_returns_typed_message(self, config_env, monkeypatch):
        """Non-dependency errors still produce a useful string."""
        def exploding(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr("utils.atomic_roundtrip_yaml_update", exploding)

        from cli import save_config_value_detailed
        ok, err = save_config_value_detailed("display.skin", "x")
        assert ok is False
        assert err is not None
        assert "OSError" in err
        assert "disk full" in err

    def test_bool_alias_still_returns_bool_only(self, config_env, monkeypatch):
        """The bool-only ``save_config_value`` alias must remain
        backward-compatible -- existing callers reading the return value
        as a plain ``bool`` keep working."""
        from utils import MissingYamlRoundtripDependency

        def exploding(*_a, **_k):
            raise MissingYamlRoundtripDependency(ImportError("missing"))

        monkeypatch.setattr("utils.atomic_roundtrip_yaml_update", exploding)

        from cli import save_config_value
        assert save_config_value("display.skin", "x") is False


# ---------------------------------------------------------------------------
# Direct tests for utils.atomic_roundtrip_yaml_update's new exception class.
# ---------------------------------------------------------------------------


class TestMissingYamlRoundtripDependency:
    def test_subclass_of_import_error(self):
        from utils import MissingYamlRoundtripDependency
        assert issubclass(MissingYamlRoundtripDependency, ImportError)

    def test_message_includes_install_hint(self):
        from utils import MissingYamlRoundtripDependency
        exc = MissingYamlRoundtripDependency(ImportError("No module named 'ruamel'"))
        assert "ruamel.yaml" in str(exc)
        assert "pip install ruamel.yaml" in str(exc)

    def test_preserves_original_import_error(self):
        from utils import MissingYamlRoundtripDependency
        original = ImportError("No module named 'ruamel'")
        exc = MissingYamlRoundtripDependency(original)
        assert exc.original is original

    def test_raised_when_ruamel_unimportable(self, monkeypatch, tmp_path):
        """End-to-end: simulate ruamel.yaml absent at import time and
        confirm atomic_roundtrip_yaml_update raises the typed wrapper."""
        import sys
        import importlib

        # Pretend the import fails by inserting a sentinel that raises.
        class _Blocker:
            def __getattr__(self, _name):
                raise ImportError("No module named 'ruamel'")

        # Drop any cached real module so the function-local import re-runs.
        for mod in list(sys.modules):
            if mod == "ruamel" or mod.startswith("ruamel."):
                monkeypatch.delitem(sys.modules, mod, raising=False)
        monkeypatch.setitem(sys.modules, "ruamel", _Blocker())

        from utils import atomic_roundtrip_yaml_update, MissingYamlRoundtripDependency

        with pytest.raises(MissingYamlRoundtripDependency) as excinfo:
            atomic_roundtrip_yaml_update(tmp_path / "x.yaml", "a.b", 1)

        assert "ruamel.yaml" in str(excinfo.value)
        assert "pip install ruamel.yaml" in str(excinfo.value)
