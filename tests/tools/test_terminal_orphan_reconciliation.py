"""Tests for startup orphan-sandbox reconciliation in ``tools.terminal_tool``.

Covers the helpers added for issue #28807:
- ``_reconcile_orphaned_docker_sandboxes``
- ``_reconcile_orphaned_daytona_sandboxes``
- ``_reconcile_orphaned_sandboxes`` (dispatch by ``env_type``)
- The one-shot wiring inside ``_start_cleanup_thread``
"""

import importlib
import subprocess
import sys
import types

import pytest


terminal_tool = importlib.import_module("tools.terminal_tool")


# ---------- helpers ----------


def _stub_get_env_config(monkeypatch, env_type: str) -> None:
    monkeypatch.setattr(
        terminal_tool, "_get_env_config", lambda: {"env_type": env_type}
    )


def _record_subprocess(monkeypatch, run_returns):
    """Patch ``terminal_tool.subprocess.run`` to record calls.

    ``run_returns`` is a list of ``CompletedProcess`` (or exceptions to raise)
    consumed in order. Returns the call-recording list.
    """
    calls = []
    iterator = iter(run_returns)

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        try:
            nxt = next(iterator)
        except StopIteration:
            raise AssertionError(
                f"subprocess.run called more times than fixture provided. "
                f"Extra call args={args!r} kwargs={kwargs!r}"
            )
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    monkeypatch.setattr(terminal_tool.subprocess, "run", fake_run)
    return calls


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


# ---------- Docker reconciliation ----------


class TestDockerReconciliation:
    def test_reaps_listed_containers(self, monkeypatch, caplog):
        monkeypatch.setattr(
            "tools.environments.docker.find_docker", lambda: "/usr/bin/docker"
        )
        calls = _record_subprocess(
            monkeypatch,
            [
                _completed(stdout="hermes-aaaaaaaa\nhermes-bbbbbbbb\n"),
                _completed(),
                _completed(),
            ],
        )
        with caplog.at_level("INFO", logger=terminal_tool.logger.name):
            terminal_tool._reconcile_orphaned_docker_sandboxes()

        # First call: docker ps with name filter
        ps_cmd = calls[0][0][0]
        assert ps_cmd[:3] == ["/usr/bin/docker", "ps", "-a"]
        assert "name=^hermes-" in ps_cmd
        assert "{{.Names}}" in ps_cmd

        # Two rm -f calls, one per container
        rm_cmds = [c[0][0] for c in calls[1:]]
        assert rm_cmds[0] == ["/usr/bin/docker", "rm", "-f", "hermes-aaaaaaaa"]
        assert rm_cmds[1] == ["/usr/bin/docker", "rm", "-f", "hermes-bbbbbbbb"]
        assert any("reaping 2 Docker" in r.message for r in caplog.records)

    def test_no_docker_binary_noops(self, monkeypatch):
        monkeypatch.setattr("tools.environments.docker.find_docker", lambda: None)
        calls = _record_subprocess(monkeypatch, [])  # any call → AssertionError
        terminal_tool._reconcile_orphaned_docker_sandboxes()
        assert calls == []

    def test_empty_listing_noops(self, monkeypatch):
        monkeypatch.setattr(
            "tools.environments.docker.find_docker", lambda: "/usr/bin/docker"
        )
        calls = _record_subprocess(monkeypatch, [_completed(stdout="\n  \n")])
        terminal_tool._reconcile_orphaned_docker_sandboxes()
        # Only the ps call was made — no rm.
        assert len(calls) == 1

    def test_ps_subprocess_error_swallowed(self, monkeypatch, caplog):
        monkeypatch.setattr(
            "tools.environments.docker.find_docker", lambda: "/usr/bin/docker"
        )
        _record_subprocess(monkeypatch, [OSError("docker daemon down")])
        with caplog.at_level("WARNING", logger=terminal_tool.logger.name):
            terminal_tool._reconcile_orphaned_docker_sandboxes()
        assert any("docker ps failed" in r.message for r in caplog.records)

    def test_ps_nonzero_exit_swallowed(self, monkeypatch, caplog):
        monkeypatch.setattr(
            "tools.environments.docker.find_docker", lambda: "/usr/bin/docker"
        )
        _record_subprocess(
            monkeypatch,
            [_completed(returncode=1, stderr="Cannot connect to daemon")],
        )
        with caplog.at_level("WARNING", logger=terminal_tool.logger.name):
            terminal_tool._reconcile_orphaned_docker_sandboxes()
        assert any("exited 1" in r.message for r in caplog.records)

    def test_rm_failure_does_not_abort_others(self, monkeypatch, caplog):
        monkeypatch.setattr(
            "tools.environments.docker.find_docker", lambda: "/usr/bin/docker"
        )
        calls = _record_subprocess(
            monkeypatch,
            [
                _completed(stdout="hermes-aaa\nhermes-bbb\n"),
                OSError("rm aaa exploded"),  # first rm fails
                _completed(),  # second rm should still run
            ],
        )
        with caplog.at_level("WARNING", logger=terminal_tool.logger.name):
            terminal_tool._reconcile_orphaned_docker_sandboxes()
        # 1 ps + 2 rm attempts
        assert len(calls) == 3
        assert any("failed to remove container hermes-aaa" in r.message for r in caplog.records)


