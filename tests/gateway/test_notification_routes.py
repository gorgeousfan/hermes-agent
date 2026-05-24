"""Tests for outbound notification reply routing metadata."""

import time

import gateway.notification_routes as routes
from gateway.notification_routes import (
    build_webui_session_url,
    register_outbound_notification,
    resolve_latest_followup,
)


def _route():
    return {
        "kind": "background_process",
        "session_key": "agent:main:telegram:dm:123:24296",
        "source": {
            "platform": "telegram",
            "chat_id": "123",
            "chat_type": "dm",
            "thread_id": "24296",
            "user_id": "456",
            "user_name": "Alice",
        },
    }


def test_latest_followup_expires(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "_hermes_home", lambda: tmp_path)
    register_outbound_notification(
        platform="telegram",
        chat_id="999",
        message_ids=["700"],
        route=_route(),
        ttl_seconds=10,
        now=1_000,
    )

    assert resolve_latest_followup(
        platform="telegram",
        chat_id="999",
        text="ок, продолжай",
        now=1_020,
    ) is None


def test_latest_followup_does_not_hijack_slash_commands(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "_hermes_home", lambda: tmp_path)
    register_outbound_notification(
        platform="telegram",
        chat_id="999",
        message_ids=["700"],
        route=_route(),
        ttl_seconds=1800,
        now=time.time(),
    )

    assert resolve_latest_followup(
        platform="telegram",
        chat_id="999",
        text="/new unrelated task",
    ) is None


def test_latest_followup_does_not_hijack_short_new_tasks(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "_hermes_home", lambda: tmp_path)
    register_outbound_notification(
        platform="telegram",
        chat_id="999",
        message_ids=["700"],
        route=_route(),
        ttl_seconds=1800,
        now=time.time(),
    )

    assert resolve_latest_followup(
        platform="telegram",
        chat_id="999",
        text="создай новый отчет",
    ) is None


def test_latest_followup_can_target_webui_api_session_without_platform_source(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "_hermes_home", lambda: tmp_path)
    register_outbound_notification(
        platform="telegram",
        chat_id="999",
        message_ids=["701"],
        route={
            "kind": "webui_session",
            "api_session_id": "20260524_142634_986d3f",
            "session_id": "20260524_142634_986d3f",
            "webui_url": "https://hermes.example.com/session/20260524_142634_986d3f",
        },
        ttl_seconds=1800,
        now=time.time(),
    )

    resolved = resolve_latest_followup(
        platform="telegram",
        chat_id="999",
        text="ок, проверь",
    )

    assert resolved is not None
    assert "source" not in resolved
    assert resolved["route"]["kind"] == "webui_session"
    assert resolved["route"]["api_session_id"] == "20260524_142634_986d3f"
    assert resolved["route"]["webui_url"] == "https://hermes.example.com/session/20260524_142634_986d3f"


def test_webui_session_url_prefers_explicit_public_url(monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_PUBLIC_URL", "https://hermes.example.com/base/")
    monkeypatch.setenv("HERMES_WEBUI_PORT", "8788")

    assert build_webui_session_url("abc123") == "https://hermes.example.com/base/chat?resume=abc123"


def test_webui_session_url_falls_back_to_server_ip_and_port(monkeypatch):
    monkeypatch.delenv("HERMES_WEBUI_PUBLIC_URL", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_BASE_URL", raising=False)
    monkeypatch.setenv("HERMES_WEBUI_PORT", "8788")
    monkeypatch.setattr(routes, "_detect_server_ip", lambda: "70.34.246.102")

    assert build_webui_session_url("00cd34bde02b") == "http://70.34.246.102:8788/chat?resume=00cd34bde02b"


def test_webui_session_url_unavailable_without_session_or_host(monkeypatch):
    monkeypatch.delenv("HERMES_WEBUI_PUBLIC_URL", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_BASE_URL", raising=False)
    monkeypatch.setenv("HERMES_WEBUI_PORT", "8788")
    monkeypatch.setattr(routes, "_detect_server_ip", lambda: None)

    assert build_webui_session_url("") is None
    assert build_webui_session_url("abc123") is None


def test_webui_session_url_uses_chat_resume_path_not_session_id_path(monkeypatch):
    """The WebUI router has no ``/session/<id>`` route — only ``/chat?resume=<id>``.

    A stale ``/session/<id>`` link would hit the catch-all and silently bounce
    the user to ``/sessions``, which presents as the "wrong chat" symptom for
    Telegram notification replies bridged into a WebUI session.
    """
    monkeypatch.setenv("HERMES_WEBUI_PUBLIC_URL", "https://hermes.example.com")
    url = build_webui_session_url("abc123")
    assert url is not None
    assert "/chat?resume=" in url
    assert "/session/abc123" not in url
