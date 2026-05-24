"""Proof trail command helpers.

Creates compact, durable Markdown proof artifacts for important agent actions.
The command is intentionally filesystem-local and dependency-light so users can
record rationale, evidence, validation, and final state without needing an
external service.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from hermes_constants import get_hermes_home


def default_proof_dir() -> Path:
    """Return the default local proof artifact directory."""
    return get_hermes_home() / "proofs"


def slugify_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").strip().lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug or "proof"


def _list_lines(items: Iterable[str], *, code: bool = False) -> str:
    values = [str(item).strip() for item in items if str(item).strip()]
    if not values:
        return "- None recorded\n"
    if code:
        return "".join(f"- `{item}`\n" for item in values)
    return "".join(f"- {item}\n" for item in values)


def _command_blocks(commands: Iterable[str]) -> str:
    values = [str(item).strip() for item in commands if str(item).strip()]
    if not values:
        return "- None recorded\n"
    return "\n".join(f"```bash\n{cmd}\n```" for cmd in values) + "\n"


def build_proof_markdown(
    *,
    title: str,
    status: str,
    rationale: str,
    inputs: list[str],
    files: list[str],
    commands: list[str],
    validations: list[str],
    related: list[str],
    final_state: str,
    references: list[str],
    timestamp: str,
) -> str:
    """Build the Markdown body for a proof artifact."""
    safe_title = title.strip() or "Task Proof"
    safe_status = status.strip() or "recorded"
    return f"""---
type: proof
status: "{safe_status}"
created: "{timestamp[:10]}"
updated: "{timestamp[:10]}"
---

# {safe_title}

## Summary

- **Status:** {safe_status}
- **Timestamp:** {timestamp}

## Rationale

{rationale.strip() or "No rationale recorded."}

## Inputs

{_list_lines(inputs)}
## Files Changed

{_list_lines(files, code=True)}
## Commands / Evidence

{_command_blocks(commands)}
## Validation

{_list_lines(validations)}
## Related Artifacts

{_list_lines(related)}
## References

{_list_lines(references)}
## Final State

{final_state.strip() or "No final state recorded."}

## History

- **{timestamp[:10]}** — Proof artifact created.
"""


def _load_index(index_path: Path) -> dict[str, Any]:
    if not index_path.exists():
        return {"proofs": []}
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("proofs"), list):
            return payload
    except Exception:
        pass
    return {"proofs": []}


def create_proof_record(
    *,
    title: str,
    status: str,
    rationale: str,
    inputs: list[str],
    files: list[str],
    commands: list[str],
    validations: list[str],
    related: list[str],
    final_state: str,
    references: list[str],
    output_dir: Path | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Create a proof Markdown file and update the local proof index."""
    timestamp = timestamp or datetime.now(timezone.utc).isoformat()
    output_dir = output_dir or default_proof_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    date_prefix = timestamp[:10]
    slug = slugify_title(title)
    proof_path = output_dir / f"{date_prefix}-{slug}.md"
    markdown = build_proof_markdown(
        title=title,
        status=status,
        rationale=rationale,
        inputs=inputs,
        files=files,
        commands=commands,
        validations=validations,
        related=related,
        final_state=final_state,
        references=references,
        timestamp=timestamp,
    )
    proof_path.write_text(markdown, encoding="utf-8")

    index_path = output_dir / "proof-index.json"
    index = _load_index(index_path)
    record = {
        "title": title,
        "status": status,
        "timestamp": timestamp,
        "path": str(proof_path),
        "slug": slug,
    }
    index["proofs"] = [p for p in index.get("proofs", []) if p.get("path") != str(proof_path)]
    index["proofs"].insert(0, record)
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"success": True, "path": str(proof_path), "index_path": str(index_path), "record": record}


def proof_trail_command(args, *, timestamp: str | None = None) -> None:
    result = create_proof_record(
        title=args.title,
        status=args.status,
        rationale=args.rationale,
        inputs=list(args.inputs or []),
        files=list(args.files or []),
        commands=list(args.commands or []),
        validations=list(args.validations or []),
        related=list(args.related or []),
        final_state=args.final_state,
        references=list(args.references or []),
        output_dir=Path(args.output_dir) if args.output_dir else None,
        timestamp=timestamp,
    )
    if bool(getattr(args, "json", False)):
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Proof written: {result['path']}")
