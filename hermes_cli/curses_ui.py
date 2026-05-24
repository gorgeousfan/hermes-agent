"""Shared curses-based UI components for Hermes CLI.

Used by `hermes tools` and `hermes skills` for interactive checklists.
Provides a curses multi-select with keyboard navigation, plus a
text-based numbered fallback for terminals without curses support.
"""
import os
import sys
from typing import Callable, List, Optional, Set

from hermes_cli.colors import Colors, color


# Tracks whether we've already nudged the user about installing
# ``windows-curses`` so we only mention it once per process — the
# nudge is helpful the first time a Windows user hits a wizard that
# would have used arrow-key navigation, noise otherwise (#24345).
_WINDOWS_CURSES_HINT_SHOWN = False


def _is_windows() -> bool:
    """Local platform check.  Mirrors :func:`hermes_cli.stdio.is_windows`.

    Kept private and dependency-free so this module stays importable
    even when ``hermes_cli.stdio`` hasn't been initialised yet (e.g.
    during early test bootstrapping).
    """
    return sys.platform == "win32"


def _use_ascii_safe_glyphs() -> bool:
    """Return True when the wizard UI should avoid exotic Unicode.

    The numbered-fallback path uses ``✓``, ``→``, ``●``, ``○``, ``↑↓``
    glyphs by default.  Those render fine on Linux/macOS terminals and
    on modern Windows Terminal with a Unicode-capable font (Cascadia
    Code, Consolas under MS Sans Serif, etc.).  They mojibake under
    cmd.exe / PowerShell 5 with the default raster font, which is the
    exact "looks not like Linux" failure mode reported in #24345.

    The detection is conservative: ASCII-only kicks in when **both**

      * the platform is native Windows, and
      * ``hermes_cli.stdio.is_legacy_windows_console()`` reports the
        console couldn't get VT processing enabled.

    Modern Windows Terminal users see no change.  Power users can also
    force the ASCII path by setting ``HERMES_ASCII_GLYPHS=1`` (handy
    for screen readers and the rare terminal font without box-drawing).
    """
    if os.environ.get("HERMES_ASCII_GLYPHS") in {"1", "true", "True", "yes"}:
        return True
    if not _is_windows():
        return False
    try:
        from hermes_cli.stdio import is_legacy_windows_console
        return is_legacy_windows_console()
    except Exception:
        return False


def _glyph(unicode_glyph: str, ascii_fallback: str) -> str:
    """Pick the Unicode or ASCII variant depending on the console.

    Centralised so the dozen-or-so call sites below don't each
    re-implement the platform check.  Returns ``ascii_fallback`` only
    when :func:`_use_ascii_safe_glyphs` says we must — everywhere else
    the user keeps the prettier Unicode rendering.
    """
    return ascii_fallback if _use_ascii_safe_glyphs() else unicode_glyph


def _maybe_show_windows_curses_hint() -> None:
    """Print a one-time hint when curses can't load on Windows (#24345).

    On native Windows the ``curses`` stdlib module isn't shipped — it
    requires the ``windows-curses`` PyPI package.  Without it every
    ``curses_*`` selector silently downgrades to the numbered-fallback
    path, which works but loses arrow-key navigation.  Users who hit
    the issue tracker describe this as "can't use the keys to
    change/toggle" because the fallback expects them to type a digit.

    This helper prints a single, gentle suggestion on the first
    fallback we trigger per process.  Subsequent fallbacks are silent
    so we don't carpet-bomb the wizard output.
    """
    global _WINDOWS_CURSES_HINT_SHOWN
    if _WINDOWS_CURSES_HINT_SHOWN or not _is_windows():
        return
    _WINDOWS_CURSES_HINT_SHOWN = True
    try:
        print(
            color(
                "  Tip: install 'windows-curses' for arrow-key navigation in setup wizards:",
                Colors.DIM,
            )
        )
        print(color("        pip install windows-curses", Colors.DIM))
    except Exception:
        pass


def flush_stdin() -> None:
    """Flush any stray bytes from the stdin input buffer.

    Must be called after ``curses.wrapper()`` (or any terminal-mode library
    like simple_term_menu) returns, **before** the next ``input()`` /
    ``getpass.getpass()`` call.  ``curses.endwin()`` restores the terminal
    but does NOT drain the OS input buffer — leftover escape-sequence bytes
    (from arrow keys, terminal mode-switch responses, or rapid keypresses)
    remain buffered and silently get consumed by the next ``input()`` call,
    corrupting user data (e.g. writing ``^[^[`` into .env files).

    On non-TTY stdin (piped, redirected) or Windows, this is a no-op.
    """
    try:
        if not sys.stdin.isatty():
            return
        import termios
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:
        pass


