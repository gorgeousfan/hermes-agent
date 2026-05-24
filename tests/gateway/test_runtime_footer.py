"""Unit tests for gateway.runtime_footer — the opt-in runtime-metadata footer
appended to final gateway replies."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from gateway.runtime_footer import (
    _home_relative_cwd,
    _model_short,
    build_footer_line,
    format_runtime_footer,
    resolve_footer_config,
)


# ---------------------------------------------------------------------------
# _model_short + _home_relative_cwd
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "model,expected",
    [
        ("openai/gpt-5.4", "gpt-5.4"),
        ("anthropic/claude-sonnet-4.6", "claude-sonnet-4.6"),
        ("gpt-5.4", "gpt-5.4"),
        ("", ""),
        (None, ""),
    ],
)
def test_model_short_drops_vendor_prefix(model, expected):
    assert _model_short(model) == expected


def test_home_relative_cwd_collapses_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sub = tmp_path / "projects" / "hermes"
    sub.mkdir(parents=True)
    result = _home_relative_cwd(str(sub))
    assert result == "~/projects/hermes"


def test_home_relative_cwd_leaves_abs_path_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "other"))
    result = _home_relative_cwd(str(tmp_path / "outside" / "dir"))
    assert result == str(tmp_path / "outside" / "dir").replace("\\", "/")


def test_home_relative_cwd_empty_returns_empty():
    assert _home_relative_cwd("") == ""


# ---------------------------------------------------------------------------
# format_runtime_footer
# ---------------------------------------------------------------------------

def test_format_footer_all_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path / "projects" / "hermes"))
    (tmp_path / "projects" / "hermes").mkdir(parents=True)
    out = format_runtime_footer(
        model="openrouter/openai/gpt-5.4",
        context_tokens=68000,
        context_length=100000,
        cwd=None,  # falls back to TERMINAL_CWD env var
        fields=("model", "context_pct", "cwd"),
    )
    assert out == "gpt-5.4 · ctx 68% · ~/projects/hermes"


def test_format_footer_skips_missing_context_length():
    out = format_runtime_footer(
        model="openai/gpt-5.4",
        context_tokens=500,
        context_length=None,
        cwd="/tmp/wd",
        fields=("model", "context_pct", "cwd"),
    )
    # context_pct dropped silently; no "?%" artifact
    assert "%" not in out
    assert "gpt-5.4" in out
    assert "tmp/wd" in out


def test_format_footer_context_pct_clamped_to_100():
    out = format_runtime_footer(
        model="m",
        context_tokens=500_000,  # way over
        context_length=100_000,
        cwd="",
        fields=("context_pct",),
    )
    assert out == "ctx 100%"


def test_format_footer_context_pct_never_negative():
    out = format_runtime_footer(
        model="m",
        context_tokens=-50,
        context_length=100,
        cwd="",
        fields=("context_pct",),
    )
    # Negative input => no field emitted (we require context_tokens >= 0)
    assert out == ""


def test_format_footer_empty_fields_returns_empty():
    out = format_runtime_footer(
        model="m", context_tokens=0, context_length=100,
        cwd="/x", fields=(),
    )
    assert out == ""


def test_format_footer_drops_cwd_when_empty(monkeypatch):
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    out = format_runtime_footer(
        model="openai/gpt-5.4",
        context_tokens=50, context_length=100,
        cwd="",
        fields=("model", "context_pct", "cwd"),
    )
    # cwd silently dropped; model + pct remain
    assert out == "gpt-5.4 · ctx 50%"


def test_format_footer_custom_field_order():
    out = format_runtime_footer(
        model="openai/gpt-5.4",
        context_tokens=50, context_length=100,
        cwd="/opt/project",
        fields=("context_pct", "model"),  # swapped + no cwd
    )
    assert out == "ctx 50% · gpt-5.4"


def test_format_footer_unknown_field_silently_ignored():
    out = format_runtime_footer(
        model="openai/gpt-5.4",
        context_tokens=50, context_length=100,
        cwd="/x",
        fields=("model", "bogus", "context_pct"),
    )
    assert out == "gpt-5.4 · ctx 50%"


# ---------------------------------------------------------------------------
# resolve_footer_config
# ---------------------------------------------------------------------------

def test_resolve_defaults_off_empty_config():
    cfg = resolve_footer_config({}, "telegram")
    assert cfg == {
        "enabled": False,
        "fields": ["model", "context_pct", "cwd"],
        "labels": {
            "context_pct": "ctx",
            "provider_window_pct": "5H",
            "provider_reset": "reset",
        },
        "timezone": None,
    }


def test_resolve_global_enable():
    user = {"display": {"runtime_footer": {"enabled": True}}}
    cfg = resolve_footer_config(user, "telegram")
    assert cfg["enabled"] is True
    assert cfg["fields"] == ["model", "context_pct", "cwd"]


def test_resolve_platform_override_wins():
    user = {
        "display": {
            "runtime_footer": {"enabled": True, "fields": ["model"]},
            "platforms": {
                "slack": {"runtime_footer": {"enabled": False}},
            },
        },
    }
    # Telegram picks up the global enable
    assert resolve_footer_config(user, "telegram")["enabled"] is True
    # Slack overrides to off
    assert resolve_footer_config(user, "slack")["enabled"] is False


def test_resolve_platform_can_add_fields_only():
    user = {
        "display": {
            "runtime_footer": {"enabled": True},
            "platforms": {
                "discord": {"runtime_footer": {"fields": ["context_pct"]}},
            },
        },
    }
    tg = resolve_footer_config(user, "telegram")
    assert tg["enabled"] is True
    assert tg["fields"] == ["model", "context_pct", "cwd"]
    dc = resolve_footer_config(user, "discord")
    assert dc["enabled"] is True
    assert dc["fields"] == ["context_pct"]


def test_resolve_ignores_malformed_config():
    # Non-dict runtime_footer shouldn't crash
    user = {"display": {"runtime_footer": "on"}}
    cfg = resolve_footer_config(user, "telegram")
    assert cfg["enabled"] is False


def test_format_footer_provider_window_pct_shows_remaining_quota_not_used():
    class Window:
        label = "Session"
        used_percent = 65.0
        reset_at = datetime(2026, 5, 20, 4, 26, tzinfo=timezone.utc)

    class Snapshot:
        windows = (Window(),)

    out = format_runtime_footer(
        model="openai-codex/gpt-5.5",
        context_tokens=41000,
        context_length=100000,
        fields=("model", "context_pct", "provider_window_pct", "provider_reset"),
        timezone_name="Europe/Berlin",
        provider_usage=Snapshot(),
    )
    assert out == "gpt-5.5 · ctx 41% · 5H 35% · reset 06:26"


def test_build_footer_fetches_provider_usage_when_provider_fields_requested(monkeypatch):
    class Window:
        label = "Session"
        used_percent = 39.0
        reset_at = "2026-05-20T04:26:00Z"

    class Snapshot:
        windows = (Window(),)

    import gateway.runtime_footer as runtime_footer

    monkeypatch.setattr(
        runtime_footer,
        "_fetch_provider_usage_cached",
        lambda provider, base_url, api_key: Snapshot(),
    )
    out = build_footer_line(
        user_config={
            "display": {
                "platforms": {
                    "telegram": {
                        "runtime_footer": {
                            "enabled": True,
                            "fields": ["model", "context_pct", "provider_window_pct", "provider_reset"],
                            "timezone": "Europe/Berlin",
                        }
                    }
                }
            }
        },
        platform_key="telegram",
        model="gpt-5.5",
        context_tokens=41000,
        context_length=100000,
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
    )
    assert out == "gpt-5.5 · ctx 41% · 5H 61% · reset 06:26"


def test_build_footer_uses_configured_provider_when_agent_result_omits_provider(monkeypatch):
    class Window:
        label = "Session"
        used_percent = 44.0
        reset_at = "2026-05-20T17:57:00Z"

    class Snapshot:
        windows = (Window(),)

    import gateway.runtime_footer as runtime_footer

    seen = {}

    def fake_fetch(provider, base_url, api_key):
        seen.update({"provider": provider, "base_url": base_url, "api_key": api_key})
        return Snapshot()

    monkeypatch.setattr(runtime_footer, "_fetch_provider_usage_cached", fake_fetch)

    out = build_footer_line(
        user_config={
            "model": {
                "provider": "openai-codex",
                "base_url": "https://chatgpt.com/backend-api/codex",
                "api_key": "from-config",
            },
            "display": {
                "runtime_footer": {
                    "enabled": True,
                    "fields": ["model", "provider_window_pct", "provider_reset"],
                    "timezone": "Europe/Berlin",
                }
            },
        },
        platform_key="telegram",
        model="gpt-5.5",
        context_tokens=0,
        context_length=None,
        provider="auto",
        base_url=None,
        api_key=None,
    )

    assert seen == {
        "provider": "openai-codex",
        "base_url": "https://chatgpt.com/backend-api/codex",
        "api_key": "from-config",
    }
    assert out == "gpt-5.5 · 5H 56% · reset 19:57"


def test_provider_usage_cache_does_not_pin_transient_missing_snapshot(monkeypatch):
    import gateway.runtime_footer as runtime_footer

    runtime_footer._USAGE_CACHE.clear()
    calls = {"count": 0}

    class Window:
        label = "Session"
        used_percent = 22.0
        reset_at = "2026-05-20T17:57:00Z"

    class Snapshot:
        windows = (Window(),)

    def fake_fetch_account_usage(provider, base_url=None, api_key=None):
        calls["count"] += 1
        return None if calls["count"] == 1 else Snapshot()

    monkeypatch.setattr(
        "agent.account_usage.fetch_account_usage",
        fake_fetch_account_usage,
    )

    assert runtime_footer._fetch_provider_usage_cached("openai-codex", None, None) is None
    assert runtime_footer._fetch_provider_usage_cached("openai-codex", None, None) is not None
    assert calls["count"] == 2


# ---------------------------------------------------------------------------
# build_footer_line — top-level entry point used by gateway/run.py
# ---------------------------------------------------------------------------

def test_build_footer_empty_when_disabled():
    out = build_footer_line(
        user_config={},
        platform_key="telegram",
        model="openai/gpt-5.4",
        context_tokens=10, context_length=100,
        cwd="/tmp",
    )
    assert out == ""


def test_build_footer_returns_rendered_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = build_footer_line(
        user_config={"display": {"runtime_footer": {"enabled": True}}},
        platform_key="telegram",
        model="openai/gpt-5.4",
        context_tokens=25, context_length=100,
        cwd=str(tmp_path / "proj"),
    )
    (tmp_path / "proj").mkdir(exist_ok=True)
    assert "gpt-5.4" in out
    assert "25%" in out


def test_build_footer_per_platform_off_suppresses():
    user = {
        "display": {
            "runtime_footer": {"enabled": True},
            "platforms": {"slack": {"runtime_footer": {"enabled": False}}},
        },
    }
    out = build_footer_line(
        user_config=user,
        platform_key="slack",
        model="openai/gpt-5.4",
        context_tokens=10, context_length=100,
        cwd="/tmp",
    )
    assert out == ""


def test_build_footer_no_data_returns_empty_even_when_enabled():
    # Enabled, but context_length is None AND cwd empty AND model empty ⇒ no fields
    out = build_footer_line(
        user_config={"display": {"runtime_footer": {"enabled": True}}},
        platform_key="telegram",
        model="",
        context_tokens=0, context_length=None,
        cwd="",
    )
    # With no TERMINAL_CWD env either
    if not os.environ.get("TERMINAL_CWD"):
        assert out == ""
