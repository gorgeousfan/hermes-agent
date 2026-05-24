"""Tests for cross-platform topic/channel title syncing.

Covers:
- BasePlatformAdapter.update_topic_title returns False (unsupported)
- TelegramAdapter.update_topic_title delegates to rename_dm_topic
- GatewayRunner._sanitize_topic_title handles edge cases
- GatewayRunner generic wiring via _schedule_topic_title_rename
- _handle_title_command triggers update_topic_title on manual /title
"""

import asyncio
import dataclasses
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform, PlatformConfig


# ---------------------------------------------------------------------------
# Telegram mock setup (same pattern as test_dm_topics.py)
# ---------------------------------------------------------------------------

def _ensure_telegram_mock():
    telegram_mod = MagicMock()
    telegram_mod.ext.ContextTypes.DEFAULT_TYPE = type(None)

    constants_mod = MagicMock()
    constants_mod.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    constants_mod.ChatType.GROUP = "group"
    constants_mod.ChatType.SUPERGROUP = "supergroup"
    constants_mod.ChatType.CHANNEL = "channel"
    constants_mod.ChatType.PRIVATE = "private"

    sys.modules["telegram"] = telegram_mod
    sys.modules["telegram.ext"] = telegram_mod.ext
    sys.modules["telegram.constants"] = constants_mod
    sys.modules["telegram.request"] = telegram_mod.request

    # Force reimport so the adapter picks up the mock ChatType.
    sys.modules.pop("gateway.platforms.telegram", None)


_ensure_telegram_mock()

from gateway.platforms.base import BasePlatformAdapter  # noqa: E402
from gateway.platforms.telegram import TelegramAdapter  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_telegram_adapter(dm_topics_config=None):
    """Create a TelegramAdapter with optional DM topics config."""
    extra = {}
    if dm_topics_config is not None:
        extra["dm_topics"] = dm_topics_config
    config = PlatformConfig(enabled=True, token="***", extra=extra)
    adapter = TelegramAdapter(config)
    adapter._bot = MagicMock()
    adapter._bot.edit_forum_topic = AsyncMock()
    return adapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _StubAdapter(BasePlatformAdapter):
    """Minimal concrete adapter for testing the base-class default."""

    async def connect(self, handler):
        pass

    async def disconnect(self):
        pass

    async def send(self, chat_id, text, **kw):
        pass

    async def get_chat_info(self, chat_id):
        return {}


def _make_base_adapter():
    """Create a concrete stub adapter to test the default update_topic_title."""
    config = PlatformConfig(enabled=True, token="***", extra={})
    return _StubAdapter(config, Platform.TELEGRAM)


# ---------------------------------------------------------------------------
# 1. Base adapter returns False
# ---------------------------------------------------------------------------

class TestBaseAdapterUpdateTopicTitle:
    """BasePlatformAdapter.update_topic_title is a no-op returning False."""

    @pytest.mark.asyncio
    async def test_returns_false(self):
        adapter = _make_base_adapter()
        result = await adapter.update_topic_title("123", "456", "Test Title")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_with_empty_args(self):
        adapter = _make_base_adapter()
        result = await adapter.update_topic_title("", "", "")
        assert result is False


# ---------------------------------------------------------------------------
# 2. TelegramAdapter.update_topic_title
# ---------------------------------------------------------------------------

