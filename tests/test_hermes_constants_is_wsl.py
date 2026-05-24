"""Tests for hermes_constants.is_wsl() — WSL detection via /proc/version."""

from unittest.mock import mock_open, patch

import pytest

import hermes_constants
from hermes_constants import is_wsl


@pytest.fixture(autouse=True)
def _reset_cache():
    # Force-clear any cached value from previous tests/workers and clear again
    # after each test so we don't restore a stale True/False into the module.
    hermes_constants._wsl_detected = None
    is_wsl.__globals__["_wsl_detected"] = None
    yield
    hermes_constants._wsl_detected = None
    is_wsl.__globals__["_wsl_detected"] = None


class TestIsWsl:
    def test_true_when_proc_version_contains_microsoft(self):
        with patch("builtins.open", mock_open(read_data="Linux 5.10 microsoft-standard-WSL2")):
            assert is_wsl() is True

    def test_true_for_lowercase_microsoft_marker(self):
        with patch("builtins.open", mock_open(read_data="microsoft hypervisor build")):
            assert is_wsl() is True

    def test_false_for_native_linux_proc_version(self):
        with patch("builtins.open", mock_open(read_data="Linux 6.1 generic-amd64")):
            assert is_wsl() is False

    def test_false_when_proc_version_missing(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            assert is_wsl() is False

    def test_false_when_proc_version_unreadable(self):
        with patch("builtins.open", side_effect=PermissionError):
            assert is_wsl() is False

    def test_result_is_cached_after_first_call(self):
        with patch("builtins.open", mock_open(read_data="microsoft")):
            assert is_wsl() is True

        with patch("builtins.open", side_effect=AssertionError("should not re-read /proc/version")):
            assert is_wsl() is True
