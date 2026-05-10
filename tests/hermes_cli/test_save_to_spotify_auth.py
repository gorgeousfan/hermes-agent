from __future__ import annotations

import argparse
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from hermes_cli.auth_commands import auth_command, auth_save_to_spotify_command
from hermes_cli.main import _register_auth_provider_parsers
from plugins.save_to_spotify import auth_helper


def test_auth_save_to_spotify_parser_defaults_to_login() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="auth_action")
    _register_auth_provider_parsers(subparsers)

    args = parser.parse_args(["save-to-spotify"])

    assert args.auth_action == "save-to-spotify"
    assert args.save_to_spotify_action == "login"
    assert args.no_browser is False


def test_auth_save_to_spotify_parser_accepts_status() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="auth_action")
    _register_auth_provider_parsers(subparsers)

    args = parser.parse_args(["save-to-spotify", "status"])

    assert args.auth_action == "save-to-spotify"
    assert args.save_to_spotify_action == "status"


def test_auth_command_dispatches_save_to_spotify(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    monkeypatch.setattr(
        "hermes_cli.auth_commands.auth_save_to_spotify_command",
        lambda args: seen.append(args.save_to_spotify_action),
    )

    auth_command(SimpleNamespace(auth_action="save-to-spotify", save_to_spotify_action="status"))

    assert seen == ["status"]


def test_status_output_when_binary_missing(capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_helper, "get_save_to_spotify_state", lambda: auth_helper.SaveToSpotifyState(
        installed=False,
        authenticated=False,
        token_path=None,
        expires_at=None,
        next_action="install",
    ))

    auth_save_to_spotify_command(SimpleNamespace(save_to_spotify_action="status", no_browser=False))

    output = capsys.readouterr().out
    assert "installed: false" in output
    assert "authenticated: false" in output
    assert "next_action: install" in output
    assert "{" not in output


def test_status_output_when_installed_but_unauthenticated(capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_helper, "get_save_to_spotify_state", lambda: auth_helper.SaveToSpotifyState(
        installed=True,
        authenticated=False,
        token_path="/tmp/token.json",
        expires_at=None,
        next_action="login",
    ))

    auth_save_to_spotify_command(SimpleNamespace(save_to_spotify_action="status", no_browser=False))

    output = capsys.readouterr().out
    assert "installed: true" in output
    assert "authenticated: false" in output
    assert "token_path: /tmp/token.json" in output
    assert "next_action: login" in output


def test_status_output_when_authenticated(capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_helper, "get_save_to_spotify_state", lambda: auth_helper.SaveToSpotifyState(
        installed=True,
        authenticated=True,
        token_path="/tmp/token.json",
        expires_at="2099-01-01T00:00:00+00:00",
        next_action="ready",
    ))

    auth_save_to_spotify_command(SimpleNamespace(save_to_spotify_action="status", no_browser=False))

    output = capsys.readouterr().out
    assert "installed: true" in output
    assert "authenticated: true" in output
    assert "expires_at: 2099-01-01T00:00:00+00:00" in output
    assert "next_action: ready" in output


def test_login_missing_binary_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_helper, "binary_path", lambda: None)

    with pytest.raises(SystemExit) as exc:
        auth_save_to_spotify_command(SimpleNamespace(save_to_spotify_action="login", no_browser=False))

    assert "not installed" in str(exc.value)


def test_logout_missing_binary_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_helper, "binary_path", lambda: None)

    with pytest.raises(SystemExit) as exc:
        auth_save_to_spotify_command(SimpleNamespace(save_to_spotify_action="logout", no_browser=False))

    assert "Requested action: `logout`." in str(exc.value)


def test_login_headless_guidance_and_no_browser_passthrough(
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[list[str], bool]] = []

    monkeypatch.setattr(
        auth_helper,
        "login_guidance",
        lambda *, no_browser: ["Hermes is delegating to the official `save-to-spotify` login flow."],
    )
    monkeypatch.setattr(
        auth_helper,
        "post_login_guidance",
        lambda output, *, no_browser: ["Open this URL manually if the browser did not open: https://accounts.spotify.com/auth"],
    )

    def fake_run(command: list[str], *, stream_output: bool = False) -> str:
        seen.append((command, stream_output))
        return "https://accounts.spotify.com/auth"

    monkeypatch.setattr("hermes_cli.auth_commands._run_save_to_spotify_auth_command", fake_run)

    auth_save_to_spotify_command(SimpleNamespace(save_to_spotify_action="login", no_browser=True))

    output = capsys.readouterr().out
    assert "delegating to the official `save-to-spotify` login flow" in output
    assert "Open this URL manually" in output
    assert seen == [(["login", "--no-browser"], True)]


def test_auth_status_json_normalization() -> None:
    state = auth_helper.auth_status_to_state(
        {
            "authenticated": True,
            "token_valid": True,
            "expires_in_seconds": 120,
        },
        installed=True,
        now=datetime(2026, 5, 11, tzinfo=timezone.utc),
    )

    assert state.installed is True
    assert state.authenticated is True
    assert state.expires_at == "2026-05-11T00:02:00+00:00"
    assert state.next_action == "ready"
