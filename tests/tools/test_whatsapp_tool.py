"""Tests for tools/whatsapp_tool.py."""

import json


def test_send_poll_posts_bridge_payload(monkeypatch):
    from tools import whatsapp_tool as mod

    calls = []

    def fake_request(method, path, payload=None, timeout=20):
        calls.append((method, path, payload, timeout))
        return {"success": True, "messageId": "poll-1"}

    monkeypatch.setattr(mod, "_request_bridge", fake_request)

    result = json.loads(mod.whatsapp_tool({
        "action": "send_poll",
        "chat_id": "120363@g.us",
        "question": "Deploy?",
        "options": ["yes", "no"],
        "selectable_count": 1,
    }))

    assert result == {"success": True, "messageId": "poll-1"}
    assert calls == [(
        "POST",
        "/send-poll",
        {
            "chatId": "120363@g.us",
            "question": "Deploy?",
            "options": ["yes", "no"],
            "selectableCount": 1,
        },
        20,
    )]


def test_group_participants_encodes_chat_id(monkeypatch):
    from tools import whatsapp_tool as mod

    calls = []

    def fake_request(method, path, payload=None, timeout=20):
        calls.append((method, path, payload, timeout))
        return {"success": True, "participants": []}

    monkeypatch.setattr(mod, "_request_bridge", fake_request)

    result = json.loads(mod.whatsapp_tool({
        "action": "group_participants",
        "chat_id": "120363@g.us",
    }))

    assert result == {"success": True, "participants": []}
    assert calls == [("GET", "/groups/120363%40g.us/participants", None, 20)]


def test_buttons_require_buttons_array(monkeypatch):
    from tools import whatsapp_tool as mod

    monkeypatch.setattr(mod, "_request_bridge", lambda *_args, **_kwargs: {"success": True})

    result = json.loads(mod.whatsapp_tool({
        "action": "send_buttons",
        "chat_id": "15551234567@s.whatsapp.net",
        "text": "Pick one",
        "buttons": [],
    }))

    assert "buttons" in result["error"]
