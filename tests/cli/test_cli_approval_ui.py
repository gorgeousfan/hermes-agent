import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cli as cli_module
from cli import HermesCLI


class _FakeBuffer:
    def __init__(self, text="", cursor_position=None):
        self.text = text
        self.cursor_position = len(text) if cursor_position is None else cursor_position

    def reset(self, append_to_history=False):
        self.text = ""
        self.cursor_position = 0


def _make_cli_stub():
    cli = HermesCLI.__new__(HermesCLI)
    cli._approval_state = None
    cli._approval_deadline = 0
    cli._approval_lock = threading.Lock()
    cli._sudo_state = None
    cli._sudo_deadline = 0
    cli._modal_input_snapshot = None
    cli._invalidate = MagicMock()
    cli._app = SimpleNamespace(invalidate=MagicMock(), current_buffer=_FakeBuffer())
    return cli


def _make_background_cli_stub():
    cli = _make_cli_stub()
    cli._background_task_counter = 0
    cli._background_tasks = {}
    cli._ensure_runtime_credentials = MagicMock(return_value=True)
    cli._resolve_turn_agent_config = MagicMock(return_value={
        "model": "test-model",
        "runtime": {
            "api_key": "test-key",
            "base_url": "https://example.test/v1",
            "provider": "test",
            "api_mode": "chat_completions",
        },
        "request_overrides": None,
    })
    cli.max_turns = 90
    cli.enabled_toolsets = []
    cli._session_db = None
    cli.reasoning_config = {}
    cli.service_tier = None
    cli._providers_only = None
    cli._providers_ignore = None
    cli._providers_order = None
    cli._provider_sort = None
    cli._provider_require_params = None
    cli._provider_data_collection = None
    cli._openrouter_min_coding_score = None
    cli._fallback_model = None
    cli._agent_running = False
    cli._spinner_text = ""
    cli.bell_on_complete = False
    cli.final_response_markdown = "strip"
    return cli


