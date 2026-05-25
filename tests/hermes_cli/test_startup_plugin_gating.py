"""Guards for CLI startup performance regression.

``hermes_cli.main`` skips eager plugin discovery at argparse-setup time
when the invocation is clearly targeting a known built-in subcommand.
This saves 500-650ms on ``hermes --help``, ``hermes version``,
``hermes logs``, etc., by not importing ``google.cloud.pubsub_v1``,
``aiohttp``, ``grpc``, and friends.

Two invariants:

1. ``_BUILTIN_SUBCOMMANDS`` must contain every subcommand that is actually
   registered by ``main()``.  If an entry is missing, plugin discovery
   runs unnecessarily for that command (correctness-safe, just slow).
   If an entry is PRESENT but the subcommand doesn't exist, a plugin
   could shadow the name — also bad.

2. ``_plugin_cli_discovery_needed()`` returns the right answer for the
   flag/positional parsing cases it's meant to handle.
"""

from __future__ import annotations

import io
import re
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from hermes_cli.main import (
    _BUILTIN_SUBCOMMANDS,
    _first_positional_argv,
    _plugin_cli_discovery_needed,
)


# ── helper: grab the live set of top-level subcommands from argparse ───────


def _live_subcommand_names() -> set[str]:
    """Run ``hermes --help`` in-process and parse the subcommand block.

    We patch ``_plugin_cli_discovery_needed`` to always return False so
    plugin-registered commands aren't included — we're validating the
    built-in-only set.
    """
    from hermes_cli import main as _main

    argv_backup = sys.argv[:]
    sys.argv = ["hermes", "--help"]
    buf = io.StringIO()
    try:
        with patch.object(_main, "_plugin_cli_discovery_needed", return_value=False):
            with redirect_stdout(buf):
                with pytest.raises(SystemExit):
                    _main.main()
    finally:
        sys.argv = argv_backup

    text = buf.getvalue()
    # argparse prints "{chat,model,...}" somewhere in the help output
    m = re.search(r"\{([a-zA-Z0-9_,\-]+)\}", text)
    assert m, f"Could not find subcommand group in --help output:\n{text[:500]}"
    return set(m.group(1).split(","))


# ── _first_positional_argv ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["hermes"], None),
        (["hermes", "--help"], None),
        (["hermes", "-h"], None),
        (["hermes", "--version"], None),
        (["hermes", "-w"], None),
        # -p / --profile is stripped from sys.argv by
        # _apply_profile_override() at import time, so it never reaches
        # _first_positional_argv. We test with just -w / --tui here.
        (["hermes", "-w", "--tui"], None),
        (["hermes", "version"], "version"),
        (["hermes", "--tui", "chat"], "chat"),
        (["hermes", "-w", "logs"], "logs"),
        (["hermes", "chat", "hello world"], "chat"),
        (["hermes", "gateway", "run"], "gateway"),
        # Top-level value-taking flags: the value should be skipped.
        (["hermes", "-m", "gpt5", "chat"], "chat"),
        (["hermes", "--model", "gpt5", "chat", "hi"], "chat"),
        (["hermes", "-m", "gpt5", "--provider", "openai", "chat"], "chat"),
        (["hermes", "-z", "hello world"], None),
        (["hermes", "-z", "hello", "chat"], "chat"),
        (["hermes", "--model=gpt5", "chat"], "chat"),     # inline form
        (["hermes", "--", "chat"], "chat"),               # -- terminator
        (["hermes", "-w", "--"], None),
        # Unknown positional after skipped flags → plugin-cmd candidate.
        (["hermes", "some-plugin-cmd"], "some-plugin-cmd"),
        (["hermes", "-m", "gpt5", "some-plugin-cmd"], "some-plugin-cmd"),
    ],
)
def test_first_positional_argv(argv, expected):
    with patch.object(sys, "argv", argv):
        assert _first_positional_argv() == expected


# ── _plugin_cli_discovery_needed ───────────────────────────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ["hermes"],                          # bare → chat
        ["hermes", "--help"],                # top-level help
        ["hermes", "-h"],
        ["hermes", "version"],               # known built-in
        ["hermes", "logs"],
        ["hermes", "gateway", "run"],
        ["hermes", "--tui"],
        ["hermes", "-w", "--tui"],
        ["hermes", "chat", "hi"],
        ["hermes", "help"],                  # accepted built-in-ish
        ["hermes", "-m", "gpt5", "chat"],    # flag-value-skipping
    ],
)
def test_discovery_skipped_for_builtins(argv):
    with patch.object(sys, "argv", argv):
        assert _plugin_cli_discovery_needed() is False