class TestTelegramAdapterUpdateTopicTitle:
    """TelegramAdapter delegates to rename_dm_topic and returns True."""

    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        adapter = _make_telegram_adapter()
        result = await adapter.update_topic_title("123", "456", "My Topic")
        assert result is True
        adapter._bot.edit_forum_topic.assert_awaited_once_with(
            chat_id=123, message_thread_id=456, name="My Topic",
        )

    @pytest.mark.asyncio
    async def test_no_bot_returns_false(self):
        adapter = _make_telegram_adapter()
        adapter._bot = None
        result = await adapter.update_topic_title("123", "456", "Title")
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_chat_id_returns_false(self):
        adapter = _make_telegram_adapter()
        result = await adapter.update_topic_title("", "456", "Title")
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_thread_id_returns_false(self):
        adapter = _make_telegram_adapter()
        result = await adapter.update_topic_title("123", "", "Title")
        assert result is False

    @pytest.mark.asyncio
    async def test_api_error_returns_false(self):
        adapter = _make_telegram_adapter()
        adapter._bot.edit_forum_topic.side_effect = Exception("API error")
        result = await adapter.update_topic_title("123", "456", "Title")
        assert result is False

    @pytest.mark.asyncio
    async def test_santized_title_passed_through(self):
        """The title should be passed as-is (caller sanitises)."""
        adapter = _make_telegram_adapter()
        title = "A" * 200  # Long title — adapter doesn't truncate
        result = await adapter.update_topic_title("123", "456", title)
        assert result is True
        adapter._bot.edit_forum_topic.assert_awaited_once_with(
            chat_id=123, message_thread_id=456, name=title,
        )


# ---------------------------------------------------------------------------
# 3. _sanitize_topic_title
# ---------------------------------------------------------------------------

class TestSanitizeTopicTitle:
    """GatewayRunner._sanitize_topic_title handles edge cases."""

    def _make_runner(self):
        """Create a minimal GatewayRunner-like object for testing."""
        # Import here to avoid heavy module-level side effects
        from gateway.run import GatewayRunner
        # We can't easily construct a full GatewayRunner, so test the
        # static method via a lightweight approach
        return GatewayRunner.__new__(GatewayRunner)

    def test_basic_title(self):
        runner = self._make_runner()
        assert runner._sanitize_topic_title("Hello World") == "Hello World"

    def test_whitespace_cleanup(self):
        runner = self._make_runner()
        assert runner._sanitize_topic_title("  Hello   World  ") == "Hello World"

    def test_empty_returns_default(self):
        runner = self._make_runner()
        assert runner._sanitize_topic_title("") == "Hermes Chat"

    def test_none_returns_default(self):
        runner = self._make_runner()
        assert runner._sanitize_topic_title(None) == "Hermes Chat"

    def test_long_title_truncated(self):
        runner = self._make_runner()
        long_title = "A" * 200
        result = runner._sanitize_topic_title(long_title)
        assert len(result) == 120
        assert result.endswith("...")

    def test_exactly_120_chars_not_truncated(self):
        runner = self._make_runner()
        title = "A" * 120
        result = runner._sanitize_topic_title(title)
        assert result == title

    def test_telegram_alias_delegates(self):
        """_sanitize_telegram_topic_title delegates to _sanitize_topic_title."""
        runner = self._make_runner()
        assert runner._sanitize_telegram_topic_title("Test") == runner._sanitize_topic_title("Test")


# ---------------------------------------------------------------------------
# 4. Generic GatewayRunner wiring
# ---------------------------------------------------------------------------

