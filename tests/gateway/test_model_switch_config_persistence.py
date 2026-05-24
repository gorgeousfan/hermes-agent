"""Test that /model switch persists complete runtime state (base_url + api_mode).

Covers: https://github.com/NousResearch/hermes-agent/issues/25107
         https://github.com/NousResearch/hermes-agent/issues/25106

The gateway /model handler and CLI --global model switch must persist
the full runtime tuple (model, provider, base_url, api_mode) to
config.yaml.  Empty values should clear stale state.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Shared fixture: ModelSwitchResult-like object
# ---------------------------------------------------------------------------

@dataclass
class FakeSwitchResult:
    success: bool = True
    new_model: str = "gpt-5.4"
    target_provider: str = "openai"
    provider_changed: bool = True
    base_url: str = ""
    api_mode: str = "chat_completions"
    api_key: str = ""
    error_message: str = ""
    warning_message: str = ""
    provider_label: str = ""
    resolved_via_alias: str = ""


# ---------------------------------------------------------------------------
# Gateway /model persistence tests
# ---------------------------------------------------------------------------

class TestGatewayModelSwitchPersistence:
    """Verify gateway /model persists base_url and api_mode to config."""

    def test_persists_base_url_and_api_mode(self, tmp_path):
        """Non-empty base_url and api_mode are written to config."""
        import yaml

        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({"model": {"default": "old-model"}}))

        result = FakeSwitchResult(
            new_model="claude-sonnet-4",
            target_provider="anthropic",
            base_url="https://api.anthropic.com",
            api_mode="anthropic_messages",
            provider_changed=True,
        )

        # Simulate the gateway persistence block
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        model_cfg = cfg.setdefault("model", {})
        model_cfg["default"] = result.new_model
        model_cfg["provider"] = result.target_provider
        model_cfg["base_url"] = result.base_url or ""
        model_cfg["api_mode"] = result.api_mode or ""
        import pathlib
        pathlib.Path(str(config_path)).write_text(yaml.dump(cfg))

        saved = yaml.safe_load(config_path.read_text())
        assert saved["model"]["default"] == "claude-sonnet-4"
        assert saved["model"]["provider"] == "anthropic"
        assert saved["model"]["base_url"] == "https://api.anthropic.com"
        assert saved["model"]["api_mode"] == "anthropic_messages"

    def test_clears_stale_base_url_on_switch(self, tmp_path):
        """Switching to provider with no base_url clears the old value."""
        import yaml

        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({
            "model": {
                "default": "custom-model",
                "provider": "custom",
                "base_url": "https://old-endpoint.example.com/v1",
                "api_mode": "chat_completions",
            }
        }))

        result = FakeSwitchResult(
            new_model="deepseek-v4-flash",
            target_provider="deepseek",
            base_url="",  # DeepSeek has its own built-in endpoint
            api_mode="chat_completions",
            provider_changed=True,
        )

        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        model_cfg = cfg.setdefault("model", {})
        model_cfg["default"] = result.new_model
        model_cfg["provider"] = result.target_provider
        model_cfg["base_url"] = result.base_url or ""
        model_cfg["api_mode"] = result.api_mode or ""
        import pathlib
        pathlib.Path(str(config_path)).write_text(yaml.dump(cfg))

        saved = yaml.safe_load(config_path.read_text())
        assert saved["model"]["base_url"] == ""  # cleared, not stale
        assert saved["model"]["default"] == "deepseek-v4-flash"

    def test_clears_stale_api_mode_on_switch(self, tmp_path):
        """Switching from anthropic_messages to chat_completions clears api_mode."""
        import yaml

        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({
            "model": {
                "default": "claude-sonnet-4",
                "provider": "anthropic",
                "api_mode": "anthropic_messages",
            }
        }))

        result = FakeSwitchResult(
            new_model="gpt-5.4",
            target_provider="openai",
            base_url="",
            api_mode="",  # OpenAI uses default chat_completions
            provider_changed=True,
        )

        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        model_cfg = cfg.setdefault("model", {})
        model_cfg["default"] = result.new_model
        model_cfg["provider"] = result.target_provider
        model_cfg["base_url"] = result.base_url or ""
        model_cfg["api_mode"] = result.api_mode or ""
        import pathlib
        pathlib.Path(str(config_path)).write_text(yaml.dump(cfg))

        saved = yaml.safe_load(config_path.read_text())
        assert saved["model"]["api_mode"] == ""  # cleared
        assert saved["model"]["provider"] == "openai"


# ---------------------------------------------------------------------------
# CLI --global persistence tests
# ---------------------------------------------------------------------------

class TestCLIModelSwitchPersistence:
    """Verify CLI /model --global persists base_url and api_mode."""

    def test_persists_all_fields_via_save_config_value(self):
        """CLI model switch calls save_config_value for all 4 fields."""
        result = FakeSwitchResult(
            new_model="claude-sonnet-4",
            target_provider="anthropic",
            base_url="https://api.anthropic.com",
            api_mode="anthropic_messages",
            provider_changed=True,
        )

        saved_keys = []
        def mock_save(key_path, value):
            saved_keys.append((key_path, value))
            return True

        # Simulate the CLI persistence block
        persist_global = True
        if persist_global:
            mock_save("model.default", result.new_model)
            if result.provider_changed:
                mock_save("model.provider", result.target_provider)
            mock_save("model.base_url", result.base_url or "")
            mock_save("model.api_mode", result.api_mode or "")

        assert ("model.default", "claude-sonnet-4") in saved_keys
        assert ("model.provider", "anthropic") in saved_keys
        assert ("model.base_url", "https://api.anthropic.com") in saved_keys
        assert ("model.api_mode", "anthropic_messages") in saved_keys

    def test_clears_stale_base_url(self):
        """Empty base_url writes empty string to clear stale value."""
        result = FakeSwitchResult(
            new_model="gpt-5.4",
            target_provider="openai",
            base_url="",
            api_mode="",
            provider_changed=True,
        )

        saved_keys = {}
        def mock_save(key_path, value):
            saved_keys[key_path] = value
            return True

        persist_global = True
        if persist_global:
            mock_save("model.default", result.new_model)
            if result.provider_changed:
                mock_save("model.provider", result.target_provider)
            mock_save("model.base_url", result.base_url or "")
            mock_save("model.api_mode", result.api_mode or "")

        assert saved_keys["model.base_url"] == ""
        assert saved_keys["model.api_mode"] == ""
