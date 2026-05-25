#!/usr/bin/env python3
"""
Cursor Agent tool — delegate focused coding work to Composer via Cursor SDK.

Requires optional install: uv pip install 'hermes-agent[cursor]'
and CURSOR_API_KEY from https://cursor.com/dashboard/integrations
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

from tools.registry import registry

logger = logging.getLogger(__name__)

CURSOR_AGENT_SCHEMA: Dict[str, Any] = {
    "name": "cursor_agent",
    "description": (
        "Run a Cursor IDE agent (Composer) on a repository directory for focused "
        "coding tasks. Returns a structured summary only — use for implementation "
        "work, refactors, or test fixes. Requires CURSOR_API_KEY and the optional "
        "cursor-sdk package. Prefer this over delegate_task when you want Cursor's "
        "native editing agent rather than Hermes subagents."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "What the Cursor agent should accomplish.",
            },
            "context": {
                "type": "string",
                "description": "Optional extra context (constraints, file paths, patterns).",
            },
            "cwd": {
                "type": "string",
                "description": (
                    "Repository/working directory for the Cursor agent. "
                    "Defaults to the current process working directory."
                ),
            },
            "model": {
                "type": "string",
                "description": 'Cursor model id (default: "composer-2.5").',
                "default": "composer-2.5",
            },
            "resume_agent_id": {
                "type": "string",
                "description": (
                    "Resume a previous Cursor agent by id for multi-step work "
                    "within the same parent turn."
                ),
            },
            "cloud_repo_url": {
                "type": "string",
                "description": (
                    "Optional Git repo URL for a Cursor cloud agent instead of local cwd."
                ),
            },
            "cloud_starting_ref": {
                "type": "string",
                "description": "Git ref for cloud_repo_url (e.g. main).",
            },
        },
        "required": ["goal"],
    },
}


def check_cursor_agent_requirements() -> bool:
    """Tool is available when API key is set and cursor-sdk can be loaded."""
    if not os.environ.get("CURSOR_API_KEY", "").strip():
        return False
    try:
        from agent.cursor_sdk_client import cursor_sdk_available

        return cursor_sdk_available()
    except Exception:
        return False


def cursor_agent(
    goal: str,
    context: Optional[str] = None,
    cwd: Optional[str] = None,
    model: str = "composer-2.5",
    resume_agent_id: Optional[str] = None,
    cloud_repo_url: Optional[str] = None,
    cloud_starting_ref: Optional[str] = None,
) -> str:
    """Invoke Cursor SDK and return JSON string."""
    try:
        from agent.cursor_sdk_client import run_cursor_agent

        payload = run_cursor_agent(
            goal=goal,
            context=context,
            cwd=cwd,
            model=model,
            resume_agent_id=resume_agent_id,
            cloud_repo_url=cloud_repo_url,
            cloud_starting_ref=cloud_starting_ref,
        )
        return json.dumps(payload, ensure_ascii=False)
    except Exception as exc:
        logger.exception("cursor_agent failed")
        return json.dumps({"status": "error", "error": str(exc), "error_type": "internal"})


def _handle_cursor_agent(args: Dict[str, Any], **kwargs) -> str:
    del kwargs
    return cursor_agent(
        goal=args.get("goal", ""),
        context=args.get("context"),
        cwd=args.get("cwd"),
        model=args.get("model", "composer-2.5"),
        resume_agent_id=args.get("resume_agent_id"),
        cloud_repo_url=args.get("cloud_repo_url"),
        cloud_starting_ref=args.get("cloud_starting_ref"),
    )


registry.register(
    name="cursor_agent",
    toolset="cursor",
    schema=CURSOR_AGENT_SCHEMA,
    handler=_handle_cursor_agent,
    check_fn=check_cursor_agent_requirements,
    requires_env=["CURSOR_API_KEY"],
)
