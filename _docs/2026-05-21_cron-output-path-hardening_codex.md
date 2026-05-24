# Cron Output Path Hardening

Date: 2026-05-21
Branch: codex/hermes-cron-path-hardening-20260521

## Summary

Hardened cron job update and output handling so dashboard/API updates cannot
move a job's immutable `id` into a filesystem path, and output writes/deletes
require a single safe output directory component.

Changes:

- `cron.jobs.update_job()` rejects immutable `id` updates.
- `cron.jobs.save_job_output()` resolves output directories through a shared
  helper that rejects absolute paths, parent traversal, and nested components.
- `cron.jobs.remove_job()` uses the same helper before deleting output
  directories.
- Dashboard cron update/delete endpoints convert these validation failures into
  HTTP 400 responses.

## Verification

Commands run:

```powershell
$env:UV_PROJECT_ENVIRONMENT = Join-Path $env:TEMP 'hermes-agent-codex-test-env'
uv run --extra dev python -m pytest tests\cron\test_jobs.py::TestJobCRUD::test_remove_job_rejects_unsafe_legacy_id_before_output_cleanup tests\cron\test_jobs.py::TestUpdateJob::test_update_rejects_id_change tests\cron\test_jobs.py::TestSaveJobOutput -q --timeout-method=thread
uv run --extra dev --extra web python -m pytest tests\hermes_cli\test_web_server_cron_profiles.py::test_update_cron_job_rejects_id_mutation -q --timeout-method=thread
uv run --extra dev python -m pytest tests\cron\test_jobs.py -q --timeout-method=thread
uv run --extra dev --extra web python -m pytest tests\hermes_cli\test_web_server_cron_profiles.py -q --timeout-method=thread
uv run --extra dev python -m compileall cron\jobs.py hermes_cli\web_server.py
git diff --check
```

Results:

- Focused cron core tests: `7 passed`.
- Focused dashboard cron update test: `1 passed`.
- `tests\cron\test_jobs.py`: `85 passed`.
- `tests\hermes_cli\test_web_server_cron_profiles.py`: `7 passed`.
- `compileall` completed successfully.
- `git diff --check` reported no whitespace errors.

Note: the checkout-local `.venv` still lacks `pytest`; verification used a
temporary `UV_PROJECT_ENVIRONMENT`.
