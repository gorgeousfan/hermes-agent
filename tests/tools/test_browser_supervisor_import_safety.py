"""Import-safety regression tests for ``tools.browser_supervisor``.

Issue #31005: the Homebrew bottle ships a slim venv that omits
``websockets`` (a transitive dependency that hermes-agent does not declare
as its own runtime dep). Before the fix, the top-level
``import websockets`` in ``tools/browser_supervisor.py`` raised
``ModuleNotFoundError`` whenever tool discovery touched the module, and
``hermes`` startup logged
``Could not import tool module tools.browser_dialog_tool: No module named 'websockets'``
because ``browser_dialog_tool`` imports ``SUPERVISOR_REGISTRY`` from
``browser_supervisor`` at module level.

These tests prove:

1. ``tools.browser_supervisor`` continues to import cleanly even when
   ``websockets`` is unavailable.
2. The downstream ``tools.browser_dialog_tool`` also imports cleanly, so
   tool registration no longer fails at startup.
3. Attempting to actually start a supervisor without ``websockets`` raises
   a clear ``ImportError`` pointing to the install command, instead of a
   confusing ``NameError`` deep inside the background thread.
"""

from __future__ import annotations

import importlib
import sys

import pytest


def _reload_supervisor_module(monkeypatch: pytest.MonkeyPatch, available: bool):
    """Reload ``tools.browser_supervisor`` with websockets present or absent."""
    if not available:
        monkeypatch.setitem(sys.modules, "websockets", None)
        monkeypatch.setitem(sys.modules, "websockets.asyncio", None)
        monkeypatch.setitem(sys.modules, "websockets.asyncio.client", None)
    sys.modules.pop("tools.browser_supervisor", None)
    sys.modules.pop("tools.browser_dialog_tool", None)
    return importlib.import_module("tools.browser_supervisor")


def test_browser_supervisor_imports_without_websockets(monkeypatch):
    module = _reload_supervisor_module(monkeypatch, available=False)
    assert module._WS_AVAILABLE is False
    assert module.SUPERVISOR_REGISTRY is not None


def test_browser_dialog_tool_imports_without_websockets(monkeypatch):
    _reload_supervisor_module(monkeypatch, available=False)
    dialog = importlib.import_module("tools.browser_dialog_tool")
    assert dialog.SUPERVISOR_REGISTRY is not None


def test_supervisor_start_raises_clear_importerror_without_websockets(monkeypatch):
    module = _reload_supervisor_module(monkeypatch, available=False)
    supervisor = module.CDPSupervisor(task_id="t", cdp_url="ws://example/devtools")
    with pytest.raises(ImportError, match="websockets"):
        supervisor.start()


def test_browser_supervisor_module_state_with_websockets_present():
    pytest.importorskip("websockets")
    sys.modules.pop("tools.browser_supervisor", None)
    module = importlib.import_module("tools.browser_supervisor")
    assert module._WS_AVAILABLE is True
    assert module.websockets is not None
