"""Path parity test helpers (FR #28984 Phase 3).

A lightweight test utility that verifies: if execution path A (e.g. startup)
reads or configures field X, then path B (e.g. /model switch, gateway restart,
fallback activation) must also consume X.

This turns "path divergence" bugs into CI failures before they reach users.

Usage::

    from hermes_cli.path_parity import assert_field_parity

    assert_field_parity(
        field="fallback_model",
        paths={
            "gateway_init": lambda: _extract_gateway_init_fields(),
            "tui_make_agent": lambda: _extract_tui_agent_fields(),
            "/model_switch": lambda: _extract_model_switch_fields(),
        },
    )

If any path does not include ``field`` in its returned dict, the assertion
fails with a detailed diff showing which paths consume the field and which
don't.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple


class PathParityError(AssertionError):
    """Raised when one or more paths diverge on field consumption."""

    pass


def assert_field_parity(
    field: str,
    paths: Dict[str, Callable[[], Dict[str, Any]]],
    *,
    ignore_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Assert that all execution paths consume the same config field.

    Parameters
    ----------
    field:
        The config/runtime field to check (e.g. ``"fallback_model"``,
        ``"credential_pool"``).
    paths:
        A dict mapping human-readable path names to callables that return
        a dict of fields consumed by that path.  The callable should extract
        the relevant config fields from the code path it represents.
    ignore_paths:
        Optional list of path names to skip (e.g. legacy paths that are
        intentionally not updated).

    Returns
    -------
    dict
        A summary of field values across all paths.

    Raises
    ------
    PathParityError
        If any non-ignored path does not include ``field`` in its result.
    """
    ignore = set(ignore_paths or [])
    results: Dict[str, Any] = {}
    consuming: List[str] = []
    missing: List[str] = []

    for path_name, extractor in paths.items():
        if path_name in ignore:
            results[path_name] = "<skipped>"
            continue
        try:
            fields = extractor()
            results[path_name] = fields.get(field, "<MISSING>")
            if field in fields:
                consuming.append(path_name)
            else:
                missing.append(path_name)
        except Exception as exc:
            results[path_name] = f"<error: {exc}>"
            missing.append(path_name)

    if missing:
        msg = (
            f"Path parity failure for field '{field}':\n"
            f"  ✅ Consuming: {', '.join(consuming) or '(none)'}\n"
            f"  ❌ Missing:   {', '.join(missing)}\n\n"
            f"All paths should consume '{field}' to prevent silent failures."
        )
        raise PathParityError(msg)

    return results


def assert_fields_parity(
    fields: List[str],
    paths: Dict[str, Callable[[], Dict[str, Any]]],
    *,
    ignore_paths: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Assert parity for multiple fields at once.

    Returns a dict mapping each field to its parity result.
    Raises PathParityError on the first failing field.
    """
    results = {}
    for field in fields:
        results[field] = assert_field_parity(
            field, paths, ignore_paths=ignore_paths
        )
    return results


def compare_paths(
    paths: Dict[str, Callable[[], Dict[str, Any]]],
    *,
    ignore_paths: Optional[List[str]] = None,
) -> Dict[str, Dict[str, str]]:
    """Compare all fields across all paths and return a diff.

    Unlike ``assert_field_parity``, this does not raise — it returns a report
    of which fields are present in which paths.

    Returns
    -------
    dict
        ``{field: {path_name: "present" | "missing" | "error"}}``
    """
    ignore = set(ignore_paths or [])
    all_fields: set = set()
    raw: Dict[str, Dict[str, Any]] = {}

    for path_name, extractor in paths.items():
        if path_name in ignore:
            continue
        try:
            fields = extractor()
            raw[path_name] = fields
            all_fields.update(fields.keys())
        except Exception:
            raw[path_name] = {}

    report: Dict[str, Dict[str, str]] = {}
    for field in sorted(all_fields):
        report[field] = {}
        for path_name, fields in raw.items():
            report[field][path_name] = "present" if field in fields else "missing"

    return report
