"""Tests for Railway terminal-backend setup and config."""

from __future__ import annotations

import importlib

import pytest


def _config_mod():
    return importlib.import_module("hermes_cli.config")


def test_default_config_includes_terminal_railway():
    cfg = _config_mod()
    default = getattr(cfg, "DEFAULT_CONFIG", None)
    assert default is not None
    terminal = default.get("terminal", {})
    railway = terminal.get("railway", {})
    for key in ("project_id", "service_id", "environment_id",
                "deployment_instance_id", "identity_file"):
        assert key in railway, f"terminal.railway.{key} missing"


def test_extra_env_keys_include_railway_overrides():
    cfg = _config_mod()
    extras = getattr(cfg, "_EXTRA_ENV_KEYS", None) or getattr(
        cfg, "EXTRA_ENV_KEYS", None)
    assert extras is not None
    flat = set()
    if isinstance(extras, dict):
        for v in extras.values():
            if isinstance(v, (list, tuple)):
                flat.update(v)
            else:
                flat.add(v)
    elif isinstance(extras, (list, tuple, set, frozenset)):
        flat.update(extras)
    for env in ("RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID",
                "RAILWAY_ENVIRONMENT_ID"):
        assert env in flat, f"{env} not in extra env keys"


def test_wizard_offers_railway():
    setup = importlib.import_module("hermes_cli.setup")
    backends = getattr(setup, "TERMINAL_BACKEND_CHOICES", None)
    if backends is None:
        backends = getattr(setup, "_TERMINAL_BACKEND_CHOICES", None)
    assert backends is not None
    assert "railway" in backends


def test_config_validates_railway_keys():
    cfg = _config_mod()
    validate = getattr(cfg, "validate_terminal_railway",
                       None) or getattr(cfg, "validate_terminal", None)
    if validate is None:
        validate = getattr(cfg, "validate", None)
    assert validate is not None
    bad = {"terminal": {"backend": "railway",
                         "railway": {"project_id": "", "service_id": "",
                                      "environment_id": ""}}}
    with pytest.raises(ValueError):
        validate(bad)