# ---------- Daytona reconciliation ----------


class _FakeSandbox:
    def __init__(self, name: str, sid: str = "sb-id"):
        self.name = name
        self.id = sid


class _FakeDaytonaClient:
    def __init__(self, sandboxes=None, list_raises=None, delete_raises=None):
        self._sandboxes = sandboxes or []
        self._list_raises = list_raises
        self._delete_raises = delete_raises
        self.deleted = []

    def list(self):
        if self._list_raises:
            raise self._list_raises
        return iter(self._sandboxes)

    def delete(self, sandbox):
        self.deleted.append(sandbox)
        if self._delete_raises:
            raise self._delete_raises


def _install_fake_daytona_module(monkeypatch, client_factory):
    """Install a fake ``daytona`` module so ``from daytona import Daytona`` works."""
    fake_mod = types.ModuleType("daytona")
    fake_mod.Daytona = client_factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "daytona", fake_mod)


class TestDaytonaReconciliation:
    def test_reaps_hermes_sandboxes(self, monkeypatch, caplog):
        sandboxes = [
            _FakeSandbox("hermes-task1"),
            _FakeSandbox("hermes-task2"),
            _FakeSandbox("other-project-sb"),
        ]
        client = _FakeDaytonaClient(sandboxes=sandboxes)
        _install_fake_daytona_module(monkeypatch, lambda: client)

        with caplog.at_level("INFO", logger=terminal_tool.logger.name):
            terminal_tool._reconcile_orphaned_daytona_sandboxes()

        assert [s.name for s in client.deleted] == ["hermes-task1", "hermes-task2"]
        assert any("reaping 2 Daytona" in r.message for r in caplog.records)

    def test_no_daytona_sdk_noops(self, monkeypatch):
        # Force ImportError by absent daytona module.
        monkeypatch.setitem(sys.modules, "daytona", None)
        # Should not raise.
        terminal_tool._reconcile_orphaned_daytona_sandboxes()

    def test_client_init_failure_swallowed(self, monkeypatch, caplog):
        def boom():
            raise RuntimeError("DAYTONA_API_KEY missing")

        _install_fake_daytona_module(monkeypatch, boom)
        with caplog.at_level("WARNING", logger=terminal_tool.logger.name):
            terminal_tool._reconcile_orphaned_daytona_sandboxes()
        assert any("client init failed" in r.message for r in caplog.records)

    def test_list_failure_swallowed(self, monkeypatch, caplog):
        client = _FakeDaytonaClient(list_raises=RuntimeError("network unreachable"))
        _install_fake_daytona_module(monkeypatch, lambda: client)
        with caplog.at_level("WARNING", logger=terminal_tool.logger.name):
            terminal_tool._reconcile_orphaned_daytona_sandboxes()
        assert any("Daytona list failed" in r.message for r in caplog.records)
        assert client.deleted == []

    def test_delete_failure_does_not_abort_others(self, monkeypatch, caplog):
        sandboxes = [_FakeSandbox("hermes-a"), _FakeSandbox("hermes-b")]
        client = _FakeDaytonaClient(
            sandboxes=sandboxes, delete_raises=RuntimeError("forbidden")
        )
        _install_fake_daytona_module(monkeypatch, lambda: client)
        with caplog.at_level("WARNING", logger=terminal_tool.logger.name):
            terminal_tool._reconcile_orphaned_daytona_sandboxes()
        # Both attempted even though both raised.
        assert [s.name for s in client.deleted] == ["hermes-a", "hermes-b"]
        assert (
            sum("failed to delete Daytona" in r.message for r in caplog.records) == 2
        )

    def test_no_hermes_sandboxes_noops(self, monkeypatch):
        client = _FakeDaytonaClient(sandboxes=[_FakeSandbox("other-sb")])
        _install_fake_daytona_module(monkeypatch, lambda: client)
        terminal_tool._reconcile_orphaned_daytona_sandboxes()
        assert client.deleted == []


