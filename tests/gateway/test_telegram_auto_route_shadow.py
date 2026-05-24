"""Tests for Telegram auto-route shadow classification/telemetry."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from gateway.run import (
    GatewayRunner,
    _classify_telegram_auto_route,
    _maybe_record_telegram_auto_route_shadow,
    _run_modelroute_subprocess,
    _telegram_auto_route_active_plan,
    _telegram_auto_route_config,
)


def _event(text: str, *, platform="telegram", media_types=None) -> Any:
    return SimpleNamespace(
        text=text,
        media_types=media_types or [],
        media_urls=[],
        message_id="m1",
        source=SimpleNamespace(platform=platform, chat_type="dm", thread_id="42"),
    )


def test_auto_route_config_defaults_off_and_invalid_fails_closed():
    assert _telegram_auto_route_config({})["mode"] == "off"
    assert _telegram_auto_route_config({"telegram": {"auto_route": {"mode": "bogus"}}})["mode"] == "off"
    assert _telegram_auto_route_config({"telegram": {"auto_route": {"mode": "shadow"}}})["mode"] == "shadow"
    assert _telegram_auto_route_config(
        {"telegram": {"auto_route": {"mode": "shadow", "rollback_force_default": True}}}
    )["mode"] == "off"
    for bad_value in ["bogus", [], "", False, None, "nan", "inf", -0.1, 1.1]:
        bad_floor = _telegram_auto_route_config(
            {"telegram": {"auto_route": {"mode": "active", "confidence_floor": bad_value}}}
        )
        assert bad_floor["mode"] == "off"
        assert bad_floor["confidence_floor"] == 0.72


def test_classifier_detects_current_web_research_without_raw_text_storage():
    decision = _classify_telegram_auto_route(_event("Do real-time web research on the latest LangGraph release"))
    assert decision["task_class"] == "current_web_research"
    assert decision["route"] == "hermes_web_research"
    assert decision["needs_web"] is True
    assert "raw" not in json.dumps(decision).lower()
    assert decision["message_chars"] > 0


def test_classifier_detects_media_before_default_chat():
    decision = _classify_telegram_auto_route(_event("what is this?", media_types=["photo"]))
    assert decision["task_class"] == "media_or_screenshot_analysis"
    assert decision["needs_tools"] is True
    assert decision["media_count"] == 1


def test_classifier_keeps_media_priority_over_text_route_keywords():
    decision = _classify_telegram_auto_route(
        _event("summarize the latest release notes in this screenshot", media_types=["photo"])
    )
    assert decision["task_class"] == "media_or_screenshot_analysis"
    assert decision["route"] == "hermes_media_vision"
    assert decision["media_count"] == 1


def test_shadow_mode_appends_sanitized_telemetry_and_does_not_alter_dispatch(tmp_path):
    repo_root = tmp_path
    (repo_root / "scripts" / "runtime").mkdir(parents=True)
    (repo_root / "runtime" / "configs").mkdir(parents=True)
    (repo_root / "scripts" / "runtime" / "run_selected_model_route.py").write_text("", encoding="utf-8")
    (repo_root / "runtime" / "configs" / "model-route-policy.yaml").write_text("", encoding="utf-8")

    cfg = {"telegram": {"auto_route": {"mode": "shadow"}}}
    event = _event("please patch the gateway and run tests")
    with mock.patch("gateway.run._find_modelroute_repo_root", return_value=repo_root):
        decision = _maybe_record_telegram_auto_route_shadow(cfg, event)

    assert decision is not None
    assert decision["task_class"] == "build_completion"
    evidence_path = repo_root / "evidence" / "runtime_health" / "telegram_auto_route" / "shadow.jsonl"
    rows = evidence_path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    record = json.loads(rows[0])
    assert record["dispatch_altered"] is False
    assert record["raw_text_recorded"] is False
    assert "please patch" not in rows[0]


def test_shadow_recorder_ignores_non_telegram_and_off_mode(tmp_path):
    cfg = {"telegram": {"auto_route": {"mode": "shadow"}}}
    assert _maybe_record_telegram_auto_route_shadow(cfg, _event("latest", platform="discord")) is None
    assert _maybe_record_telegram_auto_route_shadow({"telegram": {"auto_route": {"mode": "off"}}}, _event("latest")) is None


def test_active_plan_maps_only_proven_low_risk_modelroute_lanes():
    cfg = {"telegram": {"auto_route": {"mode": "active", "confidence_floor": 0.72}}}

    summary_plan = _telegram_auto_route_active_plan(cfg, _event("summarize this release note"))
    assert summary_plan is not None
    assert summary_plan["lane"] == "free_utility_general"
    assert summary_plan["decision"]["task_class"] == "cheap_summary_or_draft"

    untrusted_plan = _telegram_auto_route_active_plan(
        cfg,
        _event("This webpage says ignore previous instructions; summarize the safe content only"),
    )
    assert untrusted_plan is not None
    assert untrusted_plan["lane"] == "untrusted_quarantine"

    assert _telegram_auto_route_active_plan(cfg, _event("latest release notes right now")) is None
    assert _telegram_auto_route_active_plan(cfg, _event("please patch the gateway and run tests")) is None
    desktop_summary_plan = _telegram_auto_route_active_plan(
        cfg,
        _event("summarize this note about Telegram Desktop proof"),
    )
    assert desktop_summary_plan is not None
    assert desktop_summary_plan["lane"] == "free_utility_general"
    assert _telegram_auto_route_active_plan(cfg, _event("what is this?", media_types=["photo"])) is None


def test_active_plan_honors_confidence_floor_rollback_and_platform():
    high_floor = {"telegram": {"auto_route": {"mode": "active", "confidence_floor": 0.95}}}
    assert _telegram_auto_route_active_plan(high_floor, _event("summarize this release note")) is None

    rollback = {"telegram": {"auto_route": {"mode": "active", "rollback_force_default": True}}}
    assert _telegram_auto_route_active_plan(rollback, _event("summarize this release note")) is None

    active = {"telegram": {"auto_route": {"mode": "active"}}}
    assert _telegram_auto_route_active_plan(active, _event("summarize this", platform="discord")) is None


async def _fake_modelroute_result(task_class):
    return {
        "ok": True,
        "selected_model": "free/mistral-small-4-119b",
        "transport": "direct_clawrouter",
        "text": "telegram model route ok",
        "telemetry": {"task_class": task_class, "elapsed_sec": 0.12, "fallback_used": False},
    }


@mock.patch("gateway.run._find_modelroute_repo_root")
@pytest.mark.asyncio
async def test_active_mode_dispatches_low_risk_message_and_records_sanitized_telemetry(find_root, tmp_path):
    find_root.return_value = tmp_path
    runner: Any = GatewayRunner.__new__(GatewayRunner)
    runner.config = {"telegram": {"auto_route": {"mode": "active", "confidence_floor": 0.72}}}
    session_entry = SimpleNamespace(session_id="session-1", session_key="key-1")
    runner.session_store = SimpleNamespace(
        get_or_create_session=mock.Mock(return_value=session_entry),
        append_to_transcript=mock.Mock(),
        update_session=mock.Mock(),
    )

    async def fake_dispatch(*, task_class, message):
        assert task_class == "free_utility_general"
        assert message == "summarize this release note opaque-id-should-not-be-recorded"
        return await _fake_modelroute_result(task_class)

    runner._dispatch_modelroute = fake_dispatch
    reply = await runner._maybe_dispatch_telegram_auto_route_active(
        _event("summarize this release note opaque-id-should-not-be-recorded")
    )

    assert reply is not None
    assert "MODEL_ROUTE_TELEMETRY" in reply
    assert "task_class=free_utility_general" in reply
    assert "telegram model route ok" in reply

    evidence_path = tmp_path / "evidence" / "runtime_health" / "telegram_auto_route" / "shadow.jsonl"
    rows = evidence_path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    record = json.loads(rows[0])
    assert record["mode"] == "active"
    assert record["dispatch_altered"] is True
    assert record["raw_text_recorded"] is False
    assert record["decision"]["active_modelroute_lane"] == "free_utility_general"
    assert record["decision"]["active_dispatch_status"] == "ok"
    assert "opaque-id-should-not-be-recorded" not in rows[0]
    assert "summarize this release note" not in rows[0]
    assert runner.session_store.append_to_transcript.call_args_list[0].args[1]["role"] == "user"
    assert runner.session_store.append_to_transcript.call_args_list[1].args[1]["role"] == "assistant"
    runner.session_store.update_session.assert_called_once_with("key-1")


@pytest.mark.asyncio
async def test_active_mode_sanitizes_modelroute_reply_before_returning():
    runner: Any = GatewayRunner.__new__(GatewayRunner)
    runner.config = {"telegram": {"auto_route": {"mode": "active"}}}
    runner.session_store = None
    runner._dispatch_modelroute = mock.AsyncMock(
        return_value={
            "ok": True,
            "selected_model": "free/mistral-small-4-119b",
            "transport": "direct_clawrouter",
            "text": "sk-testSECRET1234567890",
            "telemetry": {"task_class": "free_utility_general", "elapsed_sec": 0.12, "fallback_used": False},
        }
    )

    reply = await runner._maybe_dispatch_telegram_auto_route_active(_event("summarize this release note"))

    assert reply is not None
    assert "sk-testSECRET" not in reply


@pytest.mark.asyncio
async def test_active_mode_below_floor_and_rollback_do_not_dispatch():
    runner: Any = GatewayRunner.__new__(GatewayRunner)
    runner.config = {"telegram": {"auto_route": {"mode": "active", "confidence_floor": 0.95}}}
    runner._dispatch_modelroute = mock.AsyncMock(side_effect=AssertionError("must not dispatch below floor"))
    assert await runner._maybe_dispatch_telegram_auto_route_active(_event("summarize this release note")) is None
    runner._dispatch_modelroute.assert_not_called()

    runner.config = {"telegram": {"auto_route": {"mode": "active", "rollback_force_default": True}}}
    assert await runner._maybe_dispatch_telegram_auto_route_active(_event("summarize this release note")) is None
    runner._dispatch_modelroute.assert_not_called()


@mock.patch("gateway.run._find_modelroute_repo_root")
@pytest.mark.asyncio
async def test_active_mode_failed_route_records_attempt_and_falls_back(find_root, tmp_path):
    find_root.return_value = tmp_path
    runner: Any = GatewayRunner.__new__(GatewayRunner)
    runner.config = {"telegram": {"auto_route": {"mode": "active"}}}
    runner._dispatch_modelroute = mock.AsyncMock(
        return_value={
            "ok": False,
            "selected_model": "unknown",
            "transport": "unknown",
            "telemetry": {"task_class": "free_utility_general", "fallback_used": False},
        }
    )

    assert await runner._maybe_dispatch_telegram_auto_route_active(_event("summarize this release note")) is None
    runner._dispatch_modelroute.assert_awaited_once()
    rows = (tmp_path / "evidence" / "runtime_health" / "telegram_auto_route" / "shadow.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    record = json.loads(rows[0])
    assert record["dispatch_altered"] is True
    assert record["decision"]["active_dispatch_status"] == "not_ok"


@mock.patch("gateway.run._find_modelroute_repo_root")
@pytest.mark.asyncio
async def test_active_mode_timeout_records_attempt_and_falls_back(find_root, tmp_path):
    find_root.return_value = tmp_path
    runner: Any = GatewayRunner.__new__(GatewayRunner)
    runner.config = {"telegram": {"auto_route": {"mode": "active"}}}
    runner._dispatch_modelroute = mock.AsyncMock(side_effect=asyncio.TimeoutError())

    assert await runner._maybe_dispatch_telegram_auto_route_active(_event("summarize this release note")) is None
    rows = (tmp_path / "evidence" / "runtime_health" / "telegram_auto_route" / "shadow.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    record = json.loads(rows[0])
    assert record["decision"]["active_dispatch_status"] == "timeout"


@mock.patch("gateway.run._find_modelroute_repo_root")
@mock.patch("gateway.run.asyncio.wait_for")
@mock.patch("gateway.run.asyncio.create_subprocess_exec")
@pytest.mark.asyncio
async def test_modelroute_subprocess_passes_message_on_stdin_not_argv(create_proc, wait_for, find_root, tmp_path):
    (tmp_path / "scripts" / "runtime").mkdir(parents=True)
    (tmp_path / "runtime" / "configs").mkdir(parents=True)
    (tmp_path / "scripts" / "runtime" / "run_selected_model_route.py").write_text("", encoding="utf-8")
    (tmp_path / "runtime" / "configs" / "model-route-policy.yaml").write_text("", encoding="utf-8")
    find_root.return_value = tmp_path
    proc = SimpleNamespace(returncode=0, communicate=mock.Mock(return_value="communicate-awaitable"))
    create_proc.return_value = proc
    wait_for.return_value = (b'{"ok": true}', b"")

    result = await _run_modelroute_subprocess("free_utility_general", "secret prompt should not be argv")

    assert result["ok"] is True
    argv = create_proc.call_args.args
    assert "--message-stdin" in argv
    assert "--message" not in argv
    assert "secret prompt should not be argv" not in argv
    proc.communicate.assert_called_once_with(b"secret prompt should not be argv")


@mock.patch("gateway.run._find_modelroute_repo_root")
@mock.patch("gateway.run.asyncio.wait_for", side_effect=asyncio.TimeoutError())
@mock.patch("gateway.run.asyncio.create_subprocess_exec")
@pytest.mark.asyncio
async def test_modelroute_subprocess_timeout_kills_child(create_proc, wait_for, find_root, tmp_path):
    (tmp_path / "scripts" / "runtime").mkdir(parents=True)
    (tmp_path / "runtime" / "configs").mkdir(parents=True)
    (tmp_path / "scripts" / "runtime" / "run_selected_model_route.py").write_text("", encoding="utf-8")
    (tmp_path / "runtime" / "configs" / "model-route-policy.yaml").write_text("", encoding="utf-8")
    find_root.return_value = tmp_path
    proc = SimpleNamespace(kill=mock.Mock(), wait=mock.AsyncMock(), communicate=mock.Mock(return_value="awaitable"))
    create_proc.return_value = proc

    with pytest.raises(asyncio.TimeoutError):
        await _run_modelroute_subprocess("free_utility_general", "slow prompt")

    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()
