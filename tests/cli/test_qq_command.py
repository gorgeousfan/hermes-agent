"""Tests for the /qq quick-question slash command."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch


def _import_cli():
    import hermes_cli.config as config_mod

    if not hasattr(config_mod, "save_env_value_secure"):
        config_mod.save_env_value_secure = lambda key, value: {
            "success": True,
            "stored_as": key,
            "validated": False,
        }

    import cli as cli_mod

    return cli_mod


class _ImmediateThread:
    def __init__(self, target, daemon=False, name=None):
        self._target = target
        self.daemon = daemon
        self.name = name

    def start(self):
        self._target()


class _FakeQqAgent:
    instances = []
    response = {"final_response": "quick answer"}
    error: BaseException | None = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.session_id = kwargs["session_id"]
        self._print_fn = None
        self.thinking_callback = None
        type(self).instances.append(self)

    def run_conversation(self, user_message, task_id):
        assert user_message == "what do you remember?"
        assert task_id == self.kwargs["session_id"]
        err = type(self).error
        if err is not None:
            raise err
        return type(self).response


class TestQqCommand:
    def _make_cli(self) -> Any:
        session_db = MagicMock()
        return SimpleNamespace(
            max_turns=3,
            enabled_toolsets=["terminal"],
            _session_db=session_db,
            reasoning_config=None,
            service_tier=None,
            _providers_only=None,
            _providers_ignore=None,
            _providers_order=None,
            _provider_sort=None,
            _provider_require_params=False,
            _provider_data_collection=None,
            _openrouter_min_coding_score=None,
            _fallback_model=None,
            _sudo_password_callback=None,
            _approval_callback=None,
            _secret_capture_callback=None,
            _agent_running=False,
            _spinner_text="",
            _app=None,
            final_response_markdown=True,
            bell_on_complete=False,
            _ensure_runtime_credentials=lambda: True,
            _resolve_turn_agent_config=lambda _prompt: {
                "model": "test-model",
                "runtime": {
                    "api_key": "test-key",
                    "base_url": "https://example.test",
                    "provider": "test-provider",
                    "api_mode": "chat_completions",
                },
                "request_overrides": {},
            },
        )

    def setup_method(self):
        _FakeQqAgent.instances = []
        _FakeQqAgent.response = {"final_response": "quick answer"}
        _FakeQqAgent.error = None

    def test_qq_deletes_transient_session_after_answer(self):
        cli_mod = _import_cli()
        stub = self._make_cli()

        with (
            patch.object(cli_mod, "AIAgent", _FakeQqAgent),
            patch.object(cli_mod.threading, "Thread", _ImmediateThread),
            patch.object(cli_mod, "ChatConsole", return_value=MagicMock()),
            patch.object(cli_mod, "_cprint"),
        ):
            cli_mod.HermesCLI._handle_qq_command(stub, "/qq what do you remember?")

        assert len(_FakeQqAgent.instances) == 1
        task_id = _FakeQqAgent.instances[0].kwargs["session_id"]
        assert task_id.startswith("qq_")
        stub._session_db.delete_session.assert_called_once_with(
            task_id,
            sessions_dir=cli_mod.get_hermes_home() / "sessions",
        )

    def test_qq_deletes_transient_session_after_error(self):
        cli_mod = _import_cli()
        stub = self._make_cli()
        _FakeQqAgent.error = RuntimeError("boom")

        with (
            patch.object(cli_mod, "AIAgent", _FakeQqAgent),
            patch.object(cli_mod.threading, "Thread", _ImmediateThread),
            patch.object(cli_mod, "_cprint"),
        ):
            cli_mod.HermesCLI._handle_qq_command(stub, "/qq what do you remember?")

        assert len(_FakeQqAgent.instances) == 1
        task_id = _FakeQqAgent.instances[0].kwargs["session_id"]
        assert task_id.startswith("qq_")
        stub._session_db.delete_session.assert_called_once_with(
            task_id,
            sessions_dir=cli_mod.get_hermes_home() / "sessions",
        )
