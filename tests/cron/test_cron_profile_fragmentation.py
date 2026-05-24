"""Regression tests for #25295: Cron profile fragmentation.

Verify that cron job paths resolve dynamically based on the active
Hermes profile instead of being frozen at module import time.

The bug: CRON_DIR, JOBS_FILE, and OUTPUT_DIR were module-level constants
computed once from get_hermes_home() at import time.  When the CLI uses
the default profile but the gateway uses a named profile, jobs created
via CLI wrote to a different directory than the gateway reads from.

The fix: internal code now uses _resolve_cron_dir() / _resolve_jobs_file()
/ _resolve_output_dir() helpers that re-evaluate get_hermes_home() on
every call, so profile changes are honored.
"""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_homes(tmp_path):
    """Create two fake Hermes home directories: default and a named profile."""
    default_home = tmp_path / "default" / ".hermes"
    profile_home = tmp_path / "profile" / ".hermes"
    default_home.mkdir(parents=True)
    profile_home.mkdir(parents=True)
    return default_home, profile_home


def _write_jobs_file(home: Path, jobs: list):
    """Write a jobs.json with the given jobs list under *home*/cron/."""
    cron_dir = home / "cron"
    cron_dir.mkdir(parents=True, exist_ok=True)
    jobs_file = cron_dir / "jobs.json"
    jobs_file.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
    return jobs_file


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDynamicPathResolution:
    """_resolve_* helpers must re-evaluate get_hermes_home() each call."""

    def test_resolve_cron_dir_uses_get_hermes_home(self, fake_homes):
        default_home, profile_home = fake_homes
        from cron.jobs import _resolve_cron_dir

        with patch("cron.jobs.get_hermes_home", return_value=default_home):
            assert _resolve_cron_dir() == default_home / "cron"

        with patch("cron.jobs.get_hermes_home", return_value=profile_home):
            assert _resolve_cron_dir() == profile_home / "cron"

    def test_resolve_jobs_file_uses_get_hermes_home(self, fake_homes):
        default_home, profile_home = fake_homes
        from cron.jobs import _resolve_jobs_file

        with patch("cron.jobs.get_hermes_home", return_value=default_home):
            assert _resolve_jobs_file() == default_home / "cron" / "jobs.json"

        with patch("cron.jobs.get_hermes_home", return_value=profile_home):
            assert _resolve_jobs_file() == profile_home / "cron" / "jobs.json"

    def test_resolve_output_dir_uses_get_hermes_home(self, fake_homes):
        default_home, profile_home = fake_homes
        from cron.jobs import _resolve_output_dir

        with patch("cron.jobs.get_hermes_home", return_value=default_home):
            assert _resolve_output_dir() == default_home / "cron" / "output"

        with patch("cron.jobs.get_hermes_home", return_value=profile_home):
            assert _resolve_output_dir() == profile_home / "cron" / "output"


class TestLoadJobsProfileSwitch:
    """load_jobs() must read from the *current* profile, not the import-time one."""

    def test_load_from_default_profile(self, fake_homes):
        default_home, profile_home = fake_homes
        _write_jobs_file(default_home, [{"id": "aaa", "name": "default-job"}])
        _write_jobs_file(profile_home, [{"id": "bbb", "name": "profile-job"}])

        from cron.jobs import load_jobs
        with patch("cron.jobs.get_hermes_home", return_value=default_home):
            jobs = load_jobs()
        assert len(jobs) == 1
        assert jobs[0]["name"] == "default-job"

    def test_load_from_named_profile(self, fake_homes):
        default_home, profile_home = fake_homes
        _write_jobs_file(default_home, [{"id": "aaa", "name": "default-job"}])
        _write_jobs_file(profile_home, [{"id": "bbb", "name": "profile-job"}])

        from cron.jobs import load_jobs
        with patch("cron.jobs.get_hermes_home", return_value=profile_home):
            jobs = load_jobs()
        assert len(jobs) == 1
        assert jobs[0]["name"] == "profile-job"

    def test_load_switches_profile_mid_session(self, fake_homes):
        """Simulate CLI creating under default, then gateway loading under profile."""
        default_home, profile_home = fake_homes
        _write_jobs_file(default_home, [{"id": "aaa", "name": "cli-job"}])
        _write_jobs_file(profile_home, [{"id": "bbb", "name": "gateway-job"}])

        from cron.jobs import load_jobs
        # First call as default profile (CLI)
        with patch("cron.jobs.get_hermes_home", return_value=default_home):
            jobs_cli = load_jobs()
        assert jobs_cli[0]["name"] == "cli-job"

        # Second call as named profile (gateway)
        with patch("cron.jobs.get_hermes_home", return_value=profile_home):
            jobs_gw = load_jobs()
        assert jobs_gw[0]["name"] == "gateway-job"


