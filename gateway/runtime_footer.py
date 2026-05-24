"""Gateway runtime-metadata footer.

Renders a compact footer showing runtime state (model, context %, cwd) and
optional provider/account usage metadata, then appends it to the FINAL message
of an agent turn when enabled. Off by default to keep replies minimal.

Config (``~/.hermes/config.yaml``)::

    display:
      runtime_footer:
        enabled: true
        fields: [model, context_pct, provider_window_pct, provider_reset]
        labels:
          context_pct: ctx
          provider_window_pct: 5H
          provider_reset: reset
        timezone: Europe/Berlin

Per-platform overrides live under ``display.platforms.<platform>.runtime_footer``.
Users can toggle the global setting with ``/footer on|off`` from both the CLI
and any gateway platform.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

_DEFAULT_FIELDS: tuple[str, ...] = ("model", "context_pct", "cwd")
_DEFAULT_LABELS: dict[str, str] = {
    "context_pct": "ctx",
    "provider_window_pct": "5H",
    "provider_reset": "reset",
}
_SEP = " · "
_USAGE_CACHE_TTL_SECONDS = 60.0
_USAGE_CACHE: dict[tuple[str, str], tuple[float, Any]] = {}


def _home_relative_cwd(cwd: str) -> str:
    """Return *cwd* with the user's home collapsed to ``~``.

    Prefer an explicitly-provided ``HOME`` env var in tests/MSYS shells, then
    fall back to platform expansion.  Normalize separators to ``/`` so gateway
    footers stay compact and readable on Windows/Telegram.
    """
    if not cwd:
        return ""
    try:
        home = os.environ.get("HOME") or os.path.expanduser("~")
        p = os.path.abspath(cwd)
        home_abs = os.path.abspath(home) if home else ""
        if home_abs:
            rel = os.path.relpath(p, home_abs)
            if rel == ".":
                return "~"
            if rel != os.pardir and not rel.startswith(os.pardir + os.sep):
                return ("~/" + rel).replace("\\", "/")
        return p.replace("\\", "/")
    except Exception:
        return str(cwd).replace("\\", "/")


def _model_short(model: Optional[str]) -> str:
    """Drop ``vendor/`` prefix for readability (``openai/gpt-5.4`` → ``gpt-5.4``)."""
    if not model:
        return ""
    return model.rsplit("/", 1)[-1]


def _merge_labels(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, str]:
    labels = {str(k): str(v) for k, v in (base or {}).items() if v is not None}
    if isinstance(override, dict):
        labels.update({str(k): str(v) for k, v in override.items() if v is not None})
    return labels


def resolve_footer_config(
    user_config: dict[str, Any] | None,
    platform_key: str | None = None,
) -> dict[str, Any]:
    """Resolve effective runtime-footer config for *platform_key*.

    Merge order (later wins):
        1. Built-in defaults (enabled=False)
        2. ``display.runtime_footer``
        3. ``display.platforms.<platform_key>.runtime_footer``
    """
    resolved: dict[str, Any] = {
        "enabled": False,
        "fields": list(_DEFAULT_FIELDS),
        "labels": dict(_DEFAULT_LABELS),
        "timezone": None,
    }
    cfg = (user_config or {}).get("display") or {}

    global_cfg = cfg.get("runtime_footer")
    if isinstance(global_cfg, dict):
        if "enabled" in global_cfg:
            resolved["enabled"] = bool(global_cfg.get("enabled"))
        if isinstance(global_cfg.get("fields"), list) and global_cfg["fields"]:
            resolved["fields"] = [str(f) for f in global_cfg["fields"]]
        resolved["labels"] = _merge_labels(resolved.get("labels", {}), global_cfg.get("labels"))
        if "timezone" in global_cfg:
            resolved["timezone"] = global_cfg.get("timezone")

    if platform_key:
        platforms = cfg.get("platforms") or {}
        plat_cfg = platforms.get(platform_key)
        if isinstance(plat_cfg, dict):
            plat_footer = plat_cfg.get("runtime_footer")
            if isinstance(plat_footer, dict):
                if "enabled" in plat_footer:
                    resolved["enabled"] = bool(plat_footer.get("enabled"))
                if isinstance(plat_footer.get("fields"), list) and plat_footer["fields"]:
                    resolved["fields"] = [str(f) for f in plat_footer["fields"]]
                resolved["labels"] = _merge_labels(resolved.get("labels", {}), plat_footer.get("labels"))
                if "timezone" in plat_footer:
                    resolved["timezone"] = plat_footer.get("timezone")

    return resolved


def _label(labels: dict[str, str] | None, field: str, default: str) -> str:
    value = (labels or {}).get(field)
    return str(value) if value not in {None, ""} else default


def _format_context_pct(context_tokens: int, context_length: Optional[int], labels: dict[str, str] | None) -> str:
    if context_length and context_length > 0 and context_tokens >= 0:
        pct = max(0, min(100, round((context_tokens / context_length) * 100)))
        prefix = _label(labels, "context_pct", "ctx")
        return f"{prefix} {pct}%" if prefix else f"{pct}%"
    return ""


def _coerce_reset_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _format_reset_time(reset_at: Any, timezone_name: str | None) -> str:
    dt = _coerce_reset_datetime(reset_at)
    if not dt:
        return ""
    try:
        tz = ZoneInfo(str(timezone_name)) if timezone_name else None
    except Exception:
        tz = None
    local_dt = dt.astimezone(tz) if tz else dt.astimezone()
    return local_dt.strftime("%H:%M")


def _usage_windows(provider_usage: Any) -> list[Any]:
    if provider_usage is None:
        return []
    if isinstance(provider_usage, dict):
        return list(provider_usage.get("windows") or [])
    return list(getattr(provider_usage, "windows", ()) or ())


def _window_value(window: Any, name: str) -> Any:
    if isinstance(window, dict):
        return window.get(name)
    return getattr(window, name, None)


def _select_provider_window(provider_usage: Any) -> Any:
    """Pick the real usage window to show in compact footers.

    The first window emitted by ``agent.account_usage`` is the current/primary
    window for Codex/OpenAI and Anthropic OAuth. If labels are available, prefer
    obvious short-session/five-hour labels over weekly/monthly quota windows.
    """
    windows = [w for w in _usage_windows(provider_usage) if _window_value(w, "used_percent") is not None]
    if not windows:
        return None
    preferred_terms = ("session", "five", "5", "current")
    for window in windows:
        label = str(_window_value(window, "label") or "").lower()
        if any(term in label for term in preferred_terms):
            return window
    return windows[0]


def _fetch_provider_usage_cached(provider: Optional[str], base_url: Optional[str], api_key: Optional[str]) -> Any:
    normalized_provider = str(provider or "").strip().lower()
    if not normalized_provider:
        return None
    cache_key = (normalized_provider, str(base_url or ""))
    now = time.time()
    cached = _USAGE_CACHE.get(cache_key)
    if cached and now - cached[0] < _USAGE_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        from agent.account_usage import fetch_account_usage
        snapshot = fetch_account_usage(normalized_provider, base_url=base_url, api_key=api_key)
    except Exception:
        snapshot = None
    # Do not cache transient misses.  A missing usage snapshot makes quota/reset
    # disappear from the Telegram footer; retry on the next response instead of
    # pinning that bad state for the TTL window.
    if snapshot is not None:
        _USAGE_CACHE[cache_key] = (now, snapshot)
    else:
        _USAGE_CACHE.pop(cache_key, None)
    return snapshot


def _model_config(user_config: dict[str, Any] | None) -> dict[str, Any]:
    cfg = (user_config or {}).get("model") or {}
    return cfg if isinstance(cfg, dict) else {}


def _resolve_provider_lookup_args(
    user_config: dict[str, Any] | None,
    provider: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve provider usage lookup inputs, falling back to model config.

    Gateway turns normally pass provider/base_url from ``run_conversation``.  If
    an older/partial agent result omits them, footer rendering should still be
    able to retrieve real quota/reset data from ``config.yaml`` instead of
    dropping the provider fields.
    """
    model_cfg = _model_config(user_config)
    provider_text = str(provider or "").strip()
    if not provider_text or provider_text.lower() in {"auto", "custom"}:
        resolved_provider = model_cfg.get("provider")
    else:
        resolved_provider = provider_text
    resolved_base_url = base_url or model_cfg.get("base_url")
    resolved_api_key = api_key or model_cfg.get("api_key")
    return (
        str(resolved_provider) if resolved_provider not in {None, ""} else None,
        str(resolved_base_url) if resolved_base_url not in {None, ""} else None,
        str(resolved_api_key) if resolved_api_key not in {None, ""} else None,
    )


