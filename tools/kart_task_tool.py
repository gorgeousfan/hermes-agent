"""
kart_task_tool.py — Hermes tool adapter for the Willow Kart task queue.

Exposes Willow 2.0's Postgres task queue (public.tasks) as a Hermes-compatible tool.
Agents submit shell work; Kart worker (core/kart_worker.py) executes in bwrap sandbox.

Registration (in your Hermes config or tools/__init__.py):
    from tools.kart_task_tool import register_kart_tool
    register_kart_tool(registry)

Requirements:
    - Willow 2.0 with Postgres (pg_bridge bootstrap)
    - WILLOW_PG_DB env var (default: willow_20)
    - WILLOW_PG_USER env var (default: $USER)
    - Kart worker running (dashboard daemon or core/kart_worker.py)

b17: HKT2W
ΔΣ=42
"""

import json
import os
import random
import string
from typing import Optional

try:
    import psycopg2
    import psycopg2.extras

    _PG_AVAILABLE = True
except ImportError:
    _PG_AVAILABLE = False

_pg_conn = None

_ALPHABET = string.ascii_uppercase + string.digits
_ALPHABET = "".join(c for c in _ALPHABET if c not in "IO")  # base32-ish, no ambiguous chars


def _get_pg():
    global _pg_conn
    try:
        if _pg_conn is None or _pg_conn.closed:
            _pg_conn = psycopg2.connect(
                dbname=os.environ.get("WILLOW_PG_DB", "willow_20"),
                user=os.environ.get("WILLOW_PG_USER", os.environ.get("USER", "")),
            )
            _pg_conn.autocommit = True
        _pg_conn.cursor().execute("SELECT 1")
        return _pg_conn
    except Exception:
        _pg_conn = None
        return None


def check_kart_requirements() -> bool:
    if not _PG_AVAILABLE:
        return False
    pg = _get_pg()
    if not pg:
        return False
    try:
        cur = pg.cursor()
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'tasks'"
        )
        ok = cur.fetchone() is not None
        cur.close()
        return ok
    except Exception:
        return False


def _new_task_id() -> str:
    return "".join(random.choices(_ALPHABET, k=8))


def kart_task_tool(
    action: str,
    task: Optional[str] = None,
    task_id: Optional[str] = None,
    agent: str = "kart",
    submitted_by: str = "hermes",
    limit: int = 10,
    **kwargs,
) -> str:
    """
    Interact with the Willow Kart task queue (public.tasks).

    Actions:
      submit  — queue a task for sandboxed execution. Returns task id.
      status  — check status/result by id.
      list    — list pending tasks for an agent.

    Task format (for submit): full shell command string, e.g.
      cd /path && pytest tests/test_foo.py -q
    Optional first line: # allow_net  (Kart worker honors network in bwrap)
    """
    pg = _get_pg()
    if not pg:
        return json.dumps({"error": "Willow Postgres not available"})

    cur = pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if action == "submit":
        if not task:
            cur.close()
            return json.dumps({"error": "task is required for submit"})
        tid = _new_task_id()
        cur.execute(
            "INSERT INTO tasks (id, task, submitted_by, agent, status) "
            "VALUES (%s, %s, %s, %s, 'pending')",
            (tid, task, submitted_by, agent),
        )
        cur.close()
        return json.dumps({
            "task_id": tid,
            "status": "pending",
            "message": (
                f"Task queued on public.tasks. "
                f"Poll with action=status task_id={tid} (requires Kart worker)."
            ),
        })

    if action == "status":
        if not task_id:
            cur.close()
            return json.dumps({"error": "task_id is required for status"})
        cur.execute(
            "SELECT id, status, result, task, agent, submitted_by, created_at, updated_at "
            "FROM tasks WHERE id = %s",
            (task_id,),
        )
        row = cur.fetchone()
        cur.close()
        if not row:
            return json.dumps({"error": "task not found", "task_id": task_id})
        out = dict(row)
        for k in ("created_at", "updated_at"):
            if out.get(k) is not None:
                out[k] = str(out[k])
        if out.get("result") is not None and not isinstance(out["result"], (dict, list)):
            try:
                out["result"] = json.loads(out["result"])
            except Exception:
                pass
        return json.dumps(out, default=str)

    if action == "list":
        cur.execute(
            "SELECT id, task, submitted_by, created_at, status "
            "FROM tasks WHERE agent = %s AND status = 'pending' "
            "ORDER BY created_at ASC LIMIT %s",
            (agent, limit),
        )
        rows = cur.fetchall()
        cur.close()
        return json.dumps({
            "tasks": [
                {
                    "task_id": r["id"],
                    "task": str(r["task"])[:120],
                    "submitted_by": r["submitted_by"],
                    "created_at": str(r["created_at"]),
                    "status": r["status"],
                }
                for r in rows
            ],
            "count": len(rows),
        })

    cur.close()
    return json.dumps({"error": f"unknown action: {action}. Use submit|status|list"})


_SCHEMA = {
    "name": "kart_task",
    "description": (
        "Submit tasks to the Willow 2.0 Kart queue (public.tasks) for bwrap-sandboxed execution. "
        "Use action=submit to queue work, action=status to poll results, action=list for pending rows."
    ),
    "parameters": {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": ["submit", "status", "list"],
                "description": "submit a task, check status, or list pending tasks",
            },
            "task": {
                "type": "string",
                "description": (
                    "Full shell command (required for submit). "
                    "Optional first line '# allow_net' for network in sandbox."
                ),
            },
            "task_id": {
                "type": "string",
                "description": "Task id from submit (required for status).",
            },
            "agent": {
                "type": "string",
                "default": "kart",
                "description": "Target worker agent.",
            },
            "limit": {
                "type": "integer",
                "default": 10,
                "description": "Max tasks to return for list.",
            },
        },
    },
}


def register_kart_tool(registry) -> None:
    """Register the Kart tool with a Hermes tool registry."""
    registry.register(
        name="kart_task",
        toolset="willow",
        emoji="⚙️",
        handler=lambda **kwargs: kart_task_tool(**kwargs),
        schema=_SCHEMA,
        check_requirements=check_kart_requirements,
    )
