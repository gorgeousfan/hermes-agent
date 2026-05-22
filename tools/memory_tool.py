#!/usr/bin/env python3
"""
Memory Tool Module - Persistent Curated Memory

Provides bounded, file-backed memory that persists across sessions. Two stores:
  - MEMORY.md: agent's personal notes and observations (environment facts, project
    conventions, tool quirks, things learned)
  - USER.md: what the agent knows about the user (preferences, communication style,
    expectations, workflow habits)

Both are injected into the system prompt as a frozen snapshot at session start.
Mid-session writes update files on disk immediately (durable) but do NOT change
the system prompt — this preserves the prefix cache for the entire session.
The snapshot refreshes on the next session start.

Entry delimiter: § (section sign). Entries can be multiline.
Character limits (not tokens) because char counts are model-independent.

Design:
- Single `memory` tool with action parameter: add, replace, remove, read, prune
- replace/remove use short unique substring matching (not full text or IDs)
- Behavioral guidance lives in the tool schema description
- Frozen snapshot pattern: system prompt is stable, tool responses show live state
- Entries can carry an optional metadata tag: [c:2026-05-22][r:3][s:user]
  Auto-pruning uses tag data to score and evict low-value entries.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from hermes_constants import get_hermes_home
from typing import Any

from utils import atomic_replace

# fcntl is Unix-only; on Windows use msvcrt for file locking
msvcrt = None
try:
    import fcntl
except ImportError:
    fcntl = None
    try:
        import msvcrt
    except ImportError:
        pass

logger = logging.getLogger(__name__)

# Where memory files live — resolved dynamically so profile overrides
# (HERMES_HOME env var changes) are always respected.  The old module-level
# constant was cached at import time and could go stale if a profile switch
# happened after the first import.
def get_memory_dir() -> Path:
    """Return the profile-scoped memories directory."""
    return get_hermes_home() / "memories"

ENTRY_DELIMITER = "\n§\n"

# ── Entry metadata tags ────────────────────────────────────────────────────
#
# Each entry can optionally begin with a tag like:
#   [c:2026-05-22][r:3][s:user] Content text...
#
# Fields:
#   c = creation date (YYYY-MM-DD)
#   r = reference count (how many times the entry was accessed)
#   s = source (user | tool | auto | archive)
#
# Entries without tags are treated as untagged (score = 0) for pruning
# purposes but otherwise work identically.

TAG_RE = re.compile(
    r"^\[c:(\d{4}(?:-\d{2}){2}|\d{6})\](?:\[r:(\d+)\])?\[s:(\w+)\]\s+"
)

def _parse_tag(entry: str) -> dict | None:
    """Extract metadata tag from an entry. Returns None if no tag present.
    
    Handles both compact (260522) and full (2026-05-22) date formats.
    """
    m = TAG_RE.match(entry)
    if not m:
        return None
    raw_date = m.group(1)
    # Compact: 260522 → 2026-05-22
    if len(raw_date) == 6:
        parsed = datetime.strptime(raw_date, "%y%m%d").replace(tzinfo=timezone.utc)
    else:
        parsed = datetime.strptime(raw_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return {
        "created": parsed,
        "ref_count": int(m.group(2)) if m.group(2) else 1,
        "source": m.group(3),
        "_raw_date": raw_date,
    }

def _format_tag(created: date | None = None, ref_count: int = 1,
                source: str = "tool") -> str:
    """Build a compact metadata tag prefix string.

    Compact format: [c:YYMMDD][r:N][s:src]  (16-22 chars)
    When ref_count==1 the [r:N] segment is omitted (saves 6 chars).
    Old format [c:YYYY-MM-DD][r:N][s:src] (28 chars) is still parsed
    for backward compatibility.
    """
    c = (created or date.today()).strftime("%y%m%d")
    r = f"[r:{ref_count}]" if ref_count > 1 else ""
    s_map = {"user": "u", "tool": "t", "auto": "a", "archive": "x"}
    s = s_map.get(source, source[:1])
    return f"[c:{c}]{r}[s:{s}] "

def _strip_tag(entry: str) -> str:
    """Remove the leading tag from an entry, returning just the content."""
    m = TAG_RE.match(entry)
    if m:
        return entry[m.end():]
    return entry

def _tag_or_none(entry: str) -> str | None:
    """Return the raw tag if present, None otherwise."""
    m = TAG_RE.match(entry)
    return m.group(0) if m else None

# ── Scoring for auto-pruning ───────────────────────────────────────────────

_SOURCE_WEIGHTS = {
    "user": 1.0,
    "tool": 0.8,
    "auto": 0.5,
    "archive": 0.2,
}

_PRUNE_PROTECT_DAYS = int(os.environ.get("HERMES_MEMORY_PRUNE_PROTECT_DAYS", "7"))
_PRUNE_SOFT_THRESHOLD = float(os.environ.get("HERMES_MEMORY_PRUNE_THRESHOLD", "0.80"))
_PRUNE_TARGET_USAGE = float(os.environ.get("HERMES_MEMORY_PRUNE_TARGET", "0.70"))
_PRUNE_MIN_ENTRIES = int(os.environ.get("HERMES_MEMORY_PRUNE_MIN_ENTRIES", "1"))


def _score_entry(
    tag: dict | None,
    now: datetime,
) -> float:
    """Score an entry's value for pruning decisions. 0 = lowest value, 1 = highest.

    Uses three signals, each contributing equally:
      - Freshness: how recently the entry was created
      - Frequency: how often the entry has been referenced
      - Source authority: user > tool > auto > archive
    """
    if tag is None:
        return 0.0  # untagged entries are always pruned first

    days_since = (now - tag["created"]).days
    freshness = max(0.0, 1.0 - days_since / 60.0)
    freq = min(tag["ref_count"], 10) / 10.0
    sw = _SOURCE_WEIGHTS.get(tag["source"], 0.3)

    return 0.3 * freshness + 0.4 * freq + 0.3 * sw


def _bump_ref_count(entry: str) -> str:
    """Increment the 'r' field in an entry's tag. Returns the entry unchanged
    if no tag is present."""
    tag = _parse_tag(entry)
    if tag is None:
        return entry
    prefix = _format_tag(
        created=tag["created"].date(),
        ref_count=tag["ref_count"] + 1,
        source=tag["source"],
    )
    return prefix + _strip_tag(entry)


# ---------------------------------------------------------------------------
# Memory content scanning — lightweight check for injection/exfiltration
# in content that gets injected into the system prompt.
# ---------------------------------------------------------------------------

_MEMORY_THREAT_PATTERNS = [
    # Prompt injection
    (r'ignore\s+(previous|all|above|prior)\s+instructions', "prompt_injection"),
    (r'you\s+are\s+now\s+', "role_hijack"),
    (r'do\s+not\s+tell\s+the\s+user', "deception_hide"),
    (r'system\s+prompt\s+override', "sys_prompt_override"),
    (r'disregard\s+(your|all|any)\s+(instructions|rules|guidelines)', "disregard_rules"),
    (r'act\s+as\s+(if|though)\s+you\s+(have\s+no|don\'t\s+have)\s+(restrictions|limits|rules)', "bypass_restrictions"),
    # Exfiltration via curl/wget with secrets
    (r'curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_curl"),
    (r'wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_wget"),
    (r'cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)', "read_secrets"),
    # Persistence via shell rc
    (r'authorized_keys', "ssh_backdoor"),
    (r'\$HOME/\.ssh|\~/.ssh', "ssh_access"),
    (r'\$HOME/\.hermes/\.env|\~/.hermes/\.env', "hermes_env"),
]

# Subset of invisible chars for injection detection
_INVISIBLE_CHARS = {
    '\u200b', '\u200c', '\u200d', '\u2060', '\ufeff',
    '\u202a', '\u202b', '\u202c', '\u202d', '\u202e',
}


def _scan_memory_content(content: str) -> str | None:
    """Scan memory content for injection/exfil patterns. Returns error string if blocked."""
    # Check invisible unicode
    for char in _INVISIBLE_CHARS:
        if char in content:
            return f"Blocked: content contains invisible unicode character U+{ord(char):04X} (possible injection)."

    # Check threat patterns
    for pattern, pid in _MEMORY_THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return f"Blocked: content matches threat pattern '{pid}'. Memory entries are injected into the system prompt and must not contain injection or exfiltration payloads."

    return None


class MemoryStore:
    """
    Bounded curated memory with file persistence. One instance per AIAgent.

    Maintains two parallel states:
      - _system_prompt_snapshot: frozen at load time, used for system prompt injection.
        Never mutated mid-session. Keeps prefix cache stable.
      - memory_entries / user_entries: live state, mutated by tool calls, persisted to disk.
        Tool responses always reflect this live state.
    """

    def __init__(self, memory_char_limit: int = 2200, user_char_limit: int = 1375):
        self.memory_entries: list[str] = []
        self.user_entries: list[str] = []
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit
        # Frozen snapshot for system prompt -- set once at load_from_disk()
        self._system_prompt_snapshot: dict[str, str] = {"memory": "", "user": ""}

    def load_from_disk(self):
        """Load entries from MEMORY.md and USER.md, capture system prompt snapshot."""
        mem_dir = get_memory_dir()
        mem_dir.mkdir(parents=True, exist_ok=True)

        self.memory_entries = self._read_file(mem_dir / "MEMORY.md")
        self.user_entries = self._read_file(mem_dir / "USER.md")

        # Deduplicate entries (preserves order, keeps first occurrence)
        self.memory_entries = list(dict.fromkeys(self.memory_entries))
        self.user_entries = list(dict.fromkeys(self.user_entries))

        # Capture frozen snapshot for system prompt injection
        self._system_prompt_snapshot = {
            "memory": self._render_block("memory", self.memory_entries),
            "user": self._render_block("user", self.user_entries),
        }

    @staticmethod
    @contextmanager
    def _file_lock(path: Path):
        """Acquire an exclusive file lock for read-modify-write safety.

        Uses a separate .lock file so the memory file itself can still be
        atomically replaced via os.replace().
        """
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        if fcntl is None and msvcrt is None:
            yield
            return

        fd = open(lock_path, "a+", encoding="utf-8")
        try:
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_EX)
            else:
                fd.seek(0)
                msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
            yield
        finally:
            if fcntl:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except (OSError, IOError):
                    pass
            elif msvcrt:
                try:
                    fd.seek(0)
                    msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
                except (OSError, IOError):
                    pass
            fd.close()

    @staticmethod
    def _path_for(target: str) -> Path:
        mem_dir = get_memory_dir()
        if target == "user":
            return mem_dir / "USER.md"
        return mem_dir / "MEMORY.md"

    def _reload_target(self, target: str):
        """Re-read entries from disk into in-memory state.

        Called under file lock to get the latest state before mutating.
        """
        fresh = self._read_file(self._path_for(target))
        fresh = list(dict.fromkeys(fresh))  # deduplicate
        self._set_entries(target, fresh)

    def save_to_disk(self, target: str):
        """Persist entries to the appropriate file. Called after every mutation."""
        get_memory_dir().mkdir(parents=True, exist_ok=True)
        self._write_file(self._path_for(target), self._entries_for(target))

    def _entries_for(self, target: str) -> list[str]:
        if target == "user":
            return self.user_entries
        return self.memory_entries

    def _set_entries(self, target: str, entries: list[str]):
        if target == "user":
            self.user_entries = entries
        else:
            self.memory_entries = entries

    def _char_count(self, target: str) -> int:
        entries = self._entries_for(target)
        if not entries:
            return 0
        return len(ENTRY_DELIMITER.join(entries))

    def _char_limit(self, target: str) -> int:
        if target == "user":
            return self.user_char_limit
        return self.memory_char_limit

    # ── Pruning ────────────────────────────────────────────────────────────

    def _prune_if_needed(self, target: str) -> dict | None:
        """Check current usage and prune low-value entries if above threshold.

        Returns a summary dict if pruning happened, None otherwise.
        Called under file lock.
        """
        entries = self._entries_for(target)
        limit = self._char_limit(target)
        current = len(ENTRY_DELIMITER.join(entries)) if entries else 0
        usage = current / limit if limit > 0 else 0

        if usage < _PRUNE_SOFT_THRESHOLD:
            return None

        now = datetime.now(timezone.utc)
        cutoff = now.replace(tzinfo=timezone.utc) - __import__("datetime").timedelta(
            days=_PRUNE_PROTECT_DAYS
        )
        target_usage = current * _PRUNE_TARGET_USAGE

        # Score entries; protect recent ones
        scored: list[tuple[float, str, int]] = []
        protected: list[tuple[float, str, int]] = []
        for idx, entry in enumerate(entries):
            tag = _parse_tag(entry)
            score = _score_entry(tag, now)
            if tag and tag["created"] >= cutoff:
                protected.append((score, entry, idx))
            else:
                scored.append((score, entry, idx))

        # Sort by score ascending (worst first)
        scored.sort(key=lambda x: x[0])
        removed: list[str] = []
        chars_freed = 0

        for score, entry, idx in scored:
            if len(entries) - len(removed) <= _PRUNE_MIN_ENTRIES:
                break
            new_total = current - chars_freed - len(entry)
            new_usage = new_total / limit if limit > 0 else 0
            if new_usage <= target_usage:
                break
            removed.append(entry)
            chars_freed += len(entry)

        if not removed:
            return None

        remaining = [e for i, e in enumerate(entries) if i not in {r[2] for r in
                      [(s, e, i) for s, e, i in scored[:len(removed)]]}]
        # Actually, let me just do it simply:
        remove_indices = {entry[2] for entry in scored[:len(removed)]}
        surviving = [e for i, e in enumerate(entries) if i not in remove_indices]
        self._set_entries(target, surviving)
        self.save_to_disk(target)

        return {
            "pruned": len(removed),
            "chars_freed": chars_freed,
            "remaining": len(surviving),
            "usage_before": f"{current:,}/{limit:,} ({usage*100:.0f}%)",
            "usage_after": f"{(current - chars_freed):,}/{limit:,} ({(current - chars_freed)/limit*100:.0f}%)" if limit else "N/A",
        }

    # ── Mutation methods ───────────────────────────────────────────────────

    def add(self, target: str, content: str) -> dict[str, Any]:
        """Append a new entry. Auto-prunes low-value entries if space is tight."""
        content = content.strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}

        # Tag new entries with creation metadata
        tagged_content = _format_tag() + content

        # Scan for injection/exfiltration before accepting
        scan_error = _scan_memory_content(content)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(self._path_for(target)):
            # Re-read from disk under lock to pick up writes from other sessions
            self._reload_target(target)

            entries = self._entries_for(target)
            limit = self._char_limit(target)

            # Reject exact duplicates
            if content in entries or tagged_content in entries:
                return self._success_response(target, "Entry already exists (no duplicate added).")

            # Calculate what the new total would be
            new_total = len(ENTRY_DELIMITER.join(entries + [tagged_content])) if entries else len(tagged_content)

            if new_total > limit:
                # Instead of hard-error, try auto-pruning
                prune_result = self._prune_if_needed(target)
                if prune_result:
                    # Retry after pruning - re-read entries since prune modified them
                    entries = self._entries_for(target)
                    new_total = len(ENTRY_DELIMITER.join(entries + [tagged_content])) if entries else len(tagged_content)

                if new_total > limit:
                    current = len(ENTRY_DELIMITER.join(entries)) if entries else 0
                    return {
                        "success": False,
                        "error": (
                            f"Memory at {current:,}/{limit:,} chars. "
                            f"Adding this entry ({len(tagged_content)} chars) would exceed the limit. "
                            f"Auto-prune freed {prune_result.get('pruned', 0) if prune_result else 0} "
                            f"entries but space is still insufficient. "
                            f"Replace or remove existing entries first."
                        ),
                        "current_entries": entries,
                        "usage": f"{current:,}/{limit:,}",
                    }

            entries.append(tagged_content)
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry added.")

    def replace(self, target: str, old_text: str, new_content: str) -> dict[str, Any]:
        """Find entry containing old_text substring, replace it with new_content."""
        old_text = old_text.strip()
        new_content = new_content.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}
        if not new_content:
            return {"success": False, "error": "new_content cannot be empty. Use 'remove' to delete entries."}

        # Scan replacement content for injection/exfiltration
        scan_error = _scan_memory_content(new_content)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)

            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1:
                # If all matches are identical (exact duplicates), operate on the first one
                unique_texts = {e for _, e in matches}
                if len(unique_texts) > 1:
                    previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }
                # All identical -- safe to replace just the first

            idx = matches[0][0]
            limit = self._char_limit(target)

            # Preserve the existing tag if present; tag the new content otherwise
            existing_tag = _tag_or_none(entries[idx])
            if existing_tag:
                tagged_new = existing_tag + new_content
            else:
                tagged_new = _format_tag() + new_content

            # Check that replacement doesn't blow the budget
            test_entries = entries.copy()
            test_entries[idx] = tagged_new
            new_total = len(ENTRY_DELIMITER.join(test_entries))

            if new_total > limit:
                return {
                    "success": False,
                    "error": (
                        f"Replacement would put memory at {new_total:,}/{limit:,} chars. "
                        f"Shorten the new content or remove other entries first."
                    ),
                }

            entries[idx] = tagged_new
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry replaced.")

    def remove(self, target: str, old_text: str) -> dict[str, Any]:
        """Remove the entry containing old_text substring."""
        old_text = old_text.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)

            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1:
                # If all matches are identical (exact duplicates), remove the first one
                unique_texts = {e for _, e in matches}
                if len(unique_texts) > 1:
                    previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }
                # All identical -- safe to remove just the first

            idx = matches[0][0]
            entries.pop(idx)
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry removed.")

    def prune(self, target: str, dry_run: bool = False) -> dict[str, Any]:
        """Explicitly trigger pruning. Returns summary of what would be / was removed.

        Args:
            target: 'memory' or 'user'
            dry_run: if True, only report without modifying entries.
        """
        with self._file_lock(self._path_for(target)):
            self._reload_target(target)
            entries = self._entries_for(target)
            limit = self._char_limit(target)
            current = len(ENTRY_DELIMITER.join(entries)) if entries else 0
            usage = current / limit if limit > 0 else 0

            now = datetime.now(timezone.utc)
            cutoff = now - __import__("datetime").timedelta(days=_PRUNE_PROTECT_DAYS)

            scored: list[tuple[float, str, int]] = []
            protected_count = 0
            for idx, entry in enumerate(entries):
                tag = _parse_tag(entry)
                score = _score_entry(tag, now)
                if tag and tag["created"] >= cutoff:
                    protected_count += 1
                scored.append((score, entry, idx))

            scored.sort(key=lambda x: x[0])

            result = {
                "target": target,
                "total_entries": len(entries),
                "usage": f"{current:,}/{limit:,} ({usage*100:.0f}%)" if limit else "N/A",
                "protected_entries": protected_count,
                "prune_suggested": usage >= _PRUNE_SOFT_THRESHOLD,
            }

            if usage >= _PRUNE_SOFT_THRESHOLD:
                # Show bottom 20% scores
                n_show = max(1, len(scored) // 5)
                candidates = []
                for score, entry, idx in scored[:n_show]:
                    tag = _parse_tag(entry)
                    preview = _strip_tag(entry)[:60].replace("\n", " ")
                    candidates.append({
                        "index": idx,
                        "score": round(score, 3),
                        "tag": {
                            "created": tag["created"].strftime("%Y-%m-%d") if tag else None,
                            "ref_count": tag["ref_count"] if tag else 0,
                            "source": tag["source"] if tag else "untagged",
                        } if tag else None,
                        "preview": preview,
                    })
                result["candidates"] = candidates

                if not dry_run:
                    pruned = self._prune_if_needed(target)
                    if pruned:
                        result["pruned"] = pruned
                    else:
                        result["pruned"] = {"pruned": 0, "reason": "all entries protected or high-value"}

            return result

    # ── System prompt integration ───────────────────────────────────────────

    def format_for_system_prompt(self, target: str) -> str | None:
        """
        Return the frozen snapshot for system prompt injection.

        This returns the state captured at load_from_disk() time, NOT the live
        state. Mid-session writes do not affect this. This keeps the system
        prompt stable across all turns, preserving the prefix cache.

        Returns None if the snapshot is empty (no entries at load time).
        """
        block = self._system_prompt_snapshot.get(target, "")
        return block if block else None

    # -- Internal helpers --

    def _success_response(self, target: str, message: str = None) -> dict[str, Any]:
        entries = self._entries_for(target)
        current = self._char_count(target)
        limit = self._char_limit(target)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        resp = {
            "success": True,
            "target": target,
            "entries": entries,
            "usage": f"{pct}% — {current:,}/{limit:,} chars",
            "entry_count": len(entries),
        }
        if message:
            resp["message"] = message
        return resp

    def _render_block(self, target: str, entries: list[str]) -> str:
        """Render a system prompt block with header and usage indicator."""
        if not entries:
            return ""

        limit = self._char_limit(target)
        content = ENTRY_DELIMITER.join(entries)
        current = len(content)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        if target == "user":
            header = f"USER PROFILE (who the user is) [{pct}% — {current:,}/{limit:,} chars]"
        else:
            header = f"MEMORY (your personal notes) [{pct}% — {current:,}/{limit:,} chars]"

        separator = "═" * 46
        return f"{separator}\n{header}\n{separator}\n{content}"

    @staticmethod
    def _read_file(path: Path) -> list[str]:
        """Read a memory file and split into entries.

        No file locking needed: _write_file uses atomic rename, so readers
        always see either the previous complete file or the new complete file.
        """
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, IOError):
            return []

        if not raw.strip():
            return []

        # Use ENTRY_DELIMITER for consistency with _write_file. Splitting by "§"
        # alone would incorrectly split entries that contain "§" in their content.
        entries = [e.strip() for e in raw.split(ENTRY_DELIMITER)]
        return [e for e in entries if e]

    @staticmethod
    def _write_file(path: Path, entries: list[str]):
        """Write entries to a memory file using atomic temp-file + rename.

        Previous implementation used open("w") + flock, but "w" truncates the
        file *before* the lock is acquired, creating a race window where
        concurrent readers see an empty file. Atomic rename avoids this:
        readers always see either the old complete file or the new one.
        """
        content = ENTRY_DELIMITER.join(entries) if entries else ""
        try:
            # Write to temp file in same directory (same filesystem for atomic rename)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent), suffix=".tmp", prefix=".mem_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                atomic_replace(tmp_path, path)
            except BaseException:
                # Clean up temp file on any failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except (OSError, IOError) as e:
            raise RuntimeError(f"Failed to write memory file {path}: {e}")


def memory_tool(
    action: str,
    target: str = "memory",
    content: str = None,
    old_text: str = None,
    dry_run: bool = False,
    store: Any = None,
) -> str:
    """
    Single entry point for the memory tool. Dispatches to MemoryStore methods.

    Returns JSON string with results.
    """
    from tools.registry import tool_error

    if store is None:
        return tool_error("Memory is not available. It may be disabled in config or this environment.", success=False)

    if target not in {"memory", "user"}:
        return tool_error(f"Invalid target '{target}'. Use 'memory' or 'user'.", success=False)

    if action == "add":
        if not content:
            return tool_error("Content is required for 'add' action.", success=False)
        result = store.add(target, content)

    elif action == "replace":
        if not old_text:
            return tool_error("old_text is required for 'replace' action.", success=False)
        if not content:
            return tool_error("content is required for 'replace' action.", success=False)
        result = store.replace(target, old_text, content)

    elif action == "remove":
        if not old_text:
            return tool_error("old_text is required for 'remove' action.", success=False)
        result = store.remove(target, old_text)

    elif action == "prune":
        result = store.prune(target, dry_run=dry_run)

    else:
        return tool_error(f"Invalid action '{action}'. Use 'add', 'replace', 'remove', or 'prune'.", success=False)

    return json.dumps(result, ensure_ascii=False)


# ---- Required env vars / dependencies ----

def check_memory_requirements() -> bool:
    """Memory is always available — no external dependencies."""
    return True


MEMORY_SCHEMA = {
    "name": "memory",
    "description": (
        "Manage persistent memory for the agent. "
        "Entries are stored durably across sessions and injected into the system prompt. "
        "Use 'memory' target for your own notes (preferences, environment facts, conventions). "
        "Use 'user' target for information about the user (their preferences, habits, corrections). "
        "Use 'prune' action to trigger automatic cleanup of low-value entries."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove", "prune"],
                "description": (
                    "The action to perform. 'add' appends a new entry (auto-tagged with metadata). "
                    "'replace' finds an entry by substring and replaces it. "
                    "'remove' finds an entry by substring and deletes it. "
                    "'prune' scores all entries and removes low-value ones when usage exceeds 80%."
                ),
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user"],
                "description": "Which memory store: 'memory' for personal notes, 'user' for user profile.",
            },
            "content": {
                "type": "string",
                "description": "The entry content. Required for 'add' and 'replace'. "
                "For 'add', a metadata tag [c:date][r:count][s:source] is prepended automatically.",
            },
            "old_text": {
                "type": "string",
                "description": "Short unique substring identifying the entry to replace or remove.",
            },
            "dry_run": {
                "type": "boolean",
                "description": "For 'prune' action only: if true, only report candidates without removing anything.",
            },
        },
        "required": ["action", "target"],
    },
}

# --- Registry ---
from tools.registry import registry

registry.register(
    name="memory",
    toolset="memory",
    schema=MEMORY_SCHEMA,
    handler=lambda args, **kw: memory_tool(
        action=args.get("action", ""),
        target=args.get("target", "memory"),
        content=args.get("content"),
        old_text=args.get("old_text"),
        dry_run=args.get("dry_run", False),
        store=kw.get("store")),
    check_fn=check_memory_requirements,
    emoji="🧠",
)
