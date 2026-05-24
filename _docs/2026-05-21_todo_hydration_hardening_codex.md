# Todo Hydration Hardening

Date: 2026-05-21
Branch: codex/hermes-security-review-20260521

## Summary

Hardened `AIAgent._hydrate_todo_store()` so persisted todo state is restored
only from a tool result that is paired with an earlier assistant `todo` tool
call. This narrows session-history hydration to canonical tool-call history
instead of accepting any tool-shaped message that happens to contain a
`todos` array.

The todo store now also caps restored and newly written todo data:

- maximum todo items: 100
- maximum todo id length: 128 characters
- maximum todo content length: 1000 characters
- maximum todo hydration result payload: 128000 characters

## Verification

Commands run:

```powershell
$env:UV_PROJECT_ENVIRONMENT = Join-Path $env:TEMP 'hermes-agent-codex-test-env'
uv run --extra dev python -m pytest tests\run_agent\test_run_agent.py::TestHydrateTodoStore tests\tools\test_todo_tool.py -q --timeout-method=thread
uv run --extra dev python -m pytest tests\tools\test_read_loop_detection.py::TestTodoInjectionFiltering -q --timeout-method=thread
uv run --extra dev python -m compileall run_agent.py tools\todo_tool.py
git diff --check
```

Results:

- `19 passed` for hydrate and todo-tool tests.
- `4 passed` for todo injection filtering.
- `compileall` completed successfully.
- `git diff --check` reported no whitespace errors.

Note: the checkout-local `.venv` did not have `pytest`, and `uv run` against
that `.venv` could not replace a locked `hermes.exe`. Verification therefore
used `UV_PROJECT_ENVIRONMENT` under `%TEMP%`.
