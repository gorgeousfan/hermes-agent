"""Pin the contract that ``hermes -z`` (oneshot mode) surfaces structured
agent failures to stderr and exits with rc=1.

Prior to this fix ``run_oneshot`` always returned 0 and only printed
``response`` to stdout. When the agent's ``run_conversation`` returned a
``failed=True`` / ``compression_exhausted=True`` result (e.g. the
context-overflow recovery chain exhausted its retries on a local model),
the caller saw an exit-0 process with empty stdout and empty stderr —
indistinguishable from a clean run that happened to produce no text.

Wrappers like the MeshBoard launcher classify silent rc=1 differently
from a structured error; the fingerprint of the 2026-05-23 incident
(103 incidents in 20 days, 76 in the last 24h, all chain_depth=3 on
qwen3.6-35b-a3b) was driven by exactly this gap. After the fix:

* A failed result writes ``hermes -z: <error>`` to real_stderr and the
  process exits 1.
* A ``compression_exhausted=True`` result writes an additional
  diagnostic line explaining the recovery hint.
* Successful results still print response to stdout and exit 0 unchanged.
* Exceptions inside ``agent.run_conversation`` are captured as
  synthetic failed-result dicts so the stderr formatter produces a
  uniform line instead of a Python traceback.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _stub_run_agent(result):
    """Patch hermes_cli.oneshot._run_agent to return ``result``."""
    return patch("hermes_cli.oneshot._run_agent", return_value=result)


# ``capfd`` captures at the file-descriptor level so it sees writes that
# went through ``sys.__stdout__`` / ``sys.__stderr__`` as well as
# ``sys.stdout`` / ``sys.stderr``. ``run_oneshot`` captures
# ``sys.stdout`` / ``sys.stderr`` at function entry — capfd sees the
# eventual fd-level write either way, which matches how the MeshBoard
# launcher actually observes Hermes's output (subprocess pipes).


def test_compression_exhausted_writes_stderr_and_exits_1(capfd):
    """The 2026-05-23 incident shape: failed + compression_exhausted +
    error="Context length exceeded..." must produce stderr output and
    rc=1, not a silent rc=0."""
    from hermes_cli.oneshot import run_oneshot

    failed_result = {
        "final_response": "",
        "failed": True,
        "compression_exhausted": True,
        "error": (
            "Context length exceeded (50000 tokens). "
            "Cannot compress further."
        ),
        "messages": [],
        "api_calls": 0,
    }
    with _stub_run_agent(failed_result):
        rc = run_oneshot("any prompt", model=None, provider=None)
    captured = capfd.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "hermes -z:" in captured.err
    assert "Context length exceeded" in captured.err
    # Compression-exhausted-specific diagnostic line.
    assert "context compression exhausted" in captured.err.lower()


def test_failed_without_compression_exhausted_writes_stderr(capfd):
    """Generic ``failed=True`` (e.g. invalid model slug, provider 4xx)
    still surfaces to stderr with rc=1, just without the
    compression-specific hint."""
    from hermes_cli.oneshot import run_oneshot

    failed_result = {
        "final_response": "",
        "failed": True,
        "error": "Invalid model 'bogus-model'",
        "messages": [],
        "api_calls": 0,
    }
    with _stub_run_agent(failed_result):
        rc = run_oneshot("any prompt", model=None, provider=None)
    captured = capfd.readouterr()
    assert rc == 1
    assert "hermes -z: Invalid model 'bogus-model'" in captured.err
    assert "context compression exhausted" not in captured.err.lower()


def test_partial_with_error_also_writes_stderr(capfd):
    """``partial=True`` (truncated output) is treated the same as
    ``failed=True`` for stderr-surfacing purposes — both indicate the
    agent did not produce a clean response."""
    from hermes_cli.oneshot import run_oneshot

    partial_result = {
        "final_response": "",
        "partial": True,
        "error": "Stream truncated mid-response",
        "messages": [],
        "api_calls": 1,
    }
    with _stub_run_agent(partial_result):
        rc = run_oneshot("any prompt", model=None, provider=None)
    captured = capfd.readouterr()
    assert rc == 1
    assert "Stream truncated mid-response" in captured.err


def test_failed_with_response_text_still_writes_stderr_and_returns_1(capfd):
    """Even when the agent produced SOME response text alongside a
    failure flag, the stderr signal must still fire so wrappers can
    distinguish a partial-success from a clean run."""
    from hermes_cli.oneshot import run_oneshot

    failed_with_partial_response = {
        "final_response": "Here is what I got before the error...",
        "failed": True,
        "error": "Provider timeout after 60s",
        "messages": [],
        "api_calls": 1,
    }
    with _stub_run_agent(failed_with_partial_response):
        rc = run_oneshot("any prompt", model=None, provider=None)
    captured = capfd.readouterr()
    # Response goes to stdout (it's real partial content).
    assert "Here is what I got before the error" in captured.out
    # Error still surfaces to stderr.
    assert "Provider timeout after 60s" in captured.err
    # And the exit code reflects failure.
    assert rc == 1


def test_successful_result_unchanged(capfd):
    """The happy path must remain identical to the previous behaviour:
    write response to stdout, no stderr, rc=0."""
    from hermes_cli.oneshot import run_oneshot

    ok_result = {
        "final_response": "The answer is 42.",
        "messages": [],
        "api_calls": 1,
    }
    with _stub_run_agent(ok_result):
        rc = run_oneshot("any prompt", model=None, provider=None)
    captured = capfd.readouterr()
    assert rc == 0
    assert captured.out.rstrip("\n") == "The answer is 42."
    assert captured.err == ""


def test_response_without_trailing_newline_gets_one_appended(capfd):
    """Pre-existing behaviour: response without a trailing newline gets
    one appended so terminal display lines up. Pin it here so the new
    structured-result path doesn't accidentally regress."""
    from hermes_cli.oneshot import run_oneshot

    ok_result = {"final_response": "no newline"}
    with _stub_run_agent(ok_result):
        run_oneshot("any prompt", model=None, provider=None)
    captured = capfd.readouterr()
    assert captured.out == "no newline\n"


