"""Tests for terminal_tool Railway environment routing."""

from __future__ import annotations

import importlib
import os

import pytest


def _tt():
    return importlib.import_module("tools.terminal_tool")


def _rail():
    return importlib.import_module("tools.environments.railway")


def test_create_environment_railway_routes_to_railway_environment(monkeypatch):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p_test")
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "s_test")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "e_test")
    env = _tt()._create_environment(
        env_type="railway", image="", cwd="/data",
        timeout=30, ssh_config=None, container_config=None,
        local_config=None, task_id="t1",
    )
    assert isinstance(env, _rail().RailwayEnvironment)


def test_create_environment_railway_requires_project_service_environment(monkeypatch):
    for key in ("RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID",
                "RAILWAY_ENVIRONMENT_ID"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError) as exc:
        _tt()._create_environment(
            env_type="railway", image="", cwd="/data",
            timeout=30, ssh_config=None, container_config=None,
            local_config=None, task_id="t1",
        )
    msg = str(exc.value)
    assert "project_id" in msg.lower() or "RAILWAY_PROJECT_ID" in msg


def test_create_environment_railway_threads_config(monkeypatch):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p_cfg")
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "s_cfg")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "e_cfg")
    env = _tt()._create_environment(
        env_type="railway", image="", cwd="/data",
        timeout=30, ssh_config=None, container_config=None,
        local_config=None, task_id="t1",
    )
    assert env.project_id == "p_cfg"
    assert env.service_id == "s_cfg"
    assert env.environment_id == "e_cfg"


def test_create_environment_railway_identity_file_optional(monkeypatch):
    for key in ("RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID",
                "RAILWAY_ENVIRONMENT_ID"):
        monkeypatch.setenv(key, "x")
    monkeypatch.delenv("RAILWAY_IDENTITY_FILE", raising=False)
    env = _tt()._create_environment(
        env_type="railway", image="", cwd="/data",
        timeout=30, ssh_config=None, container_config=None,
        local_config=None, task_id="t1",
    )
    assert getattr(env, "identity_file", None) in (None, "")
