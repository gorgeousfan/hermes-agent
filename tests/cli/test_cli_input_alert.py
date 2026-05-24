"""Tests for ``cli._notify_input_needed`` — the OSC 9 input-prompt alert.

Covers:

* Sequence shape (ESC ] 9 ; <body> BEL).
* Disable via ``display.input_alert: false``.
* No-op when stdout is not a TTY (the redirected-log guard).
* Failure path silently absorbs IO errors (no /dev/tty in the sandbox).
"""
from __future__ import annotations

from unittest.mock import patch, mock_open, MagicMock

import cli as cli_module


def test_notify_writes_osc9_with_bel_terminator(monkeypatch):
    """Helper writes ``ESC ] 9 ; <body> BEL`` to /dev/tty when enabled + TTY."""
    monkeypatch.setitem(cli_module.CLI_CONFIG, "display", {"input_alert": True})

    fake_tty = MagicMock()
    m = mock_open()
    m.return_value.__enter__.return_value = fake_tty

    with patch("cli.sys.stdout.isatty", return_value=True), \
         patch("builtins.open", m):
        cli_module._notify_input_needed("hello world")

    m.assert_called_once_with("/dev/tty", "w", buffering=1, encoding="utf-8")
    fake_tty.write.assert_called_once_with("\x1b]9;hello world\x07")


def test_notify_disabled_via_config(monkeypatch):
    """Setting ``display.input_alert: false`` short-circuits before any IO."""
    monkeypatch.setitem(cli_module.CLI_CONFIG, "display", {"input_alert": False})

    m = mock_open()
    with patch("cli.sys.stdout.isatty", return_value=True), \
         patch("builtins.open", m):
        cli_module._notify_input_needed("hello")

    m.assert_not_called()


def test_notify_skipped_when_stdout_not_tty(monkeypatch):
    """Redirected stdout (pipes, file logs) — no notification emitted."""
    monkeypatch.setitem(cli_module.CLI_CONFIG, "display", {"input_alert": True})

    m = mock_open()
    with patch("cli.sys.stdout.isatty", return_value=False), \
         patch("builtins.open", m):
        cli_module._notify_input_needed("hello")

    m.assert_not_called()


def test_notify_swallows_io_errors(monkeypatch):
    """If /dev/tty can't be opened (sandbox, non-POSIX), the helper is silent."""
    monkeypatch.setitem(cli_module.CLI_CONFIG, "display", {"input_alert": True})

    def _boom(*_args, **_kwargs):
        raise OSError("no tty")

    with patch("cli.sys.stdout.isatty", return_value=True), \
         patch("builtins.open", _boom):
        # Must not raise. The whole point of the helper is best-effort.
        cli_module._notify_input_needed("hello")


def test_notify_default_on(monkeypatch):
    """Default (no ``input_alert`` key in config) is enabled."""
    monkeypatch.setitem(cli_module.CLI_CONFIG, "display", {})

    fake_tty = MagicMock()
    m = mock_open()
    m.return_value.__enter__.return_value = fake_tty

    with patch("cli.sys.stdout.isatty", return_value=True), \
         patch("builtins.open", m):
        cli_module._notify_input_needed("hi")

    fake_tty.write.assert_called_once_with("\x1b]9;hi\x07")


def test_notify_robust_to_missing_display_config(monkeypatch):
    """Missing ``display`` section entirely — fall back to default-on."""
    # Replace the whole CLI_CONFIG with something that has no display key.
    # Use a fresh dict so we don't bleed into other tests.
    monkeypatch.setattr(cli_module, "CLI_CONFIG", {})

    fake_tty = MagicMock()
    m = mock_open()
    m.return_value.__enter__.return_value = fake_tty

    with patch("cli.sys.stdout.isatty", return_value=True), \
         patch("builtins.open", m):
        cli_module._notify_input_needed("hi")

    fake_tty.write.assert_called_once_with("\x1b]9;hi\x07")
