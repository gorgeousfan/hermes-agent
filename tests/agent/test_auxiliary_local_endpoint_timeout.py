"""Tests for local-endpoint timeout auto-bump in agent.auxiliary_client.

When an auxiliary call (session_search, skills_hub, title_generation,
vision, etc.) routes to a local LLM provider (Ollama, llama.cpp, vLLM),
the 30s default timeout causes ReadTimeout → retry → inference-server
queue saturation on slow prefill (#21566). The main agent loop already
auto-bumps timeouts for local endpoints; auxiliary calls should do the
same when neither the caller nor config has set an explicit value.
"""

import os

import pytest
from unittest.mock import patch

from agent.auxiliary_client import (
    _DEFAULT_AUX_TIMEOUT,
    _get_task_timeout,
    _local_aux_default_timeout,
)


class TestGetTaskTimeoutLocalBump:
    """``_get_task_timeout`` should auto-bump to local default for local URLs."""

    @pytest.mark.parametrize("base_url", [
        "http://localhost:11434",
        "http://127.0.0.1:8080",
        "http://0.0.0.0:5000",
        "http://192.168.1.100:8000",
        "http://10.0.0.5:1234",
        "http://host.docker.internal:11434",
    ])
    def test_local_endpoint_no_config_override_bumps(self, base_url, monkeypatch):
        """Local URL + no caller/config override → bumped to local default (1800s)."""
        monkeypatch.delenv("HERMES_API_TIMEOUT", raising=False)
        with patch("agent.auxiliary_client._get_auxiliary_task_config", return_value={}):
            result = _get_task_timeout("session_search", base_url=base_url)
        assert result == 1800.0

    def test_local_endpoint_respects_explicit_config(self, monkeypatch):
        """Explicit ``auxiliary.<task>.timeout`` wins over local-endpoint bump."""
        monkeypatch.delenv("HERMES_API_TIMEOUT", raising=False)
        with patch(
            "agent.auxiliary_client._get_auxiliary_task_config",
            return_value={"timeout": 60},
        ):
            result = _get_task_timeout("session_search", base_url="http://localhost:11434")
        assert result == 60.0

    def test_local_endpoint_respects_caller_default(self, monkeypatch):
        """Non-default ``default=`` arg means caller is overriding — no bump."""
        monkeypatch.delenv("HERMES_API_TIMEOUT", raising=False)
        with patch("agent.auxiliary_client._get_auxiliary_task_config", return_value={}):
            result = _get_task_timeout(
                "session_search", default=120.0, base_url="http://localhost:11434",
            )
        assert result == 120.0

    @pytest.mark.parametrize("base_url", [
        "https://api.openai.com",
        "https://openrouter.ai/api",
        "https://api.anthropic.com",
        "https://api.moonshot.ai",
    ])
    def test_remote_endpoint_keeps_default(self, base_url, monkeypatch):
        """Remote URL → keep 30s default."""
        monkeypatch.delenv("HERMES_API_TIMEOUT", raising=False)
        with patch("agent.auxiliary_client._get_auxiliary_task_config", return_value={}):
            result = _get_task_timeout("session_search", base_url=base_url)
        assert result == _DEFAULT_AUX_TIMEOUT

    def test_no_base_url_keeps_default(self, monkeypatch):
        """No base_url (None or empty) → keep 30s default (no bump applied)."""
        monkeypatch.delenv("HERMES_API_TIMEOUT", raising=False)
        with patch("agent.auxiliary_client._get_auxiliary_task_config", return_value={}):
            assert _get_task_timeout("session_search") == _DEFAULT_AUX_TIMEOUT
            assert _get_task_timeout("session_search", base_url=None) == _DEFAULT_AUX_TIMEOUT
            assert _get_task_timeout("session_search", base_url="") == _DEFAULT_AUX_TIMEOUT

    def test_empty_task_with_local_base_url_still_bumps(self, monkeypatch):
        """Empty task (used by ad-hoc call_llm) + local URL → still bump."""
        monkeypatch.delenv("HERMES_API_TIMEOUT", raising=False)
        result = _get_task_timeout("", base_url="http://localhost:11434")
        assert result == 1800.0

    def test_empty_task_with_remote_base_url_keeps_default(self):
        """Empty task + remote URL → 30s default (no bump)."""
        result = _get_task_timeout("", base_url="https://api.openai.com")
        assert result == _DEFAULT_AUX_TIMEOUT

    def test_hermes_api_timeout_env_overrides_local_default(self, monkeypatch):
        """Local-endpoint default reads ``HERMES_API_TIMEOUT`` like run_agent.py does."""
        monkeypatch.setenv("HERMES_API_TIMEOUT", "900")
        with patch("agent.auxiliary_client._get_auxiliary_task_config", return_value={}):
            result = _get_task_timeout("session_search", base_url="http://localhost:11434")
        assert result == 900.0

    def test_hermes_api_timeout_invalid_falls_back_to_1800(self, monkeypatch):
        """Garbage ``HERMES_API_TIMEOUT`` falls back to 1800s, never crashes."""
        monkeypatch.setenv("HERMES_API_TIMEOUT", "not-a-number")
        with patch("agent.auxiliary_client._get_auxiliary_task_config", return_value={}):
            result = _get_task_timeout("session_search", base_url="http://localhost:11434")
        assert result == 1800.0


class TestLocalAuxDefaultTimeout:
    """``_local_aux_default_timeout`` reads HERMES_API_TIMEOUT with safe fallback."""

    def test_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("HERMES_API_TIMEOUT", raising=False)
        assert _local_aux_default_timeout() == 1800.0

    def test_reads_env(self, monkeypatch):
        monkeypatch.setenv("HERMES_API_TIMEOUT", "600")
        assert _local_aux_default_timeout() == 600.0

    def test_invalid_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("HERMES_API_TIMEOUT", "")
        assert _local_aux_default_timeout() == 1800.0
        monkeypatch.setenv("HERMES_API_TIMEOUT", "garbage")
        assert _local_aux_default_timeout() == 1800.0


class TestRegressionBaseline:
    """Confirms the bug exists without the base_url argument (old signature).

    Without ``base_url`` passed in, the function returns the unbumped 30s
    default — proving the local-endpoint awareness only kicks in when
    callers thread the URL through.
    """

    def test_old_signature_returns_30s_for_local_workload(self):
        with patch("agent.auxiliary_client._get_auxiliary_task_config", return_value={}):
            assert _get_task_timeout("session_search") == 30.0
