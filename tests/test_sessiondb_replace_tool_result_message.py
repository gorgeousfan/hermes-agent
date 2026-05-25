import sqlite3

import pytest

from hermes_state import SessionDB


def test_replace_tool_result_message_updates_only_tool_content(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("s1", "cli")
    db.append_message(
        "s1",
        "assistant",
        content="",
        tool_calls=[{"id": "tc1", "function": {"name": "terminal", "arguments": "{}"}}],
    )
    db.append_message(
        "s1",
        "tool",
        content="raw output",
        tool_name="terminal",
        tool_call_id="tc1",
    )

    raw_ref = "tool-result://s1/tc1/abc"
    new_content = f"[TOOL RESULT COMPRESSED BY IDLE TOOL RESULT COMPACTOR]\nraw_ref: {raw_ref}\nsummary"
    result = db.replace_tool_result_message(
        "s1",
        "tc1",
        new_content,
        raw_ref,
        metadata={"plugin": "test"},
    )

    assert result["success"] is True
    messages = db.get_messages("s1")
    tool_msg = messages[1]
    assert tool_msg["role"] == "tool"
    assert tool_msg["content"] == new_content
    assert tool_msg["tool_call_id"] == "tc1"
    assert tool_msg["tool_name"] == "terminal"

    conn = sqlite3.connect(tmp_path / "state.db")
    count = conn.execute("SELECT COUNT(*) FROM tool_result_message_audit").fetchone()[0]
    assert count == 1


def test_replace_tool_result_message_rejects_non_tool_message(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("s1", "cli")
    db.append_message("s1", "assistant", content="not a tool", tool_call_id="tc1")

    raw_ref = "tool-result://s1/tc1/abc"
    with pytest.raises(ValueError, match="not role=tool"):
        db.replace_tool_result_message("s1", "tc1", f"raw_ref: {raw_ref}", raw_ref)


def test_replace_tool_result_message_rejects_duplicate_tool_call_id(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("s1", "cli")
    for _ in range(2):
        db.append_message(
            "s1",
            "tool",
            content="raw output",
            tool_name="terminal",
            tool_call_id="tc1",
        )

    raw_ref = "tool-result://s1/tc1/abc"
    with pytest.raises(ValueError, match="expected exactly one"):
        db.replace_tool_result_message("s1", "tc1", f"raw_ref: {raw_ref}", raw_ref)
