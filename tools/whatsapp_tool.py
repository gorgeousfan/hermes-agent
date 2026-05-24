"""WhatsApp bridge tool -- rich WhatsApp operations via the local bridge."""

import json
import os
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


WHATSAPP_SCHEMA = {
    "name": "whatsapp",
    "description": (
        "Use the local WhatsApp bridge for rich WhatsApp features: polls, "
        "buttons, lists, presence, group discovery, participants, LID map, "
        "account info, and labels. Use send_message for plain text/media."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "send_poll",
                    "send_buttons",
                    "send_list",
                    "set_presence",
                    "list_groups",
                    "group_participants",
                    "lid_map",
                    "account",
                    "labels",
                ],
                "description": "WhatsApp bridge action to perform.",
            },
            "chat_id": {
                "type": "string",
                "description": "WhatsApp chat JID, e.g. 15551234567@s.whatsapp.net or 120363...@g.us.",
            },
            "text": {
                "type": "string",
                "description": "Message body for button/list sends.",
            },
            "question": {
                "type": "string",
                "description": "Poll question.",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Poll options.",
            },
            "selectable_count": {
                "type": "integer",
                "description": "Poll selectable option count. Defaults to 1.",
            },
            "buttons": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Button objects: [{id, text}] or [{id, title}].",
            },
            "sections": {
                "type": "array",
                "items": {"type": "object"},
                "description": "List sections: [{title, rows:[{id, title, description?}]}].",
            },
            "button_text": {
                "type": "string",
                "description": "List open button text. Defaults to Choose.",
            },
            "title": {
                "type": "string",
                "description": "Optional list title.",
            },
            "footer": {
                "type": "string",
                "description": "Optional footer.",
            },
            "state": {
                "type": "string",
                "enum": ["typing", "paused", "recording"],
                "description": "Presence state. Defaults to typing.",
            },
        },
        "required": ["action"],
    },
}


def _error(message: str) -> dict:
    return {"error": message}


def _load_whatsapp_extra() -> Dict[str, Any]:
    from gateway.config import Platform, load_gateway_config

    config = load_gateway_config()
    pconfig = config.platforms.get(Platform.WHATSAPP)
    if not pconfig or not pconfig.enabled:
        raise RuntimeError("WhatsApp platform is not enabled in gateway config.")
    return dict(pconfig.extra or {})


def _bridge_port(extra: Optional[Dict[str, Any]] = None) -> int:
    value = (extra or {}).get("bridge_port") or os.getenv("WHATSAPP_BRIDGE_PORT") or 3000
    try:
        return int(value)
    except (TypeError, ValueError):
        return 3000


def _request_bridge(method: str, path: str, payload: Optional[Dict[str, Any]] = None, timeout: int = 20) -> dict:
    extra = _load_whatsapp_extra()
    url = f"http://127.0.0.1:{_bridge_port(extra)}{path}"
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {"success": True}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"error": raw or str(exc)}
        data.setdefault("status", exc.code)
        return data
    except (URLError, TimeoutError, OSError) as exc:
        return _error(f"WhatsApp bridge request failed: {exc}")


def _require(value: Any, name: str) -> Optional[dict]:
    if value:
        return None
    return _error(f"{name} is required")


def whatsapp_tool(args, **_kw):
    action = str(args.get("action", "")).strip()
    chat_id = str(args.get("chat_id", "")).strip()

    try:
        if action == "send_poll":
            missing = _require(chat_id, "chat_id") or _require(args.get("question"), "question")
            options = args.get("options") or []
            if missing:
                return json.dumps(missing)
            if not isinstance(options, list) or len(options) < 2:
                return json.dumps(_error("options must contain at least two strings"))
            return json.dumps(_request_bridge("POST", "/send-poll", {
                "chatId": chat_id,
                "question": str(args.get("question")),
                "options": [str(option) for option in options],
                "selectableCount": int(args.get("selectable_count") or 1),
            }))

        if action == "send_buttons":
            missing = _require(chat_id, "chat_id") or _require(args.get("text"), "text")
            buttons = args.get("buttons") or []
            if missing:
                return json.dumps(missing)
            if not isinstance(buttons, list) or not buttons:
                return json.dumps(_error("buttons must be a non-empty array"))
            return json.dumps(_request_bridge("POST", "/send-buttons", {
                "chatId": chat_id,
                "text": str(args.get("text")),
                "buttons": buttons,
                "footer": args.get("footer") or None,
            }))

        if action == "send_list":
            missing = _require(chat_id, "chat_id") or _require(args.get("text"), "text")
            sections = args.get("sections") or []
            if missing:
                return json.dumps(missing)
            if not isinstance(sections, list) or not sections:
                return json.dumps(_error("sections must be a non-empty array"))
            return json.dumps(_request_bridge("POST", "/send-list", {
                "chatId": chat_id,
                "text": str(args.get("text")),
                "sections": sections,
                "buttonText": args.get("button_text") or "Choose",
                "title": args.get("title") or None,
                "footer": args.get("footer") or None,
            }))

        if action == "set_presence":
            missing = _require(chat_id, "chat_id")
            if missing:
                return json.dumps(missing)
            return json.dumps(_request_bridge("POST", "/presence", {
                "chatId": chat_id,
                "state": args.get("state") or "typing",
            }, timeout=5))

        if action == "list_groups":
            return json.dumps(_request_bridge("GET", "/groups"))

        if action == "group_participants":
            missing = _require(chat_id, "chat_id")
            if missing:
                return json.dumps(missing)
            return json.dumps(_request_bridge("GET", f"/groups/{quote(chat_id, safe='')}/participants"))

        if action == "lid_map":
            return json.dumps(_request_bridge("GET", "/lid-map"))

        if action == "account":
            return json.dumps(_request_bridge("GET", "/account"))

        if action == "labels":
            return json.dumps(_request_bridge("GET", "/labels"))

        return json.dumps(_error(f"Unknown WhatsApp action: {action}"))
    except Exception as exc:
        return json.dumps(_error(f"WhatsApp tool failed: {exc}"))


def check_whatsapp_tool_requirements() -> bool:
    try:
        _load_whatsapp_extra()
        return True
    except Exception:
        return False


from tools.registry import registry

registry.register(
    name="whatsapp",
    toolset="whatsapp",
    schema=WHATSAPP_SCHEMA,
    handler=whatsapp_tool,
    check_fn=check_whatsapp_tool_requirements,
    emoji="WA",
)
