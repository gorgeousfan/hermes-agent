"""Cross-platform desktop notification helpers.

Sends native OS notifications so users with multiple terminal tabs
can see at a glance which Hermes session needs attention.

Supports:
- **Linux**: ``notify-send`` (freedesktop.org)
- **macOS**: ``osascript display notification``
- **Windows**: PowerShell toast via WinRT

All functions are best-effort — no exceptions propagate into the agent loop.

This module is separate from ``agent.display`` (which handles tab titles via
OSC escape sequences).  Use ``agent.display`` for window/tab title updates
and this module for OS-level notification tray popups.

Desktop notifications are intentionally NOT enabled by default.  Users who
want them must set ``display.notifications.desktop: true`` in config or run
``/notif desktop on`` in-session.  Tab title updates (``display.notifications.enabled:
true``) use the lightweight OSC approach and are the recommended first step.
"""

import logging
import os
import platform
import shutil
import subprocess

logger = logging.getLogger(__name__)


# ── Platform-specific notification command builders ────────────────────


def _linux_notify_cmd(summary: str, body: str) -> list[str]:
    """Use ``notify-send`` if available (desktop Linux, freedesktop.org)."""
    if not shutil.which("notify-send"):
        return []
    return [
        "notify-send",
        "--urgency", "normal",
        "--category", "im.received",
        "--expire-time", "5000",
        "--icon", "utilities-terminal",
        summary,
        body,
    ]


def _macos_notify_cmd(summary: str, body: str) -> list[str]:
    """Use ``osascript`` (AppleScript) for macOS notifications."""
    if platform.system() != "Darwin":
        return []
    if not shutil.which("osascript"):
        return []
    safe_summary = summary.replace('"', '\\"')
    safe_body = body.replace('"', '\\"')
    return [
        "osascript",
        "-e", f'display notification "{safe_body}" with title "{safe_summary}"',
    ]


def _windows_notify_cmd(summary: str, body: str) -> list[str]:
    """Use PowerShell to show a Windows 10+ toast notification.

    Uses the WinRT Windows.UI.Notifications API via PowerShell,
    which is the most reliable built-in approach (no pip packages).
    """
    if platform.system() != "Windows":
        return []
    if not shutil.which("powershell"):
        return []
    # Escape single quotes for PowerShell
    safe_summary = summary.replace("'", "''")
    safe_body = body.replace("'", "''")
    ps_script = (
        f"[Windows.UI.Notifications.ToastNotificationManager, "
        f"Windows.UI.Notifications, ContentType = WindowsRuntime] | "
        f"Out-Null; "
        f"$template = [Windows.UI.Notifications.ToastNotificationManager]"
        f"::GetTemplateContent("
        f"[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        f"$template.GetElementsByTagName('text')[0].AppendChild("
        f"$template.CreateTextNode('{safe_summary}')); "
        f"$template.GetElementsByTagName('text')[1].AppendChild("
        f"$template.CreateTextNode('{safe_body}')); "
        f"$notifier = [Windows.UI.Notifications.ToastNotificationManager]"
        f"::CreateToastNotifier('Hermes Agent'); "
        f"$notifier.Show($template)"
    )
    return ["powershell", "-NoProfile", "-Command", ps_script]


def _build_notification_cmd(summary: str, body: str) -> list[str]:
    """Return the platform-appropriate notification command.

    Returns an empty list if no suitable backend is available.
    """
    system = platform.system()
    if system == "Linux":
        return _linux_notify_cmd(summary, body)
    elif system == "Darwin":
        return _macos_notify_cmd(summary, body)
    elif system == "Windows":
        return _windows_notify_cmd(summary, body)
    return []


# ── Core notification function ─────────────────────────────────────────


def send_desktop_notification(
    summary: str,
    body: str = "",
    *,
    enabled: bool = True,
) -> bool:
    """Send a desktop notification (best-effort, never raises).

    Parameters
    ----------
    summary : str
        Short title shown at the top of the notification.
    body : str
        Longer detail text.
    enabled : bool
        Master switch — if False the call is a no-op.

    Returns
    -------
    bool
        True if a notification command was spawned, False if skipped.
    """
    if not enabled or not summary:
        return False

    # Skip when running without a desktop session (headless server, CI,
    # systemd, SSH without XDG_RUNTIME_DIR / DBUS).
    if platform.system() == "Linux":
        if (
            not os.environ.get("DISPLAY")
            and not os.environ.get("WAYLAND_DISPLAY")
            and not os.environ.get("XDG_RUNTIME_DIR")
        ):
            logger.debug(
                "Skipping desktop notification: no display session detected"
            )
            return False

    cmd = _build_notification_cmd(summary, body)
    if not cmd:
        logger.debug("Skipping desktop notification: no backend available")
        return False

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError as exc:
        logger.debug("Desktop notification failed: %s", exc)
        return False


# ── Convenience wrappers for specific agent events ─────────────────────


def notify_approval_needed(tool_name: str = "", command: str = "") -> bool:
    """Send a notification that the agent is blocked on command approval."""
    parts = ["Command approval needed"]
    if tool_name:
        parts.append(f"via {tool_name}")
    if command:
        cmd_preview = command[:120]
        if len(command) > 120:
            cmd_preview += "..."
        parts.append(f"`{cmd_preview}`")
    return send_desktop_notification(
        summary="Hermes - Awaiting Approval",
        body=" ".join(parts),
    )


def notify_question(question: str = "") -> bool:
    """Notify the user that the agent has a clarify question."""
    body = str(question)[:200] if question else "The agent has a question for you."
    return send_desktop_notification(
        summary="Hermes - Question for You",
        body=body,
    )


def notify_error(error_summary: str = "") -> bool:
    """Notify on a blocking error (tool failure, API error, etc.)."""
    body = str(error_summary)[:200] if error_summary else "An error occurred."
    return send_desktop_notification(
        summary="Hermes - Error",
        body=body,
    )


def notify_turn_complete(tokens: int = 0) -> bool:
    """Optional notification when the agent finishes a long turn."""
    if tokens > 0:
        return send_desktop_notification(
            summary="Hermes - Done",
            body=f"Response complete ({tokens} tokens).",
        )
    return send_desktop_notification(
        summary="Hermes - Done",
        body="Response complete.",
    )
