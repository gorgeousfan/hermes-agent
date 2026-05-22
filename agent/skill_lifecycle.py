"""Metadata-only skill lifecycle audit for Hermes.

Tier 5 is deliberately read-only: it inventories skill health and promotion
state without deleting, rewriting, or exposing raw skill content/paths/names.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from hermes_constants import get_hermes_home

SCHEMA_VERSION = 1
CONTENT_POLICY = "metadata_only"
ALLOWED_SUPPORT_DIRS = {"references", "templates", "scripts", "assets"}
REQUIRED_FRONTMATTER_FIELDS = {"name", "description"}
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_frontmatter(content: str) -> tuple[Dict[str, Any], str]:
    try:
        from agent.skill_utils import parse_frontmatter

        return parse_frontmatter(content)
    except Exception:
        pass

    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    raw = content[3:end]
    body = content[end + 4 :]
    fields: Dict[str, Any] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields, body


def _issue(
    code: str,
    *,
    severity: str = "warning",
    count: int = 1,
    skill_name: Optional[str] = None,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    item: Dict[str, Any] = {"code": code, "severity": severity, "count": count}
    if skill_name:
        item["skill_name_sha256"] = _sha256_text(skill_name)
    if path is not None:
        item["path_sha256"] = _sha256_text(str(path))
    return item


def _iter_skill_files(root: Path) -> Iterable[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return sorted(path for path in root.rglob("SKILL.md") if path.is_file())


def _skill_roots(hermes_home: Path, extra_roots: Optional[Iterable[Path]] = None) -> List[Path]:
    roots = [hermes_home / "skills"]
    if extra_roots:
        roots.extend(Path(root) for root in extra_roots)
    elif hermes_home == get_hermes_home():
        try:
            from agent.skill_utils import get_external_skills_dirs

            roots.extend(get_external_skills_dirs())
        except Exception:
            pass
    unique: List[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _is_local_reference(target: str) -> bool:
    stripped = target.strip()
    if not stripped or stripped.startswith("#"):
        return False
    lowered = stripped.lower()
    return not (
        "://" in lowered
        or lowered.startswith("mailto:")
        or lowered.startswith("tel:")
        or lowered.startswith("data:")
    )


def _extract_local_references(body: str) -> List[str]:
    refs: List[str] = []
    for match in _MARKDOWN_LINK_RE.finditer(body):
        target = match.group(1).split("#", 1)[0].strip()
        if _is_local_reference(target):
            refs.append(target)
    return refs


def _support_file_violations(skill_dir: Path) -> int:
    count = 0
    for path in skill_dir.rglob("*"):
        if not path.is_file() or path.name == "SKILL.md":
            continue
        try:
            rel = path.relative_to(skill_dir)
        except ValueError:
            continue
        if not rel.parts or rel.parts[0] not in ALLOWED_SUPPORT_DIRS:
            count += 1
    return count


def _file_age_days(path: Path, now: datetime) -> Optional[float]:
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None
    return max(0.0, (now - modified).total_seconds() / 86400.0)


def _read_registry(hermes_home: Path) -> Dict[str, Any]:
    path = hermes_home / "harness" / "skill-registry.json"
    if not path.exists() or not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "skills": {}}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {"schema_version": SCHEMA_VERSION, "skills": {}}
    except Exception:
        return {"schema_version": SCHEMA_VERSION, "skills": {}}


def audit_skill_lifecycle(
    *,
    hermes_home: Optional[Path] = None,
    skill_roots: Optional[Iterable[Path]] = None,
    now: Optional[datetime] = None,
    stale_days: int = 180,
) -> Dict[str, Any]:
    """Return a read-only, content-free skill lifecycle audit.

    The audit reports structure, counts, hashes, and issue codes only. It does
    not include raw skill names, paths, bodies, reference labels, or support-file
    contents, and it never mutates the skill library.
    """
    home = Path(hermes_home) if hermes_home is not None else get_hermes_home()
    current_time = now or datetime.now(timezone.utc)
    roots = _skill_roots(home, skill_roots)
    skill_files: List[Path] = []
    for root in roots:
        skill_files.extend(_iter_skill_files(root))

    issues: List[Dict[str, Any]] = []
    name_counts: Dict[str, int] = {}
    invalid_frontmatter = 0
    missing_references = 0
    support_file_violations = 0
    stale_skills = 0
    body_line_count = 0
    total_bytes = 0
    named_count = 0
    versioned_count = 0

    for skill_file in skill_files:
        content = _read_text(skill_file)
        frontmatter, body = _parse_frontmatter(content)
        name = str(frontmatter.get("name") or "").strip()
        total_bytes += skill_file.stat().st_size
        body_line_count += len(body.splitlines())
        if name:
            named_count += 1
            name_counts[name] = name_counts.get(name, 0) + 1
        if frontmatter.get("version"):
            versioned_count += 1

        missing_fields = [field for field in REQUIRED_FRONTMATTER_FIELDS if not frontmatter.get(field)]
        if missing_fields:
            invalid_frontmatter += 1
            issues.append(_issue(
                "skill_frontmatter_incomplete",
                skill_name=name or None,
                path=skill_file,
            ))

        for target in _extract_local_references(body):
            resolved = (skill_file.parent / target).resolve()
            try:
                resolved.relative_to(skill_file.parent.resolve())
            except ValueError:
                missing_references += 1
                issues.append(_issue(
                    "skill_reference_escapes_directory",
                    severity="error",
                    skill_name=name or None,
                    path=skill_file,
                ))
                continue
            if not resolved.exists():
                missing_references += 1
                issues.append(_issue(
                    "skill_missing_reference",
                    skill_name=name or None,
                    path=skill_file,
                ))

        violation_count = _support_file_violations(skill_file.parent)
        if violation_count:
            support_file_violations += violation_count
            issues.append(_issue(
                "skill_support_file_policy_violation",
                count=violation_count,
                skill_name=name or None,
                path=skill_file,
            ))

        age_days = _file_age_days(skill_file, current_time)
        if age_days is not None and age_days > stale_days:
            stale_skills += 1
            issues.append(_issue(
                "skill_stale",
                skill_name=name or None,
                path=skill_file,
            ))

    duplicate_names = {name: count for name, count in name_counts.items() if count > 1}
    for name, count in duplicate_names.items():
        issues.append(_issue("skill_duplicate_names", count=count, skill_name=name))

    registry = _read_registry(home)
    registered = registry.get("skills", {}) if isinstance(registry.get("skills"), dict) else {}
    promoted_without_gate = 0
    needs_verification = 0
    promoted = 0
    removed = 0
    for name, record in registered.items():
        if not isinstance(record, dict):
            continue
        status = str(record.get("status") or "")
        promotion_status = str(record.get("promotion_status") or "")
        gate_status = str(record.get("promotion_gate_status") or "")
        if promotion_status == "needs_verification":
            needs_verification += 1
        if promotion_status == "verified" or status == "promoted":
            promoted += 1
        if status == "removed":
            removed += 1
        if (status == "promoted" or promotion_status == "verified") and gate_status != "passed":
            promoted_without_gate += 1
            issues.append(_issue("skill_promoted_without_gate", severity="error", skill_name=str(name)))

    return {
        "schema_version": SCHEMA_VERSION,
        "content_policy": CONTENT_POLICY,
        "mode": "audit_only_no_delete",
        "skill_count": len(skill_files),
        "root_count": len(roots),
        "root_hashes": [_sha256_text(str(root)) for root in roots],
        "named_skill_count": named_count,
        "versioned_skill_count": versioned_count,
        "duplicate_name_count": len(duplicate_names),
        "invalid_frontmatter_count": invalid_frontmatter,
        "missing_reference_count": missing_references,
        "support_file_violation_count": support_file_violations,
        "stale_skill_count": stale_skills,
        "stale_days": stale_days,
        "bytes": total_bytes,
        "body_line_count": body_line_count,
        "promotion": {
            "registered_count": len(registered),
            "needs_verification_count": needs_verification,
            "promoted_count": promoted,
            "removed_count": removed,
            "promoted_without_gate_count": promoted_without_gate,
        },
        "issues": issues,
        "issue_count": len(issues),
    }
