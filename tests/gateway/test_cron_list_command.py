"""Tests for the gateway /cron-list slash command."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource, build_session_key


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(text=text, source=_make_source(), message_id="m1")


def _make_runner(session_entry: SessionEntry):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    adapter = MagicMock()
    adapter.send = AsyncMock()
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(
        emit=AsyncMock(),
        emit_collect=AsyncMock(return_value=[]),
        loaded_hooks=False,
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store.load_transcript.return_value = []
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.append_to_transcript = MagicMock()
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.update_session = MagicMock()
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._session_run_generation = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._show_reasoning = False
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._should_send_voice_reply = lambda *_args, **_kwargs: False
    runner._send_voice_reply = AsyncMock()
    runner._capture_gateway_honcho_if_configured = lambda *args, **kwargs: None
    runner._emit_gateway_run_progress = AsyncMock()
    return runner


def _make_session_entry() -> SessionEntry:
    return SessionEntry(
        session_key=build_session_key(_make_source()),
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
        total_tokens=0,
    )


@pytest.mark.asyncio
async def test_cron_list_command_reports_jobs(monkeypatch):
    runner = _make_runner(_make_session_entry())

    monkeypatch.setattr(
        "cron.jobs.list_jobs",
        lambda include_disabled=False: [
            {
                "id": "job-1",
                "name": "Daily sweep",
                "schedule_display": "every 1h",
                "state": "scheduled",
                "enabled": True,
                "last_run_at": "2026-05-15T00:00:00+00:00",
                "next_run_at": "2026-05-15T01:00:00+00:00",
            }
        ],
    )

    result = await runner._handle_message(_make_event("/cron-list"))

    assert "**Scheduled Jobs:** 1" in result
    assert "`job-1` Daily sweep" in result
    assert "schedule: every 1h" in result
    assert "status: active" in result
    assert "last_run: 2026-05-15T00:00:00+00:00" in result
    assert "next_run: 2026-05-15T01:00:00+00:00" in result
    assert "Try `/cron-list all`" not in result


@pytest.mark.asyncio
async def test_cron_list_all_includes_disabled_jobs(monkeypatch):
    runner = _make_runner(_make_session_entry())
    calls: list[bool] = []

    def _fake_list_jobs(*, include_disabled=False):
        calls.append(include_disabled)
        return [
            {
                "id": "job-2",
                "name": "Paused sweep",
                "schedule_display": "0 9 * * *",
                "state": "paused",
                "enabled": False,
                "last_run_at": None,
                "next_run_at": None,
            }
        ]

    monkeypatch.setattr("cron.jobs.list_jobs", _fake_list_jobs)

    result = await runner._handle_message(_make_event("/cron_list all"))

    assert calls == [True]
    assert "`job-2` Paused sweep" in result
    assert "status: paused" in result
    assert "last_run: never" in result
    assert "next_run: not scheduled" in result


@pytest.mark.asyncio
async def test_cron_list_bypasses_running_agent_guard(monkeypatch):
    session_entry = _make_session_entry()
    runner = _make_runner(session_entry)
    session_key = build_session_key(_make_source())
    running_agent = MagicMock()
    runner._running_agents[session_key] = running_agent

    monkeypatch.setattr("cron.jobs.list_jobs", lambda include_disabled=False: [])

    result = await runner._handle_message(_make_event("/cron-list"))

    assert "No active scheduled jobs." in result
    running_agent.interrupt.assert_not_called()
    assert runner._pending_messages == {}
