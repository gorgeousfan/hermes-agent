"""Tests for the gateway's destructive-slash-confirm wrapper.

When ``approvals.destructive_slash_confirm`` is True (default), /new,
/reset, and /undo route through the slash-confirm primitive — native
yes/no buttons on Telegram/Discord/Slack, text fallback elsewhere.
When False (after "Always Approve"), the destructive action runs
immediately.
"""

from __future__ import annotations

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


def _make_runner():
    """Mirror tests/gateway/test_unknown_command.py::_make_runner."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    adapter = MagicMock()
    adapter.send = AsyncMock()
    # No send_slash_confirm override -> button render returns None,
    # _request_slash_confirm falls back to text path.
    adapter.send_slash_confirm = AsyncMock(return_value=None)
    runner.adapters = {Platform.TELEGRAM: adapter}

    session_entry = SessionEntry(
        session_key=build_session_key(_make_source()),
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store.load_transcript.return_value = []
    runner.session_store.append_to_transcript = MagicMock()
    runner.session_store.rewrite_transcript = MagicMock()

    runner._running_agents = {}
    runner._pending_messages = {}
    import itertools as _it
    runner._slash_confirm_counter = _it.count(1)
    runner.hooks = SimpleNamespace(
        emit=AsyncMock(),
        emit_collect=AsyncMock(return_value=[]),
        loaded_hooks=False,
    )
    runner._thread_metadata_for_source = lambda *a, **kw: None
    runner._reply_anchor_for_event = lambda _e: None
    return runner


@pytest.mark.asyncio
async def test_gate_off_runs_execute_immediately(monkeypatch):
    """When approvals.destructive_slash_confirm is False, the destructive
    action runs immediately without prompting."""
    runner = _make_runner()
    runner._read_user_config = lambda: {"approvals": {"destructive_slash_confirm": False}}
    runner._session_key_for_source = lambda src: build_session_key(src)

    sentinel = "✨ Session reset!"
    execute = AsyncMock(return_value=sentinel)

    result = await runner._maybe_confirm_destructive_slash(
        event=_make_event("/new"),
        command="new",
        title="/new",
        detail="Discards history.",
        execute=execute,
    )

    execute.assert_awaited_once()
    assert result == sentinel


@pytest.mark.asyncio
async def test_gate_on_text_fallback_returns_prompt_without_executing(monkeypatch):
    """When the gate is on and the adapter has no button UI, the user gets
    a text prompt back and the destructive action is NOT yet run."""
    runner = _make_runner()
    runner._read_user_config = lambda: {"approvals": {"destructive_slash_confirm": True}}
    runner._session_key_for_source = lambda src: build_session_key(src)

    execute = AsyncMock(return_value="should not run yet")

    result = await runner._maybe_confirm_destructive_slash(
        event=_make_event("/new"),
        command="new",
        title="/new",
        detail="Discards history.",
        execute=execute,
    )

    execute.assert_not_awaited()
    assert isinstance(result, str)
    assert "Confirm /new" in result
    assert "Approve Once" in result
    assert "Cancel" in result


@pytest.mark.asyncio
async def test_gate_on_pending_confirm_registered(monkeypatch):
    """When the gate is on, a pending slash-confirm entry is registered for
    the session — the user's /approve reply will resolve it."""
    from tools import slash_confirm as _slash_confirm_mod
    runner = _make_runner()
    runner._read_user_config = lambda: {"approvals": {"destructive_slash_confirm": True}}
    session_key = build_session_key(_make_source())
    runner._session_key_for_source = lambda src: session_key
    _slash_confirm_mod.clear(session_key)

    execute = AsyncMock(return_value="reset done")

    await runner._maybe_confirm_destructive_slash(
        event=_make_event("/new"),
        command="new",
        title="/new",
        detail="Discards history.",
        execute=execute,
    )

    pending = _slash_confirm_mod.get_pending(session_key)
    assert pending is not None
    assert pending["command"] == "new"
    _slash_confirm_mod.clear(session_key)


