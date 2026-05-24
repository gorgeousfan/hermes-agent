"""Tests for the Railway terminal backend provider."""

from __future__ import annotations

import importlib
import inspect
import os
from pathlib import Path
from unittest.mock import patch

import pytest


def _import_railway_module():
    return importlib.import_module("tools.environments.railway")


def _import_base_env():
    base = importlib.import_module("tools.environments.base")
    return base.BaseEnvironment


def _required_kwargs() -> dict:
    return {
        "project_id": "p_test",
        "service_id": "s_test",
        "environment_id": "e_test",
        "deployment_instance_id": "i_test",
        "identity_file": str(Path("/tmp/fake_id_ed25519")),
        "cwd": "/data",
        "timeout": 30,
    }


def test_railway_environment_subclass_of_base():
    mod = _import_railway_module()
    cls = getattr(mod, "RailwayEnvironment", None)
    assert cls is not None, "RailwayEnvironment is not exported"
    assert issubclass(cls, _import_base_env())


def test_railway_environment_method_parity():
    mod = _import_railway_module()
    cls = mod.RailwayEnvironment
    base_required = ("_run_bash", "cleanup", "execute", "stop",
                     "init_session", "get_temp_dir")
    for name in base_required:
        assert hasattr(cls, name), f"RailwayEnvironment missing {name}"


def test_spawn_returns_structured_outcome():
    mod = _import_railway_module()
    env = mod.RailwayEnvironment(**_required_kwargs())
    with patch.object(env, "_run_railway_ssh") as run:
        run.return_value = {"output": "hello\n", "returncode": 0}
        result = env.execute("echo hello")
    assert isinstance(result, dict)
    assert "output" in result and "returncode" in result
    assert result["returncode"] == 0


def test_spawn_propagates_exit_code():
    mod = _import_railway_module()
    env = mod.RailwayEnvironment(**_required_kwargs())
    with patch.object(env, "_run_railway_ssh") as run:
        run.return_value = {"output": "boom\n", "returncode": 17}
        result = env.execute("false-command")
    assert result["returncode"] == 17


def test_spawn_respects_timeout():
    mod = _import_railway_module()
    kwargs = _required_kwargs()
    kwargs["timeout"] = 1
    env = mod.RailwayEnvironment(**kwargs)
    with patch.object(env, "_run_railway_ssh") as run:
        def fake_run(*a, **kw):
            assert kw.get("timeout") == 1
            return {"output": "", "returncode": 124}
        run.side_effect = fake_run
        result = env.execute("sleep 9999")
    assert result["returncode"] == 124


def test_failure_cause_categorizes_auth_vs_network_vs_deploy_missing():
    mod = _import_railway_module()
    failure_cls = getattr(mod, "RailwayFailure", None)
    assert failure_cls is not None
    expected = {"auth", "network", "deploy_missing", "volume_missing",
                "cli_missing", "invalid_config", "cancelled"}
    categories = getattr(mod, "RAILWAY_FAILURE_CATEGORIES", None)
    assert categories is not None
    assert set(categories) == expected


def test_retry_with_jittered_backoff_until_budget():
    mod = _import_railway_module()
    policy = getattr(mod, "RAILWAY_RETRY_POLICY", None)
    assert policy is not None
    assert policy["base_seconds"] == pytest.approx(0.25)
    assert policy["max_attempts"] == 4
    assert policy["only_for"] == ("network",)


def test_persistent_shell_carries_cwd_between_calls():
    mod = _import_railway_module()
    env = mod.RailwayEnvironment(**_required_kwargs())
    marker = env._cwd_marker
    with patch.object(env, "_run_railway_ssh") as run:
        run.return_value = {
            "output": f"\n{marker}/data/sub{marker}\n", "returncode": 0,
        }
        env.execute("cd sub")
    assert env.cwd.endswith("sub") or env.cwd == "/data/sub"


def test_persistent_shell_carries_env_between_calls():
    mod = _import_railway_module()
    env = mod.RailwayEnvironment(**_required_kwargs())
    with patch.object(env, "_run_railway_ssh") as run:
        run.return_value = {"output": "K=V\n", "returncode": 0}
        env.execute("export K=V")
        env.execute("echo $K")
    assert run.call_count >= 2


def test_persistent_shell_releases_on_disconnect():
    mod = _import_railway_module()
    env = mod.RailwayEnvironment(**_required_kwargs())
    env.cleanup()
    assert getattr(env, "_persistent_session", None) in (None, False)


def test_file_sync_round_trip(tmp_path):
    mod = _import_railway_module()
    env = mod.RailwayEnvironment(**_required_kwargs())
    src = tmp_path / "in.txt"
    src.write_bytes(b"hello\n")
    with patch.object(env, "_run_railway_ssh") as run:
        run.return_value = {"output": "", "returncode": 0}
        env.upload_file(str(src), "/data/x.txt")
        env.download_file("/data/x.txt", str(tmp_path / "out.txt"))


def test_file_sync_uses_volume_mount_path():
    mod = _import_railway_module()
    mount = getattr(mod, "RAILWAY_VOLUME_DEFAULT_MOUNT", None)
    assert mount == "/data"


def test_railway_module_file_size_within_budget():
    mod = _import_railway_module()
    src_path = Path(inspect.getsourcefile(mod))
    line_count = sum(1 for _ in src_path.open("r", encoding="utf-8"))
    assert line_count <= 200, f"railway.py is {line_count} lines"
