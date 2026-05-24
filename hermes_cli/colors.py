"""Shared ANSI color utilities for Hermes CLI modules."""

import os
import sys


def should_use_color() -> bool:
    """Return True when colored output is appropriate.

    Respects the NO_COLOR environment variable (https://no-color.org/)
    and TERM=dumb, in addition to the existing TTY check.

    On Windows we additionally consult the VT-processing detector from
    :mod:`hermes_cli.stdio`.  If ``configure_windows_stdio`` was unable
    to enable ``ENABLE_VIRTUAL_TERMINAL_PROCESSING`` for the attached
    console (legacy Windows Console Host, very old cmd.exe, etc.) we
    suppress colors entirely — otherwise the setup wizard's banner
    prints as literal ``←[35m┌────...`` garbage which is exactly the
    issue users hit on Windows native (#24345).
    """
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    if not sys.stdout.isatty():
        return False
    # Defensive import: colors.py is loaded very early during CLI
    # startup, sometimes before configure_windows_stdio() has run.  We
    # tolerate a missing detector rather than break colour output on
    # POSIX in that window.
    try:
        from hermes_cli.stdio import is_legacy_windows_console
        if is_legacy_windows_console():
            return False
    except Exception:
        pass
    return True


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"


def color(text: str, *codes) -> str:
    """Apply color codes to text (only when color output is appropriate)."""
    if not should_use_color():
        return text
    return "".join(codes) + text + Colors.RESET
