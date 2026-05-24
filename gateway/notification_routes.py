"""Durable routing index for replies to outbound gateway notifications.

Notifications from cron/background/watchers are delivered into a platform chat,
but user replies should often continue the originating session/process rather
than create a new delivery-chat workflow.  This module stores only routing-safe
metadata under HERMES_HOME; it intentionally does not persist notification body
text or secrets.
"""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
from urllib.parse import quote

from hermes_constants import get_hermes_home

from .config import Platform
from .session import SessionSource

_DEFAULT_TTL_SECONDS = 30 * 60
_CONTEXTUAL_PREFIXES = (
    "ok",
    "okay",
    "ок",
    "окей",
    "да",
    "yes",
    "yep",
    "продолж",
    "continue",
    "go on",
    "проверь",
    "check",
    "останов",
    "stop",
    "отмени",
    "cancel",
    "сделай",
    "do it",
    "запусти",
    "run",
)


def _hermes_home() -> Path:
    return Path(get_hermes_home())


def _routes_path() -> Path:
    return _hermes_home() / "gateway" / "notification_routes.json"


def _detect_server_ip() -> Optional[str]:
    """Best-effort externally usable server IP for WebUI link fallback."""
    try:
        candidates = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        candidates = []
    for ip in candidates:
        if not ip.startswith("127.") and not ip.startswith("169.254."):
            return ip
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        return None
    return None


def build_webui_session_url(session_id: Optional[str] = None) -> Optional[str]:
    """Build a direct WebUI chat/session URL when enough runtime data exists.

    Priority:
    1. Explicit public/base URL: HERMES_WEBUI_PUBLIC_URL or HERMES_WEBUI_BASE_URL.
    2. Runtime fallback: detected server IP + HERMES_WEBUI_PORT.

    Returns None instead of inventing a link when session id, host, or port is
    unavailable.

    The path is ``/chat?resume=<session_id>`` — the same deep link the WebUI's
    Sessions page uses to resume a session in its embedded TUI. The earlier
    ``/session/<id>`` shape does not exist as a React route, so the WebUI's
    catch-all redirected the user to ``/sessions`` instead of the actual chat.
    """
    sid = (session_id or os.getenv("HERMES_SESSION_ID") or "").strip()
    if not sid:
        return None
    encoded = quote(sid, safe="")
    base = (
        os.getenv("HERMES_WEBUI_PUBLIC_URL")
        or os.getenv("HERMES_WEBUI_BASE_URL")
        or os.getenv("WEBUI_PUBLIC_URL")
        or ""
    ).strip()
    if base:
        return f"{base.rstrip('/')}/chat?resume={encoded}"
    port = (os.getenv("HERMES_WEBUI_PORT") or "").strip()
    if not port:
        return None
    host = _detect_server_ip()
    if not host:
        return None
    return f"http://{host}:{port}/chat?resume={encoded}"


def _load_state() -> Dict[str, Any]:
    path = _routes_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {"messages": {}, "latest_by_scope": {}}
    except (OSError, json.JSONDecodeError):
        return {"messages": {}, "latest_by_scope": {}}
    if not isinstance(data, dict):
        return {"messages": {}, "latest_by_scope": {}}
    data.setdefault("messages", {})
    data.setdefault("latest_by_scope", {})
    return data


def _save_state(state: Mapping[str, Any]) -> None:
    path = _routes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, sort_keys=True, indent=2)
    tmp.replace(path)


def _norm(value: Any) -> str:
    return "" if value is None else str(value)


def _message_key(platform: str, chat_id: Any, message_id: Any, thread_id: Any = None) -> str:
    return f"{_norm(platform)}:{_norm(chat_id)}:{_norm(thread_id)}:{_norm(message_id)}"


def _scope_key(platform: str, chat_id: Any, thread_id: Any = None) -> str:
    return f"{_norm(platform)}:{_norm(chat_id)}:{_norm(thread_id)}"


def _source_from_route(route: Mapping[str, Any]) -> Optional[SessionSource]:
    raw = route.get("source")
    if not isinstance(raw, Mapping):
        return None
    data = dict(raw)
    if "platform" in data and not isinstance(data["platform"], Platform):
        data["platform"] = str(data["platform"])
    try:
        return SessionSource.from_dict(data)
    except Exception:
        return None


