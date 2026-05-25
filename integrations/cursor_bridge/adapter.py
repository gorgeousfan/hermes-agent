"""Map OpenAI Chat Completions payloads to Cursor SDK prompts and responses."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple


CHAT_ONLY_PREFIX = (
    "You are answering as a chat model behind an OpenAI-compatible API bridge. "
    "Respond concisely in plain language. Avoid broad repo refactors unless the "
    "user explicitly asks for code changes.\n\n"
)


def _extract_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: List[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("text", "input_text"):
                chunks.append(str(part.get("text", "")))
            elif part.get("type") == "image_url":
                chunks.append("[image omitted]")
        return "\n".join(c for c in chunks if c)
    return str(content)


def messages_to_prompt(messages: List[Dict[str, Any]], *, chat_only: bool = False) -> str:
    """Flatten OpenAI-style messages into one prompt for Agent.prompt()."""
    lines: List[str] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "user"))
        text = _extract_text(msg.get("content"))
        if not text.strip():
            continue
        lines.append(f"[{role}]\n{text.strip()}")
    body = "\n\n".join(lines) if lines else "Hello"
    if chat_only:
        return CHAT_ONLY_PREFIX + body
    return body


def build_completion_response(
    *,
    assistant_text: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> Dict[str, Any]:
    """Return an OpenAI chat.completion object."""
    total = prompt_tokens + completion_tokens
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": assistant_text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total,
        },
    }


def run_chat_completion(
    body: Dict[str, Any],
    *,
    default_model: str = "composer-2.5",
    default_cwd: Optional[str] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Execute one chat completion via Cursor SDK. Returns (http_status, json_body)."""
    from agent.cursor_sdk_client import (
        DEFAULT_MODEL,
        _resolve_api_key,
        _resolve_cwd,
        cursor_sdk_available,
    )

    if not cursor_sdk_available():
        return 503, {
            "error": {
                "message": "cursor-sdk not installed (pip install 'hermes-agent[cursor]')",
                "type": "server_error",
            }
        }

    api_key = _resolve_api_key(body.get("api_key") or os.environ.get("CURSOR_API_KEY"))
    if not api_key:
        return 401, {
            "error": {
                "message": "CURSOR_API_KEY required",
                "type": "authentication_error",
            }
        }

    messages = body.get("messages") or []
    if not isinstance(messages, list):
        return 400, {"error": {"message": "messages must be an array", "type": "invalid_request_error"}}

    model = str(body.get("model") or default_model or DEFAULT_MODEL).strip()
    cwd = _resolve_cwd(body.get("cwd") or default_cwd)
    chat_only = os.environ.get("CURSOR_BRIDGE_CHAT_ONLY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    prompt = messages_to_prompt(messages, chat_only=chat_only)

    try:
        from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions

        result = Agent.prompt(
            prompt,
            AgentOptions(
                api_key=api_key,
                model=model,
                local=LocalAgentOptions(cwd=cwd),
            ),
        )
        status = getattr(result, "status", None)
        text = getattr(result, "result", None) or getattr(result, "text", None) or ""
        if status == "error":
            return 502, {
                "error": {
                    "message": "Cursor agent run failed",
                    "type": "server_error",
                    "cursor_status": status,
                }
            }
        return 200, build_completion_response(
            assistant_text=str(text),
            model=model,
        )
    except CursorAgentError as err:
        msg = str(getattr(err, "message", err))
        return 502, {"error": {"message": msg, "type": "server_error"}}
    except Exception as exc:
        return 500, {"error": {"message": str(exc), "type": "server_error"}}


def models_list_payload(default_model: str = "composer-2.5") -> Dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": default_model,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "cursor",
            }
        ],
    }


def parse_json_body(raw: bytes) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not raw:
        return {}, None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, "JSON body must be an object"
    return data, None
