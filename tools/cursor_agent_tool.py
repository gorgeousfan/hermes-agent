"""Cursor Agent CLI tool with stream-json progress reporting.

This tool wraps Cursor's headless CLI (`agent` / `cursor-agent`) in
`--output-format stream-json` mode, parses JSONL progress events while the
process is still running, and relays periodic summaries through Hermes' tool
progress callback when available (gateway/TUI/CLI surfaces).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shlex
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from tools.registry import registry

logger = logging.getLogger(__name__)

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_DEFAULT_PROGRESS_INTERVAL_SECONDS = 45.0
_DEFAULT_MAX_CHARS = 1200
_DEFAULT_TIMEOUT_SECONDS = 60 * 60 * 2  # Cursor coding runs can be long.


_TEXT_KEYS = (
    "message",
    "text",
    "content",
    "summary",
    "title",
    "description",
    "delta",
    "output",
)

_FINAL_MARKERS = (
    "final",
    "result",
    "done",
    "complete",
    "completed",
    "assistant_message",
)

_ERROR_MARKERS = ("error", "failed", "failure", "exception")


def check_cursor_requirements() -> bool:
    return bool(_resolve_cursor_command())


def _resolve_cursor_command(explicit: str | None = None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    env = os.getenv("HERMES_CURSOR_AGENT_COMMAND", "").strip()
    if env:
        return env
    return shutil.which("cursor-agent") or shutil.which("agent")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def parse_cursor_stream_line(line: str) -> dict[str, Any] | None:
    """Parse one Cursor stream-json line.

    Cursor's CLI normally emits one JSON object per line, but versions may mix
    ANSI/plain-text diagnostics into stdout/stderr. We strip ANSI and return a
    raw-text wrapper for non-empty non-JSON lines instead of failing the run.
    """

    clean = _strip_ansi(line).strip()
    if not clean:
        return None
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, dict):
            return parsed
        return {"raw": parsed}
    except json.JSONDecodeError:
        return {"raw": clean}


def _first_text(value: Any, *, max_depth: int = 4) -> str:
    if value is None or max_depth <= 0:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_first_text(item, max_depth=max_depth - 1) for item in value[:4]]
        return " ".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in _TEXT_KEYS:
            if key in value:
                text = _first_text(value.get(key), max_depth=max_depth - 1)
                if text:
                    return text
        # Common nested event payloads.
        for key in ("data", "event", "item", "chunk", "payload", "toolCall"):
            if key in value:
                text = _first_text(value.get(key), max_depth=max_depth - 1)
                if text:
                    return text
    return ""


def _event_label(event: dict[str, Any]) -> str:
    for key in ("type", "event", "kind", "name", "status"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "raw" if "raw" in event else "event"


def summarize_cursor_event(event: dict[str, Any]) -> str | None:
    """Return a compact human-readable summary for a Cursor JSON event."""

    label = _event_label(event)
    lower = label.lower()

    if "raw" in event:
        text = _first_text(event.get("raw"))
        return text or None

    # Prefer structured tool / command hints when present.
    command = _first_text(event.get("command") or event.get("cmd"))
    path = _first_text(event.get("path") or event.get("file") or event.get("filename"))
    tool = _first_text(
        event.get("tool")
        or event.get("toolName")
        or event.get("tool_name")
        or event.get("name")
    )

    text = _first_text(event)
    if command:
        return f"{label}: `{command[:180]}`"
    if path and any(token in lower for token in ("file", "edit", "write", "patch")):
        return f"{label}: {path}"
    if tool and tool != label and any(token in lower for token in ("tool", "call", "action")):
        return f"{label}: {tool}"
    if text:
        text = " ".join(text.split())
        if len(text) > 220:
            text = text[:217] + "..."
        return f"{label}: {text}" if label and label != text else text
    return label if label != "event" else None


def _is_final_event(event: dict[str, Any]) -> bool:
    label = _event_label(event).lower()
    if any(marker == label or marker in label for marker in _FINAL_MARKERS):
        return True
    status = str(event.get("status") or "").lower()
    return status in {"done", "complete", "completed", "success"}


def _is_error_event(event: dict[str, Any]) -> bool:
    label = _event_label(event).lower()
    status = str(event.get("status") or "").lower()
    return any(marker in label for marker in _ERROR_MARKERS) or status in {"error", "failed", "failure"}


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)] + "..."


def _format_progress_message(events: Iterable[str], *, max_chars: int) -> str:
    lines = [event.strip() for event in events if event and event.strip()]
    if not lines:
        return "Cursor Agent is still running..."
    body = "\n".join(f"• {line}" for line in lines[-6:])
    return _truncate(f"Cursor Agent progress:\n{body}", max_chars)


def _emit_progress(
    progress_callback: Optional[Callable[..., Any]],
    message: str,
    *,
    status: str = "running",
) -> None:
    if not progress_callback or not message:
        return
    try:
        progress_callback(
            "tool.progress",
            "cursor_agent",
            message,
            None,
            status=status,
        )
    except Exception as exc:
        logger.debug("Cursor progress callback failed: %s", exc)


def run_cursor_agent(
    *,
    prompt: str,
    workspace: str | None = None,
    model: str | None = None,
    command: str | None = None,
    force: bool = True,
    trust: bool = True,
    stream_partial_output: bool = False,
    progress_interval_seconds: float | int | None = None,
    progress_max_chars: int | None = None,
    timeout_seconds: float | int | None = None,
    progress_callback: Optional[Callable[..., Any]] = None,
    task_id: str | None = None,
) -> str:
    """Run Cursor Agent in stream-json mode and return a JSON result string."""

    del task_id  # reserved for future per-task process bookkeeping

    if not isinstance(prompt, str) or not prompt.strip():
        return json.dumps({"success": False, "error": "prompt is required"}, ensure_ascii=False)

    cursor_cmd = _resolve_cursor_command(command)
    if not cursor_cmd:
        return json.dumps(
            {
                "success": False,
                "error": "Cursor Agent CLI not found. Install it or set HERMES_CURSOR_AGENT_COMMAND.",
            },
            ensure_ascii=False,
        )

    cwd = Path(workspace or os.getcwd()).expanduser().resolve()
    if not cwd.exists() or not cwd.is_dir():
        return json.dumps(
            {"success": False, "error": f"workspace does not exist or is not a directory: {cwd}"},
            ensure_ascii=False,
        )

    interval = float(progress_interval_seconds or _DEFAULT_PROGRESS_INTERVAL_SECONDS)
    max_chars = int(progress_max_chars or _DEFAULT_MAX_CHARS)
    timeout = float(timeout_seconds or _DEFAULT_TIMEOUT_SECONDS)

    args = [
        cursor_cmd,
        "-p",
        "--output-format",
        "stream-json",
        "--workspace",
        str(cwd),
    ]
    if force:
        args.append("--force")
    if trust:
        args.append("--trust")
    if stream_partial_output:
        args.append("--stream-partial-output")
    if model:
        args.extend(["--model", str(model)])
    args.append(prompt)

    _emit_progress(
        progress_callback,
        f"Cursor Agent started in `{cwd}` with stream-json output.",
        status="started",
    )

    started = time.monotonic()
    event_queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
    stdout_tail: deque[str] = deque(maxlen=80)
    stderr_tail: deque[str] = deque(maxlen=80)
    progress_tail: deque[str] = deque(maxlen=40)
    final_chunks: list[str] = []
    error_events: list[str] = []

    try:
        proc = subprocess.Popen(
            args,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=os.environ.copy(),
        )
    except FileNotFoundError as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)

    def _reader(stream, source: str) -> None:
        if stream is None:
            return
        for line in stream:
            parsed = parse_cursor_stream_line(line)
            clean = _strip_ansi(line).rstrip("\n")
            if source == "stdout":
                stdout_tail.append(clean)
            else:
                stderr_tail.append(clean)
            if parsed is not None:
                event_queue.put((source, parsed))

    threads = [
        threading.Thread(target=_reader, args=(proc.stdout, "stdout"), daemon=True),
        threading.Thread(target=_reader, args=(proc.stderr, "stderr"), daemon=True),
    ]
    for thread in threads:
        thread.start()

    last_emit = time.monotonic()
    last_activity = last_emit
    pending: list[str] = []
    timed_out = False

    try:
        while True:
            now = time.monotonic()
            if now - started > timeout:
                timed_out = True
                proc.terminate()
                break

            try:
                _source, event = event_queue.get(timeout=0.25)
                last_activity = time.monotonic()
            except queue.Empty:
                event = None

            if event is not None:
                summary = summarize_cursor_event(event)
                if summary:
                    progress_tail.append(summary)
                    pending.append(summary)
                if _is_final_event(event):
                    text = _first_text(event)
                    if text:
                        final_chunks.append(text)
                if _is_error_event(event):
                    text = summary or _first_text(event) or json.dumps(event, ensure_ascii=False)
                    error_events.append(text)

            now = time.monotonic()
            should_emit = pending and (now - last_emit >= interval)
            if should_emit:
                _emit_progress(progress_callback, _format_progress_message(pending, max_chars=max_chars))
                pending.clear()
                last_emit = now
            elif not pending and now - last_emit >= max(interval * 2, 120):
                idle_for = int(now - last_activity)
                _emit_progress(
                    progress_callback,
                    f"Cursor Agent is still running; no new event for {idle_for}s.",
                )
                last_emit = now

            if proc.poll() is not None:
                # Drain any remaining queued events before leaving.
                while True:
                    try:
                        _source, event = event_queue.get_nowait()
                    except queue.Empty:
                        break
                    summary = summarize_cursor_event(event)
                    if summary:
                        progress_tail.append(summary)
                        pending.append(summary)
                    if _is_final_event(event):
                        text = _first_text(event)
                        if text:
                            final_chunks.append(text)
                    if _is_error_event(event):
                        error_events.append(summary or json.dumps(event, ensure_ascii=False))
                break
    finally:
        if timed_out and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    return_code = proc.wait(timeout=5) if proc.poll() is None else proc.returncode
    duration = round(time.monotonic() - started, 2)

    if pending:
        _emit_progress(progress_callback, _format_progress_message(pending, max_chars=max_chars))

    final_output = "\n".join(chunk for chunk in final_chunks if chunk).strip()
    if not final_output:
        # Fall back to stdout tail for Cursor versions whose final stream event
        # does not use a recognizable type marker.
        final_output = "\n".join(line for line in stdout_tail if line).strip()
    final_output = _truncate(final_output, 12000)

    # A Cursor run may emit intermediate error/test-failure events and still
    # recover before exiting 0, so the process exit/timeout determines success;
    # captured error events remain available in the result for diagnostics.
    success = (return_code == 0) and not timed_out
    status = "timeout" if timed_out else ("completed" if return_code == 0 else "error")
    _emit_progress(
        progress_callback,
        f"Cursor Agent {status} after {duration}s (exit {return_code}).",
        status=status,
    )

    result = {
        "success": success,
        "exit_code": return_code,
        "timed_out": timed_out,
        "duration_seconds": duration,
        "command": " ".join(shlex.quote(part) for part in args[:-1]) + " <prompt>",
        "workspace": str(cwd),
        "final_output": final_output,
        "progress_tail": list(progress_tail)[-20:],
        "errors": error_events[-10:],
        "stderr_tail": list(stderr_tail)[-20:],
    }
    return json.dumps(result, ensure_ascii=False)


CURSOR_AGENT_SCHEMA = {
    "name": "cursor_agent",
    "description": (
        "Delegate heavy coding work to Cursor Agent CLI in headless stream-json mode. "
        "Hermes starts Cursor as a subprocess, polls JSONL progress, and reports periodic progress "
        "instead of staying silent during long coding runs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Detailed coding task for Cursor Agent.",
            },
            "workspace": {
                "type": "string",
                "description": "Project root. Defaults to the current working directory.",
            },
            "model": {
                "type": "string",
                "description": "Optional Cursor model hint, e.g. gpt-5 or sonnet-4.",
            },
            "force": {
                "type": "boolean",
                "description": "Pass --force so Cursor may apply edits/commands in headless mode. Default true.",
            },
            "trust": {
                "type": "boolean",
                "description": "Pass --trust to avoid workspace trust prompts in automation. Default true.",
            },
            "stream_partial_output": {
                "type": "boolean",
                "description": "Pass --stream-partial-output for more granular stream-json deltas. Default false to reduce noise.",
            },
            "progress_interval_seconds": {
                "type": "number",
                "description": "Minimum seconds between progress updates. Default 45.",
            },
            "progress_max_chars": {
                "type": "integer",
                "description": "Max characters per progress update. Default 1200.",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "Hard wall-clock timeout. Default 7200 seconds.",
            },
            "command": {
                "type": "string",
                "description": "Optional Cursor Agent binary path. Defaults to HERMES_CURSOR_AGENT_COMMAND, cursor-agent, then agent.",
            },
        },
        "required": ["prompt"],
    },
}


registry.register(
    name="cursor_agent",
    toolset="cursor",
    schema=CURSOR_AGENT_SCHEMA,
    handler=lambda args, **kwargs: run_cursor_agent(
        prompt=args.get("prompt", ""),
        workspace=args.get("workspace"),
        model=args.get("model"),
        command=args.get("command"),
        force=args.get("force", True),
        trust=args.get("trust", True),
        stream_partial_output=args.get("stream_partial_output", False),
        progress_interval_seconds=args.get("progress_interval_seconds"),
        progress_max_chars=args.get("progress_max_chars"),
        timeout_seconds=args.get("timeout_seconds"),
        task_id=kwargs.get("task_id"),
    ),
    check_fn=check_cursor_requirements,
    description="Run Cursor Agent CLI with stream-json progress updates",
    emoji="🖱️",
)
