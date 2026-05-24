"""Per-session working directory override for gateway tool isolation.

The gateway can process multiple Discord/Slack/Telegram sessions concurrently in
one Python process.  Environment variables such as ``TERMINAL_CWD`` are process
-global and therefore unsafe for per-session worktree routing.  This module
provides a tiny ContextVar-backed override that tool handlers can consult before
falling back to the process environment.
"""
from __future__ import annotations

from contextvars import ContextVar, Token

_SESSION_CWD: ContextVar[str] = ContextVar("HERMES_SESSION_CWD", default="")


def set_session_cwd(path: str) -> Token[str]:
    """Bind *path* as the current session's working directory."""
    return _SESSION_CWD.set(str(path or ""))


def reset_session_cwd(token: Token[str]) -> None:
    """Restore the previous session working-directory binding."""
    _SESSION_CWD.reset(token)


def get_session_cwd(default: str = "") -> str:
    """Return the current session cwd override, or *default* if unset."""
    value = _SESSION_CWD.get("")
    return value or default