@pytest.mark.parametrize(
    "argv",
    [
        ["hermes", "meet", "join"],          # potential google_meet plugin
        ["hermes", "honcho", "status"],      # potential memory plugin
        ["hermes", "unknown-subcmd"],
    ],
)
def test_discovery_runs_for_unknown_positional(argv):
    with patch.object(sys, "argv", argv):
        assert _plugin_cli_discovery_needed() is True


# ── _BUILTIN_SUBCOMMANDS ↔ argparse registration parity ────────────────────


def test_builtin_set_covers_every_registered_subcommand():
    """Every subcommand registered in main() must appear in the set.

    Missing entries cause a slow-path regression (correctness stays
    fine — discovery just runs unnecessarily).
    """
    live = _live_subcommand_names()
    # "help" is synthetic — an argparse-implicit convenience we include
    # in the set so ``hermes help <cmd>`` skips discovery; it won't show
    # up as a subparser in the --help output.
    declared = _BUILTIN_SUBCOMMANDS - {"help"}
    missing_from_declaration = live - declared
    assert not missing_from_declaration, (
        f"_BUILTIN_SUBCOMMANDS is missing these live subcommands: "
        f"{sorted(missing_from_declaration)}. Add them to "
        f"hermes_cli/main.py::_BUILTIN_SUBCOMMANDS so plugin discovery "
        f"can be skipped when the user targets them."
    )


def test_builtin_set_has_no_phantom_entries():
    """No entry in the set should refer to a subcommand that no longer exists.

    A phantom entry means plugin discovery gets incorrectly skipped for
    a name that — if a plugin actually registered it — would fail to
    parse. Keeps the set honest.
    """
    live = _live_subcommand_names()
    allowed_synthetic = {"help"}
    phantom = _BUILTIN_SUBCOMMANDS - live - allowed_synthetic
    assert not phantom, (
        f"_BUILTIN_SUBCOMMANDS has entries that are not registered as "
        f"top-level subparsers: {sorted(phantom)}"
    )


# ── deprecated `hermes login` subparser is hidden from --help (#24756) ─────


def _hermes_help_text() -> str:
    """Capture ``hermes --help`` output via in-process invocation."""
    from hermes_cli import main as _main

    argv_backup = sys.argv[:]
    sys.argv = ["hermes", "--help"]
    buf = io.StringIO()
    try:
        with patch.object(_main, "_plugin_cli_discovery_needed", return_value=False):
            with redirect_stdout(buf):
                with pytest.raises(SystemExit):
                    _main.main()
    finally:
        sys.argv = argv_backup
    return buf.getvalue()


def test_login_subparser_hidden_from_help_description():
    """The deprecated `hermes login` subcommand must not advertise itself.

    `hermes login` is a removed command whose runtime (`login_command` in
    `hermes_cli/auth.py`) prints a deprecation message and exits.  Until #5670
    only the documentation/error-message references were migrated to `hermes
    auth` — the subparser itself was still registered with a help string
    ("Authenticate with an inference provider") that made it look like a
    working command in `hermes --help`.  Issue #24756 flagged the
    inconsistency: users discover the command from `--help`, run it, and are
    told it has been removed.

    Hiding the description row via `help=argparse.SUPPRESS` is the minimum
    surgical fix.  Old scripts that invoke `hermes login [--flags]` still get
    the actionable deprecation message rather than an argparse error.
    """
    text = _hermes_help_text()
    # The misleading description row that pointed users at a removed command:
    assert "Authenticate with an inference provider" not in text, (
        "Deprecated `hermes login` description still shown in --help: \n" + text
    )
    # Sanity check: actually-functional auth subcommand is still listed.
    assert "auth" in text


def test_login_command_still_responds_with_deprecation_message():
    """Hidden-from-help must NOT mean removed-from-argv.

    Old scripts/aliases invoking `hermes login [--flags]` should keep
    receiving the existing "command has been removed → use `hermes auth`"
    message rather than an argparse "invalid choice: 'login'" error.
    """
    from hermes_cli.auth import login_command

    class _Args:
        provider = None
        portal_url = None
        inference_url = None
        client_id = None
        scope = None
        no_browser = False
        timeout = 15.0
        ca_bundle = None
        insecure = False

    buf = io.StringIO()
    with redirect_stdout(buf):
        with pytest.raises(SystemExit) as exc_info:
            login_command(_Args())
    assert exc_info.value.code == 0
    out = buf.getvalue()
    assert "has been removed" in out
    assert "hermes auth" in out
