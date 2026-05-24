from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


def test_gquota_uses_chat_console_when_tui_is_live():
    from agent.google_oauth import GoogleOAuthError
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli.console = MagicMock()
    cli._app = object()

    live_console = MagicMock()

    with patch("cli.ChatConsole", return_value=live_console), \
         patch("agent.google_oauth.get_valid_access_token", side_effect=GoogleOAuthError("No Google OAuth credentials found")), \
         patch("agent.google_oauth.load_credentials", return_value=None), \
         patch("agent.google_code_assist.retrieve_user_quota"):
        cli._handle_gquota_command("/gquota")

    assert live_console.print.call_count == 2
    cli.console.print.assert_not_called()


def test_quota_command_fetches_current_provider_account_usage(capsys):
    from agent.account_usage import AccountUsageSnapshot, AccountUsageWindow
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli.agent = MagicMock(provider="openai-codex", base_url="https://chatgpt.com/backend-api/codex", api_key="tok")
    snapshot = AccountUsageSnapshot(
        provider="openai-codex",
        source="usage_api",
        fetched_at=datetime.now(timezone.utc),
        windows=(AccountUsageWindow(label="Session", used_percent=12),),
    )

    with patch("agent.account_usage.fetch_account_usage", return_value=snapshot) as fetch:
        cli._show_account_quota("/quota")

    fetch.assert_called_once_with(
        "openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        api_key="tok",
    )
    out = capsys.readouterr().out
    assert "Account limits" in out
    assert "openai-codex" in out
    assert "Session: 88% remaining" in out