class TestCliApprovalUi:
    def test_sudo_prompt_restores_existing_draft_after_response(self):
        cli = _make_cli_stub()
        cli._app.current_buffer = _FakeBuffer("draft command", cursor_position=5)
        result = {}

        def _run_callback():
            result["value"] = cli._sudo_password_callback()

        with patch.object(cli_module, "_cprint"):
            thread = threading.Thread(target=_run_callback, daemon=True)
            thread.start()

            deadline = time.time() + 2
            while cli._sudo_state is None and time.time() < deadline:
                time.sleep(0.01)

            assert cli._sudo_state is not None
            assert cli._app.current_buffer.text == ""

            cli._app.current_buffer.text = "secret"
            cli._app.current_buffer.cursor_position = len("secret")
            cli._sudo_state["response_queue"].put("secret")

            thread.join(timeout=2)

        assert result["value"] == "secret"
        assert cli._app.current_buffer.text == "draft command"
        assert cli._app.current_buffer.cursor_position == 5

    def test_approval_callback_includes_view_for_long_commands(self):
        cli = _make_cli_stub()
        command = "sudo dd if=/tmp/githubcli-keyring.gpg of=/usr/share/keyrings/githubcli-archive-keyring.gpg bs=4M status=progress"
        result = {}

        def _run_callback():
            result["value"] = cli._approval_callback(command, "disk copy")

        thread = threading.Thread(target=_run_callback, daemon=True)
        thread.start()

        deadline = time.time() + 2
        while cli._approval_state is None and time.time() < deadline:
            time.sleep(0.01)

        assert cli._approval_state is not None
        assert "view" in cli._approval_state["choices"]

        cli._approval_state["response_queue"].put("deny")
        thread.join(timeout=2)
        assert result["value"] == "deny"

    def test_handle_approval_selection_view_expands_in_place(self):
        cli = _make_cli_stub()
        cli._approval_state = {
            "command": "sudo dd if=/tmp/in of=/usr/share/keyrings/githubcli-archive-keyring.gpg bs=4M status=progress",
            "description": "disk copy",
            "choices": ["once", "session", "always", "deny", "view"],
            "selected": 4,
            "response_queue": queue.Queue(),
        }

        cli._handle_approval_selection()

        assert cli._approval_state is not None
        assert cli._approval_state["show_full"] is True
        assert "view" not in cli._approval_state["choices"]
        assert cli._approval_state["selected"] == 3
        assert cli._approval_state["response_queue"].empty()

    def test_approval_display_places_title_inside_box_not_border(self):
        cli = _make_cli_stub()
        cli._approval_state = {
            "command": "sudo dd if=/tmp/in of=/usr/share/keyrings/githubcli-archive-keyring.gpg bs=4M status=progress",
            "description": "disk copy",
            "choices": ["once", "session", "always", "deny", "view"],
            "selected": 0,
            "response_queue": queue.Queue(),
        }

        fragments = cli._get_approval_display_fragments()
        rendered = "".join(text for _style, text in fragments)
        lines = rendered.splitlines()

        assert lines[0].startswith("╭")
        assert "Dangerous Command" not in lines[0]
        assert any("Dangerous Command" in line for line in lines[1:3])
        assert "Show full command" in rendered
        assert "githubcli-archive-keyring.gpg" not in rendered

    def test_approval_display_shows_full_command_after_view(self):
        cli = _make_cli_stub()
        full_command = "sudo dd if=/tmp/in of=/usr/share/keyrings/githubcli-archive-keyring.gpg bs=4M status=progress"
        cli._approval_state = {
            "command": full_command,
            "description": "disk copy",
            "choices": ["once", "session", "always", "deny"],
            "selected": 0,
            "show_full": True,
            "response_queue": queue.Queue(),
        }

        fragments = cli._get_approval_display_fragments()
        rendered = "".join(text for _style, text in fragments)

        assert "..." not in rendered
        assert "githubcli-" in rendered
        assert "archive-" in rendered
        assert "keyring.gpg" in rendered
        assert "status=progress" in rendered

    def test_sudo_prompt_marshals_buffer_reset_to_loop_thread(self):
        """Regression for #25707: buffer mutations from a worker thread can be
        silently dropped by some terminals (WSL2 + VSCode Windows Terminal), so
        ``_sudo_password_callback`` must route snapshot capture + state setup
        through the prompt_toolkit event-loop thread via ``call_soon_threadsafe``.
        """

        cli = _make_cli_stub()

        # _FakeLoop captures call_soon_threadsafe callbacks so the test can
        # drive them on a chosen "loop thread", mirroring what prompt_toolkit
        # does in production.
        scheduled: list = []
        scheduled_lock = threading.Lock()
        mutation_thread_ids: list = []

        class _FakeLoop:
            def is_running(self):
                return True

            def call_soon_threadsafe(self, cb, *args):
                with scheduled_lock:
                    scheduled.append((cb, args))

        class _TrackingBuffer(_FakeBuffer):
            def reset(self, append_to_history=False):
                mutation_thread_ids.append(threading.get_ident())
                super().reset(append_to_history=append_to_history)

        cli._app = SimpleNamespace(
            invalidate=MagicMock(),
            current_buffer=_TrackingBuffer("draft command", cursor_position=5),
            loop=_FakeLoop(),
        )
        loop_thread_id = threading.get_ident()

        result = {}

        def _run_callback():
            result["value"] = cli._sudo_password_callback()

        with patch.object(cli_module, "_cprint"):
            worker = threading.Thread(target=_run_callback, daemon=True)
            worker.start()

            # Setup must NOT have happened on the worker yet — the worker is
            # blocked waiting on the scheduled callback to run on the loop
            # thread.  Poll the schedule queue instead of the state field.
            deadline = time.time() + 2
            while not scheduled and time.time() < deadline:
                time.sleep(0.005)
            assert scheduled, "setup was not scheduled via call_soon_threadsafe"
            assert cli._sudo_state is None, (
                "_sudo_state was assigned on the worker thread before the loop "
                "ran the scheduled setup — the marshalling regressed."
            )
            assert cli._app.current_buffer.text == "draft command", (
                "Buffer was reset on the worker thread before the loop ran the "
                "scheduled setup — the marshalling regressed."
            )

            # Drive the loop on this (test main) thread.
            with scheduled_lock:
                setup_cb, _args = scheduled.pop(0)
            setup_cb()

            # After the loop ran setup, the modal state and reset buffer are
            # both visible to the worker.
            assert cli._sudo_state is not None
            assert cli._app.current_buffer.text == ""
            assert mutation_thread_ids == [loop_thread_id], (
                "buf.reset() must execute on the loop thread, not the worker"
            )

            # Worker is now waiting on the response_queue.  Deliver password.
            cli._app.current_buffer.text = "secret"
            cli._app.current_buffer.cursor_position = len("secret")
            cli._sudo_state["response_queue"].put("secret")

            # Teardown is scheduled via the same call_soon_threadsafe path.
            deadline = time.time() + 2
            while not scheduled and time.time() < deadline:
                time.sleep(0.005)
            assert scheduled, "teardown was not scheduled via call_soon_threadsafe"
            with scheduled_lock:
                teardown_cb, _args = scheduled.pop(0)
            teardown_cb()

            worker.join(timeout=2)

        assert result["value"] == "secret"
        assert cli._sudo_state is None
        assert cli._app.current_buffer.text == "draft command"
        assert cli._app.current_buffer.cursor_position == 5

    def test_run_on_loop_thread_runs_inline_without_app(self):
        """Fallback path: callers without a live prompt_toolkit app (tests,
        shutdown, pre-init) still get ``_run_on_loop_thread`` to execute the
        callable inline instead of blocking indefinitely on an Event."""

        cli = _make_cli_stub()
        cli._app = None
        ran = []

        cli._run_on_loop_thread(lambda: ran.append(threading.get_ident()))

        assert ran == [threading.get_ident()]

    def test_run_on_loop_thread_runs_inline_when_loop_not_running(self):
        """If the app exists but its loop is not running, fall back to inline
        execution rather than scheduling a callback that nothing will drive."""

        cli = _make_cli_stub()

        class _IdleLoop:
            def is_running(self):
                return False

            def call_soon_threadsafe(self, cb, *args):  # pragma: no cover
                raise AssertionError("must not schedule when loop is idle")

        cli._app = SimpleNamespace(
            invalidate=MagicMock(),
            current_buffer=_FakeBuffer(),
            loop=_IdleLoop(),
        )
        ran = []

        cli._run_on_loop_thread(lambda: ran.append(True))

        assert ran == [True]

    def test_approval_display_preserves_command_and_choices_with_long_description(self):
        """Regression: long tirith descriptions used to push approve/deny off-screen.

        The panel must always render the command and every choice, even when
        the description would otherwise wrap into 10+ lines. The description
        gets truncated with a marker instead.
        """
        cli = _make_cli_stub()
        long_desc = (
            "Security scan — [CRITICAL] Destructive shell command with wildcard expansion: "
            "The command performs a recursive deletion of log files which may contain "
            "audit information relevant to active incident investigations, running services "
            "that rely on log files for state, rotated archives, and other system artifacts. "
            "Review whether this is intended before approving. Consider whether a targeted "
            "deletion with more specific filters would better match the intent."
        )
        cli._approval_state = {
            "command": "rm -rf /var/log/apache2/*.log",
            "description": long_desc,
            "choices": ["once", "session", "always", "deny"],
            "selected": 0,
            "response_queue": queue.Queue(),
        }

        # Simulate a compact terminal where the old unbounded panel would overflow.
        import shutil as _shutil

        with patch("cli.shutil.get_terminal_size",
                   return_value=_shutil.os.terminal_size((100, 20))):
            fragments = cli._get_approval_display_fragments()

        rendered = "".join(text for _style, text in fragments)

        # Command must be fully visible (rm -rf /var/log/apache2/*.log is short).
        assert "rm -rf /var/log/apache2/*.log" in rendered

        # Every choice must render — this is the core bug: approve/deny were
        # getting clipped off the bottom of the panel.
        assert "Allow once" in rendered
        assert "Allow for this session" in rendered
        assert "Add to permanent allowlist" in rendered
        assert "Deny" in rendered

        # The bottom border must render (i.e. the panel is self-contained).
        assert rendered.rstrip().endswith("╯")

        # The description gets truncated — marker should appear.
        assert "(description truncated)" in rendered

    def test_approval_display_skips_description_on_very_short_terminal(self):
        """On a 12-row terminal, only the command and choices have room.

        The description is dropped entirely rather than partially shown, so the
        choices never get clipped.
        """
        cli = _make_cli_stub()
        cli._approval_state = {
            "command": "rm -rf /var/log/apache2/*.log",
            "description": "recursive delete",
            "choices": ["once", "session", "always", "deny"],
            "selected": 0,
            "response_queue": queue.Queue(),
        }

        import shutil as _shutil

        with patch("cli.shutil.get_terminal_size",
                   return_value=_shutil.os.terminal_size((100, 12))):
            fragments = cli._get_approval_display_fragments()

        rendered = "".join(text for _style, text in fragments)

        # Command visible.
        assert "rm -rf /var/log/apache2/*.log" in rendered
        # All four choices visible.
        for label in ("Allow once", "Allow for this session",
                      "Add to permanent allowlist", "Deny"):
            assert label in rendered, f"choice {label!r} missing"

    def test_approval_display_truncates_giant_command_in_view_mode(self):
        """If the user hits /view on a massive command, choices still render.

        The command gets truncated with a marker; the description gets dropped
        if there's no remaining row budget.
        """
        cli = _make_cli_stub()
        # 50 lines of command when wrapped at ~64 chars.
        giant_cmd = "bash -c 'echo " + ("x" * 3000) + "'"
        cli._approval_state = {
            "command": giant_cmd,
            "description": "shell command via -c/-lc flag",
            "choices": ["once", "session", "always", "deny"],
            "selected": 0,
            "show_full": True,
            "response_queue": queue.Queue(),
        }

        import shutil as _shutil

        with patch("cli.shutil.get_terminal_size",
                   return_value=_shutil.os.terminal_size((100, 24))):
            fragments = cli._get_approval_display_fragments()

        rendered = "".join(text for _style, text in fragments)

        # All four choices visible even with a huge command.
        for label in ("Allow once", "Allow for this session",
                      "Add to permanent allowlist", "Deny"):
            assert label in rendered, f"choice {label!r} missing"

        # Command got truncated with a marker.
        assert "(command truncated" in rendered

    def test_background_task_registers_thread_local_approval_callbacks(self):
        """Background /btw tasks must use the prompt_toolkit approval UI.

        The foreground chat path registers dangerous-command callbacks inside
        its worker thread because tools.terminal_tool stores them in
        threading.local(). /background used to skip that, so dangerous commands
        fell back to raw input() in a background thread and timed out under
        prompt_toolkit.
        """
        cli = _make_background_cli_stub()
        seen = {}

        class FakeAgent:
            def __init__(self, **kwargs):
                self._print_fn = None
                self.thinking_callback = None

            def run_conversation(self, **kwargs):
                from tools.terminal_tool import (
                    _get_approval_callback,
                    _get_sudo_password_callback,
                )

                seen["approval"] = _get_approval_callback()
                seen["sudo"] = _get_sudo_password_callback()
                return {
                    "final_response": "done",
                    "messages": [],
                    "completed": True,
                    "failed": False,
                }

        with patch.object(cli_module, "AIAgent", FakeAgent), \
             patch.object(cli_module, "_cprint"), \
             patch.object(cli_module, "ChatConsole") as chat_console:
            chat_console.return_value.print = MagicMock()
            cli._handle_background_command("/btw check weather")

            deadline = time.time() + 2
            while cli._background_tasks and time.time() < deadline:
                time.sleep(0.01)

        assert seen["approval"].__self__ is cli
        assert seen["approval"].__func__ is HermesCLI._approval_callback
        assert seen["sudo"].__self__ is cli
        assert seen["sudo"].__func__ is HermesCLI._sudo_password_callback
        assert not cli._background_tasks


