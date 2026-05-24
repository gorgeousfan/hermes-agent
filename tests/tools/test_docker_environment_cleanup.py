"""Tests for DockerEnvironment cleanup command construction."""

from types import SimpleNamespace

from tools.environments import docker as docker_env
from tools.environments.docker import DockerEnvironment


class _ImmediateThread:
    def __init__(self, target, daemon=False, **kwargs):
        self.target = target
        self.daemon = daemon

    def start(self):
        self.target()


def _make_env(*, persistent=False):
    env = DockerEnvironment.__new__(DockerEnvironment)
    env._container_id = "abc; touch /tmp/pwn"
    env._docker_exe = "/tmp/docker;evil"
    env._persistent = persistent
    env._workspace_dir = None
    env._home_dir = None
    return env


def test_cleanup_uses_argv_form_without_shell(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(docker_env.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(docker_env.subprocess, "run", fake_run)

    env = _make_env(persistent=False)
    env.cleanup()

    assert env._container_id is None
    assert [call[0] for call in calls] == [
        ["/tmp/docker;evil", "stop", "--time", "60", "abc; touch /tmp/pwn"],
        ["/tmp/docker;evil", "rm", "-f", "abc; touch /tmp/pwn"],
    ]
    for args, kwargs in calls:
        assert isinstance(args, list)
        assert "shell" not in kwargs
        assert kwargs["stdout"] is docker_env.subprocess.DEVNULL
        assert kwargs["stderr"] is docker_env.subprocess.DEVNULL


def test_cleanup_persistent_container_removes_only_when_stop_fails(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=1 if args[1] == "stop" else 0)

    monkeypatch.setattr(docker_env.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(docker_env.subprocess, "run", fake_run)

    env = _make_env(persistent=True)
    env.cleanup()

    assert [call[0] for call in calls] == [
        ["/tmp/docker;evil", "stop", "--time", "60", "abc; touch /tmp/pwn"],
        ["/tmp/docker;evil", "rm", "-f", "abc; touch /tmp/pwn"],
    ]


def test_cleanup_persistent_container_keeps_stopped_container(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(docker_env.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(docker_env.subprocess, "run", fake_run)

    env = _make_env(persistent=True)
    env.cleanup()

    assert [call[0] for call in calls] == [
        ["/tmp/docker;evil", "stop", "--time", "60", "abc; touch /tmp/pwn"],
    ]
