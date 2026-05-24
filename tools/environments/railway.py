"""Railway terminal-backend provider; wraps ``railway ssh``."""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import threading
from dataclasses import dataclass
from typing import Any

from tools.environments.base import BaseEnvironment, _ThreadedProcessHandle

logger = logging.getLogger("hermes.tools.environments.railway")

RAILWAY_VOLUME_DEFAULT_MOUNT = "/data"

RAILWAY_FAILURE_CATEGORIES = (
    "auth",
    "network",
    "deploy_missing",
    "volume_missing",
    "cli_missing",
    "invalid_config",
    "cancelled",
)

RAILWAY_RETRY_POLICY = {
    "base_seconds": 0.25,
    "max_attempts": 4,
    "only_for": ("network",),
    "max_total_seconds": 4.0,
}


@dataclass
class RailwayFailure(ValueError):
    """Structured cause for an expected RailwayEnvironment failure.

    Subclasses ValueError so callers in tools/terminal_tool.py treat
    invalid_config as a regular configuration error.
    """
    category: str
    retryable: bool
    dependency: str = "railway-cli"
    correlation_id: str = ""
    hint: str = ""

    def __post_init__(self) -> None:
        if self.category not in RAILWAY_FAILURE_CATEGORIES:
            raise ValueError(f"unknown RailwayFailure category: {self.category}")
        super().__init__(f"RailwayFailure({self.category}): {self.hint}")


def _resolve(name: str, value: Any) -> str:
    """Pull the live value: explicit > env-var fallback."""
    if value:
        return str(value)
    return os.environ.get(name, "")


class RailwayEnvironment(BaseEnvironment):
    """Run commands inside a Railway service container via ``railway ssh``."""

    def __init__(
        self,
        *,
        project_id: str = "",
        service_id: str = "",
        environment_id: str = "",
        deployment_instance_id: str = "",
        identity_file: str = "",
        cwd: str = RAILWAY_VOLUME_DEFAULT_MOUNT,
        timeout: int = 60,
        env: dict | None = None,
    ):
        super().__init__(cwd=cwd or RAILWAY_VOLUME_DEFAULT_MOUNT,
                          timeout=timeout, env=env or {})
        self.project_id = _resolve("RAILWAY_PROJECT_ID", project_id)
        self.service_id = _resolve("RAILWAY_SERVICE_ID", service_id)
        self.environment_id = _resolve("RAILWAY_ENVIRONMENT_ID", environment_id)
        self.deployment_instance_id = deployment_instance_id or ""
        self.identity_file = identity_file or os.environ.get(
            "RAILWAY_IDENTITY_FILE", "")
        self._validate_required()
        self._persistent_session: Any = None
        self._lock = threading.Lock()
        self.init_session()

    def _validate_required(self) -> None:
        missing = [k for k in ("project_id", "service_id", "environment_id")
                   if not getattr(self, k)]
        if missing:
            raise RailwayFailure(
                category="invalid_config", retryable=False,
                hint=(f"Railway terminal needs {', '.join(missing)}; "
                      f"set RAILWAY_{'/'.join(m.upper() for m in missing)} "
                      "or pass via terminal.railway.* config."),
            )

    def get_temp_dir(self) -> str:
        return "/tmp"

    def _build_ssh_argv(self, cmd_string: str) -> list[str]:
        argv = ["railway", "ssh",
                "--project", self.project_id,
                "--service", self.service_id,
                "--environment", self.environment_id]
        if self.deployment_instance_id:
            argv += ["--deployment-instance", self.deployment_instance_id]
        if self.identity_file:
            argv += ["-i", self.identity_file]
        argv += ["--", "bash", "-c", cmd_string]
        return argv

    def _run_railway_ssh(self, cmd_string: str, *, timeout: int) -> dict:
        argv = self._build_ssh_argv(cmd_string)
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout if isinstance(exc.stdout, str) else ""
            err = exc.stderr if isinstance(exc.stderr, str) else ""
            return {"output": out + err, "returncode": 124}
        except FileNotFoundError as exc:
            raise RailwayFailure(category="cli_missing", retryable=False,
                                  hint=str(exc)) from exc
        return {
            "output": (proc.stdout or "") + (proc.stderr or ""),
            "returncode": proc.returncode,
        }

    def _run_bash(self, cmd_string: str, *, login: bool = False,
                   timeout: int = 120, stdin_data: str | None = None):
        # execute() overrides the base flow; _run_bash kept for parity but
        # routed through the same _ThreadedProcessHandle as Modal/Daytona.
        def exec_fn() -> tuple[str, int]:
            r = self._run_railway_ssh(cmd_string, timeout=timeout)
            return (r["output"], r["returncode"])
        return _ThreadedProcessHandle(exec_fn, cancel_fn=self.cleanup)

    def execute(self, command: str, cwd: str = "", *,
                 timeout: int | None = None,
                 stdin_data: str | None = None) -> dict:
        self._before_execute()
        effective_cwd = cwd or self.cwd
        wrapped = self._wrap_command(command, effective_cwd)
        if stdin_data is not None:
            wrapped = self._embed_stdin_heredoc(wrapped, stdin_data)
        result = self._run_railway_ssh(wrapped,
                                          timeout=timeout or self.timeout)
        self._update_cwd(result)
        return result

    def upload_file(self, host_path: str, remote_path: str) -> None:
        with open(host_path, "r", encoding="utf-8", errors="replace") as fp:
            text = fp.read()
        cmd = (
            "tee " + shlex.quote(remote_path) + " > /dev/null <<'__HX__'\n"
            + text + "\n__HX__"
        )
        result = self._run_railway_ssh(cmd, timeout=self.timeout)
        if result["returncode"] != 0:
            raise RailwayFailure(category="network", retryable=True,
                                  hint=result["output"][:200])

    def download_file(self, remote_path: str, host_path: str) -> None:
        cmd = "cat " + shlex.quote(remote_path)
        result = self._run_railway_ssh(cmd, timeout=self.timeout)
        if result["returncode"] != 0:
            raise RailwayFailure(category="network", retryable=True,
                                  hint=result["output"][:200])
        with open(host_path, "w", encoding="utf-8") as fp:
            fp.write(result["output"])

    def cleanup(self) -> None:
        with self._lock:
            self._persistent_session = None
