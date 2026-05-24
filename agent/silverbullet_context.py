"""Compact SilverBullet workflow context for the system prompt."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping, Optional


DEFAULT_SPACE_PATH = "/srv/seedbox/config/silverbullet/space"
INDEX_PAGES = ("_activity.md", "_ops.md", "_projects.md", "_review.md")
ENTRYPOINT_DIRS = ("Services", "Projects")
MAX_SOURCE_CHARS = 12_000
MAX_LINES_PER_INDEX = 8


def build_silverbullet_context_prompt(config: Optional[Mapping[str, Any]] = None) -> str:
    """Build a small, session-stable SilverBullet context block.

    Returns an empty string when disabled, missing, or unreadable. The output is
    capped by ``max_chars`` and deliberately avoids injecting full project,
    service, or daily-note pages.
    """
    if config is None:
        try:
            from hermes_cli.config import load_config_readonly
            config = load_config_readonly()
        except Exception:
            return ""

    context_cfg = _silverbullet_context_config(config)
    if not _as_bool(context_cfg.get("enabled"), default=False):
        return ""

    space = _resolve_space_path(context_cfg)
    if not space or not space.is_dir():
        return ""

    max_chars = _as_int(context_cfg.get("max_chars"), default=2000, minimum=80)
    parts = [
        "# SilverBullet Workflow Context",
        (
            "Use SilverBullet as active workflow context for durable plans, "
            "operations notes, reviews, and project state; keep prompt context "
            "compact and update the relevant page when workflow state should persist."
        ),
        _entrypoints_block(space),
    ]

    if _as_bool(context_cfg.get("include_activity"), default=True):
        activity = _index_page_block(space, "_activity.md")
        if activity:
            parts.append(activity)

    if _as_bool(context_cfg.get("include_indexes"), default=True):
        for rel in INDEX_PAGES[1:]:
            block = _index_page_block(space, rel)
            if block:
                parts.append(block)

    recent_daily_notes = _as_int(context_cfg.get("recent_daily_notes"), default=1, minimum=0)
    if recent_daily_notes > 0:
        carry = _carry_forward_block(space)
        if carry:
            parts.append(carry)

    return _truncate("\n\n".join(part for part in parts if part), max_chars)


def _silverbullet_context_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    silverbullet = config.get("silverbullet")
    if not isinstance(silverbullet, Mapping):
        return {}
    context = silverbullet.get("context")
    return context if isinstance(context, Mapping) else {}


def _resolve_space_path(context_cfg: Mapping[str, Any]) -> Optional[Path]:
    raw = context_cfg.get("space_path") or os.getenv("SILVERBULLET_SPACE") or DEFAULT_SPACE_PATH
    if not isinstance(raw, str) or not raw.strip():
        return None
    return Path(os.path.expandvars(os.path.expanduser(raw))).resolve()


def _entrypoints_block(_space: Path) -> str:
    entries = [f"- `{rel}`" for rel in (*INDEX_PAGES, *ENTRYPOINT_DIRS)]
    return "Entrypoints:\n" + "\n".join(entries)


def _index_page_block(space: Path, rel: str) -> str:
    path = space / rel
    text = _read_text(path)
    if not text:
        return ""
    lines = _extract_bullet_or_link_lines(text, limit=MAX_LINES_PER_INDEX)
    if not lines:
        return ""
    return f"## `{rel}`\n" + "\n".join(lines)


def _extract_bullet_or_link_lines(text: str, *, limit: int) -> list[str]:
    lines: list[str] = []
    in_fence = False
    for raw_line in text.splitlines():
        if _is_fence_line(raw_line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = _clean_line(raw_line)
        if not line:
            continue
        if _is_bullet(line) or _has_markdown_link(line):
            lines.append(_ensure_bullet(line))
            if len(lines) >= limit:
                break
    return lines


def _carry_forward_block(space: Path) -> str:
    daily_note = _find_latest_daily_note(space)
    if daily_note is None:
        return ""
    text = _read_text(daily_note)
    if not text:
        return ""
    carry = _extract_carry_forward(text, limit=8)
    if not carry:
        return ""
    rel = daily_note.relative_to(space).as_posix()
    return f"## Carry Forward from `{rel}`\n" + "\n".join(carry)


def _find_latest_daily_note(space: Path) -> Optional[Path]:
    candidates: list[tuple[str, float, Path]] = []
    for path in space.rglob("*.md"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(space).parts
        if rel_parts and rel_parts[0] in ENTRYPOINT_DIRS:
            continue
        date_key = _date_key(path)
        if date_key:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            candidates.append((date_key, mtime, path))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2].as_posix()), reverse=True)
    return candidates[0][2]


def _date_key(path: Path) -> str:
    rel = path.as_posix()
    match = re.search(r"(20\d{2})[-_/](\d{2})[-_/](\d{2})", rel)
    if match:
        return "".join(match.groups())
    match = re.search(r"(20\d{2})(\d{2})(\d{2})", rel)
    return "".join(match.groups()) if match else ""


def _extract_carry_forward(text: str, *, limit: int) -> list[str]:
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if re.match(r"^\s{0,3}#{1,6}\s+carry\s+forward\b", line, re.IGNORECASE):
            start = idx + 1
            break
    if start is None:
        return []

    extracted: list[str] = []
    in_fence = False
    for raw_line in lines[start:]:
        if _is_fence_line(raw_line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if re.match(r"^\s{0,3}#{1,6}\s+", raw_line):
            break
        line = _clean_line(raw_line)
        if not line:
            continue
        if _is_bullet(line) or _has_markdown_link(line):
            extracted.append(_ensure_bullet(line))
        else:
            extracted.append(f"- {line}")
        if len(extracted) >= limit:
            break
    return extracted


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:MAX_SOURCE_CHARS]
    except OSError:
        return ""


def _clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip())


def _is_bullet(line: str) -> bool:
    return bool(re.match(r"^([-*+]|\d+[.)])\s+", line))


def _has_markdown_link(line: str) -> bool:
    return "[[" in line or bool(re.search(r"\[[^\]]+\]\([^)]+\)", line))


def _is_fence_line(line: str) -> bool:
    return bool(re.match(r"^\s{0,3}(```|~~~)", line))


def _ensure_bullet(line: str) -> str:
    if _is_bullet(line):
        return line
    return f"- {line}"


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    marker = "\n\n[SilverBullet context truncated to configured max_chars.]"
    if max_chars <= len(marker):
        return marker[:max_chars]
    cutoff = max_chars - len(marker)
    prefix = text[:cutoff].rstrip()
    line_cutoff = prefix.rfind("\n")
    if line_cutoff > 0:
        prefix = prefix[:line_cutoff].rstrip()
    return prefix + marker


def _as_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int(value: Any, *, default: int, minimum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


__all__ = ["build_silverbullet_context_prompt"]
