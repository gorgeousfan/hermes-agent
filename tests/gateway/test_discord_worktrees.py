import os
import subprocess
from pathlib import Path

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.session import SessionSource
from gateway import discord_worktrees as wt
from tools.session_cwd import reset_session_cwd, set_session_cwd


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def _config(repo: Path) -> GatewayConfig:
    return GatewayConfig(
        platforms={
            Platform.DISCORD: PlatformConfig(
                enabled=True,
                extra={"thread_worktrees": True, "worktree_repo": str(repo)},
            )
        }
    )


def _source(thread_id: str | None = "1234567890") -> SessionSource:
    return SessionSource(
        platform=Platform.DISCORD,
        chat_id="channel-1",
        chat_type="thread" if thread_id else "channel",
        thread_id=thread_id,
        user_id="user-1",
    )


def test_ensure_thread_worktree_creates_and_reuses(tmp_path):
    repo = _make_repo(tmp_path)
    config = _config(repo)
    source = _source("thread.42")

    first = wt.ensure_thread_worktree(config, source, create=True)
    assert first is not None
    assert first.created is True
    assert Path(first.path).is_dir()
    assert first.branch == "hermes/discord-thread.42"
    assert _git(Path(first.path), "branch", "--show-current").stdout.strip() == first.branch
    assert not (repo / ".gitignore").exists()
    assert ".worktrees/" in (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")

    second = wt.ensure_thread_worktree(config, source, create=True)
    assert second is not None
    assert second.path == first.path
    assert second.created is False


def test_ensure_thread_worktree_ignores_non_threads(tmp_path):
    repo = _make_repo(tmp_path)
    assert wt.ensure_thread_worktree(_config(repo), _source(None), create=True) is None


def test_session_cwd_context_overrides_process_env(monkeypatch, tmp_path):
    from tools.file_tools import _resolve_path

    env_cwd = tmp_path / "env"
    session_cwd = tmp_path / "session"
    env_cwd.mkdir()
    session_cwd.mkdir()
    monkeypatch.setenv("TERMINAL_CWD", str(env_cwd))

    token = set_session_cwd(str(session_cwd))
    try:
        assert _resolve_path("note.txt") == (session_cwd / "note.txt").resolve()
    finally:
        reset_session_cwd(token)

    assert _resolve_path("note.txt") == (env_cwd / "note.txt").resolve()


def test_worktree_commit_all_commits_changes(tmp_path):
    repo = _make_repo(tmp_path)
    config = _config(repo)
    source = _source("commit-thread")
    info = wt.ensure_thread_worktree(config, source, create=True)
    assert info is not None
    Path(info.path, "file.txt").write_text("change\n", encoding="utf-8")

    result = wt.commit_all(config, source, "thread change")
    assert "Committed" in result
    assert "hermes/discord-commit-thread" in result
    log = _git(Path(info.path), "log", "--oneline", "-1").stdout
    assert "thread change" in log
