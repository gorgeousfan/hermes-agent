"""Shared auth/status helpers for the Save to Spotify CLI wrapper."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

CLI_NAME = "save-to-spotify"
STATUS_TIMEOUT_SECONDS = 10.0
AUTH_URL_RE = re.compile(r"https?://[^\s)]+")


@dataclass
class SaveToSpotifyState:
    installed: bool
    authenticated: bool
    token_path: str | None
    expires_at: str | None
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _config_dir() -> Path:
    raw = os.getenv("XDG_CONFIG_HOME")
    if raw:
        return Path(raw).expanduser() / "save-to-spotify"
    return Path.home() / ".config" / "save-to-spotify"


def token_path() -> Path:
    return _config_dir() / "token.json"


def token_path_for_display() -> str | None:
    path = token_path()
    if path.exists():
        return str(path)
    return None


def install_message() -> str:
    return (
        "Save to Spotify CLI is not installed. Install the `save-to-spotify` binary, "
        "then run `hermes auth save-to-spotify`."
    )


def login_message() -> str:
    return (
        "Save to Spotify is not authenticated. Run `hermes auth save-to-spotify` "
        "(or `save-to-spotify auth login`) first."
    )


def command_not_available_message(action: str) -> str:
    if action == "status":
        return install_message()
    return f"{install_message()} Requested action: `{action}`."


def binary_path() -> str | None:
    return shutil.which(CLI_NAME)


def is_ssh_session() -> bool:
    return bool(
        os.getenv("SSH_CLIENT")
        or os.getenv("SSH_CONNECTION")
        or os.getenv("SSH_TTY")
    )


def is_remote_dev_session() -> bool:
    return bool(
        is_ssh_session()
        or os.getenv("REMOTE_CONTAINERS")
        or os.getenv("CODESPACES")
        or os.getenv("GITPOD_WORKSPACE_ID")
        or os.getenv("TERMINAL_ENV") == "ssh"
    )


def is_headless_environment() -> bool:
    if sys.platform == "darwin":
        return False
    return not (os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))


def auth_environment_flags() -> dict[str, bool]:
    return {
        "ssh": is_ssh_session(),
        "remote": is_remote_dev_session(),
        "headless": is_headless_environment(),
    }


def auth_status_to_state(
    payload: dict[str, Any] | None,
    *,
    installed: bool,
    now: datetime | None = None,
) -> SaveToSpotifyState:
    normalized_payload = payload or {}
    authenticated = bool(normalized_payload.get("authenticated")) and bool(
        normalized_payload.get("token_valid", normalized_payload.get("authenticated"))
    )
    expires_at: str | None = None
    if authenticated:
        expires_in = normalized_payload.get("expires_in_seconds")
        if isinstance(expires_in, (int, float)):
            when = (now or datetime.now(timezone.utc)) + timedelta(seconds=float(expires_in))
            expires_at = when.isoformat()
        elif isinstance(normalized_payload.get("expires_at"), str):
            expires_at = str(normalized_payload["expires_at"]).strip() or None
    return SaveToSpotifyState(
        installed=installed,
        authenticated=authenticated,
        token_path=token_path_for_display(),
        expires_at=expires_at,
        next_action="ready" if authenticated else ("login" if installed else "install"),
    )


def get_save_to_spotify_state() -> SaveToSpotifyState:
    binary = binary_path()
    if not binary:
        return SaveToSpotifyState(
            installed=False,
            authenticated=False,
            token_path=token_path_for_display(),
            expires_at=None,
            next_action="install",
        )

    try:
        completed = subprocess.run(
            [binary, "auth", "status", "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=STATUS_TIMEOUT_SECONDS,
        )
    except Exception:
        return SaveToSpotifyState(
            installed=True,
            authenticated=False,
            token_path=token_path_for_display(),
            expires_at=None,
            next_action="login",
        )

    stdout = (completed.stdout or "").strip()
    if not stdout:
        return SaveToSpotifyState(
            installed=True,
            authenticated=False,
            token_path=token_path_for_display(),
            expires_at=None,
            next_action="login",
        )

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict):
        payload = None
    return auth_status_to_state(payload, installed=True)


def format_status_lines(state: SaveToSpotifyState) -> list[str]:
    lines = ["save-to-spotify:"]
    lines.append(f"  installed: {str(state.installed).lower()}")
    lines.append(f"  authenticated: {str(state.authenticated).lower()}")
    if state.token_path:
        lines.append(f"  token_path: {state.token_path}")
    if state.expires_at:
        lines.append(f"  expires_at: {state.expires_at}")
    lines.append(f"  next_action: {state.next_action}")
    return lines


def runtime_blocker_message() -> str:
    state = get_save_to_spotify_state()
    if not state.installed:
        return install_message()
    if not state.authenticated:
        return login_message()
    return "Save to Spotify is ready."


def extract_auth_url(text: str) -> str | None:
    match = AUTH_URL_RE.search(text or "")
    if not match:
        return None
    return match.group(0)


def login_guidance(*, no_browser: bool) -> list[str]:
    env = auth_environment_flags()
    lines = [
        "Hermes is delegating to the official `save-to-spotify` login flow.",
    ]
    if no_browser or env["ssh"] or env["remote"] or env["headless"]:
        lines.append(
            "If the CLI prints an authorization URL, open it in a browser yourself."
        )
        lines.append(
            "The official CLI expects the browser to redirect back to `http://127.0.0.1:<port>` on the same environment running the command."
        )
        if env["ssh"] or env["remote"]:
            lines.append(
                "If you are on SSH or a remote devbox, a local browser that cannot reach that environment's localhost callback will block the login."
            )
        if env["headless"] and not env["ssh"] and not env["remote"]:
            lines.append(
                "This environment looks headless, so browser auto-open may fail even if the auth server is running."
            )
    return lines


def post_login_guidance(output: str, *, no_browser: bool) -> list[str]:
    env = auth_environment_flags()
    if not (no_browser or env["ssh"] or env["remote"] or env["headless"]):
        return []
    url = extract_auth_url(output)
    lines: list[str] = []
    if url:
        lines.append(f"Open this URL manually if the browser did not open: {url}")
    lines.append(
        "The login only completes after Spotify redirects back to the localhost callback on the machine running `save-to-spotify`."
    )
    if env["ssh"] or env["remote"]:
        lines.append(
            "If your browser cannot reach that remote machine's localhost callback, the flow cannot finish from this session."
        )
    return lines
