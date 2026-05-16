import json
from types import SimpleNamespace

from run_agent import AIAgent, _parse_tool_call_arguments_for_execution


def _tool_call(name="read_file", arguments='{"path": "README.md"}'):
    return SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _assistant_message(*tool_calls):
    return SimpleNamespace(tool_calls=list(tool_calls))


def _agent_for_tool_execution():
    agent = object.__new__(AIAgent)
    agent._interrupt_requested = False
    agent.quiet_mode = True
    agent.verbose_logging = False
    agent.tool_delay = 0
    agent.tool_progress_callback = None
    agent.tool_start_callback = None
    agent.tool_complete_callback = None
    agent._context_engine_tool_names = set()
    agent._memory_manager = None
    agent.valid_tool_names = {"read_file"}
    agent.session_id = "test-session"
    agent.log_prefix_chars = 80
    agent.log_prefix = ""
    agent._current_tool = None
    agent._subdirectory_hints = SimpleNamespace(check_tool_call=lambda *_args, **_kwargs: "")
    agent._checkpoint_mgr = SimpleNamespace(enabled=False)
    agent._tool_guardrails = SimpleNamespace(
        before_call=lambda *_args, **_kwargs: SimpleNamespace(allows_execution=True)
    )
    agent._touch_activity = lambda *_args, **_kwargs: None
    agent._apply_pending_steer_to_tool_results = lambda *_args, **_kwargs: None
    agent._append_guardrail_observation = lambda _name, _args, result, failed=False: result
    agent._record_file_mutation_result = lambda *_args, **_kwargs: None
    agent._should_emit_quiet_tool_messages = lambda: False
    agent._should_start_quiet_spinner = lambda: False
    return agent


def test_parse_tool_call_arguments_accepts_valid_json_object():
    args, error = _parse_tool_call_arguments_for_execution(
        '{"path": "README.md"}',
        "read_file",
    )

    assert error is None
    assert args == {"path": "README.md"}


def test_parse_tool_call_arguments_rejects_malformed_json():
    args, error = _parse_tool_call_arguments_for_execution(
        '{"path": "README.md"',
        "read_file",
    )

    assert args == {}
    payload = json.loads(error)
    assert payload["success"] is False
    assert "Malformed tool call arguments for 'read_file'" in payload["error"]
    assert "Tool was not executed" in payload["error"]


def test_parse_tool_call_arguments_rejects_non_object_json():
    args, error = _parse_tool_call_arguments_for_execution(
        '["README.md"]',
        "read_file",
    )

    assert args == {}
    payload = json.loads(error)
    assert "expected a JSON object, got list" in payload["error"]


def test_sequential_tool_call_with_malformed_arguments_fails_closed(monkeypatch):
    agent = _agent_for_tool_execution()
    messages = []

    def _should_not_execute(*_args, **_kwargs):
        raise AssertionError("malformed tool call arguments must not execute tools")

    monkeypatch.setattr("run_agent.handle_function_call", _should_not_execute)

    agent._execute_tool_calls_sequential(
        _assistant_message(_tool_call(arguments='{"path": "README.md"')),
        messages,
        effective_task_id="task-1",
    )

    assert len(messages) == 1
    assert messages[0]["role"] == "tool"
    assert messages[0]["name"] == "read_file"
    assert messages[0]["tool_call_id"] == "call_1"
    payload = json.loads(messages[0]["content"])
    assert payload["success"] is False
    assert "Malformed tool call arguments" in payload["error"]


def test_concurrent_tool_call_with_malformed_arguments_fails_closed(monkeypatch):
    agent = _agent_for_tool_execution()
    messages = []

    def _should_not_execute(*_args, **_kwargs):
        raise AssertionError("malformed tool call arguments must not execute tools")

    monkeypatch.setattr("run_agent.handle_function_call", _should_not_execute)

    agent._execute_tool_calls_concurrent(
        _assistant_message(_tool_call(arguments='{"path": "README.md"')),
        messages,
        effective_task_id="task-1",
    )

    payload = json.loads(messages[0]["content"])
    assert payload["success"] is False
    assert "Malformed tool call arguments" in payload["error"]


def test_sequential_tool_call_with_valid_arguments_still_executes(monkeypatch):
    agent = _agent_for_tool_execution()
    messages = []

    def _execute(name, args, *_pos, **_kwargs):
        assert name == "read_file"
        assert args == {"path": "README.md"}
        return json.dumps({"ok": True})

    monkeypatch.setattr("run_agent.handle_function_call", _execute)

    agent._execute_tool_calls_sequential(
        _assistant_message(_tool_call(arguments='{"path": "README.md"}')),
        messages,
        effective_task_id="task-1",
    )

    assert json.loads(messages[0]["content"]) == {"ok": True}
