"""Tests for cross-platform desktop notification helpers.

Covers:
- Notification module (agent.notification)
- Tab title helpers in agent.display
- KawaiiSpinner integration with tab titles
- CLI /notif command
- Config defaults for display.notifications
"""

import json
import sys
import subprocess
import unittest
from unittest import mock


# ── agent.notification tests ───────────────────────────────────────────


class TestDesktopNotification(unittest.TestCase):

    def test_send_disabled_is_noop(self):
        """send_desktop_notification with enabled=False returns False."""
        from agent.notification import send_desktop_notification
        result = send_desktop_notification("test", "body", enabled=False)
        self.assertIs(result, False)

    def test_send_empty_summary_is_noop(self):
        """Empty summary should be skipped."""
        from agent.notification import send_desktop_notification
        result = send_desktop_notification("", "body", enabled=True)
        self.assertIs(result, False)

    def test_empty_summary_is_none(self):
        """None-like summary should be skipped."""
        from agent.notification import send_desktop_notification
        result = send_desktop_notification(None, "body", enabled=True)
        self.assertIs(result, False)

    @mock.patch("agent.notification._linux_notify_cmd")
    @mock.patch.dict("os.environ", {"DISPLAY": ":0"}, clear=False)
    def test_linux_cmd_called_when_display_set(self, mock_cmd):
        """On Linux with DISPLAY set, notify-send should be attempted."""
        mock_cmd.return_value = ["notify-send", "title", "body"]
        with mock.patch("agent.notification.subprocess.Popen") as mock_popen:
            from agent.notification import send_desktop_notification
            with mock.patch("platform.system", return_value="Linux"):
                send_desktop_notification("title", "body", enabled=True)
            mock_popen.assert_called_once()

    @mock.patch.dict("os.environ", {}, clear=True)
    def test_linux_no_display_skipped(self):
        """On Linux without DISPLAY/WAYLAND_DISPLAY/XDG_RUNTIME_DIR, skip."""
        import platform
        with mock.patch.object(platform, "system", return_value="Linux"):
            from agent.notification import send_desktop_notification
            result = send_desktop_notification("title", "body", enabled=True)
            self.assertIs(result, False)

    def test_macos_builds_osascript_cmd(self):
        """macOS should return an osascript command."""
        from agent.notification import _macos_notify_cmd, _build_notification_cmd
        with mock.patch("platform.system", return_value="Darwin"):
            with mock.patch("shutil.which", return_value="/usr/bin/osascript"):
                cmd = _macos_notify_cmd("title", "body")
                self.assertIsInstance(cmd, list)
                self.assertTrue(len(cmd) > 0)

    def test_windows_builds_powershell_cmd(self):
        """Windows should return a powershell command."""
        from agent.notification import _windows_notify_cmd
        with mock.patch("platform.system", return_value="Windows"):
            with mock.patch("shutil.which", return_value="powershell"):
                cmd = _windows_notify_cmd("title", "body")
                self.assertIsInstance(cmd, list)
                self.assertTrue(len(cmd) > 0)
                # Check it's powershell
                self.assertEqual(cmd[0], "powershell")

    def test_unknown_platform_returns_empty(self):
        """Unknown platform should return empty list."""
        from agent.notification import _build_notification_cmd
        with mock.patch("platform.system", return_value="FreeBSD"):
            cmd = _build_notification_cmd("title", "body")
            self.assertEqual(cmd, [])

    def test_notify_approval_needed_helper(self):
        """notify_approval_needed should build correct summary."""
        from agent.notification import notify_approval_needed
        with mock.patch(
            "agent.notification.send_desktop_notification"
        ) as mock_send:
            notify_approval_needed(tool_name="terminal", command="rm -rf /tmp")
            mock_send.assert_called_once()
            call_kwargs = mock_send.call_args
            self.assertEqual(call_kwargs.kwargs["summary"], "Hermes - Awaiting Approval")

    def test_notify_question_helper(self):
        """notify_question helper."""
        from agent.notification import notify_question
        with mock.patch(
            "agent.notification.send_desktop_notification"
        ) as mock_send:
            notify_question("Which directory?")
            mock_send.assert_called_once()
            self.assertEqual(
                mock_send.call_args.kwargs["summary"],
                "Hermes - Question for You",
            )

    def test_notify_error_helper(self):
        """notify_error helper."""
        from agent.notification import notify_error
        with mock.patch(
            "agent.notification.send_desktop_notification"
        ) as mock_send:
            notify_error("API timeout")
            mock_send.assert_called_once()
            self.assertEqual(
                mock_send.call_args.kwargs["summary"],
                "Hermes - Error",
            )

    def test_notify_turn_complete_helper(self):
        """notify_turn_complete helper."""
        from agent.notification import notify_turn_complete
        with mock.patch(
            "agent.notification.send_desktop_notification"
        ) as mock_send:
            notify_turn_complete(tokens=1500)
            mock_send.assert_called_once()
            self.assertEqual(
                mock_send.call_args.kwargs["summary"],
                "Hermes - Done",
            )


# ── agent.display notification tests ───────────────────────────────────


