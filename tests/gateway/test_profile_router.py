"""Tests for the multi-profile gateway routing boundary."""

from __future__ import annotations

import importlib

import pytest


_ADAPTER_CONTAINER_PAIRS = (
    ("discord", "111111111111111111"),
    ("telegram", "-1001234567890"),
    ("slack", "T0AAAAAAA"),
    ("whatsapp", "120363111111111111@g.us"),
    ("signal", "GROUP_alpha"),
    ("email", "team@example.com"),
    ("sms", "+155****4567"),
    ("matrix", "!space:matrix.example.org"),
    ("mattermost", "team_alpha"),
    ("homeassistant", "https://ha.local:8123"),
    ("dingtalk", "ding_org_alpha"),
    ("feishu", "feishu_tenant_alpha"),
    ("wecom", "wecom_corp_alpha"),
    ("bluebubbles", "iMessage;-;chat_alpha"),
    ("weixin", "gh_alpha_wx_account"),
    ("api_server", "tenant_alpha"),
    ("webhook", "wh_alpha_endpoint"),
)


def _import_router():
    return importlib.import_module("gateway.platforms.profile_router")


def _env_name(adapter_id: str, container_id: str) -> str:
    safe_container = "".join(c if c.isalnum() else "_" for c in container_id)
    return f"{adapter_id.upper()}_PROFILE_{safe_container.upper()}"


def test_discord_category_binds_to_profile(monkeypatch):
    monkeypatch.setenv(
        "DISCORD_PROFILE_CATEGORIES",
        "111111111111111111:alpha,222222222222222222:beta",
    )
    mod = _import_router()
    assert mod.route_container_to_profile("discord", "111111111111111111") == "alpha"
    assert mod.route_container_to_profile("discord", "222222222222222222") == "beta"


def test_channel_session_isolated_inside_profile(monkeypatch):
    monkeypatch.setenv("DISCORD_PROFILE_CATEGORIES", "111111111111111111:alpha")
    mod = _import_router()
    key_a = mod.route_session_key("discord", "111111111111111111", "ch_aaa", "user_one")
    key_b = mod.route_session_key("discord", "111111111111111111", "ch_bbb", "user_one")
    assert key_a != key_b
    assert "alpha" in key_a and "alpha" in key_b


def test_dm_uses_default_profile(monkeypatch):
    monkeypatch.delenv("DISCORD_PROFILE_CATEGORIES", raising=False)
    mod = _import_router()
    assert mod.route_container_to_profile("discord", None) is None


@pytest.mark.parametrize("adapter_id, container_id", _ADAPTER_CONTAINER_PAIRS)
def test_adapter_routes_container_to_profile(adapter_id, container_id, monkeypatch):
    monkeypatch.setenv(_env_name(adapter_id, container_id), "alpha")
    mod = _import_router()
    profile = mod.route_container_to_profile(adapter_id, container_id)
    assert profile == "alpha", f"{adapter_id}/{container_id} -> {profile}"


@pytest.mark.parametrize("adapter_id, container_id", _ADAPTER_CONTAINER_PAIRS)
def test_session_key_carries_profile_and_adapter(adapter_id, container_id, monkeypatch):
    monkeypatch.setenv(_env_name(adapter_id, container_id), "beta")
    mod = _import_router()
    key = mod.route_session_key(adapter_id, container_id, "conv1", "user1")
    assert "beta" in key
    assert adapter_id in key


def test_route_returns_none_when_no_mapping(monkeypatch):
    for adapter in ("discord", "slack", "telegram"):
        monkeypatch.delenv(f"{adapter.upper()}_PROFILE_CATEGORIES", raising=False)
    mod = _import_router()
    assert mod.route_container_to_profile("discord", "999") is None


def test_invalid_profile_name_is_rejected(monkeypatch):
    monkeypatch.setenv("DISCORD_PROFILE_CATEGORIES", "111:NOT VALID PROFILE")
    mod = _import_router()
    with pytest.raises(ValueError):
        mod.route_container_to_profile("discord", "111")
