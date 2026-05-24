"""Tests for issue #26670 — concurrent hermes.exe detection and improved
quarantine retry / reboot-deferred fallback during `hermes update` on Windows.

These tests force ``_is_windows`` to return ``True`` via patching so the
Windows-specific code paths can be exercised on any host.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hermes_cli import main as cli_main


# Tests in this module either exercise the REAL _detect_concurrent_hermes_instances
# helper (and need the autouse stub in tests/hermes_cli/conftest.py disabled),
# or supply their own explicit return value via patch.object. Mark the whole
# module so the conftest fixture skips its default stub.
pytestmark = pytest.mark.real_concurrent_gate


# ---------------------------------------------------------------------------
# _detect_concurrent_hermes_instances
# ---------------------------------------------------------------------------


def _make_proc(pid: int, exe: str, name: str = "hermes.exe"):
    """Build a duck-typed psutil Process stand-in with the .info dict."""
    proc = MagicMock()
    proc.info = {"pid": pid, "exe": exe, "name": name}
    return proc


@patch.object(cli_main, "_is_windows", return_value=True)
def test_detect_concurrent_returns_empty_when_no_other_processes(_winp, tmp_path):
    scripts_dir = tmp_path
    (scripts_dir / "hermes.exe").write_bytes(b"")
    (scripts_dir / "hermes-gateway.exe").write_bytes(b"")

    fake_psutil = types.SimpleNamespace(process_iter=lambda attrs: iter([]))
    with patch.dict(sys.modules, {"psutil": fake_psutil}):
        result = cli_main._detect_concurrent_hermes_instances(scripts_dir)

    assert result == []


@patch.object(cli_main, "_is_windows", return_value=True)
def test_detect_concurrent_excludes_self_pid(_winp, tmp_path):
    scripts_dir = tmp_path
    shim = scripts_dir / "hermes.exe"
    shim.write_bytes(b"")
    my_pid = os.getpid()

    procs = [_make_proc(my_pid, str(shim), "hermes.exe")]
    fake_psutil = types.SimpleNamespace(process_iter=lambda attrs: iter(procs))
    with patch.dict(sys.modules, {"psutil": fake_psutil}):
        result = cli_main._detect_concurrent_hermes_instances(scripts_dir)

    assert result == []


@patch.object(cli_main, "_is_windows", return_value=True)
def test_detect_concurrent_finds_other_hermes_process(_winp, tmp_path):
    scripts_dir = tmp_path
    shim = scripts_dir / "hermes.exe"
    shim.write_bytes(b"")

    other_pid = os.getpid() + 1
    procs = [
        _make_proc(other_pid, str(shim), "hermes.exe"),
        _make_proc(os.getpid() + 2, r"C:\\Windows\\System32\\notepad.exe", "notepad.exe"),
    ]
    fake_psutil = types.SimpleNamespace(process_iter=lambda attrs: iter(procs))
    with patch.dict(sys.modules, {"psutil": fake_psutil}):
        result = cli_main._detect_concurrent_hermes_instances(scripts_dir)

    assert result == [(other_pid, "hermes.exe")]


@patch.object(cli_main, "_is_windows", return_value=True)
def test_detect_concurrent_matches_case_insensitively(_winp, tmp_path):
    scripts_dir = tmp_path
    shim = scripts_dir / "hermes.exe"
    shim.write_bytes(b"")

    # Simulate the desktop spawning hermes.EXE (uppercase ext) from same path
    upper = str(shim).replace("hermes.exe", "HERMES.EXE")
    procs = [_make_proc(9999, upper, "HERMES.EXE")]
    fake_psutil = types.SimpleNamespace(process_iter=lambda attrs: iter(procs))
    with patch.dict(sys.modules, {"psutil": fake_psutil}):
        result = cli_main._detect_concurrent_hermes_instances(scripts_dir)

    assert result == [(9999, "HERMES.EXE")]


@patch.object(cli_main, "_is_windows", return_value=True)
def test_detect_concurrent_no_psutil_returns_empty(_winp, tmp_path):
    scripts_dir = tmp_path
    (scripts_dir / "hermes.exe").write_bytes(b"")

    # Block psutil import — simulate environment without it.
    with patch.dict(sys.modules, {"psutil": None}):
        result = cli_main._detect_concurrent_hermes_instances(scripts_dir)

    assert result == []


@patch.object(cli_main, "_is_windows", return_value=False)
def test_detect_concurrent_is_noop_off_windows(_winp, tmp_path):
    """No process enumeration off-Windows; the file-lock issue is Windows-only."""
    assert cli_main._detect_concurrent_hermes_instances(tmp_path) == []


# ---------------------------------------------------------------------------
# Launcher-shim ancestor exclusion (issue #29341)
# ---------------------------------------------------------------------------
#
# On Windows ``hermes`` is a distlib-generated ``Scripts\\hermes.exe`` console
# launcher that spawns a child ``python.exe`` and stays alive waiting on it.
# Detection runs inside the *child* (where ``os.getpid()`` lives), so the
# launcher PID is left in ``process_iter`` and gets reported as "another
# hermes.exe is running". The fix walks the parent chain and excludes every
# consecutive ancestor whose ``exe`` resolves to one of the shim paths.


def _fake_psutil_with_chain(
    procs, *, parent_chain, my_pid: int | None = None,
):
    """Build a psutil stand-in with both ``process_iter`` and ancestor walk.

    ``parent_chain`` is the list of ``(pid, exe)`` returned by
    ``Process().parent()...parent()`` starting from the current process.
    The first element is the immediate parent; an empty list means the
    current process has no parent (terminates the walk).
    """
    if my_pid is None:
        my_pid = os.getpid()

    class _FakeProc:
        def __init__(self, pid, exe):
            self.pid = pid
            self._exe = exe
            self._idx = -1  # index into parent_chain

        def exe(self):
            return self._exe

        def parent(self):
            nxt = self._idx + 1
            if nxt >= len(parent_chain):
                return None
            pid, exe = parent_chain[nxt]
            p = _FakeProc(pid, exe)
            p._idx = nxt
            return p

    def _Process(pid=None):
        return _FakeProc(my_pid, None)

    return types.SimpleNamespace(
        process_iter=lambda attrs: iter(procs),
        Process=_Process,
        Error=Exception,
        NoSuchProcess=Exception,
        AccessDenied=Exception,
    )


@patch.object(cli_main, "_is_windows", return_value=True)
def test_detect_concurrent_excludes_launcher_shim_parent(_winp, tmp_path):
    """The distlib ``hermes.exe`` launcher (parent of this Python) is excluded.

    Reproduces the exact scenario from #29341: PowerShell → hermes.exe (PID
    18608) → python.exe (current). Without the parent-chain walk, 18608
    would be flagged as a concurrent peer every time.
    """
    scripts_dir = tmp_path
    shim = scripts_dir / "hermes.exe"
    shim.write_bytes(b"")
    launcher_pid = os.getpid() + 1
    procs = [
        _make_proc(launcher_pid, str(shim), "hermes.exe"),
    ]
    fake = _fake_psutil_with_chain(
        procs,
        parent_chain=[(launcher_pid, str(shim))],
    )
    with patch.dict(sys.modules, {"psutil": fake}):
        result = cli_main._detect_concurrent_hermes_instances(scripts_dir)

    assert result == []


@patch.object(cli_main, "_is_windows", return_value=True)
def test_detect_concurrent_still_flags_unrelated_hermes_peer(_winp, tmp_path):
    """Excluding the launcher ancestor must NOT mask genuinely concurrent peers.

    A second ``hermes.exe`` somewhere else in the process table (e.g. Hermes
    Desktop's backend child, or a second terminal) is a sibling, not an
    ancestor — the walk must not exclude it.
    """
    scripts_dir = tmp_path
    shim = scripts_dir / "hermes.exe"
    shim.write_bytes(b"")
    launcher_pid = os.getpid() + 1
    real_peer_pid = os.getpid() + 2  # NOT in the parent chain
    procs = [
        _make_proc(launcher_pid, str(shim), "hermes.exe"),
        _make_proc(real_peer_pid, str(shim), "hermes.exe"),
    ]
    fake = _fake_psutil_with_chain(
        procs,
        parent_chain=[(launcher_pid, str(shim))],
    )
    with patch.dict(sys.modules, {"psutil": fake}):
        result = cli_main._detect_concurrent_hermes_instances(scripts_dir)

    assert result == [(real_peer_pid, "hermes.exe")]


@patch.object(cli_main, "_is_windows", return_value=True)
def test_detect_concurrent_walks_multiple_shim_ancestors(_winp, tmp_path):
    """Walk keeps climbing while ancestors are shims, stops at first non-shim.

    Some installer layouts double-wrap: ``hermes.exe`` (outer) →
    ``hermes-gateway.exe`` (inner, for some niche commands) → python.
    Both should be excluded; the shell ancestor above them must not be
    walked through (and isn't a shim anyway).
    """
    scripts_dir = tmp_path
    outer = scripts_dir / "hermes.exe"
    inner = scripts_dir / "hermes-gateway.exe"
    outer.write_bytes(b"")
    inner.write_bytes(b"")
    outer_pid = os.getpid() + 10
    inner_pid = os.getpid() + 11
    shell_pid = os.getpid() + 12
    procs = [
        _make_proc(outer_pid, str(outer), "hermes.exe"),
        _make_proc(inner_pid, str(inner), "hermes-gateway.exe"),
        _make_proc(shell_pid, r"C:\\Windows\\System32\\cmd.exe", "cmd.exe"),
    ]
    fake = _fake_psutil_with_chain(
        procs,
        parent_chain=[
            (inner_pid, str(inner)),
            (outer_pid, str(outer)),
            (shell_pid, r"C:\\Windows\\System32\\cmd.exe"),
        ],
    )
    with patch.dict(sys.modules, {"psutil": fake}):
        result = cli_main._detect_concurrent_hermes_instances(scripts_dir)

    assert result == []


@patch.object(cli_main, "_is_windows", return_value=True)
def test_detect_concurrent_walk_stops_at_first_non_shim_ancestor(_winp, tmp_path):
    """A non-shim ancestor terminates the walk so deeper shims stay visible.

    Concocted but important contract: if the immediate parent is a shell
    and there happens to be a ``hermes.exe`` further up the tree (e.g. a
    user wrapper script), we do NOT skip past the shell to exclude it.
    Anything beyond the first non-shim ancestor is treated as foreign.
    """
    scripts_dir = tmp_path
    shim = scripts_dir / "hermes.exe"
    shim.write_bytes(b"")
    shell_pid = os.getpid() + 20
    far_hermes_pid = os.getpid() + 21
    procs = [
        _make_proc(far_hermes_pid, str(shim), "hermes.exe"),
    ]
    fake = _fake_psutil_with_chain(
        procs,
        parent_chain=[
            (shell_pid, r"C:\\Windows\\System32\\cmd.exe"),
            (far_hermes_pid, str(shim)),
        ],
    )
    with patch.dict(sys.modules, {"psutil": fake}):
        result = cli_main._detect_concurrent_hermes_instances(scripts_dir)

    # The hermes.exe above the cmd.exe ancestor is NOT excluded — it's a
    # genuinely separate Hermes process from this invocation's POV.
    assert result == [(far_hermes_pid, "hermes.exe")]


@patch.object(cli_main, "_is_windows", return_value=True)
def test_detect_concurrent_walk_tolerates_psutil_errors(_winp, tmp_path):
    """An exception in the parent walk must not crash detection.

    psutil routinely raises ``AccessDenied`` / ``NoSuchProcess`` on the
    PID 0 / PID 4 ancestors on Windows. The walk should degrade
    gracefully — at worst the launcher gets reported, but the rest of
    the gate must keep working.
    """
    scripts_dir = tmp_path
    shim = scripts_dir / "hermes.exe"
    shim.write_bytes(b"")
    peer_pid = os.getpid() + 30

    class _ExplodingProc:
        pid = -1

        def parent(self):
            raise RuntimeError("simulated AccessDenied")

        def exe(self):
            raise RuntimeError("simulated AccessDenied")

    procs = [_make_proc(peer_pid, str(shim), "hermes.exe")]
    fake = types.SimpleNamespace(
        process_iter=lambda attrs: iter(procs),
        Process=lambda pid=None: _ExplodingProc(),
        Error=Exception,
    )
    with patch.dict(sys.modules, {"psutil": fake}):
        result = cli_main._detect_concurrent_hermes_instances(scripts_dir)

    # The peer is reported (no special launcher to exclude got found), and
    # nothing crashed.
    assert result == [(peer_pid, "hermes.exe")]


@patch.object(cli_main, "_is_windows", return_value=True)
def test_detect_concurrent_walk_is_bounded(_winp, tmp_path):
    """A pathological parent chain must not loop forever.

    Defensive: walk caps at 16 hops so a buggy psutil/proc table can't
    hang ``hermes update``.
    """
    scripts_dir = tmp_path
    shim = scripts_dir / "hermes.exe"
    shim.write_bytes(b"")

    # Build an infinite-looking chain of shim ancestors. The walk must
    # terminate on its own bound and not stack-overflow / hang.
    long_chain = [(os.getpid() + 100 + i, str(shim)) for i in range(64)]
    procs = [_make_proc(pid, exe, "hermes.exe") for pid, exe in long_chain]

    fake = _fake_psutil_with_chain(procs, parent_chain=long_chain)
    with patch.dict(sys.modules, {"psutil": fake}):
        result = cli_main._detect_concurrent_hermes_instances(scripts_dir)

    # First 16 ancestors are excluded; the remaining 48 are reported. The
    # exact split is implementation-detail-coupled to the bound, but the
    # invariant we care about is: detection terminated AND did not
    # exclude more than the bound.
    excluded = len(long_chain) - len(result)
    assert excluded <= 16 + 1  # +1 for os.getpid()
    assert len(result) >= len(long_chain) - 16


# ---------------------------------------------------------------------------
# _format_concurrent_instances_message
# ---------------------------------------------------------------------------


def test_format_message_mentions_pids_and_remediation(tmp_path):
    matches = [(1234, "hermes.exe"), (5678, "hermes.exe")]
    msg = cli_main._format_concurrent_instances_message(matches, tmp_path)

    assert "1234" in msg
    assert "5678" in msg
    assert "hermes.exe" in msg
    assert "Hermes Desktop" in msg
    assert "--force" in msg
    # Mentions the file that would have been overwritten
    assert str(tmp_path / "hermes.exe") in msg


# ---------------------------------------------------------------------------
# _quarantine_running_hermes_exe — retry + reboot-deferred fallback
# ---------------------------------------------------------------------------


@patch.object(cli_main, "_is_windows", return_value=True)
def test_quarantine_succeeds_first_attempt(_winp, tmp_path):
    """When the rename works immediately, no warning, single rename pair returned."""
    shim = tmp_path / "hermes.exe"
    shim.write_bytes(b"old")

    pairs = cli_main._quarantine_running_hermes_exe(tmp_path)

    assert len(pairs) == 1
    orig, quarantine = pairs[0]
    assert orig == shim
    assert quarantine.name.startswith("hermes.exe.old.")
    assert quarantine.exists()
    assert not shim.exists()


@patch.object(cli_main, "_is_windows", return_value=True)
def test_quarantine_retries_then_succeeds(_winp, tmp_path, monkeypatch):
    """A transient OSError on the first attempt should not be fatal."""
    shim = tmp_path / "hermes.exe"
    shim.write_bytes(b"old")

    original_rename = Path.rename
    call_count = {"n": 0}

    def flaky_rename(self, target):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError(32, "share violation (simulated AV scan)")
        return original_rename(self, target)

    # Speed up the test: avoid actual sleeps in the backoff schedule.
    monkeypatch.setattr(cli_main, "_hermes_exe_shims", lambda d: [shim])
    with patch.object(Path, "rename", flaky_rename), patch(
        "time.sleep", lambda *_a, **_k: None
    ):
        pairs = cli_main._quarantine_running_hermes_exe(tmp_path)

    assert call_count["n"] >= 2
    assert len(pairs) == 1
    assert not shim.exists()


@patch.object(cli_main, "_is_windows", return_value=True)
def test_quarantine_falls_back_to_reboot_schedule(_winp, tmp_path, capsys, monkeypatch):
    """When every retry fails, we schedule via MoveFileEx and warn helpfully."""
    shim = tmp_path / "hermes.exe"
    shim.write_bytes(b"locked")

    def always_fails(self, target):
        raise OSError(32, "The process cannot access the file (simulated lock)")

    scheduled_calls: list[tuple[Path, Path]] = []

    def fake_schedule(s: Path, q: Path) -> bool:
        scheduled_calls.append((s, q))
        return True

    monkeypatch.setattr(cli_main, "_hermes_exe_shims", lambda d: [shim])
    with patch.object(Path, "rename", always_fails), patch.object(
        cli_main, "_schedule_replace_on_reboot", fake_schedule
    ), patch("time.sleep", lambda *_a, **_k: None):
        pairs = cli_main._quarantine_running_hermes_exe(tmp_path)

    captured = capsys.readouterr().out

    # The reboot-deferred path was used.
    assert scheduled_calls and scheduled_calls[0][0] == shim
    # It is NOT added to the returned roll-back list (the issue calls this
    # out — don't undo a deferred operation).
    assert pairs == []
    # The user got a clear message, not raw [WinError 32].
    assert "scheduled" in captured.lower()
    assert "reboot" in captured.lower()


@patch.object(cli_main, "_is_windows", return_value=True)
def test_quarantine_actionable_warning_when_everything_fails(
    _winp, tmp_path, capsys, monkeypatch
):
    """When even MoveFileEx fails we should print remediation hints, not a bare error."""
    shim = tmp_path / "hermes.exe"
    shim.write_bytes(b"locked")

    def always_fails(self, target):
        raise OSError(32, "share violation")

    monkeypatch.setattr(cli_main, "_hermes_exe_shims", lambda d: [shim])
    with patch.object(Path, "rename", always_fails), patch.object(
        cli_main, "_schedule_replace_on_reboot", lambda *_a, **_k: False
    ), patch("time.sleep", lambda *_a, **_k: None):
        pairs = cli_main._quarantine_running_hermes_exe(tmp_path)

    captured = capsys.readouterr().out
    assert pairs == []
    # New message format: no raw "[WinError 32]" dump; instead names the cause
    # and tells the user what to do.
    assert "another process" in captured.lower()
    assert "Hermes Desktop" in captured or "gateway" in captured.lower()


# ---------------------------------------------------------------------------
# cmd_update integration — concurrent-instance gate
# ---------------------------------------------------------------------------


@patch.object(cli_main, "_is_windows", return_value=True)
def test_cmd_update_aborts_on_concurrent_instance(_winp, tmp_path, capsys):
    """If another hermes.exe is running, the update bails out before
    touching the working tree (exit code 2)."""
    scripts_dir = tmp_path / "Scripts"
    scripts_dir.mkdir()

    args = SimpleNamespace(
        check=False,
        gateway=False,
        yes=False,
        force=False,
        backup=False,
        no_backup=True,
    )

    with patch.object(
        cli_main, "_venv_scripts_dir", return_value=scripts_dir
    ), patch.object(
        cli_main,
        "_detect_concurrent_hermes_instances",
        return_value=[(4242, "hermes.exe")],
    ), patch.object(
        cli_main, "_run_pre_update_backup"
    ) as mock_backup, patch.object(
        cli_main, "_install_hangup_protection", return_value={}
    ), patch.object(
        cli_main, "_finalize_update_output"
    ):
        with pytest.raises(SystemExit) as excinfo:
            cli_main.cmd_update(args)

    assert excinfo.value.code == 2
    # The pre-update backup runs AFTER the concurrent check; should not have
    # been invoked.
    mock_backup.assert_not_called()

    captured = capsys.readouterr().out
    assert "4242" in captured
    assert "--force" in captured


@patch.object(cli_main, "_is_windows", return_value=True)
def test_cmd_update_force_bypasses_concurrent_check(_winp, tmp_path):
    """--force lets the update proceed past the concurrent-instance gate
    (subsequent steps are mocked so we only verify the gate is skipped)."""
    scripts_dir = tmp_path / "Scripts"
    scripts_dir.mkdir()

    args = SimpleNamespace(
        check=False,
        gateway=False,
        yes=False,
        force=True,  # ← the bypass
        backup=False,
        no_backup=True,
    )

    detect = MagicMock(return_value=[(9, "hermes.exe")])

    # Short-circuit out of _cmd_update_impl via a sentinel raise immediately
    # AFTER the gate. _run_pre_update_backup is the first call after the gate.
    sentinel = RuntimeError("reached post-gate body")
    with patch.object(
        cli_main, "_venv_scripts_dir", return_value=scripts_dir
    ), patch.object(
        cli_main, "_detect_concurrent_hermes_instances", detect
    ), patch.object(
        cli_main, "_run_pre_update_backup", side_effect=sentinel
    ), patch.object(
        cli_main, "_install_hangup_protection", return_value={}
    ), patch.object(
        cli_main, "_finalize_update_output"
    ):
        with pytest.raises(RuntimeError, match="reached post-gate body"):
            cli_main.cmd_update(args)

    # When --force is set, we should not have even consulted psutil.
    detect.assert_not_called()