class TestDisplayNotifications(unittest.TestCase):

    def setUp(self):
        """Reset notification state before each test."""
        from agent.display import init_notifications
        init_notifications(enabled=False, tab_title=True, desktop=False)

    def test_init_sets_disabled_state(self):
        """init_notifications with enabled=False should keep everything off."""
        from agent.display import get_notification_config
        cfg = get_notification_config()
        self.assertFalse(cfg["enabled"])

    def test_init_sets_enabled_state(self):
        """init_notifications with enabled=True."""
        from agent.display import init_notifications, get_notification_config
        init_notifications(enabled=True, tab_title=True, desktop=True)
        cfg = get_notification_config()
        self.assertTrue(cfg["enabled"])
        self.assertTrue(cfg["tab_title"])
        self.assertTrue(cfg["desktop"])

    def test_tab_title_noop_when_disabled(self):
        """Tab title should not write when notifications are disabled."""
        from agent.display import _update_tab_title
        import io
        captured = io.StringIO()
        with mock.patch("sys.stdout", captured):
            _update_tab_title("Hermes - Thinking...")
        # When disabled, nothing should be written
        self.assertEqual(captured.getvalue(), "")

    def test_tab_title_writes_when_enabled(self):
        """Tab title should write OSC escape sequence when enabled."""
        from agent.display import (
            init_notifications,
            _update_tab_title,
        )
        init_notifications(enabled=True, tab_title=True, desktop=False)
        import io
        captured = io.StringIO()
        with mock.patch.object(sys.stdout, "isatty", return_value=True):
            with mock.patch.object(sys.stdout, "write") as mock_write:
                with mock.patch.object(sys.stdout, "flush"):
                    _update_tab_title("Test Title")
                    mock_write.assert_called()
                    call_args = mock_write.call_args[0][0]
                    self.assertIn("Test Title", call_args)
                    self.assertIn("\033]0;", call_args)


# ── KawaiiSpinner tab title integration tests ──────────────────────────


class TestSpinnerTabTitleIntegration(unittest.TestCase):
    """Test that KawaiiSpinner start/stop updates tab titles."""

    def setUp(self):
        from agent.display import init_notifications
        # Reset to disabled
        init_notifications(enabled=False, tab_title=True, desktop=False)

    @mock.patch("agent.display._update_tab_title")
    @mock.patch("agent.display._reset_tab_title")
    def test_spinner_updates_tab_title_on_start_when_enabled(
        self, mock_reset, mock_update
    ):
        """Spinner start() should set tab title when notifications enabled."""
        from agent.display import (
            init_notifications,
            KawaiiSpinner,
        )
        init_notifications(enabled=True, tab_title=True, desktop=False)
        spinner = KawaiiSpinner("test message")
        # Don't actually start animation thread — just test the start logic
        spinner.running = False
        spinner.start()
        mock_update.assert_called_with("Hermes - Thinking...")

    @mock.patch("agent.display._update_tab_title")
    @mock.patch("agent.display._reset_tab_title")
    def test_spinner_resets_tab_title_on_stop_when_enabled(self, mock_reset, mock_update):
        """Spinner stop() should reset tab title when notifications enabled."""
        from agent.display import init_notifications, KawaiiSpinner
        init_notifications(enabled=True, tab_title=True, desktop=False)
        spinner = KawaiiSpinner("test message")
        spinner.running = True
        spinner.start_time = 0
        spinner.last_line_len = 0
        spinner.thread = None
        spinner._print_fn = lambda x: None
        # _is_tty is a property — patch _write directly to skip TTY checks
        with mock.patch.object(spinner, "_write", lambda *a, **kw: None):
            with mock.patch.object(type(spinner), "_is_tty",
                                   new_callable=mock.PropertyMock, return_value=False):
                spinner.stop()
        mock_reset.assert_called()

    @mock.patch("agent.display._update_tab_title")
    def test_spinner_noop_when_disabled(self, mock_update):
        """Spinner should not update tab titles when disabled."""
        from agent.display import (
            init_notifications,
            KawaiiSpinner,
        )
        init_notifications(enabled=False, tab_title=True, desktop=False)
        spinner = KawaiiSpinner("test message")
        spinner.running = False
        spinner.start()
        mock_update.assert_not_called()


# ── Config defaults test ───────────────────────────────────────────────


class TestNotificationConfigDefaults(unittest.TestCase):

    def test_default_config_has_notifications_key(self):
        """DEFAULT_CONFIG should contain display.notifications."""
        from hermes_cli.config import DEFAULT_CONFIG
        self.assertIn("notifications", DEFAULT_CONFIG["display"])

    def test_default_notifications_disabled(self):
        """Notifications should be OFF by default."""
        from hermes_cli.config import DEFAULT_CONFIG
        notif = DEFAULT_CONFIG["display"]["notifications"]
        self.assertFalse(notif["enabled"])

    def test_default_config_keys(self):
        """Config should have expected keys."""
        from hermes_cli.config import DEFAULT_CONFIG
        notif = DEFAULT_CONFIG["display"]["notifications"]
        self.assertIn("enabled", notif)
        self.assertIn("tab_title", notif)
        self.assertIn("desktop", notif)
        self.assertIn("events", notif)

    def test_event_subkeys(self):
        """Events should have approval, clarify, error, turn_complete."""
        from hermes_cli.config import DEFAULT_CONFIG
        events = DEFAULT_CONFIG["display"]["notifications"]["events"]
        self.assertIn("approval", events)
        self.assertIn("clarify", events)
        self.assertIn("error", events)
        self.assertIn("turn_complete", events)


# ── Command registration test ──────────────────────────────────────────


class TestNotifCommandRegistration(unittest.TestCase):

    def test_notif_command_in_registry(self):
        """/notif should be in COMMAND_REGISTRY."""
        from hermes_cli.commands import COMMAND_REGISTRY
        cmd_names = [c.name for c in COMMAND_REGISTRY]
        self.assertIn("notif", cmd_names)

    def test_notif_command_has_description(self):
        """/notif should have a useful description."""
        from hermes_cli.commands import COMMAND_REGISTRY
        cmd = next((c for c in COMMAND_REGISTRY if c.name == "notif"), None)
        self.assertIsNotNone(cmd)
        self.assertIn("notification", cmd.description.lower())


if __name__ == "__main__":
    unittest.main()