@pytest.mark.asyncio
async def test_resolve_once_runs_execute_and_returns_result():
    """Resolving the pending confirm with 'once' runs the destructive
    action and returns its output."""
    from tools import slash_confirm as _slash_confirm_mod
    runner = _make_runner()
    runner._read_user_config = lambda: {"approvals": {"destructive_slash_confirm": True}}
    session_key = build_session_key(_make_source())
    runner._session_key_for_source = lambda src: session_key
    _slash_confirm_mod.clear(session_key)

    execute = AsyncMock(return_value="✨ fresh session")

    await runner._maybe_confirm_destructive_slash(
        event=_make_event("/new"),
        command="new",
        title="/new",
        detail="Discards history.",
        execute=execute,
    )

    pending = _slash_confirm_mod.get_pending(session_key)
    assert pending is not None

    resolved = await _slash_confirm_mod.resolve(
        session_key, pending["confirm_id"], "once",
    )

    execute.assert_awaited_once()
    assert resolved == "✨ fresh session"
    # Pending should be cleared after resolve.
    assert _slash_confirm_mod.get_pending(session_key) is None


@pytest.mark.asyncio
async def test_resolve_cancel_does_not_run_execute():
    """Resolving with 'cancel' must NOT run the destructive action."""
    from tools import slash_confirm as _slash_confirm_mod
    runner = _make_runner()
    runner._read_user_config = lambda: {"approvals": {"destructive_slash_confirm": True}}
    session_key = build_session_key(_make_source())
    runner._session_key_for_source = lambda src: session_key
    _slash_confirm_mod.clear(session_key)

    execute = AsyncMock(side_effect=AssertionError("execute must NOT run on cancel"))

    await runner._maybe_confirm_destructive_slash(
        event=_make_event("/new"),
        command="new",
        title="/new",
        detail="Discards history.",
        execute=execute,
    )

    pending = _slash_confirm_mod.get_pending(session_key)
    assert pending is not None

    resolved = await _slash_confirm_mod.resolve(
        session_key, pending["confirm_id"], "cancel",
    )

    execute.assert_not_awaited()
    assert resolved is not None
    assert "cancelled" in resolved.lower()


@pytest.mark.asyncio
async def test_resolve_always_persists_opt_out_and_runs_execute(monkeypatch):
    """Resolving with 'always' must (a) flip the config gate to False,
    (b) run execute, and (c) include a one-time opt-out note in the reply."""
    from tools import slash_confirm as _slash_confirm_mod
    runner = _make_runner()
    runner._read_user_config = lambda: {"approvals": {"destructive_slash_confirm": True}}
    session_key = build_session_key(_make_source())
    runner._session_key_for_source = lambda src: session_key
    _slash_confirm_mod.clear(session_key)

    saved: dict = {}

    def _fake_save_detailed(path, value):
        saved[path] = value
        return True, None

    # Issue #27660 reworked the gateway to call ``save_config_value_detailed``
    # (which surfaces the failure reason) instead of the bool-only
    # ``save_config_value``.  Patch the new symbol so this test still
    # exercises the post-fix code path; keep the bool-only alias patched
    # too for any indirect callers.
    import cli as cli_mod
    monkeypatch.setattr(cli_mod, "save_config_value_detailed", _fake_save_detailed)
    monkeypatch.setattr(
        cli_mod, "save_config_value", lambda p, v: _fake_save_detailed(p, v)[0]
    )

    execute = AsyncMock(return_value="✨ fresh")

    await runner._maybe_confirm_destructive_slash(
        event=_make_event("/new"),
        command="new",
        title="/new",
        detail="Discards history.",
        execute=execute,
    )

    pending = _slash_confirm_mod.get_pending(session_key)
    assert pending is not None
    resolved = await _slash_confirm_mod.resolve(
        session_key, pending["confirm_id"], "always",
    )

    execute.assert_awaited_once()
    assert saved.get("approvals.destructive_slash_confirm") is False
    assert resolved is not None
    assert "✨ fresh" in resolved
    assert "config.yaml" in resolved


