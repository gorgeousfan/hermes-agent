"""
Tests for Slack section-block chunking and _safe_post_message error surfacing.

Covers:
- send() with a 50KB report: posts N chunks via chat_postMessage; content
  reconstructs across chunks.
- send() with a 2KB report: posts exactly 1 chunk (no marker pollution).
- send_slash_confirm() with a 5KB message: section block stays under 3000 chars;
  remainder posts as threaded plain-text replies with continuation markers.
- send_slash_confirm() with a 200-char message: posts 1 block, no follow-up.
- _safe_post_message logs text_len + error code on SlackApiError.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Ensure repo root is on sys.path
# ---------------------------------------------------------------------------
_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


# ---------------------------------------------------------------------------
# Minimal Slack SDK mock so SlackAdapter can be imported without real deps
# ---------------------------------------------------------------------------

def _ensure_slack_mock():
    if "slack_bolt" in sys.modules:
        return
    slack_bolt = MagicMock()
    slack_bolt.async_app.AsyncApp = MagicMock
    sys.modules["slack_bolt"] = slack_bolt
    sys.modules["slack_bolt.async_app"] = slack_bolt.async_app
    handler_mod = MagicMock()
    handler_mod.AsyncSocketModeHandler = MagicMock
    sys.modules["slack_bolt.adapter"] = MagicMock()
    sys.modules["slack_bolt.adapter.socket_mode"] = MagicMock()
    sys.modules["slack_bolt.adapter.socket_mode.async_handler"] = handler_mod
    sdk_mod = MagicMock()
    sdk_mod.web = MagicMock()
    sdk_mod.web.async_client = MagicMock()
    sdk_mod.web.async_client.AsyncWebClient = MagicMock
    sys.modules["slack_sdk"] = sdk_mod
    sys.modules["slack_sdk.web"] = sdk_mod.web
    sys.modules["slack_sdk.web.async_client"] = sdk_mod.web.async_client
    sys.modules.setdefault("aiohttp", MagicMock())


_ensure_slack_mock()

import gateway.platforms.slack as _slack_mod
_slack_mod.SLACK_AVAILABLE = True

from gateway.platforms.slack import SlackAdapter  # noqa: E402
from gateway.config import Platform, PlatformConfig  # noqa: E402
from gateway.platforms.base import SendResult  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter() -> SlackAdapter:
    """Return a SlackAdapter wired with a mock Slack client on team T1 / channel C1."""
    config = PlatformConfig(enabled=True, token="xoxb-test-token")
    adapter = SlackAdapter(config)
    adapter._app = MagicMock()
    adapter._bot_user_id = "U_BOT"
    adapter._team_clients = {"T1": AsyncMock()}
    adapter._team_bot_user_ids = {"T1": "U_BOT"}
    adapter._channel_team = {"C1": "T1"}
    return adapter


# ---------------------------------------------------------------------------
# send() — top-level message chunking
# ---------------------------------------------------------------------------

class TestSendChunking:
    """send() already chunks at MAX_MESSAGE_LENGTH; verify the plumbing still works."""

    @pytest.mark.asyncio
    async def test_large_report_posts_multiple_chunks(self):
        """A 50KB report must be split into several chat_postMessage calls."""
        adapter = _make_adapter()
        client = adapter._team_clients["T1"]
        client.chat_postMessage = AsyncMock(return_value={"ts": "111.1"})

        big_message = "x" * 50_000

        result = await adapter.send(chat_id="C1", content=big_message)

        assert result.success is True
        assert client.chat_postMessage.call_count > 1

        # Reconstruct content across all calls (strip chunk markers).
        all_text = "".join(
            c[1]["text"]
            for c in client.chat_postMessage.call_args_list
        )
        # All original x's must be present somewhere (markers are additions, not deletions).
        raw_x_count = all_text.count("x")
        assert raw_x_count == 50_000

    @pytest.mark.asyncio
    async def test_small_report_posts_one_chunk_no_marker(self):
        """A 2KB report must post exactly one chunk with no continuation marker."""
        adapter = _make_adapter()
        client = adapter._team_clients["T1"]
        client.chat_postMessage = AsyncMock(return_value={"ts": "222.2"})

        small_message = "y" * 2_000

        result = await adapter.send(chat_id="C1", content=small_message)

        assert result.success is True
        assert client.chat_postMessage.call_count == 1

        sent_text = client.chat_postMessage.call_args[1]["text"]
        assert "continued" not in sent_text
        assert "/" not in sent_text or "http" in sent_text  # no i/N marker


# ---------------------------------------------------------------------------
# send_slash_confirm() — section-block chunking
# ---------------------------------------------------------------------------

class TestSendSlashConfirmChunking:
    """Section block text must stay under MAX_SECTION_TEXT_LENGTH (2900 chars)."""

    @pytest.mark.asyncio
    async def test_large_message_keeps_section_under_limit(self):
        """A 5KB message body: section block text must be ≤ MAX_SECTION_TEXT_LENGTH."""
        adapter = _make_adapter()
        client = adapter._team_clients["T1"]
        client.chat_postMessage = AsyncMock(return_value={"ts": "333.3"})

        big_message = "A" * 5_000

        result = await adapter.send_slash_confirm(
            chat_id="C1",
            title="Big Report",
            message=big_message,
            session_key="sk",
            confirm_id="cid",
        )

        assert result.success is True
        # First call must carry the button blocks.
        first_call_kwargs = client.chat_postMessage.call_args_list[0][1]
        section_text = first_call_kwargs["blocks"][0]["text"]["text"]
        assert len(section_text) <= adapter.MAX_SECTION_TEXT_LENGTH

    @pytest.mark.asyncio
    async def test_large_message_posts_continuation_replies(self):
        """A 5KB message: overflow must arrive as threaded replies with [i/N continued] markers."""
        adapter = _make_adapter()
        client = adapter._team_clients["T1"]
        client.chat_postMessage = AsyncMock(return_value={"ts": "444.4"})

        big_message = "B" * 5_000

        await adapter.send_slash_confirm(
            chat_id="C1",
            title="Big",
            message=big_message,
            session_key="sk",
            confirm_id="cid",
        )

        assert client.chat_postMessage.call_count >= 2

        # All calls after the first must be text-only (no blocks) continuations.
        for follow_call in client.chat_postMessage.call_args_list[1:]:
            kw = follow_call[1]
            assert "blocks" not in kw
            assert "continued" in kw["text"]

    @pytest.mark.asyncio
    async def test_large_message_full_content_preserved(self):
        """The full message body must be recoverable by concatenating all sent text."""
        adapter = _make_adapter()
        client = adapter._team_clients["T1"]
        client.chat_postMessage = AsyncMock(return_value={"ts": "555.5"})

        big_message = "C" * 5_000

        await adapter.send_slash_confirm(
            chat_id="C1",
            title="Preserve",
            message=big_message,
            session_key="sk",
            confirm_id="cid",
        )

        all_sent = "".join(
            c[1]["text"] if "blocks" not in c[1]
            else c[1]["blocks"][0]["text"]["text"]
            for c in client.chat_postMessage.call_args_list
        )
        assert all_sent.count("C") == 5_000

    @pytest.mark.asyncio
    async def test_small_message_single_post_no_follow_up(self):
        """A 200-char message: exactly one chat_postMessage, no threaded follow-up."""
        adapter = _make_adapter()
        client = adapter._team_clients["T1"]
        client.chat_postMessage = AsyncMock(return_value={"ts": "666.6"})

        small_message = "D" * 200

        result = await adapter.send_slash_confirm(
            chat_id="C1",
            title="Small",
            message=small_message,
            session_key="sk",
            confirm_id="cid",
        )

        assert result.success is True
        assert client.chat_postMessage.call_count == 1

        kw = client.chat_postMessage.call_args[1]
        assert "blocks" in kw
        section_text = kw["blocks"][0]["text"]["text"]
        assert "D" * 200 in section_text
        assert "continued" not in section_text

    @pytest.mark.asyncio
    async def test_continuation_replies_are_threaded(self):
        """Overflow chunks must include thread_ts equal to the first post's ts."""
        adapter = _make_adapter()
        client = adapter._team_clients["T1"]
        # First call returns a ts; subsequent calls inherit it as thread_ts.
        client.chat_postMessage = AsyncMock(return_value={"ts": "777.7"})

        big_message = "E" * 5_000

        await adapter.send_slash_confirm(
            chat_id="C1",
            title="Thread",
            message=big_message,
            session_key="sk",
            confirm_id="cid",
        )

        for follow_call in client.chat_postMessage.call_args_list[1:]:
            kw = follow_call[1]
            assert kw.get("thread_ts") == "777.7"


