"""Regression tests for LINE group authorization through the generic gateway gate."""

from types import SimpleNamespace


def _runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.pairing_store = SimpleNamespace(is_approved=lambda platform, user_id: False)
    return runner


def test_line_allowed_group_authorizes_any_sender_in_group(monkeypatch):
    from gateway.config import Platform
    from gateway.session import SessionSource

    monkeypatch.setenv("LINE_ALLOWED_USERS", "Uandy")
    monkeypatch.setenv("LINE_ALLOWED_GROUPS", "Callowed")
    monkeypatch.delenv("GATEWAY_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("GATEWAY_ALLOW_ALL_USERS", raising=False)

    source = SessionSource(
        platform=Platform("line"),
        chat_id="Callowed",
        chat_type="group",
        user_id="Ukelly",
        user_name="Kelly",
    )

    assert _runner()._is_user_authorized(source) is True


def test_line_group_does_not_authorize_dm_from_same_sender(monkeypatch):
    from gateway.config import Platform
    from gateway.session import SessionSource

    monkeypatch.setenv("LINE_ALLOWED_USERS", "Uandy")
    monkeypatch.setenv("LINE_ALLOWED_GROUPS", "Callowed")
    monkeypatch.delenv("GATEWAY_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("GATEWAY_ALLOW_ALL_USERS", raising=False)

    source = SessionSource(
        platform=Platform("line"),
        chat_id="Ukelly",
        chat_type="dm",
        user_id="Ukelly",
        user_name="Kelly",
    )

    assert _runner()._is_user_authorized(source) is False
