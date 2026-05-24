"""CLI-backed Save to Spotify tools."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from tools.registry import tool_error, tool_result

COMMON_STRING = {"type": "string"}
COMMON_BOOL = {"type": "boolean"}

_CLI_NAME = "save-to-spotify"
_DEFAULT_TIMEOUT_SECONDS = 45
_DEFAULT_WAIT_SECONDS = 300
_WAIT_TIMEOUT_BUFFER_SECONDS = 30
_AUTH_REQUIRED_MESSAGE = (
    "Save to Spotify is not authenticated. Run `save-to-spotify auth login` first."
)
_INSTALL_MESSAGE = (
    "Save to Spotify CLI is not installed. Install the `save-to-spotify` binary first."
)
_HANG_TIMEOUT_MESSAGE = (
    "Save to Spotify CLI timed out unexpectedly before finishing. This is a system timeout, "
    "not the normal episode readiness timeout."
)


class SaveToSpotifyError(RuntimeError):
    """Raised when the CLI returns a structured or execution error."""


def _check_binary() -> str:
    binary = shutil.which(_CLI_NAME)
    if not binary:
        raise SaveToSpotifyError(_INSTALL_MESSAGE)
    return binary


def _validate_existing_file(path_value: Any, field_name: str) -> str:
    value = str(path_value or "").strip()
    if not value:
        raise SaveToSpotifyError(f"{field_name} is required")
    path = Path(value).expanduser()
    if not path.exists():
        raise SaveToSpotifyError(f"{field_name} does not exist: {path}")
    if not path.is_file():
        raise SaveToSpotifyError(f"{field_name} is not a file: {path}")
    return str(path)


def _validate_optional_file(path_value: Any, field_name: str) -> str | None:
    if path_value in (None, ""):
        return None
    return _validate_existing_file(path_value, field_name)


def _coerce_bool(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        cleaned = raw.strip().lower()
        if cleaned in {"1", "true", "yes", "on"}:
            return True
        if cleaned in {"0", "false", "no", "off"}:
            return False
    return default


def _parse_duration_to_seconds(value: str) -> float:
    text = str(value or "").strip()
    if not text:
        raise SaveToSpotifyError("wait_timeout must be a non-empty duration string")
    total = 0.0
    idx = 0
    pattern = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")
    for match in pattern.finditer(text):
        if match.start() != idx:
            raise SaveToSpotifyError(
                "wait_timeout must use Go-style duration units like `90s` or `2m`"
            )
        amount = float(match.group(1))
        unit = match.group(2)
        if unit == "ms":
            total += amount / 1000.0
        elif unit == "s":
            total += amount
        elif unit == "m":
            total += amount * 60.0
        elif unit == "h":
            total += amount * 3600.0
        idx = match.end()
    if idx != len(text):
        raise SaveToSpotifyError(
            "wait_timeout must use Go-style duration units like `90s` or `2m`"
        )
    return total


def _hard_timeout_seconds(*, wait: bool = False, wait_timeout: str | None = None) -> float:
    if not wait:
        return _DEFAULT_TIMEOUT_SECONDS
    if wait_timeout:
        return _parse_duration_to_seconds(wait_timeout) + _WAIT_TIMEOUT_BUFFER_SECONDS
    return _DEFAULT_WAIT_SECONDS + _WAIT_TIMEOUT_BUFFER_SECONDS


def _auth_error_from_message(message: str) -> str | None:
    lowered = message.lower()
    if "auth login" in lowered:
        return _AUTH_REQUIRED_MESSAGE
    if "not authenticated" in lowered or "not logged in" in lowered:
        return _AUTH_REQUIRED_MESSAGE
    if "login required" in lowered or "unauthorized" in lowered:
        return _AUTH_REQUIRED_MESSAGE
    return None


def _extract_cli_message(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("error", "message", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(payload, str):
        return payload.strip()
    return ""


def _normalize_json_payload(stdout: str) -> dict[str, Any] | list[Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise SaveToSpotifyError(
            f"Save to Spotify returned invalid JSON: {exc.msg}"
        ) from exc
    if isinstance(payload, dict) and payload.get("error"):
        message = _extract_cli_message(payload)
        raise SaveToSpotifyError(_auth_error_from_message(message) or message)
    return payload


def _run_cli(
    command: list[str],
    *,
    wait: bool = False,
    wait_timeout: str | None = None,
) -> dict[str, Any] | list[Any]:
    binary = _check_binary()
    full_cmd = [binary, "--json", *command]
    try:
        completed = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=_hard_timeout_seconds(wait=wait, wait_timeout=wait_timeout),
        )
    except subprocess.TimeoutExpired as exc:
        raise SaveToSpotifyError(_HANG_TIMEOUT_MESSAGE) from exc

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()

    if stdout:
        payload = _normalize_json_payload(stdout)
        if completed.returncode != 0:
            message = _extract_cli_message(payload) or stderr or "Save to Spotify command failed"
            raise SaveToSpotifyError(_auth_error_from_message(message) or message)
        return payload

    message = stderr or f"Save to Spotify command failed with exit code {completed.returncode}"
    raise SaveToSpotifyError(_auth_error_from_message(message) or message)


def _ensure_timeline_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SaveToSpotifyError("timeline must be an object with an `items` array")
    items = payload.get("items")
    if not isinstance(items, list):
        raise SaveToSpotifyError("timeline.items must be a list")
    for item in items:
        if not isinstance(item, dict):
            continue
        spotify_entity = item.get("spotify_entity")
        if spotify_entity is None:
            continue
        if not isinstance(spotify_entity, dict):
            raise SaveToSpotifyError("timeline spotify_entity entries must be objects")
        uri = str(spotify_entity.get("uri") or "").strip()
        if not uri.startswith("spotify:"):
            raise SaveToSpotifyError(
                "timeline spotify_entity.uri must use a full `spotify:...` URI"
            )
    return payload


def _append_flag(cmd: list[str], flag: str, value: Any) -> None:
    if value in (None, ""):
        return
    cmd.extend([flag, str(value)])


def handle_save_to_spotify_upload(args: dict, **_: Any) -> str:
    try:
        file_path = _validate_existing_file(args.get("file_path"), "file_path")
        title = str(args.get("title") or "").strip()
        if not title:
            raise SaveToSpotifyError("title is required")
        wait = _coerce_bool(args.get("wait"))
        wait_timeout = str(args.get("wait_timeout") or "").strip() or None

        cmd = ["upload", file_path, "--title", title]
        _append_flag(cmd, "--show-id", args.get("show_id"))
        _append_flag(cmd, "--new-show", args.get("new_show_title"))
        _append_flag(cmd, "--summary", args.get("summary"))
        _append_flag(cmd, "--image", _validate_optional_file(args.get("image_path"), "image_path"))
        _append_flag(cmd, "--language", args.get("language"))
        if wait:
            cmd.append("--wait")
            if wait_timeout:
                cmd.append(wait_timeout)

        return tool_result(_run_cli(cmd, wait=wait, wait_timeout=wait_timeout))
    except SaveToSpotifyError as exc:
        return tool_error(str(exc))


def handle_save_to_spotify_shows(args: dict, **_: Any) -> str:
    action = str(args.get("action") or "list").strip().lower()
    try:
        cmd = ["shows"]
        if action == "list":
            pass
        elif action == "get":
            show_id = str(args.get("show_id") or args.get("id") or "").strip()
            if not show_id:
                raise SaveToSpotifyError("show_id is required for action='get'")
            cmd.extend(["get", show_id])
        elif action == "create":
            title = str(args.get("title") or "").strip()
            if not title:
                raise SaveToSpotifyError("title is required for action='create'")
            cmd.extend(["create", "--title", title])
            _append_flag(cmd, "--summary", args.get("summary"))
            _append_flag(cmd, "--image", _validate_optional_file(args.get("image_path"), "image_path"))
            _append_flag(cmd, "--language", args.get("language"))
        elif action == "delete":
            show_id = str(args.get("show_id") or args.get("id") or "").strip()
            if not show_id:
                raise SaveToSpotifyError("show_id is required for action='delete'")
            cmd.extend(["delete", show_id])
        else:
            raise SaveToSpotifyError(f"Unknown save_to_spotify_shows action: {action}")

        return tool_result(_run_cli(cmd))
    except SaveToSpotifyError as exc:
        return tool_error(str(exc))


def handle_save_to_spotify_episodes(args: dict, **_: Any) -> str:
    action = str(args.get("action") or "list").strip().lower()
    try:
        cmd = ["episodes"]
        wait = False
        wait_timeout = None

        if action == "list":
            _append_flag(cmd, "--show-id", args.get("show_id"))
        elif action == "create":
            title = str(args.get("title") or "").strip()
            if not title:
                raise SaveToSpotifyError("title is required for action='create'")
            file_path = _validate_existing_file(args.get("file_path") or args.get("file"), "file_path")
            cmd.extend(["create", "--title", title, "--file", file_path])
            _append_flag(cmd, "--show-id", args.get("show_id"))
            _append_flag(cmd, "--summary", args.get("summary"))
            _append_flag(cmd, "--image", _validate_optional_file(args.get("image_path"), "image_path"))
            _append_flag(cmd, "--language", args.get("language"))
        elif action == "status":
            episode_id = str(args.get("episode_id") or args.get("id") or "").strip()
            if not episode_id:
                raise SaveToSpotifyError("episode_id is required for action='status'")
            cmd.extend(["status", episode_id])
            wait = _coerce_bool(args.get("wait"))
            wait_timeout = str(args.get("wait_timeout") or "").strip() or None
            if wait:
                cmd.append("--wait")
                if wait_timeout:
                    cmd.append(wait_timeout)
        elif action == "delete":
            episode_id = str(args.get("episode_id") or args.get("id") or "").strip()
            if not episode_id:
                raise SaveToSpotifyError("episode_id is required for action='delete'")
            cmd.extend(["delete", episode_id])
        else:
            raise SaveToSpotifyError(f"Unknown save_to_spotify_episodes action: {action}")

        return tool_result(_run_cli(cmd, wait=wait, wait_timeout=wait_timeout))
    except SaveToSpotifyError as exc:
        return tool_error(str(exc))


def handle_save_to_spotify_timeline(args: dict, **_: Any) -> str:
    action = str(args.get("action") or "").strip().lower()
    try:
        episode_id = str(args.get("episode_id") or args.get("id") or "").strip()
        if not episode_id:
            raise SaveToSpotifyError("episode_id is required")

        if action == "get":
            return tool_result(_run_cli(["timeline", "get", episode_id]))
        if action == "delete":
            return tool_result(_run_cli(["timeline", "delete", episode_id]))
        if action != "set":
            raise SaveToSpotifyError(f"Unknown save_to_spotify_timeline action: {action}")

        timeline = _ensure_timeline_payload(args.get("timeline"))
        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".json",
                delete=False,
            ) as handle:
                json.dump(timeline, handle, ensure_ascii=False)
                temp_path = handle.name
            return tool_result(
                _run_cli(
                    ["timeline", "set", "--episode-id", episode_id, "--from-file", temp_path]
                )
            )
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
    except SaveToSpotifyError as exc:
        return tool_error(str(exc))


SAVE_TO_SPOTIFY_UPLOAD_SCHEMA = {
    "name": "save_to_spotify_upload",
    "description": "Upload a local audio file to Spotify as a personal podcast episode, optionally creating or targeting a show.",
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": COMMON_STRING,
            "title": COMMON_STRING,
            "show_id": COMMON_STRING,
            "new_show_title": COMMON_STRING,
            "summary": COMMON_STRING,
            "image_path": COMMON_STRING,
            "language": COMMON_STRING,
            "wait": COMMON_BOOL,
            "wait_timeout": {
                "type": "string",
                "description": "Optional readiness timeout passed through to the CLI. Use compact Go-style durations like `90s` or `2m` with no spaces.",
            },
        },
        "required": ["file_path", "title"],
    },
}

SAVE_TO_SPOTIFY_SHOWS_SCHEMA = {
    "name": "save_to_spotify_shows",
    "description": "List, inspect, create, or delete Save to Spotify shows.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "get", "create", "delete"]},
            "show_id": COMMON_STRING,
            "id": COMMON_STRING,
            "title": COMMON_STRING,
            "summary": COMMON_STRING,
            "image_path": COMMON_STRING,
            "language": COMMON_STRING,
        },
        "required": ["action"],
    },
}

SAVE_TO_SPOTIFY_EPISODES_SCHEMA = {
    "name": "save_to_spotify_episodes",
    "description": "List, create, inspect readiness status for, or delete Save to Spotify episodes.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "create", "status", "delete"]},
            "show_id": COMMON_STRING,
            "episode_id": COMMON_STRING,
            "id": COMMON_STRING,
            "title": COMMON_STRING,
            "file_path": COMMON_STRING,
            "file": COMMON_STRING,
            "summary": COMMON_STRING,
            "image_path": COMMON_STRING,
            "language": COMMON_STRING,
            "wait": COMMON_BOOL,
            "wait_timeout": {
                "type": "string",
                "description": "Optional readiness timeout passed through to the CLI. Use compact Go-style durations like `90s` or `2m` with no spaces.",
            },
        },
        "required": ["action"],
    },
}

SAVE_TO_SPOTIFY_TIMELINE_SCHEMA = {
    "name": "save_to_spotify_timeline",
    "description": "Get, replace, or delete episode timeline items like chapters, links, and Spotify-native companion entities.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["get", "set", "delete"]},
            "episode_id": COMMON_STRING,
            "id": COMMON_STRING,
            "timeline": {
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {"type": "object"}},
                },
            },
        },
        "required": ["action", "episode_id"],
    },
}
