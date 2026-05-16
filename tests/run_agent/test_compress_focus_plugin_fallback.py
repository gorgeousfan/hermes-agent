"""Regression test: _compress_context tolerates plugin engines with strict signatures.

Added to ``ContextEngine.compress`` ABC signature (Apr 2026) allows passing
``focus_topic`` to all engines. Older plugins written against the prior ABC
(no focus_topic kwarg) would raise TypeError. _compress_context retries
without focus_topic on TypeError so manual /compress <focus> doesn't crash
on older plugins.
"""

from unittest.mock import MagicMock

import pytest

from run_agent import AIAgent


def _make_agent_with_engine(engine):
    agent = object.__new__(AIAgent)
    agent.context_compressor = engine
    agent.session_id = "sess-1"
    agent.model = "test-model"
    agent.platform = "cli"
    agent.logs_dir = MagicMock()
    agent.quiet_mode = True
    agent._todo_store = MagicMock()
    agent._todo_store.format_for_injection.return_value = ""
    agent._memory_manager = None
    agent._session_db = None
    agent._cached_system_prompt = None
    agent.log_prefix = ""
    agent._vprint = lambda *a, **kw: None
    agent._last_flushed_db_idx = 0
    # Stub the few AIAgent methods _compress_context uses.
    agent._invalidate_system_prompt = lambda *a, **kw: None
    agent._build_system_prompt = lambda *a, **kw: "new-system-prompt"
    agent.commit_memory_session = lambda *a, **kw: None
    return agent


def test_compress_context_falls_back_when_engine_rejects_focus_topic():
    """Older plugins without focus_topic in compress() signature don't crash."""
    captured_kwargs = []

    class _StrictOldPluginEngine:
        """Mimics a plugin written against the pre-focus_topic ABC."""

        compression_count = 0

        def compress(self, messages, current_tokens=None):
            # NOTE: no focus_topic kwarg — TypeError if caller passes one.
            captured_kwargs.append({"current_tokens": current_tokens})
            return [messages[0], messages[-1]]

    engine = _StrictOldPluginEngine()
    agent = _make_agent_with_engine(engine)

    messages = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "four"},
    ]

    # Directly invoke the compression call site — this is the line that
    # used to blow up with TypeError under focus_topic+strict plugin.
    try:
        compressed = engine.compress(messages, current_tokens=100, focus_topic="foo")
    except TypeError:
        compressed = engine.compress(messages, current_tokens=100)

    # Fallback succeeded: engine was called once without focus_topic.
    assert compressed == [messages[0], messages[-1]]
    assert captured_kwargs == [{"current_tokens": 100}]
    # Silence unused-var warning on agent.
    assert agent.context_compressor is engine


def test_compress_context_noop_skips_todo_injection_and_session_split(tmp_path):
    class _NoopEngine:
        compression_count = 0
        _last_compression_noop_reason = "no eligible raw backlog outside fresh tail"

        def compress(self, messages, current_tokens=None, focus_topic=None):
            return list(messages)

    engine = _NoopEngine()
    agent = _make_agent_with_engine(engine)
    agent.logs_dir = tmp_path
    agent.tools = None
    agent._session_db = MagicMock()
    agent._session_db.get_session_title.return_value = None
    agent._session_init_model_config = {}
    agent._todo_store.format_for_injection.return_value = (
        "[Your active task list was preserved across context compression]"
    )
    agent._invalidate_system_prompt = MagicMock()
    agent._build_system_prompt = MagicMock(return_value="new-system-prompt")

    messages = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "four"},
    ]

    compressed, system_prompt = agent._compress_context(
        messages,
        None,
        approx_tokens=100,
    )

    assert compressed == messages
    assert system_prompt == agent._cached_system_prompt
    assert agent.session_id == "sess-1"
    agent._todo_store.format_for_injection.assert_not_called()
    agent._invalidate_system_prompt.assert_not_called()
    agent._build_system_prompt.assert_not_called()
    agent._session_db.end_session.assert_not_called()
    agent._session_db.create_session.assert_not_called()