# ---------------------------------------------------------------------------
# Issue #27660 -- "Always Approve" silent-failure regression guard.
#
# Before the fix, gateway/run.py's destructive-slash _on_confirm
# unconditionally logged "User opted out..." and appended the
# "Future /clear, /new, /reset, /undo will run without confirmation"
# note even when save_config_value silently failed (e.g. ruamel.yaml
# missing from venv).  Users could click "Always Approve" indefinitely
# with no effect.  These tests pin the post-fix behaviour:
#
#   1. When persistence fails, the reply tells the user it failed AND
#      includes the underlying reason.
#   2. When persistence fails, the misleading "future runs no
#      confirmation" note must NOT appear.
#   3. When persistence succeeds, the reply still contains the success
#      note (existing behaviour preserved).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_always_surfaces_persist_failure_to_user(monkeypatch):
    """Save failure -> user sees ⚠️ message with the failure reason."""
    from tools import slash_confirm as _slash_confirm_mod
    runner = _make_runner()
    runner._read_user_config = lambda: {"approvals": {"destructive_slash_confirm": True}}
    session_key = build_session_key(_make_source())
    runner._session_key_for_source = lambda src: session_key
    _slash_confirm_mod.clear(session_key)

    # Simulate ruamel.yaml missing -- the exact #27660 scenario.
    def _failing_save(path, value):
        return False, (
            "ruamel.yaml is required to update user config files atomically. "
            "Re-install with: pip install ruamel.yaml==0.18.17. "
            "Underlying ImportError: No module named 'ruamel'"
        )

    import cli as cli_mod
    monkeypatch.setattr(cli_mod, "save_config_value_detailed", _failing_save)

    execute = AsyncMock(return_value="✨ fresh")

    await runner._maybe_confirm_destructive_slash(
        event=_make_event("/new"),
        command="new",
        title="/new",
        detail="Discards history.",
        execute=execute,
    )

    pending = _slash_confirm_mod.get_pending(session_key)
    assert pending is not None
    resolved = await _slash_confirm_mod.resolve(
        session_key, pending["confirm_id"], "always",
    )

    execute.assert_awaited_once()
    assert resolved is not None
    # The user-facing reply must:
    assert "✨ fresh" in resolved, "the actual command output should still surface"
    assert "Could not save the opt-out preference" in resolved, (
        "user must be told the opt-out wasn't saved"
    )
    assert "ruamel.yaml" in resolved, "the failure reason must be included"
    # The pre-fix "future runs will skip the prompt" note must NOT
    # appear when the save failed -- that was the original bug.
    assert "without confirmation" not in resolved, (
        "must not falsely claim future runs will skip the prompt"
    )


@pytest.mark.asyncio
async def test_resolve_always_no_warning_when_persist_succeeds(monkeypatch):
    """Save success -> existing success note appears, no ⚠️."""
    from tools import slash_confirm as _slash_confirm_mod
    runner = _make_runner()
    runner._read_user_config = lambda: {"approvals": {"destructive_slash_confirm": True}}
    session_key = build_session_key(_make_source())
    runner._session_key_for_source = lambda src: session_key
    _slash_confirm_mod.clear(session_key)

    def _ok_save(path, value):
        return True, None

    import cli as cli_mod
    monkeypatch.setattr(cli_mod, "save_config_value_detailed", _ok_save)

    execute = AsyncMock(return_value="✨ fresh")

    await runner._maybe_confirm_destructive_slash(
        event=_make_event("/new"),
        command="new",
        title="/new",
        detail="Discards history.",
        execute=execute,
    )

    pending = _slash_confirm_mod.get_pending(session_key)
    assert pending is not None
    resolved = await _slash_confirm_mod.resolve(
        session_key, pending["confirm_id"], "always",
    )

    execute.assert_awaited_once()
    assert resolved is not None
    assert "✨ fresh" in resolved
    # Existing success branch preserved.
    assert "without confirmation" in resolved
    # No misleading warning when the save actually worked.
    assert "Could not save" not in resolved


@pytest.mark.asyncio
async def test_resolve_always_handles_unexpected_exception(monkeypatch):
    """If save_config_value_detailed raises (not just returns False), the
    user still gets a coherent error reply rather than a stack trace
    bubbling out of the handler."""
    from tools import slash_confirm as _slash_confirm_mod
    runner = _make_runner()
    runner._read_user_config = lambda: {"approvals": {"destructive_slash_confirm": True}}
    session_key = build_session_key(_make_source())
    runner._session_key_for_source = lambda src: session_key
    _slash_confirm_mod.clear(session_key)

    def _exploding_save(path, value):
        raise RuntimeError("disk caught fire")

    import cli as cli_mod
    monkeypatch.setattr(cli_mod, "save_config_value_detailed", _exploding_save)

    execute = AsyncMock(return_value="✨ fresh")
    await runner._maybe_confirm_destructive_slash(
        event=_make_event("/new"),
        command="new",
        title="/new",
        detail="Discards history.",
        execute=execute,
    )

    pending = _slash_confirm_mod.get_pending(session_key)
    resolved = await _slash_confirm_mod.resolve(
        session_key, pending["confirm_id"], "always",
    )

    execute.assert_awaited_once()
    assert resolved is not None
    assert "✨ fresh" in resolved
    assert "Could not save" in resolved
    assert "RuntimeError" in resolved
    assert "disk caught fire" in resolved
    assert "without confirmation" not in resolved