# ---------- Dispatch ----------


class TestDispatch:
    def test_docker_env_dispatches_to_docker(self, monkeypatch):
        _stub_get_env_config(monkeypatch, "docker")
        called = []
        monkeypatch.setattr(
            terminal_tool, "_reconcile_orphaned_docker_sandboxes",
            lambda: called.append("docker"),
        )
        monkeypatch.setattr(
            terminal_tool, "_reconcile_orphaned_daytona_sandboxes",
            lambda: called.append("daytona"),
        )
        terminal_tool._reconcile_orphaned_sandboxes()
        assert called == ["docker"]

    def test_daytona_env_dispatches_to_daytona(self, monkeypatch):
        _stub_get_env_config(monkeypatch, "daytona")
        called = []
        monkeypatch.setattr(
            terminal_tool, "_reconcile_orphaned_docker_sandboxes",
            lambda: called.append("docker"),
        )
        monkeypatch.setattr(
            terminal_tool, "_reconcile_orphaned_daytona_sandboxes",
            lambda: called.append("daytona"),
        )
        terminal_tool._reconcile_orphaned_sandboxes()
        assert called == ["daytona"]

    @pytest.mark.parametrize("env_type", ["local", "modal", "ssh", "vercel_sandbox"])
    def test_other_envs_skip_reconciliation(self, monkeypatch, env_type):
        _stub_get_env_config(monkeypatch, env_type)
        called = []
        monkeypatch.setattr(
            terminal_tool, "_reconcile_orphaned_docker_sandboxes",
            lambda: called.append("docker"),
        )
        monkeypatch.setattr(
            terminal_tool, "_reconcile_orphaned_daytona_sandboxes",
            lambda: called.append("daytona"),
        )
        terminal_tool._reconcile_orphaned_sandboxes()
        assert called == []


# ---------- One-shot wiring inside _start_cleanup_thread ----------


class TestStartCleanupThreadWiring:
    @pytest.fixture(autouse=True)
    def _reset_module_state(self, monkeypatch):
        """Reset the one-shot flag + cleanup thread globals around each test."""
        monkeypatch.setattr(terminal_tool, "_orphan_reconciliation_done", False)
        monkeypatch.setattr(terminal_tool, "_cleanup_thread", None)
        monkeypatch.setattr(terminal_tool, "_cleanup_running", False)
        # Prevent real thread creation
        monkeypatch.setattr(
            terminal_tool.threading, "Thread",
            lambda *a, **kw: types.SimpleNamespace(start=lambda: None, is_alive=lambda: False),
        )
        yield

    def test_runs_reconciliation_on_first_call(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            terminal_tool, "_reconcile_orphaned_sandboxes",
            lambda: called.append(True),
        )
        terminal_tool._start_cleanup_thread()
        assert called == [True]
        assert terminal_tool._orphan_reconciliation_done is True

    def test_skips_reconciliation_on_subsequent_calls(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            terminal_tool, "_reconcile_orphaned_sandboxes",
            lambda: called.append(True),
        )
        terminal_tool._start_cleanup_thread()
        terminal_tool._start_cleanup_thread()
        terminal_tool._start_cleanup_thread()
        assert called == [True]

    def test_reconciliation_exception_does_not_block_cleanup_thread(self, monkeypatch, caplog):
        def boom():
            raise RuntimeError("backend exploded")

        monkeypatch.setattr(terminal_tool, "_reconcile_orphaned_sandboxes", boom)
        with caplog.at_level("WARNING", logger=terminal_tool.logger.name):
            terminal_tool._start_cleanup_thread()
        # Flag still marked done so we don't keep retrying.
        assert terminal_tool._orphan_reconciliation_done is True
        # Cleanup thread still started (running flag set).
        assert terminal_tool._cleanup_running is True
        assert any("unexpected error" in r.message for r in caplog.records)
