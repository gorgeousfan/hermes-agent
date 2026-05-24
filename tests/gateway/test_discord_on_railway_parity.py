"""Tests for Discord behavior when terminal.backend is set to railway."""

from __future__ import annotations

import importlib


def _rail_env_kwargs() -> dict:
    return {
        "project_id": "p_test",
        "service_id": "s_test",
        "environment_id": "e_test",
        "deployment_instance_id": "i_test",
        "identity_file": "/tmp/fake_id_ed25519",
        "cwd": "/data",
        "timeout": 30,
    }


def test_terminal_tool_routes_to_railway_when_configured(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "railway")
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p_test")
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "s_test")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "e_test")
    tt = importlib.import_module("tools.terminal_tool")
    rail_mod = importlib.import_module("tools.environments.railway")
    env = tt._create_environment(
        env_type="railway",
        image="",
        cwd="/data",
        timeout=30,
        ssh_config=None,
        container_config=None,
        local_config=None,
        task_id="t1",
    )
    assert isinstance(env, rail_mod.RailwayEnvironment)


def test_voice_channel_capability_handler_uses_profile_router(monkeypatch):
    monkeypatch.setenv("DISCORD_PROFILE_CATEGORIES", "111111111111111111:alpha")
    router_mod = importlib.import_module("gateway.platforms.profile_router")
    assert router_mod.route_container_to_profile("discord", "111111111111111111") == "alpha"


def test_thread_routing_preserves_session_inside_profile(monkeypatch):
    monkeypatch.setenv("DISCORD_PROFILE_CATEGORIES", "111:alpha")
    router_mod = importlib.import_module("gateway.platforms.profile_router")
    key_thread1 = router_mod.route_session_key("discord", "111", "thread:T1", "u1")
    key_thread2 = router_mod.route_session_key("discord", "111", "thread:T2", "u1")
    assert key_thread1 != key_thread2
    assert "alpha" in key_thread1


def test_railway_backend_is_used_when_terminal_backend_railway(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "railway")
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p_test")
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "s_test")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "e_test")
    rail_mod = importlib.import_module("tools.environments.railway")
    env = rail_mod.RailwayEnvironment(**_rail_env_kwargs())
    assert hasattr(env, "_run_railway_ssh")