# ---------------------------------------------------------------------------
# _safe_post_message — error surfacing
# ---------------------------------------------------------------------------

class TestSafePostMessage:
    """_safe_post_message must log error details and re-raise on failure."""

    @pytest.mark.asyncio
    async def test_logs_error_code_on_slack_api_error(self, caplog):
        """SlackApiError with a known error code must appear in WARNING logs."""
        adapter = _make_adapter()

        # Build a minimal SlackApiError-like exception.
        mock_exc = Exception("slack api failure")
        mock_exc.response = {"error": "invalid_blocks"}  # type: ignore[attr-defined]

        mock_client = AsyncMock()
        mock_client.chat_postMessage = AsyncMock(side_effect=mock_exc)

        import logging
        with caplog.at_level(logging.WARNING, logger="gateway.platforms.slack"):
            with pytest.raises(Exception):
                await adapter._safe_post_message(
                    mock_client,
                    channel="C1",
                    text="hello",
                    blocks=[
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": "A" * 3100},
                        }
                    ],
                )

        log_text = " ".join(caplog.messages)
        assert "invalid_blocks" in log_text
        assert "text_len" in log_text

    @pytest.mark.asyncio
    async def test_logs_text_len_and_section_len_on_generic_error(self, caplog):
        """A generic (non-SlackApiError) exception must also log text_len."""
        adapter = _make_adapter()

        mock_client = AsyncMock()
        mock_client.chat_postMessage = AsyncMock(
            side_effect=RuntimeError("network error")
        )

        import logging
        with caplog.at_level(logging.WARNING, logger="gateway.platforms.slack"):
            with pytest.raises(RuntimeError):
                await adapter._safe_post_message(
                    mock_client,
                    channel="C1",
                    text="some text",
                )

        log_text = " ".join(caplog.messages)
        assert "text_len" in log_text

    @pytest.mark.asyncio
    async def test_returns_result_on_success(self):
        """_safe_post_message must pass through the response dict unchanged."""
        adapter = _make_adapter()
        expected = {"ts": "999.9", "ok": True}

        mock_client = AsyncMock()
        mock_client.chat_postMessage = AsyncMock(return_value=expected)

        result = await adapter._safe_post_message(
            mock_client, channel="C1", text="hi"
        )
        assert result == expected
