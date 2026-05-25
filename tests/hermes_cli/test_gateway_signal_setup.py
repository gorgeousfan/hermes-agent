"""Focused tests for managed Signal daemon setup helpers."""

from types import SimpleNamespace

import hermes_cli.gateway as gateway


def test_ensure_signal_cli_daemon_service_installs_launchd(monkeypatch, tmp_path):
    plist_path = tmp_path / "LaunchAgents" / "ai.hermes.signal-cli-daemon.plist"

    monkeypatch.setattr(gateway, "supports_systemd_services", lambda: False)
    monkeypatch.setattr(gateway, "is_macos", lambda: True)
    monkeypatch.setattr(gateway, "get_signal_launchd_plist_path", lambda: plist_path)
    monkeypatch.setattr(gateway, "get_signal_launchd_label", lambda: "ai.hermes.signal-cli-daemon")
    monkeypatch.setattr(gateway, "_build_service_path_dirs", lambda: ["/usr/bin"])
    monkeypatch.setattr(gateway, "_build_user_local_paths", lambda home, existing: [])
    monkeypatch.setattr("shutil.which", lambda name: "/opt/homebrew/bin/signal-cli" if name == "signal-cli" else None)

    calls = []

    def fake_run(cmd, check=False, timeout=None, **kwargs):
        calls.append((cmd, check, timeout))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(gateway.subprocess, "run", fake_run)

    ok, message = gateway.ensure_signal_cli_daemon_service("http://127.0.0.1:8080")

    assert ok is True
    assert "signal-cli-daemon" in message
    assert plist_path.exists()
    plist_text = plist_path.read_text(encoding="utf-8")
    assert "/opt/homebrew/bin/signal-cli" in plist_text
    assert "<string>127.0.0.1:8080</string>" in plist_text
    assert any(cmd[:2] == ["launchctl", "bootstrap"] for cmd, _, _ in calls)
    assert any(cmd[:2] == ["launchctl", "kickstart"] for cmd, _, _ in calls)


def test_ensure_signal_cli_daemon_service_installs_systemd(monkeypatch, tmp_path):
    unit_path = tmp_path / "systemd" / "user" / "hermes-signal-cli-daemon.service"

    monkeypatch.setattr(gateway, "supports_systemd_services", lambda: True)
    monkeypatch.setattr(gateway, "is_macos", lambda: False)
    monkeypatch.setattr(gateway, "get_signal_systemd_unit_path", lambda: unit_path)
    monkeypatch.setattr(gateway, "_build_service_path_dirs", lambda: ["/usr/bin"])
    monkeypatch.setattr(gateway, "_build_user_local_paths", lambda home, existing: [])
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/signal-cli" if name == "signal-cli" else None)
    monkeypatch.setattr(gateway, "_preflight_user_systemd", lambda auto_enable_linger=True: None)

    calls = []

    def fake_run_systemctl(args, *, system=False, **kwargs):
        calls.append((tuple(args), system))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(gateway, "_run_systemctl", fake_run_systemctl)

    ok, message = gateway.ensure_signal_cli_daemon_service("http://localhost:8080")

    assert ok is True
    assert "hermes-signal-cli-daemon" in message
    assert unit_path.exists()
    unit_text = unit_path.read_text(encoding="utf-8")
    assert "ExecStart=/usr/bin/signal-cli daemon --http localhost:8080 --no-receive-stdout" in unit_text
    assert calls == [
        (("daemon-reload",), False),
        (("enable", gateway.get_signal_service_name()), False),
        (("restart", gateway.get_signal_service_name()), False),
    ]


def test_setup_signal_auto_installs_local_service_when_probe_fails(monkeypatch, capsys):
    saved = {}
    inputs = iter([
        "http://127.0.0.1:8080",
        "+15551234567",
        "+15551234567",
    ])
    probe_calls = []
    install_calls = []

    def fake_get_env(key):
        return ""

    def fake_save_env(key, value):
        saved[key] = value

    def fake_prompt_yes_no(question, default=True):
        if "persistent signal-cli daemon service" in question:
            return True
        if "Enable group messaging" in question:
            return False
        return default

    def fake_httpx_get(url, timeout=10.0):
        probe_calls.append(url)
        if len(probe_calls) == 1:
            raise RuntimeError("connection refused")
        return SimpleNamespace(status_code=200)

    def fake_install(url, *, force=False):
        install_calls.append((url, force))
        return True, "Installed and started managed Signal daemon service."

    monkeypatch.setattr(gateway, "get_env_value", fake_get_env)
    monkeypatch.setattr(gateway, "save_env_value", fake_save_env)
    monkeypatch.setattr(gateway, "prompt_yes_no", fake_prompt_yes_no)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/signal-cli" if name == "signal-cli" else None)
    monkeypatch.setattr("httpx.get", fake_httpx_get)
    monkeypatch.setattr(gateway, "ensure_signal_cli_daemon_service", fake_install)

    gateway._setup_signal()

    out = capsys.readouterr().out
    assert install_calls == [("http://127.0.0.1:8080", False)]
    assert saved["SIGNAL_HTTP_URL"] == "http://127.0.0.1:8080"
    assert saved["SIGNAL_ACCOUNT"] == "+15551234567"
    assert saved["SIGNAL_ALLOWED_USERS"] == "+15551234567"
    assert "Installed and started managed Signal daemon service." in out
    assert "signal-cli daemon is reachable!" in out
