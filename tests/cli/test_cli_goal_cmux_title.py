from __future__ import annotations

import queue
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    from hermes_cli import goals

    goals._DB_CACHE.clear()
    yield home
    goals._DB_CACHE.clear()


def test_goal_command_updates_cmux_workspace_title(hermes_home):
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli.session_id = "sid-cmux-goal-title"
    cli._goal_manager = None
    cli._pending_input = queue.Queue()
    cli.config = {
        "cmux": {
            "auto_rename_workspace_on_goal": True,
            "goal_title_prefix": "Goal: ",
            "goal_title_max_chars": 60,
        }
    }

    with patch("cli.rename_cmux_workspace_for_goal") as rename:
        cli._handle_goal_command("/goal Improve cmux workspace titles")

    rename.assert_called_once_with(
        "Improve cmux workspace titles",
        config=cli.config,
    )
    assert cli._pending_input.get_nowait() == "Improve cmux workspace titles"
