"""
Telegram markdown parsing and text utility functions.

Extracted from telegram.py to keep the adapter class focused.
"""

import re


def check_telegram_requirements() -> bool:
    """Check if Telegram dependencies are available.

    If python-telegram-bot is missing, attempts to lazy-install it via
    ``tools.lazy_deps.ensure("platform.telegram")``. After a successful
    install, re-imports the SDK and flips ``TELEGRAM_AVAILABLE`` to True
    so the adapter's class-level type aliases get rebound.
    """
    global TELEGRAM_AVAILABLE, Update, Bot, Message, InlineKeyboardButton
    global InlineKeyboardMarkup, LinkPreviewOptions, Application
    global CommandHandler, CallbackQueryHandler, TelegramMessageHandler
    global ContextTypes, filters, ParseMode, ChatType, HTTPXRequest
    if TELEGRAM_AVAILABLE:
        return True
    try:
        from tools.lazy_deps import ensure as _lazy_ensure
        _lazy_ensure("platform.telegram", prompt=False)
    except Exception:
        return False
    try:
        from telegram import Update as _Update, Bot as _Bot, Message as _Message
        from telegram import InlineKeyboardButton as _IKB, InlineKeyboardMarkup as _IKM
        try:
            from telegram import LinkPreviewOptions as _LPO
        except ImportError:
            _LPO = None
        from telegram.ext import (
            Application as _App, CommandHandler as _CH,
            CallbackQueryHandler as _CQH,
            MessageHandler as _MH,
            ContextTypes as _CT, filters as _filters,
        )
        from telegram.constants import ParseMode as _PM, ChatType as _CtT
        from telegram.request import HTTPXRequest as _HR
    except ImportError:
        return False
    Update = _Update
    Bot = _Bot
    Message = _Message
    InlineKeyboardButton = _IKB
    InlineKeyboardMarkup = _IKM
    LinkPreviewOptions = _LPO
    Application = _App
    CommandHandler = _CH
    CallbackQueryHandler = _CQH
    TelegramMessageHandler = _MH
    ContextTypes = _CT
    filters = _filters
    ParseMode = _PM
    ChatType = _CtT
    HTTPXRequest = _HR
    TELEGRAM_AVAILABLE = True
    return True


# Matches every character that MarkdownV2 requires to be backslash-escaped
# when it appears outside a code span or fenced code block.
_MDV2_ESCAPE_RE = re.compile(r'([_*\[\]()~`>#\+\-=|{}.!\\])')


def _escape_mdv2(text: str) -> str:
    """Escape Telegram MarkdownV2 special characters with a preceding backslash."""
    return _MDV2_ESCAPE_RE.sub(r'\\\1', text)


def _strip_mdv2(text: str) -> str:
    """Strip MarkdownV2 escape backslashes to produce clean plain text.

    Also removes MarkdownV2 formatting markers so the fallback
    doesn't show stray syntax characters from format_message conversion.
    """
    # Remove escape backslashes before special characters
    cleaned = re.sub(r'\\([_*\[\]()~`>#\+\-=|{}.!\\])', r'\1', text)
    # Remove MarkdownV2 bold markers that format_message converted from **bold**
    cleaned = re.sub(r'\*([^*]+)\*', r'\1', cleaned)
    # Remove MarkdownV2 italic markers that format_message converted from *italic*
    # Use word boundary (\b) to avoid breaking snake_case like my_variable_name
    cleaned = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'\1', cleaned)
    # Remove MarkdownV2 strikethrough markers (~text~ → text)
    cleaned = re.sub(r'~([^~]+)~', r'\1', cleaned)
    # Remove MarkdownV2 spoiler markers (||text|| → text)
    cleaned = re.sub(r'\|\|([^|]+)\|\|', r'\1', cleaned)
    return cleaned


# ---------------------------------------------------------------------------
# Markdown table → Telegram-friendly row groups
# ---------------------------------------------------------------------------
# Telegram's MarkdownV2 has no table syntax — '|' is just an escaped literal,
# so pipe tables render as noisy backslash-pipe text with no alignment.
# Reformating each row into a bold heading plus bullet list keeps the content
# readable on mobile clients while preserving the source data.

# Matches a GFM table delimiter row: optional outer pipes, cells containing
# only dashes (with optional leading/trailing colons for alignment) separated
# by '|'.  Requires at least one internal '|' so lone '---' horizontal rules
# are NOT matched.
_TABLE_SEPARATOR_RE = re.compile(
    r'^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*){1,}\|?\s*$'
)


def _is_table_row(line: str) -> bool:
    """Return True if *line* could plausibly be a table data row."""
    stripped = line.strip()
    return bool(stripped) and '|' in stripped


def _split_markdown_table_row(line: str) -> list[str]:
    """Split a simple GFM table row into stripped cell values."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _render_table_block_for_telegram(table_block: list[str]) -> str:
    """Render a detected GFM table as Telegram-friendly row groups."""
    if len(table_block) < 3:
        return "\n".join(table_block)

    headers = _split_markdown_table_row(table_block[0])
    if len(headers) < 2:
        return "\n".join(table_block)

    # Detect row-label column: present when data rows have one more cell
    # than the header row (the row-label column carries no header).
    first_data_row = _split_markdown_table_row(table_block[2]) if len(table_block) > 2 else []
    has_row_label_col = len(first_data_row) == len(headers) + 1

    rendered_rows: list[str] = []
    for index, row in enumerate(table_block[2:], start=1):
        cells = _split_markdown_table_row(row)
        if has_row_label_col:
            # First cell is the row-label (heading); remaining cells align with headers.
            heading = cells[0] if cells and cells[0] else f"Row {index}"
            data_cells = cells[1:]
        else:
            # No row-label column: use first non-empty cell as heading.
            heading = next((cell for cell in cells if cell), f"Row {index}")
            data_cells = cells

        # Pad or trim data_cells to match headers length.
        if len(data_cells) < len(headers):
            data_cells.extend([""] * (len(headers) - len(data_cells)))
        elif len(data_cells) > len(headers):
            data_cells = data_cells[: len(headers)]

        rendered_rows.append(f"**{heading}**")
        rendered_rows.extend(
            f"• {header}: {value}" for header, value in zip(headers, data_cells)
        )

    return "\n\n".join(rendered_rows)


def _wrap_markdown_tables(text: str) -> str:
    """Rewrite GFM-style pipe tables into Telegram-friendly bullet groups.

    Detected by a row containing '|' immediately followed by a delimiter
    row matching :data:`_TABLE_SEPARATOR_RE`.  Subsequent pipe-containing
    non-blank lines are consumed as the table body and rewritten as
    per-row bullet groups. Tables inside existing fenced code blocks are left
    alone.
    """
    if '|' not in text or '-' not in text:
        return text

    lines = text.split('\n')
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        # Track existing fenced code blocks — never touch content inside.
        if stripped.startswith('```'):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue

        # Look for a header row (contains '|') immediately followed by a
        # delimiter row.
        if (
            '|' in line
            and i + 1 < len(lines)
            and _TABLE_SEPARATOR_RE.match(lines[i + 1])
        ):
            table_block = [line, lines[i + 1]]
            j = i + 2
            while j < len(lines) and _is_table_row(lines[j]):
                table_block.append(lines[j])
                j += 1
            out.append(_render_table_block_for_telegram(table_block))
            i = j
            continue

        out.append(line)
        i += 1

    return '\n'.join(out)