class TestSaveJobsProfileSwitch:
    """save_jobs() must write to the *current* profile directory."""

    def test_save_to_named_profile(self, fake_homes):
        default_home, profile_home = fake_homes
        # Ensure cron dirs exist
        (default_home / "cron").mkdir(parents=True)
        (profile_home / "cron").mkdir(parents=True)

        from cron.jobs import save_jobs, load_jobs
        with patch("cron.jobs.get_hermes_home", return_value=profile_home):
            save_jobs([{"id": "test1", "name": "saved-to-profile"}])

        # Verify it went to profile home, not default
        profile_jobs = profile_home / "cron" / "jobs.json"
        default_jobs = default_home / "cron" / "jobs.json"
        assert profile_jobs.exists()
        data = json.loads(profile_jobs.read_text())
        assert data["jobs"][0]["name"] == "saved-to-profile"
        assert not default_jobs.exists()

    def test_save_then_load_different_profiles(self, fake_homes):
        """Jobs saved to profile A should NOT appear when loading from profile B."""
        default_home, profile_home = fake_homes
        (default_home / "cron").mkdir(parents=True)
        (profile_home / "cron").mkdir(parents=True)

        from cron.jobs import save_jobs, load_jobs

        # Save under default profile
        with patch("cron.jobs.get_hermes_home", return_value=default_home):
            save_jobs([{"id": "only-in-default", "name": "default-job"}])

        # Load under different profile — should NOT find the job
        with patch("cron.jobs.get_hermes_home", return_value=profile_home):
            jobs = load_jobs()
        assert len(jobs) == 0


class TestEnsureDirsProfileSwitch:
    """ensure_dirs() must create directories under the current profile."""

    def test_dirs_created_under_correct_home(self, fake_homes):
        default_home, profile_home = fake_homes

        from cron.jobs import ensure_dirs
        with patch("cron.jobs.get_hermes_home", return_value=profile_home):
            ensure_dirs()

        assert (profile_home / "cron").is_dir()
        assert (profile_home / "cron" / "output").is_dir()
        # Default home should NOT have cron dirs
        assert not (default_home / "cron").exists()


class TestSaveJobOutputProfileSwitch:
    """save_job_output() must write under the current profile."""

    def test_output_saved_under_correct_profile(self, fake_homes):
        default_home, profile_home = fake_homes
        (default_home / "cron" / "output").mkdir(parents=True)
        (profile_home / "cron" / "output").mkdir(parents=True)

        from cron.jobs import save_job_output
        with patch("cron.jobs.get_hermes_home", return_value=profile_home):
            save_job_output("aabbccddeeff", "test output")

        profile_output = profile_home / "cron" / "output" / "aabbccddeeff"
        assert profile_output.is_dir()
        files = list(profile_output.glob("*.md"))
        assert len(files) == 1
        assert "test output" in files[0].read_text()

        # Default should have no output
        assert not (default_home / "cron" / "output" / "aabbccddeeff").exists()
