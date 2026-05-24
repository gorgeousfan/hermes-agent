from __future__ import annotations

import queue

from hermes_cli.model_switch import ModelSwitchResult


class _FakeAgent:
    def __init__(self):
        self.calls = []
        self.session_id = "sess-test"
        self.max_iterations = 90

    def run_conversation(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "final_response": "ok",
            "messages": [
                {"role": "user", "content": kwargs["user_message"]},
                {"role": "assistant", "content": "ok"},
            ],
            "completed": True,
            "api_calls": 1,
        }

    def switch_model(self, **kwargs):
        return None


def _make_cli():
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli.agent = _FakeAgent()
    cli.model = "old-model"
    cli.provider = "old-provider"
    cli.requested_provider = "old-provider"
    cli.api_key = ""
    cli.base_url = ""
    cli.api_mode = "chat_completions"
    cli._explicit_api_key = ""
    cli._explicit_base_url = ""
    cli._voice_mode = False
    cli._pending_model_switch_note = None
    cli._pending_skills_reload_note = None
    cli._sudo_password_callback = lambda: ""
    cli._approval_callback = lambda *a, **k: "approve_once"
    cli._secret_capture_callback = lambda *a, **k: {}
    cli._interrupt_queue = queue.Queue()
    cli._clarify_state = None
    cli._clarify_freetext = False
    cli._prompt_start_time = None
    cli._prompt_duration = 0.0
    cli._voice_tts = False
    cli._voice_tts_done = None
    cli._voice_continuous = False
    cli._voice_recording = False
    cli._last_turn_interrupted = False
    cli._ensure_runtime_credentials = lambda: True
    cli._resolve_turn_agent_config = lambda user_message: {
        "signature": "test-route",
        "model": cli.model,
        "runtime": {
            "provider": cli.provider,
            "api_key": cli.api_key,
            "base_url": cli.base_url,
            "api_mode": cli.api_mode,
            "command": None,
            "args": [],
            "credential_pool": None,
        },
        "request_overrides": None,
    }
    cli._active_agent_route_signature = "test-route"
    cli.session_id = "sess-test"
    cli.conversation_history = [{"role": "assistant", "content": "prior"}]
    cli._flush_stream = lambda: None
    cli._reset_stream_state = lambda: None
    cli._safe_print = lambda *a, **k: None
    cli._invalidate = lambda *a, **k: None
    cli._pending_title = None
    cli._session_db = None
    cli.show_reasoning = False
    cli.final_response_markdown = "raw"
    cli.bell_on_complete = False
    cli._stream_started = False
    cli._stream_box_opened = False
    cli._reasoning_shown_this_turn = False
    cli._stream_context_scrubber = None
    cli._stream_think_scrubber = None
    return cli


class ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self._alive = False
        self.ident = 1

    def start(self):
        self._alive = True
        try:
            if self._target:
                self._target(*self._args, **self._kwargs)
        finally:
            self._alive = False

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        return None


def _patch_chat_runtime(monkeypatch):
    import cli as cli_mod

    monkeypatch.setattr(cli_mod, "set_sudo_password_callback", lambda *a, **k: None)
    monkeypatch.setattr(cli_mod, "set_approval_callback", lambda *a, **k: None)
    monkeypatch.setattr(cli_mod, "set_secret_capture_callback", lambda *a, **k: None)
    monkeypatch.setattr(cli_mod.logging, "error", lambda *a, **k: None)
    monkeypatch.setattr(cli_mod.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(cli_mod.time, "sleep", lambda *a, **k: None)


def test_apply_model_switch_result_marks_next_turn_as_api_only(monkeypatch):
    import cli as cli_mod

    monkeypatch.setattr(cli_mod, "_cprint", lambda *a, **k: None)
    monkeypatch.setattr(cli_mod, "save_config_value", lambda *a, **k: None)

    cli = _make_cli()
    result = ModelSwitchResult(
        success=True,
        new_model="gpt-5.5",
        target_provider="custom:pixel",
        provider_changed=True,
        api_key="",
        base_url="",
        api_mode="chat_completions",
        warning_message="",
        provider_label="pixel",
        model_info=None,
        error_message="",
    )

    cli_mod.HermesCLI._apply_model_switch_result(cli, result, False)

    assert "switched from old-model to gpt-5.5" in cli._pending_model_switch_note


def test_chat_persists_clean_user_message_when_model_switch_note_is_prepended(monkeypatch):
    import cli as cli_mod

    _patch_chat_runtime(monkeypatch)
    monkeypatch.setattr(cli_mod.ChatConsole, "print", lambda *a, **k: None)

    cli = _make_cli()
    cli._pending_model_switch_note = "[Note: model was just switched from a to b.]"

    cli_mod.HermesCLI.chat(cli, "继续任务")

    call = cli.agent.calls[-1]
    assert call["user_message"].startswith("[Note: model was just switched")
    assert call["persist_user_message"] == "继续任务"


def test_chat_persists_clean_user_message_when_skills_reload_note_is_prepended(monkeypatch):
    import cli as cli_mod

    _patch_chat_runtime(monkeypatch)
    monkeypatch.setattr(cli_mod.ChatConsole, "print", lambda *a, **k: None)

    cli = _make_cli()
    cli._pending_skills_reload_note = "[Skills reloaded: added foo]"

    cli_mod.HermesCLI.chat(cli, "继续任务")

    call = cli.agent.calls[-1]
    assert call["user_message"].startswith("[Skills reloaded")
    assert call["persist_user_message"] == "继续任务"