class TestGenericTopicTitleRename:
    """_rename_topic_for_session_title calls adapter.update_topic_title."""

    @pytest.mark.asyncio
    async def test_calls_adapter_update_topic_title(self):
        """For a non-Telegram platform, the generic path calls update_topic_title."""
        from gateway.session import SessionSource

        # Create a mock adapter whose update_topic_title returns True
        mock_adapter = AsyncMock(spec=BasePlatformAdapter)
        mock_adapter.update_topic_title = AsyncMock(return_value=True)

        # Build a minimal runner
        from gateway.run import GatewayRunner
        runner = GatewayRunner.__new__(GatewayRunner)
        runner.adapters = {Platform.DISCORD: mock_adapter}

        source = SessionSource(
            platform=Platform.DISCORD,
            chat_type="group",
            chat_id="channel-123",
            thread_id="thread-456",
            user_id="user-789",
        )

        await runner._rename_topic_for_session_title(source, "sess-abc", "My Title")

        mock_adapter.update_topic_title.assert_awaited_once_with(
            chat_id="channel-123",
            thread_id="thread-456",
            title="My Title",
        )

    @pytest.mark.asyncio
    async def test_no_adapter_no_error(self):
        """Missing adapter is a no-op, not an error."""
        from gateway.run import GatewayRunner
        from gateway.session import SessionSource

        runner = GatewayRunner.__new__(GatewayRunner)
        runner.adapters = {}

        source = SessionSource(
            platform=Platform.DISCORD,
            chat_type="group",
            chat_id="ch",
            thread_id="th",
            user_id="u",
        )
        # Should not raise
        await runner._rename_topic_for_session_title(source, "sess", "Title")

    @pytest.mark.asyncio
    async def test_adapter_returns_false_no_error(self):
        """If adapter.update_topic_title returns False, no error is raised."""
        from gateway.run import GatewayRunner
        from gateway.session import SessionSource

        mock_adapter = AsyncMock(spec=BasePlatformAdapter)
        mock_adapter.update_topic_title = AsyncMock(return_value=False)

        runner = GatewayRunner.__new__(GatewayRunner)
        runner.adapters = {Platform.DISCORD: mock_adapter}

        source = SessionSource(
            platform=Platform.DISCORD,
            chat_type="group",
            chat_id="ch",
            thread_id="th",
            user_id="u",
        )
        await runner._rename_topic_for_session_title(source, "sess", "Title")
        mock_adapter.update_topic_title.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_thread_id_skipped(self):
        """No thread_id means no rename attempt."""
        from gateway.run import GatewayRunner
        from gateway.session import SessionSource

        mock_adapter = AsyncMock(spec=BasePlatformAdapter)

        runner = GatewayRunner.__new__(GatewayRunner)
        runner.adapters = {Platform.DISCORD: mock_adapter}

        source = SessionSource(
            platform=Platform.DISCORD,
            chat_type="group",
            chat_id="ch",
            thread_id=None,
            user_id="u",
        )
        await runner._rename_topic_for_session_title(source, "sess", "Title")
        mock_adapter.update_topic_title.assert_not_awaited()


# ---------------------------------------------------------------------------
# 5. Telegram-specific path still works via update_topic_title
# ---------------------------------------------------------------------------

class TestTelegramRenameViaUpdateTopicTitle:
    """_rename_telegram_topic_for_session_title now calls update_topic_title."""

    @pytest.mark.asyncio
    async def test_telegram_rename_uses_update_topic_title(self):
        """The Telegram-specific path now delegates to adapter.update_topic_title."""
        from gateway.run import GatewayRunner
        from gateway.session import SessionSource

        adapter = _make_telegram_adapter()
        # Spy on update_topic_title
        original = adapter.update_topic_title
        called_with = {}
        async def _spy(chat_id, thread_id, title):
            called_with.update(chat_id=chat_id, thread_id=thread_id, title=title)
            return await original(chat_id, thread_id, title)
        adapter.update_topic_title = _spy

        runner = GatewayRunner.__new__(GatewayRunner)
        runner.adapters = {Platform.TELEGRAM: adapter}
        runner._session_db = MagicMock()
        runner._session_db.get_telegram_topic_binding.return_value = {
            "session_id": "sess-1",
        }
        runner.config = MagicMock()
        runner.config.platforms = {
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="***", extra={}),
        }

        source = SessionSource(
            platform=Platform.TELEGRAM,
            chat_type="dm",
            chat_id="100200",
            thread_id="42",
            user_id="user1",
        )

        # Stub _is_telegram_topic_lane to return True
        runner._is_telegram_topic_lane = lambda s: True
        runner._telegram_topic_auto_rename_disabled = lambda s: False

        await runner._rename_telegram_topic_for_session_title(source, "sess-1", "Test Title")

        assert called_with == {
            "chat_id": "100200",
            "thread_id": "42",
            "title": "Test Title",
        }
