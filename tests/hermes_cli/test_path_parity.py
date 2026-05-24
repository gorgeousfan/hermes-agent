"""Path parity tests for known divergence bugs (FR #28984 Phase 3).

These tests verify that config/runtime fields are consumed consistently
across all execution paths.  Each test targets a known divergence bug from
the FR evidence table.
"""

from __future__ import annotations

import pytest

from hermes_cli.path_parity import (
    PathParityError,
    assert_field_parity,
    assert_fields_parity,
    compare_paths,
)


class TestAssertFieldParity:
    """Unit tests for the assert_field_parity helper."""

    def test_all_paths_present(self):
        """No error when all paths consume the field."""
        result = assert_field_parity(
            "fallback_model",
            {
                "gateway": lambda: {"fallback_model": "gpt-3.5", "model": "gpt-4"},
                "tui": lambda: {"fallback_model": "gpt-3.5", "model": "gpt-4"},
            },
        )
        assert result["gateway"] == "gpt-3.5"
        assert result["tui"] == "gpt-3.5"

    def test_one_path_missing_raises(self):
        """Error when one path does not consume the field."""
        with pytest.raises(PathParityError, match="tui"):
            assert_field_parity(
                "fallback_model",
                {
                    "gateway": lambda: {"fallback_model": "gpt-3.5"},
                    "tui": lambda: {"model": "gpt-4"},  # missing fallback_model
                },
            )

    def test_ignore_paths(self):
        """Ignored paths are skipped."""
        result = assert_field_parity(
            "fallback_model",
            {
                "gateway": lambda: {"fallback_model": "gpt-3.5"},
                "legacy_cli": lambda: {"model": "gpt-4"},  # missing
            },
            ignore_paths=["legacy_cli"],
        )
        assert result["legacy_cli"] == "<skipped>"

    def test_error_in_extractor(self):
        """Extractor exceptions are treated as missing."""
        with pytest.raises(PathParityError, match="broken"):
            assert_field_parity(
                "field",
                {
                    "ok": lambda: {"field": "value"},
                    "broken": lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                },
            )

    def test_empty_paths(self):
        """No paths → no error (vacuously true)."""
        assert_field_parity("field", {})


class TestAssertFieldsParity:
    """Tests for multi-field parity check."""

    def test_all_fields_present(self):
        result = assert_fields_parity(
            ["fallback_model", "credential_pool"],
            {
                "gateway": lambda: {"fallback_model": "x", "credential_pool": "y"},
                "tui": lambda: {"fallback_model": "x", "credential_pool": "y"},
            },
        )
        assert "fallback_model" in result
        assert "credential_pool" in result

    def test_one_field_missing(self):
        with pytest.raises(PathParityError, match="credential_pool"):
            assert_fields_parity(
                ["fallback_model", "credential_pool"],
                {
                    "gateway": lambda: {"fallback_model": "x", "credential_pool": "y"},
                    "tui": lambda: {"fallback_model": "x"},  # no credential_pool
                },
            )


class TestComparePaths:
    """Tests for the non-throwing compare_paths helper."""

    def test_present_and_missing(self):
        report = compare_paths(
            {
                "gateway": lambda: {"fallback_model": "x", "model": "y"},
                "tui": lambda: {"model": "y"},
            },
        )
        assert report["fallback_model"]["gateway"] == "present"
        assert report["fallback_model"]["tui"] == "missing"
        assert report["model"]["gateway"] == "present"
        assert report["model"]["tui"] == "present"

    def test_ignore_paths(self):
        report = compare_paths(
            {
                "gateway": lambda: {"field": "x"},
                "legacy": lambda: {},
            },
            ignore_paths=["legacy"],
        )
        assert "legacy" not in report.get("field", {})


class TestKnownDivergences:
    """Documented divergence patterns from FR #28984 evidence.

    These tests use mock extractors to verify the path_parity framework
    catches the known bugs.  When the actual code is fixed, these can be
    converted to integration tests that import real code paths.
    """

    def test_28753_fallback_model_tui_missing(self):
        """#28753: TUI doesn't propagate fallback_model."""
        with pytest.raises(PathParityError, match="tui_make_agent"):
            assert_field_parity(
                "fallback_model",
                {
                    "gateway_run_agent": lambda: {
                        "fallback_model": "gpt-3.5-turbo",
                        "model": "gpt-4",
                        "credential_pool": "pool1",
                    },
                    "tui_make_agent": lambda: {
                        # fallback_model intentionally missing — this is the bug
                        "model": "gpt-4",
                        "credential_pool": "pool1",
                    },
                },
            )

    def test_28746_session_end_idle_expiry_missing(self):
        """#28746: session:end event not emitted from idle-expiry path."""
        with pytest.raises(PathParityError, match="idle_expiry"):
            assert_field_parity(
                "session_end_event",
                {
                    "manual_new": lambda: {
                        "session_end_event": True,
                        "session_reset_event": True,
                    },
                    "idle_expiry": lambda: {
                        # session_end_event intentionally missing — this is the bug
                        "session_finalize_hook": True,
                    },
                },
            )

    def test_28637_token_usage_model_switch_missing(self):
        """#28637: Per-model token usage lost during /model switch."""
        with pytest.raises(PathParityError, match="model_switch"):
            assert_field_parity(
                "per_model_usage",
                {
                    "continuous_session": lambda: {
                        "per_model_usage": True,
                        "flight_recorder": True,
                    },
                    "model_switch": lambda: {
                        # per_model_usage intentionally missing — this is the bug
                        "flight_recorder": True,
                    },
                },
            )

    def test_all_parities_fixed(self):
        """After fixes, all known paths should have parity."""
        # This test documents the EXPECTED state after all fixes are applied
        result = assert_field_parity(
            "fallback_model",
            {
                "gateway_run_agent": lambda: {"fallback_model": "x", "model": "y"},
                "tui_make_agent": lambda: {"fallback_model": "x", "model": "y"},
                "/model_switch": lambda: {"fallback_model": "x", "model": "y"},
                "fallback_activation": lambda: {"fallback_model": "x", "model": "y"},
            },
        )
        assert len(result) == 4
