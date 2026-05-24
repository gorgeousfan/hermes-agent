"""Tests for the /cron CLI slash command."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch


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


def _printed_text(mock_print) -> str:
    return " ".join(" ".join(str(arg) for arg in call.args) for call in mock_print.call_args_list)


class TestCronSlashCommand(unittest.TestCase):
    def test_create_forwards_script_no_agent_and_workdir(self):
        cli_mod = _import_cli()
        stub = SimpleNamespace()
        payload = {
            "success": True,
            "job_id": "job-1",
            "schedule": "every 15m",
            "skills": [],
            "next_run_at": "2026-05-13T10:15:00Z",
            "job": {
                "script": "watcher.py",
                "no_agent": True,
                "workdir": "/tmp/project",
            },
        }

        with (
            patch("tools.cronjob_tools.cronjob", return_value=json.dumps(payload)) as mock_tool,
            patch("builtins.print") as mock_print,
        ):
            cli_mod.HermesCLI._handle_cron_command(
                stub,
                '/cron add "every 15m" --script watcher.py --no-agent --workdir /tmp/project',
            )

        kwargs = mock_tool.call_args.kwargs
        self.assertEqual(kwargs["action"], "create")
        self.assertEqual(kwargs["schedule"], "every 15m")
        self.assertEqual(kwargs["script"], "watcher.py")
        self.assertEqual(kwargs["workdir"], "/tmp/project")
        self.assertIs(kwargs["no_agent"], True)

        printed = _printed_text(mock_print)
        self.assertIn("Created job", printed)
        self.assertIn("Script: watcher.py", printed)
        self.assertIn("Mode: no-agent", printed)
        self.assertIn("Workdir: /tmp/project", printed)

    def test_edit_forwards_agent_toggle_and_script_clear(self):
        cli_mod = _import_cli()
        stub = SimpleNamespace()
        payload = {
            "success": True,
            "job": {
                "job_id": "job-1",
                "schedule": "every 2h",
                "skills": [],
                "workdir": "/tmp/project",
            },
        }

        with (
            patch.object(cli_mod, "get_job", return_value={"id": "job-1", "skills": [], "script": "watcher.py"}),
            patch("tools.cronjob_tools.cronjob", return_value=json.dumps(payload)) as mock_tool,
            patch("builtins.print"),
        ):
            cli_mod.HermesCLI._handle_cron_command(
                stub,
                '/cron edit job-1 --script "" --agent --workdir /tmp/project',
            )

        kwargs = mock_tool.call_args.kwargs
        self.assertEqual(kwargs["action"], "update")
        self.assertEqual(kwargs["job_id"], "job-1")
        self.assertEqual(kwargs["script"], "")
        self.assertEqual(kwargs["workdir"], "/tmp/project")
        self.assertIs(kwargs["no_agent"], False)

    def test_status_dispatches_to_shared_cron_helper(self):
        cli_mod = _import_cli()
        stub = SimpleNamespace()

        with patch("hermes_cli.cron.cron_status") as mock_status:
            cli_mod.HermesCLI._handle_cron_command(stub, "/cron status")

        mock_status.assert_called_once_with()

    def test_tick_dispatches_to_shared_cron_helper(self):
        cli_mod = _import_cli()
        stub = SimpleNamespace()

        with patch("hermes_cli.cron.cron_tick") as mock_tick:
            cli_mod.HermesCLI._handle_cron_command(stub, "/cron tick")

        mock_tick.assert_called_once_with()
