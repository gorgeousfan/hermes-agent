"""Tests for expanded WhatsApp bridge feature wrappers."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


class _AsyncCM:
    """Minimal async context manager returning a fixed value."""

    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *exc):
        return False


def _make_adapter():
    """Create a WhatsAppAdapter with test attributes (bypass __init__)."""
    from gateway.platforms.whatsapp import WhatsAppAdapter

    adapter = WhatsAppAdapter.__new__(WhatsAppAdapter)
    adapter.platform = Platform.WHATSAPP
    adapter.config = MagicMock()
    adapter.config.extra = {}
    adapter._bridge_port = 3000
    adapter._bridge_script = "/tmp/test-bridge.js"
    adapter._session_path = MagicMock()
    adapter._bridge_log_fh = None
    adapter._bridge_log = None
    adapter._bridge_process = None
    adapter._reply_prefix = None
    adapter._running = True
    adapter._message_handler = None
    adapter._fatal_error_code = None
    adapter._fatal_error_message = None
    adapter._fatal_error_retryable = True
    adapter._fatal_error_handler = None
    adapter._active_sessions = {}
    adapter._pending_messages = {}
    adapter._background_tasks = set()
    adapter._auto_tts_disabled_chats = set()
    adapter._message_queue = asyncio.Queue()
    adapter._http_session = MagicMock()
    adapter._mention_patterns = []
    return adapter


def _json_response(status=200, data=None, text=""):
    resp = MagicMock(status=status)
    resp.json = AsyncMock(return_value=data or {})
    resp.text = AsyncMock(return_value=text)
    return resp


def _clear_approval_state():
    from tools import approval as mod

    mod._gateway_queues.clear()
    mod._gateway_notify_cbs.clear()
    mod._session_approved.clear()
    mod._permanent_approved.clear()
    mod._pending.clear()


def _make_whatsapp_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.WHATSAPP: PlatformConfig(enabled=True, token="")}
    )
    adapter = MagicMock()
    adapter.resume_typing_for_chat = MagicMock()
    runner.adapters = {Platform.WHATSAPP: adapter}
    runner.session_store = None
    runner._pending_approvals = {}
    return runner


def _make_whatsapp_event(text="/approve"):
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=Platform.WHATSAPP,
            user_id="15551234567@s.whatsapp.net",
            chat_id="15551234567@s.whatsapp.net",
            user_name="operator",
            chat_type="dm",
        ),
        message_id="wa-msg-1",
    )


class TestWhatsAppExecApproval:
    """send_exec_approval should use the bridge's rich approval surface."""

    def setup_method(self):
        _clear_approval_state()

    def teardown_method(self):
        _clear_approval_state()

    def test_send_exec_approval_is_class_method(self):
        from gateway.platforms.whatsapp import WhatsAppAdapter

        assert getattr(WhatsAppAdapter, "send_exec_approval", None) is not None

    @pytest.mark.asyncio
    async def test_sends_poll_approval_prompt(self):
        adapter = _make_adapter()
        resp = _json_response(data={"success": True, "messageId": "wa-msg-1"})
        adapter._http_session.post = MagicMock(return_value=_AsyncCM(resp))

        result = await adapter.send_exec_approval(
            chat_id="15551234567@s.whatsapp.net",
            command="rm -rf /important",
            session_key="agent:main:whatsapp:dm:15551234567",
            description="dangerous deletion",
        )

        assert result.success is True
        assert result.message_id == "wa-msg-1"

        url = adapter._http_session.post.call_args.args[0]
        payload = adapter._http_session.post.call_args.kwargs["json"]
        assert url.endswith("/send-poll")
        assert payload["chatId"] == "15551234567@s.whatsapp.net"
        assert "rm -rf /important" in payload["question"]
        assert "dangerous deletion" in payload["question"]
        assert payload["options"] == [
            "Approve once",
            "Approve session",
            "Approve always",
            "Deny",
        ]
        assert payload["approvalActions"] == {
            "Approve once": "/approve",
            "Approve session": "/approve session",
            "Approve always": "/approve always",
            "Deny": "/deny",
        }

    @pytest.mark.asyncio
    async def test_poll_error_falls_back_to_text_approval(self):
        adapter = _make_adapter()
        poll_resp = _json_response(status=501, text="polls unsupported")
        text_resp = _json_response(data={"success": True, "messageId": "text-fallback"})
        adapter._http_session.post = MagicMock(
            side_effect=[_AsyncCM(poll_resp), _AsyncCM(text_resp)]
        )

        result = await adapter.send_exec_approval(
            chat_id="chat",
            command="ls",
            session_key="session",
        )

        assert result.success is True
        assert result.message_id == "text-fallback"
        first_call, second_call = adapter._http_session.post.call_args_list
        assert first_call.args[0].endswith("/send-poll")
        assert second_call.args[0].endswith("/send")
        assert "Reply `/approve`" in second_call.kwargs["json"]["message"]

    @pytest.mark.asyncio
    async def test_approval_id_is_embedded_in_poll_actions(self):
        adapter = _make_adapter()
        resp = _json_response(data={"success": True, "messageId": "wa-msg-2"})
        adapter._http_session.post = MagicMock(return_value=_AsyncCM(resp))

        await adapter.send_exec_approval(
            chat_id="15551234567@s.whatsapp.net",
            command="rm -rf /important",
            session_key="agent:main:whatsapp:dm:15551234567",
            metadata={"approval_id": "abc123"},
        )

        payload = adapter._http_session.post.call_args.kwargs["json"]
        assert payload["options"] == ["Approve once", "Approve session", "Approve always", "Deny"]
        assert payload["approvalActions"] == {
            "Approve once": "/approve abc123",
            "Approve session": "/approve abc123 session",
            "Approve always": "/approve abc123 always",
            "Deny": "/deny abc123",
        }

    @pytest.mark.asyncio
    async def test_interactive_approve_session_routes_through_gateway_queue(self):
        from tools.approval import _ApprovalEntry, _gateway_queues

        runner = _make_whatsapp_runner()
        event = _make_whatsapp_event("/approve session")
        session_key = runner._session_key_for_source(event.source)
        entry = _ApprovalEntry({"command": "rm -rf /tmp/demo"})
        _gateway_queues[session_key] = [entry]

        result = await runner._handle_approve_command(event)

        assert "session" in result.lower()
        assert entry.event.is_set()
        assert entry.result == "session"
        runner.adapters[Platform.WHATSAPP].resume_typing_for_chat.assert_called_once_with(
            "15551234567@s.whatsapp.net"
        )

    @pytest.mark.asyncio
    async def test_interactive_approval_id_resolves_matching_queue_entry(self):
        from tools.approval import _ApprovalEntry, _gateway_queues

        runner = _make_whatsapp_runner()
        event = _make_whatsapp_event("/approve approval-new session")
        session_key = runner._session_key_for_source(event.source)
        old_entry = _ApprovalEntry({"approval_id": "approval-old", "command": "first"})
        new_entry = _ApprovalEntry({"approval_id": "approval-new", "command": "second"})
        _gateway_queues[session_key] = [old_entry, new_entry]

        result = await runner._handle_approve_command(event)

        assert "session" in result.lower()
        assert not old_entry.event.is_set()
        assert new_entry.event.is_set()
        assert new_entry.result == "session"
        assert _gateway_queues[session_key] == [old_entry]


