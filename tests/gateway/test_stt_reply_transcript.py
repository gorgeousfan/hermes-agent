"""Tests for stt_reply_transcript feature."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import GatewayConfig


class TestSttReplyTranscriptConfig:
    """Config parsing for stt_reply_transcript."""

    def test_default_is_false(self):
        config = GatewayConfig.from_dict({})
        assert config.stt_reply_transcript is False

    def test_flat_key(self):
        config = GatewayConfig.from_dict({"stt_reply_transcript": True})
        assert config.stt_reply_transcript is True

    def test_nested_stt_key(self):
        config = GatewayConfig.from_dict({"stt": {"reply_transcript": True}})
        assert config.stt_reply_transcript is True

    def test_flat_key_takes_precedence(self):
        config = GatewayConfig.from_dict({
            "stt_reply_transcript": False,
            "stt": {"reply_transcript": True},
        })
        assert config.stt_reply_transcript is False

    def test_to_dict_includes_field(self):
        config = GatewayConfig.from_dict({"stt_reply_transcript": True})
        d = config.to_dict()
        assert d["stt_reply_transcript"] is True


class TestSendReplyTranscript:
    """Test _send_reply_transcript calls adapter.send() correctly."""

    def _make_runner(self):
        """Create a minimal GatewayRunner-like object with _send_reply_transcript."""
        from gateway.run import GatewayRunner

        runner = object.__new__(GatewayRunner)
        runner.adapters = {}
        return runner

    def test_calls_adapter_send(self):
        runner = self._make_runner()
        adapter = MagicMock()
        adapter.send = AsyncMock(return_value=None)
        runner.adapters["telegram"] = adapter

        asyncio.get_event_loop().run_until_complete(
            runner._send_reply_transcript("telegram", "123", "456", "🎤 «hello»")
        )

        adapter.send.assert_called_once_with(
            chat_id="123",
            content="🎤 «hello»",
            reply_to="456",
            metadata=None,
        )

    def test_passes_thread_id_in_metadata(self):
        runner = self._make_runner()
        adapter = MagicMock()
        adapter.send = AsyncMock(return_value=None)
        runner.adapters["telegram"] = adapter

        asyncio.get_event_loop().run_until_complete(
            runner._send_reply_transcript("telegram", "123", "456", "🎤 «hello»", thread_id="789")
        )

        adapter.send.assert_called_once_with(
            chat_id="123",
            content="🎤 «hello»",
            reply_to="456",
            metadata={"thread_id": "789"},
        )

    def test_noop_when_adapter_missing(self):
        runner = self._make_runner()
        # No adapter registered — should not raise
        asyncio.get_event_loop().run_until_complete(
            runner._send_reply_transcript("telegram", "123", "456", "text")
        )

    def test_noop_when_no_send_method(self):
        runner = self._make_runner()
        adapter = MagicMock(spec=[])  # no methods at all
        runner.adapters["telegram"] = adapter

        asyncio.get_event_loop().run_until_complete(
            runner._send_reply_transcript("telegram", "123", "456", "text")
        )
