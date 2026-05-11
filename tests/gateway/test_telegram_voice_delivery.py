from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_invalid_ogg_voice_is_not_sent_as_document(monkeypatch, tmp_path):
    from gateway.config import Platform
    from gateway.platforms.telegram import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter._bot = AsyncMock()
    adapter._missing_media_path_error = lambda label, path: f"{label} file not found: {path}"
    adapter._metadata_thread_id = lambda metadata: None
    adapter._message_thread_id_for_send = lambda thread_id: None
    adapter.send_document = AsyncMock()

    path = tmp_path / "bad.ogg"
    path.write_bytes(b"mp3-bytes")
    monkeypatch.setattr(
        "tools.tts_tool._is_telegram_voice_artifact",
        lambda audio_path: False,
    )

    result = await adapter.send_voice(chat_id="123", audio_path=str(path))

    assert result.success is False
    assert "valid telegram voice" in result.error.lower()
    adapter._bot.send_voice.assert_not_called()
    adapter.send_document.assert_not_called()


@pytest.mark.asyncio
async def test_voice_upload_failure_does_not_fall_back_for_native_voice(monkeypatch, tmp_path):
    from gateway.config import Platform
    from gateway.platforms.telegram import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter._bot = AsyncMock()
    adapter._missing_media_path_error = lambda label, path: f"{label} file not found: {path}"
    adapter._metadata_thread_id = lambda metadata: None
    adapter._message_thread_id_for_send = lambda thread_id: None
    adapter.send_document = AsyncMock()

    path = tmp_path / "voice.ogg"
    path.write_bytes(b"ogg-opus")
    monkeypatch.setattr(
        "tools.tts_tool._is_telegram_voice_artifact",
        lambda audio_path: True,
    )
    adapter._bot.send_voice.side_effect = RuntimeError("telegram rejected upload")

    result = await adapter.send_voice(chat_id="123", audio_path=str(path))

    assert result.success is False
    assert "telegram voice/audio upload failed" in result.error.lower()
    adapter.send_document.assert_not_called()


@pytest.mark.asyncio
async def test_send_audio_failure_preserves_base_fallback(monkeypatch, tmp_path):
    from gateway.config import Platform
    from gateway.platforms.base import SendResult
    from gateway.platforms.telegram import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter._bot = AsyncMock()
    adapter._missing_media_path_error = lambda label, path: f"{label} file not found: {path}"
    adapter._metadata_thread_id = lambda metadata: None
    adapter._message_thread_id_for_send = lambda thread_id: None
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="fallback"))

    path = tmp_path / "lesson.mp3"
    path.write_bytes(b"mp3")
    adapter._bot.send_audio.side_effect = RuntimeError("telegram rejected audio")

    result = await adapter.send_voice(chat_id="123", audio_path=str(path), caption="Audio")

    assert result.success is True
    assert result.message_id == "fallback"
    adapter.send.assert_called_once()
