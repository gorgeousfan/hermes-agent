"""Tests for Playwright tool registration + basic handler error paths."""

import json

import tools.playwright_tool as pw


def test_playwright_check_fn_false_when_missing(monkeypatch):
    monkeypatch.setattr(pw, "sync_playwright", None)
    assert pw.check_playwright_requirements() is False


def test_pw_start_returns_error_without_playwright(monkeypatch):
    monkeypatch.setattr(pw, "sync_playwright", None)
    result = json.loads(pw.pw_start({}, task_id="t1"))
    assert "error" in result


def test_pw_navigate_requires_url(monkeypatch):
    monkeypatch.setattr(pw, "sync_playwright", object())

    def _boom(*a, **k):
        raise RuntimeError("should not be called")

    monkeypatch.setattr(pw, "_ensure_session", _boom)
    result = json.loads(pw.pw_navigate({}, task_id="t1"))
    assert result["error"] == "url is required"
