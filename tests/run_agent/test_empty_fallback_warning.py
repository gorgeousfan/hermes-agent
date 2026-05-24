"""Tests for the session-start warning emitted when a retry-prone
primary provider (Anthropic, OpenAI Codex) boots with an EMPTY
``fallback_providers`` chain.

This guards against the silent in-loop retry storm pattern seen on
2026-05-18→19 (builder/ops outage), where an Anthropic primary with no
fallback configured hit prolonged 529 overload and looked like a hang.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from run_agent import AIAgent


def _make_agent(*, provider, fallback_model=None, quiet_mode=True, base_url=None):
    """Construct a minimal AIAgent with a specified primary provider."""
    # base_url is sniffed by agent_init to derive provider when ``provider``
    # arg is None; keep them aligned to avoid drift.
    if base_url is None:
        base_url = {
            "anthropic": "https://api.anthropic.com",
            "openai-codex": "https://chatgpt.com/backend-api/codex",
            "openrouter": "https://openrouter.ai/api/v1",
        }.get(provider, "https://openrouter.ai/api/v1")
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            provider=provider,
            api_key="test-key",
            base_url=base_url,
            quiet_mode=quiet_mode,
            skip_context_files=True,
            skip_memory=True,
            fallback_model=fallback_model,
        )
        agent.client = MagicMock()
        return agent


class TestEmptyFallbackWarning:
    """Warn ONLY when primary is retry-prone AND chain is empty."""

    @pytest.mark.parametrize("provider", ["anthropic", "openai-codex"])
    def test_warns_for_retry_prone_primary_with_empty_chain(self, provider, caplog):
        with caplog.at_level(logging.WARNING, logger="run_agent"):
            agent = _make_agent(provider=provider, fallback_model=None)
        assert agent._fallback_chain == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("empty fallback_providers" in r.getMessage() for r in warnings), \
            f"expected empty-fallback WARNING for primary={provider!r}, " \
            f"got {[r.getMessage() for r in warnings]!r}"
        msg = next(r.getMessage() for r in warnings
                   if "empty fallback_providers" in r.getMessage())
        assert f"primary={provider}" in msg
        assert "hermes" in msg and "fallback add" in msg

    @pytest.mark.parametrize("provider", ["anthropic", "openai-codex"])
    def test_warning_includes_profile_flag_when_non_default(self, provider, caplog):
        with (
            patch("hermes_cli.profiles.get_active_profile_name",
                  return_value="ops"),
            caplog.at_level(logging.WARNING, logger="run_agent"),
        ):
            _make_agent(provider=provider, fallback_model=None)
        msg = next(r.getMessage() for r in caplog.records
                   if "empty fallback_providers" in r.getMessage())
        assert "hermes -p ops fallback add" in msg

    @pytest.mark.parametrize("provider", ["anthropic", "openai-codex"])
    def test_warning_omits_profile_flag_for_default(self, provider, caplog):
        with (
            patch("hermes_cli.profiles.get_active_profile_name",
                  return_value="default"),
            caplog.at_level(logging.WARNING, logger="run_agent"),
        ):
            _make_agent(provider=provider, fallback_model=None)
        msg = next(r.getMessage() for r in caplog.records
                   if "empty fallback_providers" in r.getMessage())
        # bare ``hermes fallback add`` — no ``-p`` flag for the root profile.
        assert "hermes fallback add" in msg
        assert "-p" not in msg

    def test_no_warning_for_openrouter_primary(self, caplog):
        """Aggregators (OpenRouter, AI Gateway) already do server-side
        retries / failover; an empty user-side chain is not a hazard."""
        with caplog.at_level(logging.WARNING, logger="run_agent"):
            agent = _make_agent(provider="openrouter", fallback_model=None)
        assert agent._fallback_chain == []
        assert not any("empty fallback_providers" in r.getMessage()
                       for r in caplog.records)

    @pytest.mark.parametrize("provider", ["anthropic", "openai-codex"])
    def test_no_warning_when_chain_configured(self, provider, caplog):
        with caplog.at_level(logging.WARNING, logger="run_agent"):
            _make_agent(
                provider=provider,
                fallback_model=[{"provider": "openrouter",
                                  "model": "anthropic/claude-sonnet-4"}],
            )
        assert not any("empty fallback_providers" in r.getMessage()
                       for r in caplog.records)

    @pytest.mark.parametrize("provider", ["anthropic", "openai-codex"])
    def test_warning_stdout_in_interactive_mode(self, provider, capsys, caplog):
        """In non-quiet mode the warning must also reach stdout — quiet
        mode swallows the print but the logger.warning still fires."""
        with caplog.at_level(logging.WARNING, logger="run_agent"):
            _make_agent(provider=provider, fallback_model=None, quiet_mode=False)
        captured = capsys.readouterr().out
        assert "empty fallback_providers" in captured
        assert "⚠️" in captured
        # And the logger emission must have happened too.
        assert any("empty fallback_providers" in r.getMessage()
                   for r in caplog.records)

    @pytest.mark.parametrize("provider", ["anthropic", "openai-codex"])
    def test_warning_logger_fires_in_quiet_mode(self, provider, capsys, caplog):
        """Quiet mode (gateway / batch_runner) must still emit the
        WARNING to the logger — only the stdout print is suppressed."""
        with caplog.at_level(logging.WARNING, logger="run_agent"):
            _make_agent(provider=provider, fallback_model=None, quiet_mode=True)
        captured = capsys.readouterr().out
        assert "empty fallback_providers" not in captured
        assert any("empty fallback_providers" in r.getMessage()
                   for r in caplog.records)

    @pytest.mark.parametrize("provider", ["anthropic", "openai-codex"])
    def test_warning_survives_profile_lookup_failure(self, provider, caplog):
        """If ``get_active_profile_name`` raises, the warning still
        fires with the bare-``hermes`` suggestion (no ``-p`` flag)."""
        with (
            patch("hermes_cli.profiles.get_active_profile_name",
                  side_effect=RuntimeError("no profile system")),
            caplog.at_level(logging.WARNING, logger="run_agent"),
        ):
            _make_agent(provider=provider, fallback_model=None)
        msg = next(r.getMessage() for r in caplog.records
                   if "empty fallback_providers" in r.getMessage())
        assert "hermes fallback add" in msg
        assert "-p" not in msg