def curses_checklist(
    title: str,
    items: List[str],
    selected: Set[int],
    *,
    cancel_returns: Set[int] | None = None,
    status_fn: Optional[Callable[[Set[int]], str]] = None,
) -> Set[int]:
    """Curses multi-select checklist. Returns set of selected indices.

    Args:
        title: Header line displayed above the checklist.
        items: Display labels for each row.
        selected: Indices that start checked (pre-selected).
        cancel_returns: Returned on ESC/q. Defaults to the original *selected*.
        status_fn: Optional callback ``f(chosen_indices) -> str`` whose return
            value is rendered on the bottom row of the terminal.  Use this for
            live aggregate info (e.g. estimated token counts).
    """
    if cancel_returns is None:
        cancel_returns = set(selected)

    # Safety: curses and input() both hang or spin when stdin is not a
    # terminal (e.g. subprocess pipe).  Return defaults immediately.
    if not sys.stdin.isatty():
        return cancel_returns

    try:
        import curses
        chosen = set(selected)
        result_holder: list = [None]

        def _draw(stdscr):
            curses.curs_set(0)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_GREEN, -1)
                curses.init_pair(2, curses.COLOR_YELLOW, -1)
                curses.init_pair(3, 8 if curses.COLORS > 8 else curses.COLOR_WHITE, -1)  # dim gray
            cursor = 0
            scroll_offset = 0

            while True:
                stdscr.clear()
                max_y, max_x = stdscr.getmaxyx()

                # Reserve bottom row for status bar when status_fn provided
                footer_rows = 1 if status_fn else 0

                # Header
                try:
                    hattr = curses.A_BOLD
                    if curses.has_colors():
                        hattr |= curses.color_pair(2)
                    stdscr.addnstr(0, 0, title, max_x - 1, hattr)
                    stdscr.addnstr(
                        1, 0,
                        "  ↑↓ navigate  SPACE toggle  ENTER confirm  ESC cancel",
                        max_x - 1, curses.A_DIM,
                    )
                except curses.error:
                    pass

                # Scrollable item list
                visible_rows = max_y - 3 - footer_rows
                if cursor < scroll_offset:
                    scroll_offset = cursor
                elif cursor >= scroll_offset + visible_rows:
                    scroll_offset = cursor - visible_rows + 1

                for draw_i, i in enumerate(
                    range(scroll_offset, min(len(items), scroll_offset + visible_rows))
                ):
                    y = draw_i + 3
                    if y >= max_y - 1 - footer_rows:
                        break
                    check = "✓" if i in chosen else " "
                    arrow = "→" if i == cursor else " "
                    line = f" {arrow} [{check}] {items[i]}"
                    attr = curses.A_NORMAL
                    if i == cursor:
                        attr = curses.A_BOLD
                        if curses.has_colors():
                            attr |= curses.color_pair(1)
                    try:
                        stdscr.addnstr(y, 0, line, max_x - 1, attr)
                    except curses.error:
                        pass

                # Status bar (bottom row, right-aligned)
                if status_fn:
                    try:
                        status_text = status_fn(chosen)
                        if status_text:
                            # Right-align on the bottom row
                            sx = max(0, max_x - len(status_text) - 1)
                            sattr = curses.A_DIM
                            if curses.has_colors():
                                sattr |= curses.color_pair(3)
                            stdscr.addnstr(max_y - 1, sx, status_text, max_x - sx - 1, sattr)
                    except curses.error:
                        pass

                stdscr.refresh()
                key = stdscr.getch()

                if key in {curses.KEY_UP, ord("k")}:
                    cursor = (cursor - 1) % len(items)
                elif key in {curses.KEY_DOWN, ord("j")}:
                    cursor = (cursor + 1) % len(items)
                elif key == ord(" "):
                    chosen.symmetric_difference_update({cursor})
                elif key in {curses.KEY_ENTER, 10, 13}:
                    result_holder[0] = set(chosen)
                    return
                elif key in {27, ord("q")}:
                    result_holder[0] = cancel_returns
                    return

        curses.wrapper(_draw)
        flush_stdin()
        return result_holder[0] if result_holder[0] is not None else cancel_returns

    except KeyboardInterrupt:
        return cancel_returns
    except Exception:
        return _numbered_fallback(title, items, selected, cancel_returns, status_fn)