def test_exception_in_run_agent_becomes_failed_result(monkeypatch):
    """``_run_agent`` swallows exceptions and converts them to a
    synthetic failed-result dict so the stderr formatter can produce a
    uniform line. Without this, an unhandled exception would propagate
    past ``run_oneshot`` and Python's default handler would print a
    traceback to real stderr — useful, but it bypasses the structured
    error reporting path."""
    from hermes_cli import oneshot as oneshot_mod

    # Make AIAgent constructor raise the moment _run_agent tries to
    # build the agent. The exception must be captured into a synthetic
    # failed-result dict, not propagated.
    def _exploding_agent(*args, **kwargs):
        raise RuntimeError("boom in agent init")

    monkeypatch.setattr("run_agent.AIAgent", _exploding_agent)
    # Stub the upstream config/runtime resolution so _run_agent gets as
    # far as the AIAgent(...) call without running the real auth path.
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {})
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **kw: {"provider": "none", "base_url": "", "api_key": "",
                       "api_mode": "openai_chat", "credential_pool": None},
    )
    monkeypatch.setattr(
        "hermes_cli.tools_config._get_platform_tools",
        lambda cfg, plat: [],
    )
    monkeypatch.setattr(
        "hermes_cli.fallback_config.get_fallback_chain", lambda cfg: [],
    )

    result = oneshot_mod._run_agent(
        "any prompt", model=None, provider=None,
        toolsets=None, use_config_toolsets=False,
    )
    assert isinstance(result, dict)
    assert result.get("failed") is True
    assert "RuntimeError" in result.get("error", "")
    assert "boom in agent init" in result.get("error", "")
    assert result.get("final_response") == ""


def test_bare_string_return_from_run_agent_treated_as_response(capfd):
    """Defensive backwards-compat: if some path returns a bare string,
    treat it as the response (no failure)."""
    from hermes_cli.oneshot import run_oneshot

    with _stub_run_agent("just a string"):
        rc = run_oneshot("any prompt", model=None, provider=None)
    captured = capfd.readouterr()
    assert rc == 0
    assert "just a string" in captured.out
    assert captured.err == ""