def _sanitize_route(route: Mapping[str, Any]) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}
    for key in (
        "kind",
        "session_key",
        "session_id",
        "api_session_id",
        "webui_url",
        "job_id",
        "process_id",
        "repo",
        "branch",
        "issue",
        "pr",
        "state",
    ):
        value = route.get(key)
        if value is not None:
            clean[key] = str(value)
    source = route.get("source")
    if isinstance(source, SessionSource):
        clean["source"] = source.to_dict()
    elif isinstance(source, Mapping):
        allowed = {
            "platform",
            "chat_id",
            "chat_name",
            "chat_type",
            "user_id",
            "user_name",
            "thread_id",
            "chat_topic",
            "user_id_alt",
            "chat_id_alt",
            "guild_id",
            "parent_chat_id",
        }
        clean["source"] = {k: v for k, v in source.items() if k in allowed and v is not None}
    return clean


def register_outbound_notification(
    *,
    platform: str,
    chat_id: Any,
    message_ids: Iterable[Any],
    route: Mapping[str, Any],
    thread_id: Any = None,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    now: Optional[float] = None,
) -> None:
    """Register routing metadata for an actionable outbound notification."""
    created_at = time.time() if now is None else float(now)
    expires_at = created_at + max(1, int(ttl_seconds))
    clean = _sanitize_route(route)
    clean.update({"created_at": created_at, "expires_at": expires_at})

    state = _load_state()
    messages = state.setdefault("messages", {})
    latest = state.setdefault("latest_by_scope", {})

    first_key: Optional[str] = None
    for message_id in message_ids:
        if message_id is None:
            continue
        key = _message_key(platform, chat_id, message_id, thread_id)
        messages[key] = dict(clean)
        if first_key is None:
            first_key = key
    if first_key is not None:
        latest[_scope_key(platform, chat_id, thread_id)] = first_key
    _save_state(state)


def _route_if_fresh(route: Mapping[str, Any], *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    current = time.time() if now is None else float(now)
    try:
        expires_at = float(route.get("expires_at", 0))
    except (TypeError, ValueError):
        return None
    if expires_at < current:
        return None
    source = _source_from_route(route)
    if source is None and not route.get("api_session_id"):
        return None
    result: Dict[str, Any] = {"route": dict(route)}
    if source is not None:
        result["source"] = source
    return result


def resolve_exact_reply(
    *,
    platform: str,
    chat_id: Any,
    reply_to_message_id: Any,
    thread_id: Any = None,
    now: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    if reply_to_message_id is None:
        return None
    state = _load_state()
    messages = state.get("messages", {})
    route = messages.get(_message_key(platform, chat_id, reply_to_message_id, thread_id))
    if route is None and thread_id is not None:
        route = messages.get(_message_key(platform, chat_id, reply_to_message_id, None))
    if not isinstance(route, Mapping):
        return None
    return _route_if_fresh(route, now=now)


def _looks_like_continuation(text: str) -> bool:
    stripped = (text or "").strip().lower()
    if not stripped or stripped.startswith("/"):
        return False
    return any(stripped.startswith(prefix) for prefix in _CONTEXTUAL_PREFIXES)


def resolve_latest_followup(
    *,
    platform: str,
    chat_id: Any,
    text: str,
    thread_id: Any = None,
    now: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    if not _looks_like_continuation(text):
        return None
    state = _load_state()
    latest = state.get("latest_by_scope", {})
    messages = state.get("messages", {})
    route_key = latest.get(_scope_key(platform, chat_id, thread_id))
    if route_key is None and thread_id is not None:
        route_key = latest.get(_scope_key(platform, chat_id, None))
    if not route_key:
        return None
    route = messages.get(route_key)
    if not isinstance(route, Mapping):
        return None
    return _route_if_fresh(route, now=now)


def resolve_notification_route_for_message(
    *,
    platform: str,
    chat_id: Any,
    text: str,
    reply_to_message_id: Any = None,
    thread_id: Any = None,
    now: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve an inbound platform message to a notification origin if safe."""
    exact = resolve_exact_reply(
        platform=platform,
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
        thread_id=thread_id,
        now=now,
    )
    if exact is not None:
        return exact
    return resolve_latest_followup(
        platform=platform,
        chat_id=chat_id,
        text=text,
        thread_id=thread_id,
        now=now,
    )