def format_runtime_footer(
    *,
    model: Optional[str],
    context_tokens: int,
    context_length: Optional[int],
    cwd: Optional[str] = None,
    fields: Iterable[str] = _DEFAULT_FIELDS,
    labels: dict[str, str] | None = None,
    timezone_name: str | None = None,
    provider_usage: Any = None,
) -> str:
    """Render the footer line, or return "" if no fields have data.

    Fields are skipped silently when their underlying data is missing — a
    partially-populated footer is better than a line with ``?%`` or empty slots.
    Provider/quota fields are rendered only from real usage snapshots.
    """
    labels = labels or _DEFAULT_LABELS
    provider_window = _select_provider_window(provider_usage)
    parts: list[str] = []
    for field in fields:
        if field == "model":
            m = _model_short(model)
            if m:
                parts.append(m)
        elif field == "context_pct":
            rendered = _format_context_pct(context_tokens, context_length, labels)
            if rendered:
                parts.append(rendered)
        elif field == "provider_window_pct":
            if provider_window is not None:
                used = _window_value(provider_window, "used_percent")
                if used is not None:
                    # Provider APIs expose used_percent, but the compact footer
                    # is intended to show remaining quota available in the
                    # current provider window.  This should therefore decrease
                    # as the user spends quota, until the next reset.
                    pct = max(0, min(100, round(100 - float(used))))
                    prefix = _label(labels, "provider_window_pct", "5H")
                    parts.append(f"{prefix} {pct}%" if prefix else f"{pct}%")
        elif field == "provider_reset":
            if provider_window is not None:
                reset = _format_reset_time(_window_value(provider_window, "reset_at"), timezone_name)
                if reset:
                    prefix = _label(labels, "provider_reset", "reset")
                    parts.append(f"{prefix} {reset}" if prefix else reset)
        elif field == "cwd":
            rel = _home_relative_cwd(cwd or os.environ.get("TERMINAL_CWD", ""))
            if rel:
                parts.append(rel)
        # Unknown field names are silently ignored.

    if not parts:
        return ""
    return _SEP.join(parts)


