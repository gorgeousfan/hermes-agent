"""Shared HTTP 429 retry policy for web providers.

Config source: ``web`` section in ``~/.hermes/config.yaml``.
Supported keys:
- ``retry_on_429``: bool (default: true)
- ``retry_count``: int, number of retries after the first attempt (default: 3)
- ``retry_interval``: float seconds (default: 60)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_RETRY_ON_429 = True
_DEFAULT_RETRY_COUNT = 3
_DEFAULT_RETRY_INTERVAL = 60.0
_MAX_RETRY_COUNT = 20
_MAX_RETRY_INTERVAL = 3600.0


@dataclass(frozen=True)
class WebRateLimitRetryPolicy:
    retry_on_429: bool
    retry_count: int
    retry_interval: float


def _load_web_config() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        return load_config().get("web", {}) or {}
    except Exception:
        return {}


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _coerce_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def get_web_rate_limit_retry_policy() -> WebRateLimitRetryPolicy:
    cfg = _load_web_config()
    retry_on_429 = _coerce_bool(cfg.get("retry_on_429"), _DEFAULT_RETRY_ON_429)
    retry_count = _coerce_int(
        cfg.get("retry_count"),
        _DEFAULT_RETRY_COUNT,
        minimum=0,
        maximum=_MAX_RETRY_COUNT,
    )
    retry_interval = _coerce_float(
        cfg.get("retry_interval"),
        _DEFAULT_RETRY_INTERVAL,
        minimum=0.0,
        maximum=_MAX_RETRY_INTERVAL,
    )
    return WebRateLimitRetryPolicy(
        retry_on_429=retry_on_429,
        retry_count=retry_count,
        retry_interval=retry_interval,
    )


def _retry_after_seconds(response: httpx.Response | None) -> float | None:
    if response is None:
        return None
    retry_after = response.headers.get("Retry-After")
    if not retry_after:
        return None
    try:
        parsed = float(retry_after.strip())
    except ValueError:
        return None
    if parsed < 0:
        return 0.0
    return min(parsed, _MAX_RETRY_INTERVAL)


def call_with_429_retry(
    request_fn: Callable[[], httpx.Response],
    *,
    provider_name: str,
) -> httpx.Response:
    """Execute ``request_fn`` with configurable HTTP 429 retries."""
    policy = get_web_rate_limit_retry_policy()
    max_retries = policy.retry_count if policy.retry_on_429 else 0

    attempt = 0
    while True:
        attempt += 1
        try:
            response = request_fn()
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code != 429 or attempt > max_retries:
                raise
            wait_seconds = _retry_after_seconds(exc.response)
            if wait_seconds is None:
                wait_seconds = policy.retry_interval
            logger.warning(
                "%s returned HTTP 429; retrying in %.1fs (retry %d/%d)",
                provider_name,
                wait_seconds,
                attempt,
                max_retries,
            )
            time.sleep(wait_seconds)
