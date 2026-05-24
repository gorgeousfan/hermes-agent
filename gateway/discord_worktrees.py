"""Discord thread → git worktree routing helpers.

This is intentionally small and side-effect-light: callers opt in via config,
then ask for a worktree for a Discord thread/session.  The returned path is bound
through a ContextVar by gateway.run so terminal/file/code tools operate inside
that worktree without mutating process-global TERMINAL_CWD.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class ThreadWorktree:
    repo_root: str
    path: str
    branch: str
    created: bool = False


def _run_git(args: list[str], *, cwd: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _is_git_repo(path: str) -> bool:
    try:
        result = _run_git(["rev-parse", "--show-toplevel"], cwd=path)
        return result.returncode == 0
    except Exception:
        return False


def repo_root(path: str) -> Optional[str]:
    try:
        result = _run_git(["rev-parse", "--show-toplevel"], cwd=path)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    root = result.stdout.strip()
    return root or None


def _slug(value: str, *, max_len: int = 48) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip("-._")
    return (value or "thread")[:max_len]


def _config_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _discord_extra(config: Any) -> dict[str, Any]:
    try:
        from gateway.config import Platform
        platform_cfg = getattr(config, "platforms", {}).get(Platform.DISCORD)
        if platform_cfg is not None and isinstance(platform_cfg.extra, dict):
            return platform_cfg.extra
    except Exception:
        pass
    return {}


def is_enabled(config: Any) -> bool:
    extra = _discord_extra(config)
    env = os.getenv("DISCORD_THREAD_WORKTREES")
    if env:
        return _config_bool(env)
    return _config_bool(extra.get("thread_worktrees"), False)


def configured_repo(config: Any) -> Optional[str]:
    extra = _discord_extra(config)
    raw = (
        os.getenv("DISCORD_WORKTREE_REPO")
        or extra.get("worktree_repo")
        or extra.get("thread_worktree_repo")
    )
    if not raw:
        # Last-resort compatibility: use TERMINAL_CWD only if it is a git repo.
        raw = os.getenv("TERMINAL_CWD")
    if not raw:
        return None
    expanded = os.path.abspath(os.path.expanduser(str(raw)))
    return repo_root(expanded) if os.path.isdir(expanded) else None


def worktree_root(config: Any, repo: str) -> str:
    extra = _discord_extra(config)
    raw = os.getenv("DISCORD_WORKTREE_ROOT") or extra.get("worktree_root")
    if raw:
        return os.path.abspath(os.path.expanduser(str(raw)))
    return str(Path(repo) / ".worktrees" / "discord")


def _ensure_local_exclude(repo: str) -> None:
    """Hide the default in-repo worktree directory without editing tracked files."""
    entry = ".worktrees/"
    try:
        git_dir = _run_git(["rev-parse", "--git-dir"], cwd=repo).stdout.strip()
        if not git_dir:
            return
        git_path = Path(git_dir)
        if not git_path.is_absolute():
            git_path = Path(repo) / git_path
        exclude = git_path / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if entry not in existing.splitlines():
            with exclude.open("a", encoding="utf-8") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write(entry + "\n")
    except Exception:
        pass


def ensure_thread_worktree(config: Any, source: Any, *, create: bool = True) -> Optional[ThreadWorktree]:
    """Return or create the worktree for a Discord thread source.

    Only Discord threads are routed.  Plain channels remain on the normal cwd so
    one-off channel pings do not unexpectedly create branches.
    """
    try:
        from gateway.config import Platform
        if source.platform != Platform.DISCORD or not source.thread_id:
            return None
    except Exception:
        return None
    if not is_enabled(config):
        return None
    repo = configured_repo(config)
    if not repo:
        return None

    thread_slug = _slug(getattr(source, "thread_id", ""), max_len=32)
    wt_root = worktree_root(config, repo)
    wt_path = os.path.join(wt_root, thread_slug)
    branch = f"hermes/discord-{thread_slug}"

    if os.path.isdir(wt_path) and _is_git_repo(wt_path):
        return ThreadWorktree(repo_root=repo, path=wt_path, branch=branch, created=False)
    if not create:
        return None

    Path(wt_root).mkdir(parents=True, exist_ok=True)
    _ensure_local_exclude(repo)
    # If a stale directory exists but is not a git worktree, refuse to clobber it.
    if os.path.exists(wt_path):
        return None

    result = _run_git(["worktree", "add", "-b", branch, wt_path, "HEAD"], cwd=repo, timeout=120)
    if result.returncode != 0:
        # Branch may already exist after a crash/restart. Reuse it if possible.
        result = _run_git(["worktree", "add", wt_path, branch], cwd=repo, timeout=120)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "git worktree add failed").strip())
    return ThreadWorktree(repo_root=repo, path=wt_path, branch=branch, created=True)


def status_text(config: Any, source: Any) -> str:
    wt = ensure_thread_worktree(config, source, create=True)
    if wt is None:
        repo = configured_repo(config)
        return (
            "Worktree is not active for this Discord thread.\n"
            f"enabled={is_enabled(config)} repo={repo or '(not configured / not a git repo)'}"
        )
    status = _run_git(["status", "--short"], cwd=wt.path)
    branch = _run_git(["branch", "--show-current"], cwd=wt.path).stdout.strip() or wt.branch
    body = status.stdout.strip() or "clean"
    return f"Worktree: `{wt.path}`\nBranch: `{branch}`\nStatus:\n```\n{body}\n```"


def commit_all(config: Any, source: Any, message: str) -> str:
    wt = ensure_thread_worktree(config, source, create=True)
    if wt is None:
        return "No Discord thread worktree is available for this session."
    message = (message or "").strip()
    if not message:
        return "Usage: `/worktree commit <commit message>`"
    _run_git(["add", "-A"], cwd=wt.path)
    status = _run_git(["status", "--short"], cwd=wt.path).stdout.strip()
    if not status:
        return f"Nothing to commit.\n\n{status_text(config, source)}"
    result = _run_git(["commit", "-m", message], cwd=wt.path, timeout=120)
    if result.returncode != 0:
        return f"Commit failed:\n```\n{(result.stderr or result.stdout).strip()}\n```"
    sha = _run_git(["rev-parse", "--short", "HEAD"], cwd=wt.path).stdout.strip()
    return (
        f"Committed `{sha}` on `{wt.branch}`.\n"
        f"Worktree: `{wt.path}`\n\n"
        "Next if you want a PR:\n"
        f"```bash\ncd {wt.path}\ngit push -u origin {wt.branch}\ngh pr create --fill\n```"
    )


def _default_branch(repo: str) -> str:
    result = _run_git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], cwd=repo)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().split("/", 1)[-1]
    for candidate in ("main", "master"):
        result = _run_git(["rev-parse", "--verify", candidate], cwd=repo)
        if result.returncode == 0:
            return candidate
    return "HEAD~1"


def pr_summary(config: Any, source: Any) -> str:
    wt = ensure_thread_worktree(config, source, create=False)
    if wt is None:
        return "No Discord thread worktree is active."
    branch = _run_git(["branch", "--show-current"], cwd=wt.path).stdout.strip() or wt.branch
    base_branch = _default_branch(wt.repo_root)
    diff_range = f"{base_branch}...HEAD" if base_branch != "HEAD~1" else "HEAD~1..HEAD"
    diffstat = _run_git(["diff", "--stat", diff_range], cwd=wt.path).stdout.strip()
    log = _run_git(["log", "--oneline", "--decorate", "-5", diff_range], cwd=wt.path).stdout.strip()
    if not log:
        log = _run_git(["log", "--oneline", "--decorate", "-5"], cwd=wt.path).stdout.strip()
    return (
        f"Branch: `{branch}`\nBase: `{base_branch}`\nWorktree: `{wt.path}`\n\n"
        f"Recent commits:\n```\n{log or '(none)'}\n```\n"
        f"Diffstat:\n```\n{diffstat or '(no diffstat)'}\n```\n"
        "PR command:\n"
        f"```bash\ncd {wt.path}\ngit push -u origin {branch}\ngh pr create --base {base_branch} --fill\n```"
    )


def clean(config: Any, source: Any, *, force: bool = False) -> str:
    wt = ensure_thread_worktree(config, source, create=False)
    if wt is None:
        return "No Discord thread worktree is active."
    status = _run_git(["status", "--short"], cwd=wt.path).stdout.strip()
    if status and not force:
        return "Worktree has uncommitted changes. Use `/worktree clean --force` if you really want to remove it."
    result = _run_git(["worktree", "remove", wt.path, "--force"], cwd=wt.repo_root, timeout=120)
    if result.returncode != 0:
        return f"Failed to remove worktree:\n```\n{(result.stderr or result.stdout).strip()}\n```"
    # Best-effort branch cleanup only when forced/clean; ignore if checked out elsewhere.
    _run_git(["branch", "-D", wt.branch], cwd=wt.repo_root)
    if os.path.isdir(wt.path):
        shutil.rmtree(wt.path, ignore_errors=True)
    return f"Removed worktree `{wt.path}` and branch `{wt.branch}` if possible."
