"""Slack threading regressions for tools/send_message_tool.py."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import Platform
from tools.send_message_tool import _send_to_platform


def _ensure_slack_mock(monkeypatch):
    """Install enough fake Slack modules for _send_to_platform imports."""
    import sys
    from unittest.mock import MagicMock

    if "slack_bolt" in sys.modules and hasattr(sys.modules["slack_bolt"], "__file__"):
        return

    slack_bolt = MagicMock()
    slack_bolt.async_app.AsyncApp = MagicMock
    slack_bolt.adapter.socket_mode.async_handler.AsyncSocketModeHandler = MagicMock

    slack_sdk = MagicMock()
    slack_sdk.web.async_client.AsyncWebClient = MagicMock

    for name, mod in [
        ("slack_bolt", slack_bolt),
        ("slack_bolt.async_app", slack_bolt.async_app),
        ("slack_bolt.adapter", slack_bolt.adapter),
        ("slack_bolt.adapter.socket_mode", slack_bolt.adapter.socket_mode),
        ("slack_bolt.adapter.socket_mode.async_handler", slack_bolt.adapter.socket_mode.async_handler),
        ("slack_sdk", slack_sdk),
        ("slack_sdk.web", slack_sdk.web),
        ("slack_sdk.web.async_client", slack_sdk.web.async_client),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)


@pytest.mark.asyncio
async def test_send_to_platform_passes_slack_thread_id_to_standalone_sender(monkeypatch):
    """Out-of-process send_message Slack replies must keep the requested thread."""
    _ensure_slack_mock(monkeypatch)
    slack_cfg = SimpleNamespace(enabled=True, token="xoxb-test", extra={})

    with patch(
        "tools.send_message_tool._send_slack",
        new=AsyncMock(return_value={"success": True, "message_id": "171.000002"}),
    ) as send_slack:
        result = await _send_to_platform(
            Platform.SLACK,
            slack_cfg,
            "C123ABCDEF",
            "thread update",
            thread_id="171.000001",
        )

    assert result["success"] is True
    send_slack.assert_awaited_once_with(
        "xoxb-test",
        "C123ABCDEF",
        "thread update",
        thread_id="171.000001",
    )
