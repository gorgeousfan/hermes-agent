"""Tests for `hermes rotation` cooldown visibility and reset commands."""

from __future__ import annotations

import types
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = tmp_path / ".hermes"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return tmp_path


class TestRotationCommand:
    def test_list_shows_cooling_down_provider(self, isolated_home, capsys):
        from agent.provider_rotation import ProviderRotationState
        from hermes_cli.rotation_cmd import cmd_rotation_list

        ProviderRotationState.load().mark_unavailable(
            provider="openai-codex",
            model="gpt-5.3-codex",
            reason="rate_limit",
            cooldown_seconds=3600,
            now=1000.0,
        )

        cmd_rotation_list(types.SimpleNamespace(now=1200.0))

        out = capsys.readouterr().out
        assert "openai-codex" in out
        assert "gpt-5.3-codex" in out
        assert "rate_limit" in out
        assert "cooling down" in out

    def test_reset_provider_removes_matching_state(self, isolated_home, capsys):
        from agent.provider_rotation import ProviderRotationState
        from hermes_cli.rotation_cmd import cmd_rotation_reset

        ProviderRotationState.load().mark_unavailable(
            provider="anthropic",
            model="claude-sonnet-4-6",
            reason="billing",
            cooldown_seconds=3600,
            now=1000.0,
        )

        cmd_rotation_reset(types.SimpleNamespace(provider="anthropic", model=None))

        out = capsys.readouterr().out
        assert "Reset 1" in out
        assert not ProviderRotationState.load().is_unavailable(
            "anthropic", "claude-sonnet-4-6", now=1200.0
        )

    def test_clear_removes_all_state(self, isolated_home, capsys):
        from agent.provider_rotation import ProviderRotationState
        from hermes_cli.rotation_cmd import cmd_rotation_clear

        state = ProviderRotationState.load()
        state.mark_unavailable(
            provider="openai-codex",
            model="gpt-5.3-codex",
            reason="rate_limit",
            cooldown_seconds=3600,
            now=1000.0,
        )
        state = ProviderRotationState.load()
        state.mark_unavailable(
            provider="anthropic",
            model="claude-sonnet-4-6",
            reason="billing",
            cooldown_seconds=3600,
            now=1000.0,
        )

        cmd_rotation_clear(types.SimpleNamespace())

        out = capsys.readouterr().out
        assert "Cleared 2" in out
        assert ProviderRotationState.load().unavailable == {}
