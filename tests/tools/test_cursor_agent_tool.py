import json

from tools.cursor_agent_tool import (
    parse_cursor_stream_line,
    run_cursor_agent,
    summarize_cursor_event,
)


def test_parse_cursor_stream_line_strips_ansi_and_json():
    event = parse_cursor_stream_line('\x1b[32m{"type":"tool_call","name":"edit_file"}\x1b[0m\n')

    assert event == {"type": "tool_call", "name": "edit_file"}


def test_parse_cursor_stream_line_wraps_plain_text():
    assert parse_cursor_stream_line("still working\n") == {"raw": "still working"}


def test_summarize_cursor_event_prefers_command():
    summary = summarize_cursor_event(
        {"type": "command_started", "command": "python -m pytest tests/foo.py -q"}
    )

    assert summary == "command_started: `python -m pytest tests/foo.py -q`"


def test_summarize_cursor_event_handles_nested_text():
    summary = summarize_cursor_event(
        {"type": "assistant_message", "data": {"content": "Implemented the change"}}
    )

    assert summary == "assistant_message: Implemented the change"


def _write_fake_cursor(tmp_path, body: str):
    fake = tmp_path / "fake-cursor-agent"
    fake.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    fake.chmod(0o755)
    return fake


def test_run_cursor_agent_with_fake_cursor_binary_streams_progress(tmp_path):
    fake = _write_fake_cursor(
        tmp_path,
        "import json, time\n"
        "print(json.dumps({'type': 'thinking', 'message': 'planning'}), flush=True)\n"
        "time.sleep(0.02)\n"
        "print(json.dumps({'type': 'command_started', 'command': 'pytest -q'}), flush=True)\n"
        "time.sleep(0.02)\n"
        "print(json.dumps({'type': 'final', 'message': 'done'}), flush=True)\n",
    )

    progress = []
    result = json.loads(
        run_cursor_agent(
            prompt="do work",
            workspace=str(tmp_path),
            command=str(fake),
            progress_interval_seconds=0.01,
            progress_callback=lambda event_type, tool_name=None, preview=None, args=None, **kwargs: progress.append(preview),
            timeout_seconds=5,
        )
    )

    assert result["success"] is True
    assert result["final_output"] == "done"
    assert any("pytest -q" in item for item in result["progress_tail"])
    assert any("Cursor Agent progress" in item for item in progress)


def test_run_cursor_agent_intermediate_error_event_does_not_fail_successful_process(tmp_path):
    fake = _write_fake_cursor(
        tmp_path,
        "import json\n"
        "print(json.dumps({'type': 'error', 'message': 'tests failed once'}), flush=True)\n"
        "print(json.dumps({'type': 'final', 'message': 'fixed and done'}), flush=True)\n",
    )

    result = json.loads(
        run_cursor_agent(
            prompt="do work",
            workspace=str(tmp_path),
            command=str(fake),
            progress_interval_seconds=0.01,
            timeout_seconds=5,
        )
    )

    assert result["success"] is True
    assert result["errors"] == ["error: tests failed once"]
