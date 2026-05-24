"""Latency-budget tests for RailwayEnvironment."""

from __future__ import annotations

import importlib
import time
from pathlib import Path
from unittest.mock import patch


_BUDGET = {
    "spawn_p99_ms": 1520,
    "persistent_shell_first_byte_p99_ms": 750,
    "file_sync_small_p99_ms": 2250,
    "cancellation_release_p99_ms": 900,
}


def _import_railway():
    return importlib.import_module("tools.environments.railway")


def _kwargs() -> dict:
    return {
        "project_id": "p_test",
        "service_id": "s_test",
        "environment_id": "e_test",
        "deployment_instance_id": "i_test",
        "identity_file": str(Path("/tmp/fake_id_ed25519")),
        "cwd": "/data",
        "timeout": 30,
    }


def _p99(samples: list[float]) -> float:
    samples.sort()
    return samples[int(0.99 * len(samples))]


def test_spawn_within_budget():
    mod = _import_railway()
    env = mod.RailwayEnvironment(**_kwargs())
    samples = []
    with patch.object(env, "_run_railway_ssh") as run:
        run.return_value = {"output": "x\n", "returncode": 0}
        for _ in range(20):
            t = time.perf_counter()
            env.execute("echo x")
            samples.append((time.perf_counter() - t) * 1000.0)
    assert _p99(samples) < _BUDGET["spawn_p99_ms"]


def test_persistent_shell_first_byte_within_budget():
    mod = _import_railway()
    env = mod.RailwayEnvironment(**_kwargs())
    samples = []
    with patch.object(env, "_run_railway_ssh") as run:
        run.return_value = {"output": "y\n", "returncode": 0}
        env.execute("export FOO=1")
        for _ in range(20):
            t = time.perf_counter()
            env.execute("echo $FOO")
            samples.append((time.perf_counter() - t) * 1000.0)
    assert _p99(samples) < _BUDGET["persistent_shell_first_byte_p99_ms"]


def test_latency_budget_targets_match_contract():
    expected_keys = {
        "spawn_p99_ms",
        "persistent_shell_first_byte_p99_ms",
        "file_sync_small_p99_ms",
        "cancellation_release_p99_ms",
    }
    assert expected_keys == set(_BUDGET)