class TestWhatsAppFeatureWrappers:
    """Python adapter wrappers should call stable bridge endpoints."""

    @pytest.mark.asyncio
    async def test_send_poll(self):
        adapter = _make_adapter()
        resp = _json_response(data={"success": True, "messageId": "poll-1"})
        adapter._http_session.post = MagicMock(return_value=_AsyncCM(resp))

        result = await adapter.send_poll(
            "120363000000000000@g.us",
            "Deploy?",
            ["yes", "no"],
            selectable_count=1,
        )

        assert result.success is True
        assert result.message_id == "poll-1"
        url = adapter._http_session.post.call_args.args[0]
        payload = adapter._http_session.post.call_args.kwargs["json"]
        assert url.endswith("/send-poll")
        assert payload == {
            "chatId": "120363000000000000@g.us",
            "question": "Deploy?",
            "options": ["yes", "no"],
            "selectableCount": 1,
        }

    @pytest.mark.asyncio
    async def test_send_presence(self):
        adapter = _make_adapter()
        resp = _json_response(data={"success": True})
        adapter._http_session.post = MagicMock(return_value=_AsyncCM(resp))

        result = await adapter.send_presence("chat", "recording")

        assert result.success is True
        url = adapter._http_session.post.call_args.args[0]
        payload = adapter._http_session.post.call_args.kwargs["json"]
        assert url.endswith("/presence")
        assert payload == {"chatId": "chat", "state": "recording"}

    @pytest.mark.asyncio
    async def test_list_groups(self):
        adapter = _make_adapter()
        groups = [{"id": "120363@g.us", "name": "Ops"}]
        resp = _json_response(data={"success": True, "groups": groups})
        adapter._http_session.get = MagicMock(return_value=_AsyncCM(resp))

        result = await adapter.list_groups()

        assert result == groups
        assert adapter._http_session.get.call_args.args[0].endswith("/groups")

    @pytest.mark.asyncio
    async def test_get_group_participants(self):
        adapter = _make_adapter()
        participants = [{"id": "15551234567@s.whatsapp.net", "admin": "admin"}]
        resp = _json_response(data={"success": True, "participants": participants})
        adapter._http_session.get = MagicMock(return_value=_AsyncCM(resp))

        result = await adapter.get_group_participants("120363@g.us")

        assert result == participants
        assert adapter._http_session.get.call_args.args[0].endswith(
            "/groups/120363%40g.us/participants"
        )

    @pytest.mark.asyncio
    async def test_get_lid_map(self):
        adapter = _make_adapter()
        mapping = {
            "lidToPhone": {"111@lid": "15551234567"},
            "phoneToLid": {"15551234567": "111@lid"},
        }
        resp = _json_response(data={"success": True, **mapping})
        adapter._http_session.get = MagicMock(return_value=_AsyncCM(resp))

        result = await adapter.get_lid_map()

        assert result == mapping
        assert adapter._http_session.get.call_args.args[0].endswith("/lid-map")

    @pytest.mark.asyncio
    async def test_get_labels_preserves_supported_flag(self):
        adapter = _make_adapter()
        data = {"success": True, "supported": False, "labels": []}
        resp = _json_response(data=data)
        adapter._http_session.get = MagicMock(return_value=_AsyncCM(resp))

        result = await adapter.get_labels()

        assert result == {"supported": False, "labels": []}
        assert adapter._http_session.get.call_args.args[0].endswith("/labels")
