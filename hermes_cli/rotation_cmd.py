"""hermes rotation — inspect/reset provider rotation cooldown state."""

from __future__ import annotations

import time
from typing import Any


def _fmt_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _records(now: float | None = None) -> list[dict[str, Any]]:
    from agent.provider_rotation import ProviderRotationState

    timestamp = time.time() if now is None else float(now)
    state = ProviderRotationState.load()
    rows: list[dict[str, Any]] = []
    for key, record in sorted(state.unavailable.items()):
        if not isinstance(record, dict):
            continue
        retry_after = float(record.get("retry_after") or 0)
        if retry_after <= timestamp:
            continue
        rows.append({"key": key, **record, "remaining": retry_after - timestamp})
    return rows


def cmd_rotation_list(args) -> None:
    """Print provider/model cooldown records."""
    now = getattr(args, "now", None)
    rows = _records(now=now)
    print()
    if not rows:
        print("  No provider rotation cooldowns active.")
        print()
        return
    print(f"  Provider rotation cooldowns ({len(rows)} {'entry' if len(rows) == 1 else 'entries'}):")
    for idx, row in enumerate(rows, 1):
        provider = row.get("provider") or row.get("key") or "?"
        model = row.get("model") or "?"
        reason = row.get("reason") or "unknown"
        remaining = _fmt_seconds(float(row.get("remaining") or 0))
        print(f"    {idx}. {model} (via {provider}) — cooling down {remaining} [{reason}]")
    print()
    print("  Reset one with: hermes rotation reset PROVIDER [--model MODEL]")
    print("  Clear all with:  hermes rotation clear")
    print()


def cmd_rotation_reset(args) -> None:
    """Reset cooldowns for one provider, optionally one model."""
    from agent.provider_rotation import ProviderRotationState

    provider = getattr(args, "provider", None)
    model = getattr(args, "model", None)
    if not provider:
        raise SystemExit("provider is required")
    count = ProviderRotationState.load().reset(provider, model)
    suffix = f" for {provider}"
    if model:
        suffix += f"/{model}"
    print()
    print(f"  Reset {count} provider rotation cooldown {'entry' if count == 1 else 'entries'}{suffix}.")
    print()


def cmd_rotation_clear(args) -> None:  # noqa: ARG001
    """Clear all provider rotation cooldown state."""
    from agent.provider_rotation import ProviderRotationState

    count = ProviderRotationState.load().reset()
    print()
    print(f"  Cleared {count} provider rotation cooldown {'entry' if count == 1 else 'entries'}.")
    print()


def cmd_rotation(args) -> None:
    """Top-level dispatcher for ``hermes rotation [subcommand]``."""
    sub = getattr(args, "rotation_command", None)
    if sub in {None, "", "list", "ls"}:
        cmd_rotation_list(args)
    elif sub == "reset":
        cmd_rotation_reset(args)
    elif sub == "clear":
        cmd_rotation_clear(args)
    else:
        print(f"Unknown rotation subcommand: {sub}")
        print("Use one of: list, reset, clear")
        raise SystemExit(2)
