"""Regression test for issue #21301.

The Gateway's manual /compress (`tmp_agent`) and Session Hygiene auto-compress
(`_hyg_agent`) both build a temporary AIAgent. Both MUST pass
``session_db=self._session_db`` so ``_compress_context()``'s session-rotate
block runs and the original transcript is preserved with
``end_reason='compression'``. Without it, ``rewrite_transcript`` overwrites
the original session's messages, destroying searchable history.
"""

import re
from pathlib import Path


_RUN_PY = Path(__file__).resolve().parents[2] / "gateway" / "run.py"


def _find_constructor_block(var_name: str) -> str:
    src = _RUN_PY.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"\b{re.escape(var_name)}\s*=\s*AIAgent\(\s*(.*?)\s*\)",
        re.DOTALL,
    )
    matches = pattern.findall(src)
    assert matches, f"{var_name} = AIAgent(...) not found in gateway/run.py"
    # Return the first match — both call sites are unique by var_name.
    return matches[0]


def test_hyg_agent_passes_session_db():
    """Session Hygiene auto-compress path."""
    block = _find_constructor_block("_hyg_agent")
    assert "session_db=self._session_db" in block, (
        "_hyg_agent must pass session_db=self._session_db so "
        "_compress_context preserves the original transcript (issue #21301)."
    )


def test_tmp_agent_passes_session_db():
    """Manual /compress command path."""
    block = _find_constructor_block("tmp_agent")
    assert "session_db=self._session_db" in block, (
        "tmp_agent must pass session_db=self._session_db so "
        "_compress_context preserves the original transcript (issue #21301)."
    )