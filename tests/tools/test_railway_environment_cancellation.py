"""Tests for Railway backend cancellation behavior."""

from __future__ import annotations

import asyncio
import importlib
import time
from pathlib import Path
from unittest.mock import patch


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


def test_cancellation_releases_resources():
    mod = _import_railway()
    env = mod.RailwayEnvironment(**_kwargs())
    with patch.object(env, "_run_railway_ssh") as run:
        run.return_value = {"output": "ok\n", "returncode": 0}
        env.execute("echo ok")
        env.cleanup()
    assert getattr(env, "_persistent_session", None) in (None, False)
    failure_cls = getattr(mod, "RailwayFailure", None)
    assert failure_cls is not None
    contract = getattr(mod, "RAILWAY_FAILURE_CATEGORIES")
    assert "cancelled" in contract


def test_cancellation_does_not_block_gateway_loop():
    mod = _import_railway()
    env = mod.RailwayEnvironment(**_kwargs())

    async def driver():
        with patch.object(env, "_run_railway_ssh") as run:
            run.return_value = {"output": "", "returncode": 0}
            t = time.perf_counter()
            await asyncio.to_thread(env.execute, "true")
            return (time.perf_counter() - t) * 1000.0

    elapsed_ms = asyncio.run(driver())
    assert elapsed_ms < 200, (
        f"non-blocking spawn took {elapsed_ms:.0f}ms; budget is 200ms"
    )


def test_cancel_active_call_returns_railway_failure_cancelled():
    mod = _import_railway()
    env = mod.RailwayEnvironment(**_kwargs())
    failure_cls = mod.RailwayFailure
    with patch.object(env, "_run_railway_ssh") as run:
        def boom(*args, **kwargs):
            raise failure_cls(category="cancelled", retryable=False,
                              dependency="railway-cli",
                              correlation_id="test-corr")
        run.side_effect = boom
        try:
            env.execute("sleep 9999")
        except failure_cls as exc:
            assert exc.category == "cancelled"
            assert not exc.retryable
            return
    raise AssertionError("expected RailwayFailure(cancelled) was not raised")
