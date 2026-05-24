from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from plugins.web.retry_policy import call_with_429_retry


def _http_429_error(retry_after: str | None = None) -> httpx.HTTPStatusError:
    response = MagicMock()
    response.status_code = 429
    response.headers = {}
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    return httpx.HTTPStatusError("429", request=MagicMock(), response=response)


def test_call_with_429_retry_uses_retry_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "plugins.web.retry_policy.get_web_rate_limit_retry_policy",
        lambda: type(
            "Policy",
            (),
            {"retry_on_429": True, "retry_count": 2, "retry_interval": 0.25},
        )(),
    )
    sleeps: list[float] = []
    monkeypatch.setattr("plugins.web.retry_policy.time.sleep", lambda s: sleeps.append(s))

    attempts = {"count": 0}

    def _request() -> MagicMock:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise _http_429_error()
        ok = MagicMock()
        ok.raise_for_status.return_value = None
        return ok

    result = call_with_429_retry(_request, provider_name="test")
    assert attempts["count"] == 3
    assert sleeps == [0.25, 0.25]
    assert result is not None


def test_call_with_429_retry_honors_retry_after_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "plugins.web.retry_policy.get_web_rate_limit_retry_policy",
        lambda: type(
            "Policy",
            (),
            {"retry_on_429": True, "retry_count": 1, "retry_interval": 99.0},
        )(),
    )
    sleeps: list[float] = []
    monkeypatch.setattr("plugins.web.retry_policy.time.sleep", lambda s: sleeps.append(s))

    attempts = {"count": 0}

    def _request() -> MagicMock:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise _http_429_error(retry_after="1.5")
        ok = MagicMock()
        ok.raise_for_status.return_value = None
        return ok

    call_with_429_retry(_request, provider_name="test")
    assert sleeps == [1.5]


def test_call_with_429_retry_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "plugins.web.retry_policy.get_web_rate_limit_retry_policy",
        lambda: type(
            "Policy",
            (),
            {"retry_on_429": False, "retry_count": 5, "retry_interval": 1.0},
        )(),
    )
    monkeypatch.setattr("plugins.web.retry_policy.time.sleep", lambda _: None)

    attempts = {"count": 0}

    def _request() -> MagicMock:
        attempts["count"] += 1
        raise _http_429_error()

    with pytest.raises(httpx.HTTPStatusError):
        call_with_429_retry(_request, provider_name="test")
    assert attempts["count"] == 1
