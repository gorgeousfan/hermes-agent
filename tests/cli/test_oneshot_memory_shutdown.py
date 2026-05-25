"""Regression test for #27832 — oneshot (-z) mode must call
``shutdown_memory_provider()`` so gRPC-backed memory plugins (mem0-oss)
join their daemon threads before interpreter exit.

Without this, Python interpreter shutdown triggers SIGABRT (exit 134)
because gRPC's pthread forced-unwind handler fires while native threads
are still alive.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_oneshot_calls_shutdown_memory_provider():
    """_run_agent() calls agent.shutdown_memory_provider() after chat()."""
    mock_agent = MagicMock()
    mock_agent.chat.return_value = "hello"

    with patch("run_agent.AIAgent", return_value=mock_agent), \
         patch("hermes_cli.config.load_config", return_value={}), \
         patch("hermes_cli.runtime_provider.resolve_runtime_provider", return_value={
             "api_key": "test",
             "base_url": "http://localhost",
             "provider": "test",
             "api_mode": None,
             "credential_pool": None,
         }), \
         patch("hermes_cli.oneshot._create_session_db_for_oneshot", return_value=None):
        from hermes_cli.oneshot import _run_agent

        result = _run_agent("test prompt")

    assert result == "hello"
    mock_agent.shutdown_memory_provider.assert_called_once()


def test_oneshot_shutdown_called_even_on_chat_error():
    """shutdown_memory_provider() is called even when chat() raises."""
    mock_agent = MagicMock()
    mock_agent.chat.side_effect = RuntimeError("model error")

    with patch("run_agent.AIAgent", return_value=mock_agent), \
         patch("hermes_cli.config.load_config", return_value={}), \
         patch("hermes_cli.runtime_provider.resolve_runtime_provider", return_value={
             "api_key": "test",
             "base_url": "http://localhost",
             "provider": "test",
             "api_mode": None,
             "credential_pool": None,
         }), \
         patch("hermes_cli.oneshot._create_session_db_for_oneshot", return_value=None):
        from hermes_cli.oneshot import _run_agent

        try:
            _run_agent("test prompt")
        except RuntimeError:
            pass

    mock_agent.shutdown_memory_provider.assert_called_once()


def test_oneshot_shutdown_exception_swallowed():
    """A failing shutdown_memory_provider() must not crash oneshot."""
    mock_agent = MagicMock()
    mock_agent.chat.return_value = "ok"
    mock_agent.shutdown_memory_provider.side_effect = RuntimeError("gRPC boom")

    with patch("run_agent.AIAgent", return_value=mock_agent), \
         patch("hermes_cli.config.load_config", return_value={}), \
         patch("hermes_cli.runtime_provider.resolve_runtime_provider", return_value={
             "api_key": "test",
             "base_url": "http://localhost",
             "provider": "test",
             "api_mode": None,
             "credential_pool": None,
         }), \
         patch("hermes_cli.oneshot._create_session_db_for_oneshot", return_value=None):
        from hermes_cli.oneshot import _run_agent

        result = _run_agent("test prompt")

    assert result == "ok"
    mock_agent.shutdown_memory_provider.assert_called_once()
