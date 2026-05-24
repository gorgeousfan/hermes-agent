from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(kb.SCHEMA_SQL)
    kb._migrate_add_optional_columns(conn)
    try:
        yield conn
    finally:
        conn.close()


def test_parent_scratch_survives_until_child_done(
    kanban_conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "parent-scratch"
    workspace.mkdir()

    parent = kb.create_task(
        kanban_conn,
        title="parent",
        assignee="lead",
        workspace_kind="scratch",
        workspace_path=str(workspace),
    )
    child = kb.create_task(
        kanban_conn,
        title="child",
        assignee="worker",
        parents=[parent],
    )

    assert kb.complete_task(kanban_conn, parent, result="parent complete")
    assert kb.get_task(kanban_conn, child).status == "ready"
    assert workspace.is_dir()


def test_parent_scratch_cleaned_after_last_child_done(
    kanban_conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "parent-scratch-last-child"
    workspace.mkdir()

    parent = kb.create_task(
        kanban_conn,
        title="parent",
        assignee="lead",
        workspace_kind="scratch",
        workspace_path=str(workspace),
    )
    child = kb.create_task(
        kanban_conn,
        title="child",
        assignee="worker",
        parents=[parent],
    )

    assert kb.complete_task(kanban_conn, parent, result="parent complete")
    assert workspace.is_dir()
    assert kb.complete_task(kanban_conn, child, result="child complete")
    assert not workspace.exists()
