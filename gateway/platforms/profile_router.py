"""Multi-profile gateway router.

Resolves ``(adapter_id, container_id) -> profile_name | None`` and
``(adapter_id, container_id, conversation_id, user_id) -> session_key``.

Two configuration surfaces, in priority order:

1. ``<ADAPTER>_PROFILE_<CONTAINER>=<profile>`` env var (per-container)
2. ``<ADAPTER>_PROFILE_CATEGORIES`` env var with comma-separated
   ``<container_id>:<profile_name>`` pairs (Discord today; mirrors any adapter
   that exposes a category-style container concept)

When neither is set, ``route_container_to_profile`` returns ``None`` and the
adapter falls back to the gateway's default profile (single-profile install
behavior is preserved).
"""

from __future__ import annotations

import os
import re
from typing import Optional


_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_RESERVED_NAMES = frozenset({"hermes", "test", "tmp", "root", "sudo"})


def _safe_container(container_id: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in container_id)


def _validate_profile(name: str) -> str:
    canon = name.strip().lower()
    if canon == "default":
        return canon
    if not _PROFILE_ID_RE.match(canon):
        raise ValueError(f"invalid profile name from router config: {name!r}")
    if canon in _RESERVED_NAMES:
        raise ValueError(f"reserved profile name from router config: {name!r}")
    return canon


def _per_container_env(adapter_id: str, container_id: str) -> Optional[str]:
    env_name = f"{adapter_id.upper()}_PROFILE_{_safe_container(container_id).upper()}"
    raw = os.environ.get(env_name)
    return _validate_profile(raw) if raw else None


def _categories_env(adapter_id: str, container_id: str) -> Optional[str]:
    env_name = f"{adapter_id.upper()}_PROFILE_CATEGORIES"
    raw = os.environ.get(env_name) or ""
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        cid, _, profile = entry.partition(":")
        if cid.strip() == container_id:
            return _validate_profile(profile)
    return None


def route_container_to_profile(adapter_id: str,
                                 container_id: Optional[str]) -> Optional[str]:
    """Return the profile name bound to ``container_id`` or ``None``."""
    if not container_id:
        return None
    env_match = _per_container_env(adapter_id, container_id)
    if env_match:
        return env_match
    cat_match = _categories_env(adapter_id, container_id)
    if cat_match:
        return cat_match
    return None


def route_session_key(adapter_id: str, container_id: Optional[str],
                        conversation_id: Optional[str],
                        user_id: Optional[str]) -> str:
    """Return the per-profile session key, ``agent:<profile>:<adapter>:...``."""
    profile = route_container_to_profile(adapter_id, container_id) or "main"
    parts = ["agent", profile, adapter_id]
    if container_id:
        parts.append(container_id)
    if conversation_id:
        parts.append(conversation_id)
    if user_id:
        parts.append(str(user_id))
    return ":".join(parts)
