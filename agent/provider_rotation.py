"""Persistent provider rotation cooldown state.

This module intentionally keeps the first version small: providers that expose
quota APIs can add proactive probes later, while every provider benefits from
reactive cooldown after capacity errors.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from hermes_constants import get_hermes_home

STATE_VERSION = 1
STATE_FILE = "provider_rotation_state.json"
DEFAULT_COOLDOWN_SECONDS = 6 * 60 * 60


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def provider_key(provider: str | None, model: str | None = None) -> str:
    """Return stable key for provider/model rotation state."""
    provider_part = _norm(provider)
    model_part = (model or "").strip()
    return f"{provider_part}:{model_part}" if model_part else provider_part


def state_path() -> Path:
    return get_hermes_home() / STATE_FILE


@dataclass
class ProviderRotationState:
    """Durable cooldown records for provider rotation."""

    unavailable: dict[str, dict[str, Any]] = field(default_factory=dict)
    version: int = STATE_VERSION

    @classmethod
    def load(cls) -> "ProviderRotationState":
        path = state_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return cls()
        unavailable = raw.get("unavailable", {})
        if not isinstance(unavailable, dict):
            unavailable = {}
        return cls(unavailable=unavailable, version=int(raw.get("version", STATE_VERSION) or STATE_VERSION))

    def save(self) -> None:
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        payload = {"version": self.version, "unavailable": self.unavailable}
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)

    def mark_unavailable(
        self,
        *,
        provider: str,
        model: str,
        reason: str,
        cooldown_seconds: int | float = DEFAULT_COOLDOWN_SECONDS,
        now: float | None = None,
        message: str | None = None,
    ) -> None:
        timestamp = time.time() if now is None else float(now)
        cooldown = max(0.0, float(cooldown_seconds or 0))
        key = provider_key(provider, model)
        self.unavailable[key] = {
            "provider": (provider or "").strip(),
            "model": (model or "").strip(),
            "reason": (reason or "unknown").strip() or "unknown",
            "message": (message or "").strip(),
            "unavailable_at": timestamp,
            "retry_after": timestamp + cooldown,
        }
        self.save()

    def is_unavailable(self, provider: str, model: str, *, now: float | None = None) -> bool:
        timestamp = time.time() if now is None else float(now)
        record = self.unavailable.get(provider_key(provider, model))
        if not isinstance(record, dict):
            return False
        retry_after = float(record.get("retry_after") or 0)
        if retry_after <= timestamp:
            self.unavailable.pop(provider_key(provider, model), None)
            self.save()
            return False
        return True

    def reset(self, provider: str | None = None, model: str | None = None) -> int:
        """Remove matching cooldown records. Returns count removed."""
        if not provider:
            count = len(self.unavailable)
            self.unavailable.clear()
            self.save()
            return count
        provider_norm = _norm(provider)
        model_text = (model or "").strip()
        removed = 0
        for key, record in list(self.unavailable.items()):
            rec_provider = _norm(record.get("provider") if isinstance(record, dict) else key.split(":", 1)[0])
            rec_model = (record.get("model") if isinstance(record, dict) else "") or ""
            if rec_provider != provider_norm:
                continue
            if model_text and rec_model != model_text:
                continue
            self.unavailable.pop(key, None)
            removed += 1
        if removed:
            self.save()
        return removed


def filter_available_entries(entries: Iterable[dict[str, Any]], *, now: float | None = None) -> list[dict[str, Any]]:
    """Return entries not currently cooled down, preserving original order."""
    state = ProviderRotationState.load()
    available: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        provider = entry.get("provider") or ""
        model = entry.get("model") or ""
        if not provider or not model:
            continue
        if state.is_unavailable(provider, model, now=now):
            continue
        available.append(entry)
    return available


def is_rotation_enabled(config: dict[str, Any] | None) -> bool:
    section = (config or {}).get("provider_rotation", {})
    return isinstance(section, dict) and bool(section.get("enabled", False))


def cooldown_for_reason(config: dict[str, Any] | None, reason: str | None = None) -> int:
    section = (config or {}).get("provider_rotation", {}) if isinstance(config, dict) else {}
    if not isinstance(section, dict):
        return DEFAULT_COOLDOWN_SECONDS
    by_reason = section.get("cooldown_seconds_by_reason")
    reason_key = (reason or "").strip().lower()
    if isinstance(by_reason, dict) and reason_key in by_reason:
        try:
            return int(by_reason[reason_key])
        except (TypeError, ValueError):
            pass
    try:
        return int(section.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_COOLDOWN_SECONDS