def build_footer_line(
    *,
    user_config: dict[str, Any] | None,
    platform_key: str | None,
    model: Optional[str],
    context_tokens: int,
    context_length: Optional[int],
    cwd: Optional[str] = None,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    provider_usage: Any = None,
) -> str:
    """Top-level entry point used by gateway/run.py.

    Returns the footer text (empty string when disabled or no data).  Callers
    append this to the final response themselves, preserving a single blank
    line of separation.
    """
    cfg = resolve_footer_config(user_config, platform_key)
    if not cfg.get("enabled"):
        return ""
    fields = cfg.get("fields") or _DEFAULT_FIELDS
    if provider_usage is None and any(f in {"provider_window_pct", "provider_reset"} for f in fields):
        lookup_provider, lookup_base_url, lookup_api_key = _resolve_provider_lookup_args(
            user_config,
            provider,
            base_url,
            api_key,
        )
        provider_usage = _fetch_provider_usage_cached(lookup_provider, lookup_base_url, lookup_api_key)

    return format_runtime_footer(
        model=model,
        context_tokens=context_tokens,
        context_length=context_length,
        cwd=cwd,
        fields=fields,
        labels=cfg.get("labels") or _DEFAULT_LABELS,
        timezone_name=cfg.get("timezone"),
        provider_usage=provider_usage,
    )
