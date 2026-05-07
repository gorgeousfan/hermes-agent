"""Tests for tui_gateway startup race resilience (issue #21440).

Two related failure modes when the dashboard sidecar's WebSocket isn't fully
bound at the moment the gateway subprocess starts:

  1. ``WsPublisherTransport.__init__`` calls ``ws_connect`` synchronously and
     gives up after a single attempt — a brief startup race makes the gateway
     give up on the sidecar.

  2. ``_log_exit`` writes to ``sys.stderr`` unconditionally; if the parent's
     stdout pipe has already been torn down, ``print(..., flush=True)`` raises
     ``BrokenPipeError``, replacing the original failure trace with a useless
     pipe-error trace.
"""

from __future__ import annotations

import io
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ── Suggestion #1: handshake retry with backoff ──────────────────────


@pytest.fixture()
def event_publisher():
    """Import ``event_publisher`` with a fake ``websockets.sync.client``.

    Tests that want to assert specific connect behaviour can replace
    ``module.ws_connect`` directly.  The fake is deliberately a plain
    ``MagicMock`` so individual tests can program ``side_effect``.
    """
    import importlib
    mod = importlib.import_module("tui_gateway.event_publisher")
    importlib.reload(mod)
    yield mod
    importlib.reload(mod)


def _make_fake_ws():
    """Return a stand-in for the websockets sync connection object."""
    fake = MagicMock()
    fake.send = MagicMock()
    fake.close = MagicMock()
    return fake


def test_handshake_retries_on_transient_failure(event_publisher):
    """First two connect attempts fail; third succeeds → publisher is alive."""
    fake_ws = _make_fake_ws()
    attempts = []

    def flaky_connect(url, **kwargs):
        attempts.append(url)
        if len(attempts) < 3:
            raise ConnectionRefusedError("not bound yet")
        return fake_ws

    with patch.object(event_publisher, "ws_connect", side_effect=flaky_connect), \
         patch.object(event_publisher.time, "sleep") as fake_sleep:
        pub = event_publisher.WsPublisherTransport("ws://localhost:0/x")

    assert len(attempts) == 3
    assert pub._dead is False
    assert pub._ws is fake_ws
    # Backoff was used between attempts (no sleep before first attempt).
    assert fake_sleep.call_count == 2
    pub.close()


def test_handshake_retries_on_timeout(event_publisher):
    """``TimeoutError`` from a slow handshake retries the same way."""
    fake_ws = _make_fake_ws()
    attempts = []

    def flaky_connect(url, **kwargs):
        attempts.append(url)
        if len(attempts) < 2:
            raise TimeoutError("handshake response_rcvd.wait expired")
        return fake_ws

    with patch.object(event_publisher, "ws_connect", side_effect=flaky_connect), \
         patch.object(event_publisher.time, "sleep"):
        pub = event_publisher.WsPublisherTransport("ws://localhost:0/x")

    assert len(attempts) == 2
    assert pub._dead is False
    pub.close()


def test_handshake_gives_up_after_exhausting_retries(event_publisher):
    """All retries fail → publisher marks itself dead, never raises."""
    attempts = []

    def always_fail(url, **kwargs):
        attempts.append(url)
        raise ConnectionRefusedError("dashboard never came up")

    with patch.object(event_publisher, "ws_connect", side_effect=always_fail), \
         patch.object(event_publisher.time, "sleep"):
        pub = event_publisher.WsPublisherTransport("ws://localhost:0/x")

    # Multiple attempts (more than 1 — the original code only tried once).
    assert len(attempts) > 1
    assert pub._dead is True
    assert pub._ws is None
    # No worker thread spawned for a dead transport.
    assert pub._worker is None


# ── Suggestion #2: _log_exit survives broken stdout pipe ─────────────


class _BrokenPipeStderr(io.StringIO):
    """Stand-in for sys.stderr whose flush raises BrokenPipeError."""

    def write(self, s):  # noqa: D401 - mimic file API
        # Match the failure mode in the issue trace: the print itself can
        # succeed up until the implicit flush at the end.
        return super().write(s)

    def flush(self):
        raise BrokenPipeError(32, "Broken pipe")


@pytest.fixture()
def entry_module():
    """Import ``tui_gateway.entry`` with the heavy deps mocked out.

    ``entry`` reaches into ``tui_gateway.server`` for ``_CRASH_LOG`` /
    ``dispatch`` / ``resolve_skin`` / ``write_json`` and into
    ``tui_gateway.transport`` for ``TeeTransport`` — those don't matter for
    these tests, so we mock them at import time.
    """
    fake_server = types.SimpleNamespace(
        _CRASH_LOG="/tmp/hermes_test_crash.log",
        dispatch=MagicMock(return_value=None),
        resolve_skin=MagicMock(return_value="default"),
        write_json=MagicMock(return_value=True),
        _stdio_transport=MagicMock(),
    )
    fake_transport_mod = types.SimpleNamespace(TeeTransport=MagicMock())
    with patch.dict(sys.modules, {
        "tui_gateway.server": fake_server,
        "tui_gateway.transport": fake_transport_mod,
        "hermes_constants": MagicMock(get_hermes_home=MagicMock(return_value="/tmp/hermes_test")),
    }):
        import importlib
        if "tui_gateway.entry" in sys.modules:
            del sys.modules["tui_gateway.entry"]
        mod = importlib.import_module("tui_gateway.entry")
        yield mod
        if "tui_gateway.entry" in sys.modules:
            del sys.modules["tui_gateway.entry"]


def test_log_exit_swallows_broken_pipe_on_stderr(entry_module, tmp_path):
    """SIGHUP path: parent already closed our stderr pipe, _log_exit must
    not propagate BrokenPipeError on top of the original failure trace.
    """
    crash_log = tmp_path / "tui_gateway_crash.log"
    broken = _BrokenPipeStderr()
    with patch.object(entry_module, "_CRASH_LOG", str(crash_log)), \
         patch.object(entry_module.sys, "stderr", broken):
        # Should not raise — that's the entire fix.
        entry_module._log_exit("startup write failed (broken stdout pipe before first event)")

    # Crash log was still written even though stderr was broken.
    assert crash_log.exists()
    assert "reason=startup write failed" in crash_log.read_text()
