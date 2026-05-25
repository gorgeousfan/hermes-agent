"""Thin wrapper around the optional Cursor Agent SDK (cursor-sdk).

Isolates beta API churn and optional dependency import failures from tool code.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "composer-2.5"
SUMMARY_INSTRUCTION = (
    "You are invoked as a coding sub-agent from Hermes Agent. "
    "Complete the task, then reply with a concise structured summary covering: "
    "(1) what you did, (2) files changed or created, (3) commands run, "
    "(4) test/verification status if any, (5) blockers or follow-ups. "
    "Do not ask the user questions — return the summary as your final message."
)


def cursor_sdk_available() -> bool:
    """Return True when cursor_sdk is importable (after lazy install if needed)."""
    try:
        import cursor_sdk  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        from tools.lazy_deps import ensure

        ensure("tools.cursor")
        import cursor_sdk  # noqa: F401
        return True
    except Exception as exc:
        logger.debug("cursor-sdk not available: %s", exc)
        return False


def _load_sdk():
    from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions

    try:
        from cursor_sdk import CloudAgentOptions
    except ImportError:
        CloudAgentOptions = None  # type: ignore[misc, assignment]
    return Agent, AgentOptions, CursorAgentError, LocalAgentOptions, CloudAgentOptions


def _resolve_api_key(explicit: Optional[str] = None) -> Optional[str]:
    key = (explicit or os.environ.get("CURSOR_API_KEY") or "").strip()
    return key or None


def _resolve_cwd(cwd: Optional[str]) -> str:
    if cwd and str(cwd).strip():
        path = Path(cwd).expanduser()
        if not path.is_absolute():
            path = Path(os.getcwd()) / path
        return str(path.resolve())
    return os.getcwd()


def build_cursor_prompt(goal: str, context: Optional[str] = None) -> str:
    """Compose the user message sent to the Cursor agent."""
    goal = (goal or "").strip()
    if not goal:
        return SUMMARY_INSTRUCTION
    parts = [SUMMARY_INSTRUCTION, "", "## Task", goal]
    if context and str(context).strip():
        parts.extend(["", "## Additional context", str(context).strip()])
    return "\n".join(parts)


def run_cursor_agent(
    *,
    goal: str,
    context: Optional[str] = None,
    cwd: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    api_key: Optional[str] = None,
    resume_agent_id: Optional[str] = None,
    cloud_repo_url: Optional[str] = None,
    cloud_starting_ref: Optional[str] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Run one Cursor SDK agent turn and return a Hermes-friendly result dict.

    Raises CursorAgentError when the run never starts (caller maps to tool error).
    """
    if not cursor_sdk_available():
        return {
            "status": "error",
            "error": (
                "cursor-sdk is not installed. Install with: "
                "uv pip install 'hermes-agent[cursor]'"
            ),
            "error_type": "dependency",
        }

    key = _resolve_api_key(api_key)
    if not key:
        return {
            "status": "error",
            "error": "CURSOR_API_KEY is not set",
            "error_type": "auth",
        }

    (
        Agent,
        AgentOptions,
        CursorAgentError,
        LocalAgentOptions,
        CloudAgentOptions,
    ) = _load_sdk()

    work_cwd = _resolve_cwd(cwd)
    prompt = build_cursor_prompt(goal, context)
    model_id = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL

    options_kwargs: Dict[str, Any] = {
        "api_key": key,
        "model": model_id,
    }

    use_cloud = bool(cloud_repo_url and str(cloud_repo_url).strip())
    if use_cloud and CloudAgentOptions is not None:
        repo: Dict[str, Any] = {"url": str(cloud_repo_url).strip()}
        if cloud_starting_ref and str(cloud_starting_ref).strip():
            repo["startingRef"] = str(cloud_starting_ref).strip()
        options_kwargs["cloud"] = CloudAgentOptions(repos=[repo])
    else:
        options_kwargs["local"] = LocalAgentOptions(cwd=work_cwd)

    try:
        if resume_agent_id and str(resume_agent_id).strip():
            agent_ctx = Agent.resume(
                str(resume_agent_id).strip(),
                AgentOptions(**options_kwargs),
            )
        else:
            agent_ctx = Agent.create(**options_kwargs)

        with agent_ctx as agent:
            run = agent.send(prompt)
            run_id = getattr(run, "id", None) or getattr(run, "run_id", None)

            if on_progress is not None:
                try:
                    for message in run.messages():
                        if getattr(message, "type", None) == "assistant":
                            msg = getattr(message, "message", message)
                            content = getattr(msg, "content", None) or []
                            for block in content:
                                if getattr(block, "type", None) == "text":
                                    text = getattr(block, "text", "") or ""
                                    if text:
                                        on_progress(text)
                except Exception as stream_exc:
                    logger.debug("cursor run.messages() failed: %s", stream_exc)

            result = run.wait()
            status = getattr(result, "status", None) or "unknown"
            agent_id = getattr(agent, "agent_id", None) or getattr(agent, "agentId", None)
            summary = getattr(result, "result", None) or getattr(result, "text", None) or ""

            if status == "error":
                return {
                    "status": "error",
                    "error": "Cursor agent run failed",
                    "error_type": "run",
                    "agent_id": agent_id,
                    "run_id": run_id,
                    "summary": str(summary) if summary else "",
                }

            return {
                "status": "finished",
                "agent_id": agent_id,
                "run_id": run_id,
                "model": model_id,
                "cwd": work_cwd,
                "cloud": use_cloud,
                "summary": str(summary) if summary else "",
            }
    except CursorAgentError as err:
        return {
            "status": "error",
            "error": str(err.message) if hasattr(err, "message") else str(err),
            "error_type": "startup",
            "retryable": bool(getattr(err, "is_retryable", False)),
        }


def probe_cursor_api_key(api_key: Optional[str] = None) -> Dict[str, Any]:
    """Lightweight connectivity probe for hermes doctor."""
    if not cursor_sdk_available():
        return {"ok": False, "detail": "cursor-sdk not installed"}
    key = _resolve_api_key(api_key)
    if not key:
        return {"ok": False, "detail": "CURSOR_API_KEY not set"}
    try:
        from cursor_sdk import Cursor

        Cursor.models.list(api_key=key)
        return {"ok": True, "detail": "models.list OK"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}
