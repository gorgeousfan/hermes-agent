"""Proof that the bug exists without the fix.

This test verifies the ORIGINAL broken behavior: module-level constants
CRON_DIR, JOBS_FILE, OUTPUT_DIR are frozen at import time and do NOT
reflect profile changes made via get_hermes_home().

Run this test on upstream/main (before the fix) to see it FAIL.
Run this test on the fix branch to see it PASS (because the module-level
constants still exist for backward compat, but internal code now uses
the _resolve_* helpers).

However, the KEY difference: on the fix branch, even though the module-level
constants are frozen, load_jobs() and save_jobs() use the _resolve_* helpers,
so they correctly read/write from the active profile.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def fake_homes(tmp_path):
    default_home = tmp_path / "default" / ".hermes"
    profile_home = tmp_path / "profile" / ".hermes"
    default_home.mkdir(parents=True)
    profile_home.mkdir(parents=True)
    return default_home, profile_home


class TestModuleConstantsFrozenAtImport:
    """Module-level constants are frozen at import time (by design for compat).

    This test documents that behavior — the module-level CRON_DIR/JOBS_FILE
    reflect the FIRST home resolved at import time, NOT the current profile.
    """

    def test_module_constants_are_frozen(self, fake_homes):
        default_home, profile_home = fake_homes

        # Import the module-level constants
        from cron.jobs import CRON_DIR, JOBS_FILE, OUTPUT_DIR

        # They reflect whatever get_hermes_home() returned at import time,
        # which is the REAL home on this machine — NOT the default_home fixture.
        # This test documents that they do NOT change with profile:
        original_cron_dir = CRON_DIR

        # Even if we mock get_hermes_home to return something different,
        # the module-level constants are already computed
        with patch("cron.jobs.get_hermes_home", return_value=profile_home):
            # CRON_DIR is still the original value
            assert CRON_DIR == original_cron_dir
            # But _resolve_cron_dir() returns the profile home
            from cron.jobs import _resolve_cron_dir
            assert _resolve_cron_dir() == profile_home / "cron"
            assert _resolve_cron_dir() != CRON_DIR


class TestFailWithoutFix:
    """Demonstrate that WITHOUT the fix, load_jobs would use frozen paths.

    On upstream/main, load_jobs() directly uses JOBS_FILE (module-level constant).
    On the fix branch, load_jobs() uses _resolve_jobs_file() which re-evaluates.

    This test shows the fix works by verifying that load_jobs correctly
    reads from the CURRENT profile's jobs.json, not the import-time one.
    """

    def test_load_jobs_uses_dynamic_path(self, fake_homes):
        default_home, profile_home = fake_homes

        # Write different jobs to each profile's directory
        cron_dir_default = default_home / "cron"
        cron_dir_profile = profile_home / "cron"
        cron_dir_default.mkdir(parents=True)
        cron_dir_profile.mkdir(parents=True)

        (cron_dir_default / "jobs.json").write_text(
            json.dumps({"jobs": [{"id": "frozen", "name": "import-time-job"}]}),
            encoding="utf-8"
        )
        (cron_dir_profile / "jobs.json").write_text(
            json.dumps({"jobs": [{"id": "dynamic", "name": "runtime-profile-job"}]}),
            encoding="utf-8"
        )

        from cron.jobs import load_jobs

        # Load with profile home — should get the profile job, not default
        with patch("cron.jobs.get_hermes_home", return_value=profile_home):
            jobs = load_jobs()

        # With the fix: we get "runtime-profile-job" because load_jobs uses
        # _resolve_jobs_file() which calls get_hermes_home() dynamically.
        # WITHOUT the fix: we'd get "import-time-job" because load_jobs uses
        # the frozen JOBS_FILE constant.
        assert len(jobs) == 1
        assert jobs[0]["name"] == "runtime-profile-job", (
            "Bug #25295: load_jobs() is using import-time frozen path instead of "
            "the current profile path. The _resolve_jobs_file() helper should be used."
        )