class TestApprovalCallbackThreadLocalWiring:
    """Regression guard for the thread-local callback freeze (#13617 / #13618).

    After 62348cff made _approval_callback / _sudo_password_callback thread-local
    (ACP GHSA-qg5c-hvr5-hjgr), the CLI agent thread could no longer see callbacks
    registered in the main thread — the dangerous-command prompt silently fell
    back to stdin input() and deadlocked against prompt_toolkit. The fix is to
    register the callbacks INSIDE the agent worker thread (matching the ACP
    pattern). These tests lock in that invariant.
    """

    def test_main_thread_registration_is_invisible_to_child_thread(self):
        """Confirms the underlying threading.local semantics that drove the bug.

        If this ever starts passing as "visible", the thread-local isolation
        is gone and the ACP race GHSA-qg5c-hvr5-hjgr may be back.
        """
        from tools.terminal_tool import (
            set_approval_callback,
            _get_approval_callback,
        )

        def main_cb(_cmd, _desc):
            return "once"

        set_approval_callback(main_cb)
        try:
            seen = {}

            def _child():
                seen["value"] = _get_approval_callback()

            t = threading.Thread(target=_child, daemon=True)
            t.start()
            t.join(timeout=2)
            assert seen["value"] is None
        finally:
            set_approval_callback(None)

    def test_child_thread_registration_is_visible_and_cleared_in_finally(self):
        """The fix pattern: register INSIDE the worker thread, clear in finally.

        This is exactly what cli.py's run_agent() closure does. If this test
        fails, the CLI approval prompt freeze (#13617) has regressed.
        """
        from tools.terminal_tool import (
            set_approval_callback,
            set_sudo_password_callback,
            _get_approval_callback,
            _get_sudo_password_callback,
        )

        def approval_cb(_cmd, _desc):
            return "once"

        def sudo_cb():
            return "hunter2"

        seen = {}

        def _worker():
            # Mimic cli.py's run_agent() thread target.
            set_approval_callback(approval_cb)
            set_sudo_password_callback(sudo_cb)
            try:
                seen["approval"] = _get_approval_callback()
                seen["sudo"] = _get_sudo_password_callback()
            finally:
                set_approval_callback(None)
                set_sudo_password_callback(None)
                seen["approval_after"] = _get_approval_callback()
                seen["sudo_after"] = _get_sudo_password_callback()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=2)

        assert seen["approval"] is approval_cb
        assert seen["sudo"] is sudo_cb
        # Finally block must clear both slots — otherwise a reused thread
        # would hold a stale reference to a disposed CLI instance.
        assert seen["approval_after"] is None
        assert seen["sudo_after"] is None