def curses_radiolist(
    title: str,
    items: List[str],
    selected: int = 0,
    *,
    cancel_returns: int | None = None,
    description: str | None = None,
) -> int:
    """Curses single-select radio list. Returns the selected index.

    Args:
        title: Header line displayed above the list.
        items: Display labels for each row.
        selected: Index that starts selected (pre-selected).
        cancel_returns: Returned on ESC/q. Defaults to the original *selected*.
        description: Optional multi-line text shown between the title and
            the item list.  Useful for context that should survive the
            curses screen clear.
    """
    if cancel_returns is None:
        cancel_returns = selected

    if not sys.stdin.isatty():
        return cancel_returns

    desc_lines: list[str] = []
    if description:
        desc_lines = description.splitlines()

    try:
        import curses
        result_holder: list = [None]

        def _draw(stdscr):
            curses.curs_set(0)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_GREEN, -1)
                curses.init_pair(2, curses.COLOR_YELLOW, -1)
            cursor = selected
            scroll_offset = 0

            while True:
                stdscr.clear()
                max_y, max_x = stdscr.getmaxyx()

                row = 0

                # Header
                try:
                    hattr = curses.A_BOLD
                    if curses.has_colors():
                        hattr |= curses.color_pair(2)
                    stdscr.addnstr(row, 0, title, max_x - 1, hattr)
                    row += 1

                    # Description lines
                    for dline in desc_lines:
                        if row >= max_y - 1:
                            break
                        stdscr.addnstr(row, 0, dline, max_x - 1, curses.A_NORMAL)
                        row += 1

                    stdscr.addnstr(
                        row, 0,
                        "  \u2191\u2193 navigate  ENTER/SPACE select  ESC cancel",
                        max_x - 1, curses.A_DIM,
                    )
                    row += 1
                except curses.error:
                    pass

                # Scrollable item list
                items_start = row + 1
                visible_rows = max_y - items_start - 1
                if cursor < scroll_offset:
                    scroll_offset = cursor
                elif cursor >= scroll_offset + visible_rows:
                    scroll_offset = cursor - visible_rows + 1

                for draw_i, i in enumerate(
                    range(scroll_offset, min(len(items), scroll_offset + visible_rows))
                ):
                    y = draw_i + items_start
                    if y >= max_y - 1:
                        break
                    radio = "\u25cf" if i == selected else "\u25cb"
                    arrow = "\u2192" if i == cursor else " "
                    line = f" {arrow} ({radio}) {items[i]}"
                    attr = curses.A_NORMAL
                    if i == cursor:
                        attr = curses.A_BOLD
                        if curses.has_colors():
                            attr |= curses.color_pair(1)
                    try:
                        stdscr.addnstr(y, 0, line, max_x - 1, attr)
                    except curses.error:
                        pass

                stdscr.refresh()
                key = stdscr.getch()

                if key in {curses.KEY_UP, ord("k")}:
                    cursor = (cursor - 1) % len(items)
                elif key in {curses.KEY_DOWN, ord("j")}:
                    cursor = (cursor + 1) % len(items)
                elif key in {ord(" "), curses.KEY_ENTER, 10, 13}:
                    result_holder[0] = cursor
                    return
                elif key in {27, ord("q")}:
                    result_holder[0] = cancel_returns
                    return

        curses.wrapper(_draw)
        flush_stdin()
        return result_holder[0] if result_holder[0] is not None else cancel_returns

    except KeyboardInterrupt:
        return cancel_returns
    except Exception:
        return _radio_numbered_fallback(title, items, selected, cancel_returns)


def _radio_numbered_fallback(
    title: str,
    items: List[str],
    selected: int,
    cancel_returns: int,
) -> int:
    """Text-based numbered fallback for radio selection."""
    _maybe_show_windows_curses_hint()
    print(color(f"\n  {title}", Colors.YELLOW))
    print(color("  Select by number, then press Enter to confirm.\n", Colors.DIM))

    selected_glyph = _glyph("\u25cf", "*")  # ● vs *
    empty_glyph = _glyph("\u25cb", " ")      # ○ vs (space)
    for i, label in enumerate(items):
        marker = (
            color(f"({selected_glyph})", Colors.GREEN)
            if i == selected
            else f"({empty_glyph})"
        )
        print(f"  {marker} {i + 1:>2}. {label}")
    print()
    try:
        val = input(color(f"  Choice [default {selected + 1}]: ", Colors.DIM)).strip()
        if not val:
            return selected
        idx = int(val) - 1
        if 0 <= idx < len(items):
            return idx
        return selected
    except (ValueError, KeyboardInterrupt, EOFError):
        return cancel_returns


