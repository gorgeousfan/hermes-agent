import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from hermes_cli.env_loader import get_secret_source, load_hermes_dotenv


def test_user_env_overrides_stale_shell_values(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    env_file = home / ".env"
    env_file.write_text("OPENAI_BASE_URL=https://new.example/v1\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")

    loaded = load_hermes_dotenv(hermes_home=home)

    assert loaded == [env_file]
    assert os.getenv("OPENAI_BASE_URL") == "https://new.example/v1"


def test_project_env_overrides_stale_shell_values_when_user_env_missing(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    project_env = tmp_path / ".env"
    project_env.write_text("OPENAI_BASE_URL=https://project.example/v1\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")

    loaded = load_hermes_dotenv(hermes_home=home, project_env=project_env)

    assert loaded == [project_env]
    assert os.getenv("OPENAI_BASE_URL") == "https://project.example/v1"


def test_project_env_is_sanitized_before_loading(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    project_env = tmp_path / ".env"
    project_env.write_text(
        "TELEGRAM_BOT_TOKEN=0123456789:test"
        "ANTHROPIC_API_KEY=sk-ant-test123\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    loaded = load_hermes_dotenv(hermes_home=home, project_env=project_env)

    assert loaded == [project_env]
    assert os.getenv("TELEGRAM_BOT_TOKEN") == "0123456789:test"
    assert os.getenv("ANTHROPIC_API_KEY") == "sk-ant-test123"


def test_user_env_takes_precedence_over_project_env(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    user_env = home / ".env"
    project_env = tmp_path / ".env"
    user_env.write_text("OPENAI_BASE_URL=https://user.example/v1\n", encoding="utf-8")
    project_env.write_text("OPENAI_BASE_URL=https://project.example/v1\nOPENAI_API_KEY=project-key\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    loaded = load_hermes_dotenv(hermes_home=home, project_env=project_env)

    assert loaded == [user_env, project_env]
    assert os.getenv("OPENAI_BASE_URL") == "https://user.example/v1"
    assert os.getenv("OPENAI_API_KEY") == "project-key"


def test_null_bytes_in_user_env_are_stripped(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    env_file = home / ".env"
    # Null bytes can be introduced when copy-pasting API keys.
    env_file.write_text("GLM_API_KEY=abc\x00\x00\nOPENAI_API_KEY=sk-123\n", encoding="utf-8")

    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    loaded = load_hermes_dotenv(hermes_home=home)

    assert loaded == [env_file]
    assert os.getenv("GLM_API_KEY") == "abc"
    assert os.getenv("OPENAI_API_KEY") == "sk-123"


def test_main_import_applies_user_env_over_shell_values(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / ".env").write_text(
        "OPENAI_BASE_URL=https://new.example/v1\nHERMES_INFERENCE_PROVIDER=custom\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")
    monkeypatch.setenv("HERMES_INFERENCE_PROVIDER", "openrouter")

    sys.modules.pop("hermes_cli.main", None)
    importlib.import_module("hermes_cli.main")

    assert os.getenv("OPENAI_BASE_URL") == "https://new.example/v1"
    assert os.getenv("HERMES_INFERENCE_PROVIDER") == "custom"


def test_nexus_bootstrap_overrides_local_dotenv_secret(tmp_path, monkeypatch, capsys):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / ".env").write_text("TELEGRAM_BOT_TOKEN=local-token\n", encoding="utf-8")

    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_URL", "https://nexus.example")
    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_INTEGRATION_ID", "athena")
    monkeypatch.setenv("NEXUS_SERVICE_TOKEN", "x" * 64)

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"secrets": {"TELEGRAM_BOT_TOKEN": "vault-token"}}).encode("utf-8")

    with patch("hermes_cli.env_loader.urlopen", return_value=_Resp()) as mock_urlopen:
        loaded = load_hermes_dotenv(hermes_home=home)

    assert loaded == [home / ".env"]
    assert os.getenv("TELEGRAM_BOT_TOKEN") == "vault-token"
    assert get_secret_source("TELEGRAM_BOT_TOKEN") == "Nexus vault"
    req = mock_urlopen.call_args.args[0]
    assert req.full_url == "https://nexus.example/api/v1/vault/bootstrap/athena"
    assert req.headers["Authorization"] == f"Bearer {'x' * 64}"
    stderr = capsys.readouterr().err
    assert "TELEGRAM_BOT_TOKEN" in stderr
    assert "local-token" not in stderr
    assert "vault-token" not in stderr


def test_nexus_bootstrap_is_skipped_without_required_env(monkeypatch, tmp_path):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.delenv("HERMES_NEXUS_BOOTSTRAP_URL", raising=False)
    monkeypatch.delenv("HERMES_NEXUS_BOOTSTRAP_INTEGRATION_ID", raising=False)
    monkeypatch.delenv("NEXUS_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("NEXUS_TOKEN", raising=False)

    with patch("hermes_cli.env_loader.urlopen") as mock_urlopen:
        loaded = load_hermes_dotenv(hermes_home=home)

    assert loaded == []
    mock_urlopen.assert_not_called()


def test_nexus_bootstrap_ignores_empty_or_non_string_secrets(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()

    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_URL", "https://nexus.example/")
    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_INTEGRATION_ID", "athena")
    monkeypatch.setenv("NEXUS_TOKEN", "nexus-token")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    original_path = os.getenv("PATH")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "secrets": {
                        "TELEGRAM_BOT_TOKEN": "   ",
                        "OPENAI_API_KEY": 123,
                        "PATH": "/tmp/malicious",
                    }
                }
            ).encode("utf-8")

    with patch("hermes_cli.env_loader.urlopen", return_value=_Resp()):
        load_hermes_dotenv(hermes_home=home)

    assert os.getenv("TELEGRAM_BOT_TOKEN") is None
    assert os.getenv("OPENAI_API_KEY") is None
    assert os.getenv("PATH") == original_path


def test_nexus_bootstrap_failure_does_not_override_local_secret(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / ".env").write_text("TELEGRAM_BOT_TOKEN=local-token\n", encoding="utf-8")

    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_URL", "https://nexus.example")
    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_INTEGRATION_ID", "athena")
    monkeypatch.setenv("NEXUS_SERVICE_TOKEN", "x" * 64)

    with patch("hermes_cli.env_loader.urlopen", side_effect=OSError("network down")):
        load_hermes_dotenv(hermes_home=home)

    assert os.getenv("TELEGRAM_BOT_TOKEN") == "local-token"


def test_nexus_bootstrap_rejects_non_https_url(tmp_path, monkeypatch, capsys):
    home = tmp_path / "hermes"
    home.mkdir()

    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_URL", "http://nexus.example")
    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_INTEGRATION_ID", "athena")
    monkeypatch.setenv("NEXUS_SERVICE_TOKEN", "x" * 64)

    with patch("hermes_cli.env_loader.urlopen") as mock_urlopen:
        load_hermes_dotenv(hermes_home=home)

    mock_urlopen.assert_not_called()
    assert "must use https" in capsys.readouterr().err


def test_nexus_bootstrap_kill_switch_skips_fetch(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()

    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_ENABLED", "0")
    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_URL", "https://nexus.example")
    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_INTEGRATION_ID", "athena")
    monkeypatch.setenv("NEXUS_SERVICE_TOKEN", "x" * 64)

    with patch("hermes_cli.env_loader.urlopen") as mock_urlopen:
        load_hermes_dotenv(hermes_home=home)

    mock_urlopen.assert_not_called()


def test_nexus_bootstrap_url_encodes_integration_id_and_uses_timeout(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()

    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_URL", "https://nexus.example")
    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_INTEGRATION_ID", "athena/prod")
    monkeypatch.setenv("NEXUS_SERVICE_TOKEN", "x" * 64)

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"secrets": {"TELEGRAM_BOT_TOKEN": "vault-token"}}).encode("utf-8")

    with patch("hermes_cli.env_loader.urlopen", return_value=_Resp()) as mock_urlopen:
        load_hermes_dotenv(hermes_home=home)

    req = mock_urlopen.call_args.args[0]
    assert req.full_url == "https://nexus.example/api/v1/vault/bootstrap/athena%2Fprod"
    assert mock_urlopen.call_args.kwargs["timeout"] == 15


def test_nexus_bootstrap_non_dict_json_body_falls_back(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / ".env").write_text("TELEGRAM_BOT_TOKEN=local-token\n", encoding="utf-8")

    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_URL", "https://nexus.example")
    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_INTEGRATION_ID", "athena")
    monkeypatch.setenv("NEXUS_SERVICE_TOKEN", "x" * 64)

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"[]"

    with patch("hermes_cli.env_loader.urlopen", return_value=_Resp()):
        load_hermes_dotenv(hermes_home=home)

    assert os.getenv("TELEGRAM_BOT_TOKEN") == "local-token"


def test_nexus_bootstrap_non_utf8_body_falls_back(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / ".env").write_text("TELEGRAM_BOT_TOKEN=local-token\n", encoding="utf-8")

    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_URL", "https://nexus.example")
    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_INTEGRATION_ID", "athena")
    monkeypatch.setenv("NEXUS_SERVICE_TOKEN", "x" * 64)

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"\xff\xfe\x00"

    with patch("hermes_cli.env_loader.urlopen", return_value=_Resp()):
        load_hermes_dotenv(hermes_home=home)

    assert os.getenv("TELEGRAM_BOT_TOKEN") == "local-token"


def test_nexus_bootstrap_invalid_env_value_is_skipped(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()

    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_URL", "https://nexus.example")
    monkeypatch.setenv("HERMES_NEXUS_BOOTSTRAP_INTEGRATION_ID", "athena")
    monkeypatch.setenv("NEXUS_SERVICE_TOKEN", "x" * 64)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "secrets": {
                        "TELEGRAM_BOT_TOKEN": "vault-token\x00invalid",
                        "OPENAI_API_KEY": "valid-key",
                    }
                }
            ).encode("utf-8")

    with patch("hermes_cli.env_loader.urlopen", return_value=_Resp()):
        load_hermes_dotenv(hermes_home=home)

    assert os.getenv("TELEGRAM_BOT_TOKEN") is None
    assert os.getenv("OPENAI_API_KEY") == "valid-key"
