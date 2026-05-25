from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionEntry, SessionSource, build_session_key


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))

    from hermes_cli import goals

    goals._DB_CACHE.clear()
    yield home
    goals._DB_CACHE.clear()


class _RecordingAdapter:
    def __init__(self) -> None:
        self._pending_messages: dict = {}
        self._active_sessions: dict = {}
        self.sends: list[dict] = []

    async def send(self, chat_id: str, content: str, reply_to=None, metadata=None):
        self.sends.append({"chat_id": chat_id, "content": content, "metadata": metadata})
        return SimpleNamespace(success=True)

    def register_post_delivery_callback(self, session_key, callback, *, generation=None):
        self._active_sessions[session_key] = SimpleNamespace(_hermes_run_generation=generation)
        self._callback = callback


def _make_source(thread_id: str = "thread-123") -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
        thread_id=thread_id,
    )


def _make_runner_with_adapter(session_id: str = "goal-task-ledger-sid"):
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")},
    )
    runner.adapters = {}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._queued_events = {}

    src = _make_source()
    session_entry = SessionEntry(
        session_key=build_session_key(src),
        session_id=session_id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store._generate_session_key.return_value = build_session_key(src)

    adapter = _RecordingAdapter()
    runner.adapters[Platform.TELEGRAM] = adapter
    return runner, adapter, session_entry, src


@pytest.mark.asyncio
async def test_post_turn_goal_continuation_persists_source_metadata(hermes_home):
    from hermes_cli.goals import GoalManager, load_goal_task_snapshot

    runner, _adapter, session_entry, src = _make_runner_with_adapter()
    GoalManager(session_entry.session_id).set("ship the feature")

    with patch("hermes_cli.goals.judge_goal", return_value=("continue", "still needs work", False)):
        await runner._post_turn_goal_continuation(
            session_entry=session_entry,
            source=src,
            final_response="partial progress",
        )

    snap = load_goal_task_snapshot(session_entry.session_id)
    assert snap is not None
    assert snap.platform == Platform.TELEGRAM.value
    assert snap.chat_id == "c1"
    assert snap.thread_id == "thread-123"
    assert snap.status == "active"
    assert snap.last_reason == "still needs work"


@pytest.mark.asyncio
async def test_goal_stall_detection_marks_snapshot_and_sends_one_notice(hermes_home):
    from hermes_cli.goals import GoalManager, update_goal_task_snapshot, load_goal_task_snapshot

    runner, adapter, session_entry, src = _make_runner_with_adapter(session_id="goal-stalled-sid")
    GoalManager(session_entry.session_id).set("finish the task")
    update_goal_task_snapshot(
        session_entry.session_id,
        platform=Platform.TELEGRAM.value,
        chat_id="c1",
        thread_id="thread-123",
        status="active",
        last_verified_progress_at=1.0,
    )

    runner._goal_stall_timeout_minutes_from_config = MagicMock(return_value=1)

    with patch("gateway.run.time.time", return_value=1.0 + 120.0):
        should_continue = await runner._maybe_mark_stalled_goal_before_continuation(
            session_id=session_entry.session_id,
            source=src,
        )

    snap = load_goal_task_snapshot(session_entry.session_id)
    assert should_continue is False
    assert snap is not None
    assert snap.status == "stalled"
    assert snap.stall_notified_at == pytest.approx(121.0)
    assert "check provider/tool failure" in snap.next_action
    assert len(adapter.sends) == 1
    assert adapter.sends[0]["chat_id"] == "c1"
    assert adapter.sends[0]["content"] == (
        "⏸ Goal stalled: finish the task — no verified progress for 2.0 minutes. "
        "Next action: check provider/tool failure or ask user for guidance."
    )
    assert adapter.sends[0]["metadata"]["thread_id"] == "thread-123"
    assert not hasattr(adapter, "_callback")

    with patch("gateway.run.time.time", return_value=1.0 + 180.0):
        should_continue_again = await runner._maybe_mark_stalled_goal_before_continuation(
            session_id=session_entry.session_id,
            source=src,
        )

    assert should_continue_again is False
    assert len(adapter.sends) == 1