def curses_single_select(
    title: str,
    items: List[str],
    default_index: int = 0,
    *,
    cancel_label: str = "Cancel",
) -> int | None:
    """Curses single-select menu. Returns selected index or None on cancel.

    Works inside prompt_toolkit because curses.wrapper() restores the terminal
    safely, unlike simple_term_menu which conflicts with /dev/tty.
    """
    if not sys.stdin.isatty():
        return None

    try:
        import curses
        result_holder: list = [None]

        all_items = list(items) + [cancel_label]
        cancel_idx = len(items)

        def _draw(stdscr):
            curses.curs_set(0)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_GREEN, -1)
                curses.init_pair(2, curses.COLOR_YELLOW, -1)
            cursor = min(default_index, len(all_items) - 1)
            scroll_offset = 0

            while True:
                stdscr.clear()
                max_y, max_x = stdscr.getmaxyx()

                try:
                    hattr = curses.A_BOLD
                    if curses.has_colors():
                        hattr |= curses.color_pair(2)
                    stdscr.addnstr(0, 0, title, max_x - 1, hattr)
                    stdscr.addnstr(
                        1, 0,
                        "  ↑↓ navigate  ENTER confirm  ESC/q cancel",
                        max_x - 1, curses.A_DIM,
                    )
                except curses.error:
                    pass

                visible_rows = max_y - 3
                if cursor < scroll_offset:
                    scroll_offset = cursor
                elif cursor >= scroll_offset + visible_rows:
                    scroll_offset = cursor - visible_rows + 1

                for draw_i, i in enumerate(
                    range(scroll_offset, min(len(all_items), scroll_offset + visible_rows))
                ):
                    y = draw_i + 3
                    if y >= max_y - 1:
                        break
                    arrow = "→" if i == cursor else " "
                    line = f" {arrow} {all_items[i]}"
                    attr = curses.A_NORMAL
                    if i == cursor:
                        attr = curses.A_BOLD
                        if curses.has_colors():
                            attr |= curses.color_pair(1)
                    try:
                        stdscr.addnstr(y, 0, line, max_x - 1, attr)
                    except curses.error:
                        pass

                stdscr.refresh()
                key = stdscr.getch()

                if key in {curses.KEY_UP, ord("k")}:
                    cursor = (cursor - 1) % len(all_items)
                elif key in {curses.KEY_DOWN, ord("j")}:
                    cursor = (cursor + 1) % len(all_items)
                elif key in {curses.KEY_ENTER, 10, 13}:
                    result_holder[0] = cursor
                    return
                elif key in {27, ord("q")}:
                    result_holder[0] = None
                    return

        curses.wrapper(_draw)
        flush_stdin()
        if result_holder[0] is not None and result_holder[0] >= cancel_idx:
            return None
        return result_holder[0]

    except KeyboardInterrupt:
        return None
    except Exception:
        all_items = list(items) + [cancel_label]
        cancel_idx = len(items)
        return _numbered_single_fallback(title, all_items, cancel_idx)


def _numbered_single_fallback(
    title: str,
    items: List[str],
    cancel_idx: int,
) -> int | None:
    """Text-based numbered fallback for single-select."""
    _maybe_show_windows_curses_hint()
    print(f"\n  {title}\n")
    for i, label in enumerate(items, 1):
        print(f"  {i}. {label}")
    print()
    try:
        val = input(f"  Choice [1-{len(items)}]: ").strip()
        if not val:
            return None
        idx = int(val) - 1
        if 0 <= idx < len(items) and idx < cancel_idx:
            return idx
        if idx == cancel_idx:
            return None
    except (ValueError, KeyboardInterrupt, EOFError):
        pass
    return None


def _numbered_fallback(
    title: str,
    items: List[str],
    selected: Set[int],
    cancel_returns: Set[int],
    status_fn: Optional[Callable[[Set[int]], str]] = None,
) -> Set[int]:
    """Text-based toggle fallback for terminals without curses."""
    _maybe_show_windows_curses_hint()
    chosen = set(selected)
    print(color(f"\n  {title}", Colors.YELLOW))
    print(color(
        "  Type the item number to toggle it, then press Enter to confirm.\n",
        Colors.DIM,
    ))

    checked_glyph = _glyph("\u2713", "x")  # ✓ vs x
    while True:
        for i, label in enumerate(items):
            marker = (
                color(f"[{checked_glyph}]", Colors.GREEN)
                if i in chosen
                else "[ ]"
            )
            print(f"  {marker} {i + 1:>2}. {label}")
        if status_fn:
            status_text = status_fn(chosen)
            if status_text:
                print(color(f"\n  {status_text}", Colors.DIM))
        print()
        try:
            val = input(color("  Toggle # (or Enter to confirm): ", Colors.DIM)).strip()
            if not val:
                break
            idx = int(val) - 1
            if 0 <= idx < len(items):
                chosen.symmetric_difference_update({idx})
        except (ValueError, KeyboardInterrupt, EOFError):
            return cancel_returns
        print()

    return chosen
