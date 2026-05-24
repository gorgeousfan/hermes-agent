"""Tests for the LSP idle-subprocess reaper."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.lsp.manager import LSPService


def _make_service(idle_timeout: float = 1.0) -> LSPService:
    """Create a minimal LSPService with a short idle timeout for testing."""
    with patch("agent.lsp.manager._BackgroundLoop") as MockLoop:
        mock_loop = MagicMock()
        mock_loop.schedule = MagicMock(return_value=None)
        MockLoop.return_value = mock_loop
        svc = LSPService(
            enabled=True,
            wait_mode="document",
            wait_timeout=5.0,
            install_strategy="skip",
            idle_timeout=idle_timeout,
        )
    return svc


@pytest.mark.asyncio
async def test_reap_idle_removes_stale_clients():
    """Clients idle beyond the timeout are shut down and removed."""
    svc = _make_service(idle_timeout=1.0)
    mock_client = AsyncMock()
    mock_client.server_id = "pyright"
    mock_client.workspace_root = "/tmp/test"

    key = ("pyright", "/tmp/test")
    svc._clients[key] = mock_client
    svc._last_used[key] = time.time() - 10  # way past timeout

    await svc._reap_idle()

    assert key not in svc._clients
    assert key not in svc._last_used
    mock_client.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_reap_idle_keeps_active_clients():
    """Recently used clients are not reaped."""
    svc = _make_service(idle_timeout=600.0)
    mock_client = AsyncMock()
    mock_client.server_id = "pyright"
    mock_client.workspace_root = "/tmp/test"

    key = ("pyright", "/tmp/test")
    svc._clients[key] = mock_client
    svc._last_used[key] = time.time()

    await svc._reap_idle()

    assert key in svc._clients
    mock_client.shutdown.assert_not_awaited()


@pytest.mark.asyncio
async def test_reap_idle_handles_shutdown_error():
    """Reaper continues even if client.shutdown() raises."""
    svc = _make_service(idle_timeout=1.0)
    mock_client = AsyncMock()
    mock_client.server_id = "pyright"
    mock_client.workspace_root = "/tmp/test"
    mock_client.shutdown.side_effect = RuntimeError("boom")

    key = ("pyright", "/tmp/test")
    svc._clients[key] = mock_client
    svc._last_used[key] = time.time() - 10

    await svc._reap_idle()  # should not raise

    assert key not in svc._clients
