"""Regression tests for the bounded proxy connection limits (#31599).

Through a tunneling proxy, httpcore does not reliably release the underlying
socket on ConnectError, so half-closed connections accumulate in the Telegram
adapter's general-request pool and eventually exhaust the process fd limit.
The gateway now caps the proxied pools and sets a finite ``keepalive_expiry``
so idle/dead connections are evicted instead of pinned for the process
lifetime. These tests pin the defaults and the env-override knobs.
"""

from __future__ import annotations

import httpx
import pytest

from gateway.platforms.telegram import _proxy_http_limits


def test_proxy_http_limits_defaults():
    limits = _proxy_http_limits()
    assert isinstance(limits, httpx.Limits)
    assert limits.max_connections == 20
    assert limits.max_keepalive_connections == 10
    # A finite expiry is the actual leak mitigation: dead keepalive connections
    # get evicted during pool maintenance rather than living forever.
    assert limits.keepalive_expiry == 30.0


def test_proxy_http_limits_env_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HERMES_TELEGRAM_PROXY_MAX_CONNECTIONS", "64")
    monkeypatch.setenv("HERMES_TELEGRAM_PROXY_MAX_KEEPALIVE", "32")
    monkeypatch.setenv("HERMES_TELEGRAM_PROXY_KEEPALIVE_EXPIRY", "5")
    limits = _proxy_http_limits()
    assert limits.max_connections == 64
    assert limits.max_keepalive_connections == 32
    assert limits.keepalive_expiry == 5.0


def test_proxy_http_limits_ignores_garbage_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HERMES_TELEGRAM_PROXY_MAX_CONNECTIONS", "not-an-int")
    monkeypatch.setenv("HERMES_TELEGRAM_PROXY_KEEPALIVE_EXPIRY", "")
    limits = _proxy_http_limits()
    assert limits.max_connections == 20
    assert limits.keepalive_expiry == 30.0
