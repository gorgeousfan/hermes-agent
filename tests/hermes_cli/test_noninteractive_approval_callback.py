"""Regression tests for headless/single-query approval callbacks."""

from __future__ import annotations

import threading

from cli import HermesCLI


def _bare_cli_for_approval() -> HermesCLI:
    cli = HermesCLI.__new__(HermesCLI)
    cli._app = None
    cli._approval_lock = threading.Lock()
    cli._approval_state = None
    cli._approval_deadline = 0
    cli._invalidate = lambda *args, **kwargs: None
    return cli


def test_headless_approval_callback_allows_noninteractive_safe_command(monkeypatch):
    """`hermes chat -q` has no prompt_toolkit app to answer approvals.

    The callback must therefore use Hermes' non-interactive guard policy instead
    of showing an invisible prompt and timing out. Non-hardline commands should
    return `once` so codex app-server write smoke tests can proceed.
    """
    import cli as cli_module

    monkeypatch.setitem(cli_module.CLI_CONFIG.setdefault("approvals", {}), "timeout", 0)
    monkeypatch.setenv("HERMES_INTERACTIVE", "1")
    monkeypatch.delenv("HERMES_EXEC_ASK", raising=False)
    monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)

    cli = _bare_cli_for_approval()
    assert cli._approval_callback(
        "python -c \"open('/tmp/hermes_cli_headless.txt','w').write('ok')\"",
        "Codex requests exec in /tmp",
    ) == "once"


def test_headless_approval_callback_still_denies_hardline(monkeypatch):
    import cli as cli_module

    monkeypatch.setitem(cli_module.CLI_CONFIG.setdefault("approvals", {}), "timeout", 0)
    monkeypatch.setenv("HERMES_INTERACTIVE", "1")
    monkeypatch.delenv("HERMES_EXEC_ASK", raising=False)
    monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)

    cli = _bare_cli_for_approval()
    assert cli._approval_callback("rm -rf /", "Codex requests exec in /") == "deny"
