"""Tests for banner toolset name normalization and skin color usage."""

import os
from unittest.mock import patch

from rich.console import Console

import hermes_cli.banner as banner
import model_tools
import tools.mcp_tool


def test_display_toolset_name_strips_legacy_suffix():
    assert banner._display_toolset_name("homeassistant_tools") == "homeassistant"
    assert banner._display_toolset_name("honcho_tools") == "honcho"
    assert banner._display_toolset_name("web_tools") == "web"


def test_display_toolset_name_preserves_clean_names():
    assert banner._display_toolset_name("browser") == "browser"
    assert banner._display_toolset_name("file") == "file"
    assert banner._display_toolset_name("terminal") == "terminal"


def test_display_toolset_name_handles_empty():
    assert banner._display_toolset_name("") == "unknown"
    assert banner._display_toolset_name(None) == "unknown"


def test_build_welcome_banner_uses_normalized_toolset_names():
    """Unavailable toolsets should not have '_tools' appended in banner output."""
    with (
        patch.object(
            model_tools,
            "check_tool_availability",
            return_value=(
                ["web"],
                [
                    {"name": "homeassistant", "tools": ["ha_call_service"]},
                    {"name": "honcho", "tools": ["honcho_conclude"]},
                ],
            ),
        ),
        patch.object(banner, "get_available_skills", return_value={}),
        patch.object(banner, "get_update_result", return_value=None),
        patch.object(tools.mcp_tool, "get_mcp_status", return_value=[]),
    ):
        console = Console(
            record=True, force_terminal=False, color_system=None, width=160
        )
        banner.build_welcome_banner(
            console=console,
            model="anthropic/test-model",
            cwd="/tmp/project",
            tools=[
                {"function": {"name": "web_search"}},
                {"function": {"name": "read_file"}},
            ],
            get_toolset_for_tool=lambda name: {
                "web_search": "web_tools",
                "read_file": "file",
            }.get(name),
        )

    output = console.export_text()
    assert "homeassistant:" in output
    assert "honcho:" in output
    assert "web:" in output
    assert "homeassistant_tools:" not in output
    assert "honcho_tools:" not in output
    assert "web_tools:" not in output


def test_build_welcome_banner_title_is_hyperlinked_to_release():
    """Panel title (version label) is wrapped in an OSC-8 hyperlink to the GitHub release."""
    import io
    from unittest.mock import patch as _patch
    import hermes_cli.banner as _banner
    import model_tools as _mt
    import tools.mcp_tool as _mcp

    _banner._latest_release_cache = None
    tag_url = ("v2026.4.23", "https://github.com/NousResearch/hermes-agent/releases/tag/v2026.4.23")

    buf = io.StringIO()
    with (
        _patch.object(_mt, "check_tool_availability", return_value=(["web"], [])),
        _patch.object(_banner, "get_available_skills", return_value={}),
        _patch.object(_banner, "get_update_result", return_value=None),
        _patch.object(_mcp, "get_mcp_status", return_value=[]),
        _patch.object(_banner, "get_latest_release_tag", return_value=tag_url),
    ):
        console = Console(file=buf, force_terminal=True, color_system="truecolor", width=160)
        _banner.build_welcome_banner(
            console=console, model="x", cwd="/tmp",
            session_id="abc123",
            tools=[{"function": {"name": "read_file"}}],
            get_toolset_for_tool=lambda n: "file",
        )

    raw = buf.getvalue()
    # The existing version label must still be present in the title
    assert "Hermes Agent v" in raw, "Version label missing from title"
    # OSC-8 hyperlink escape sequence present with the release URL
    assert "\x1b]8;" in raw, "OSC-8 hyperlink not emitted"
    assert "releases/tag/v2026.4.23" in raw, "Release URL missing from banner output"


def test_build_welcome_banner_title_falls_back_when_no_tag():
    """Without a resolvable tag, the panel title renders as plain text (no hyperlink escape)."""
    import io
    from unittest.mock import patch as _patch
    import hermes_cli.banner as _banner
    import model_tools as _mt
    import tools.mcp_tool as _mcp

    _banner._latest_release_cache = None
    buf = io.StringIO()
    with (
        _patch.object(_mt, "check_tool_availability", return_value=(["web"], [])),
        _patch.object(_banner, "get_available_skills", return_value={}),
        _patch.object(_banner, "get_update_result", return_value=None),
        _patch.object(_mcp, "get_mcp_status", return_value=[]),
        _patch.object(_banner, "get_latest_release_tag", return_value=None),
    ):
        console = Console(file=buf, force_terminal=True, color_system="truecolor", width=160)
        _banner.build_welcome_banner(
            console=console, model="x", cwd="/tmp",
            session_id="abc123",
            tools=[{"function": {"name": "read_file"}}],
            get_toolset_for_tool=lambda n: "file",
        )

    raw = buf.getvalue()
    assert "Hermes Agent v" in raw, "Version label missing from title"
    assert "\x1b]8;" not in raw, "OSC-8 hyperlink should not be emitted without a tag"


def test_show_banner_resolves_provider_slug_via_custom_providers():
    """Integration: show_banner() resolves a provider slug to the model name
    from custom_providers config, rather than displaying the raw slug."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch as _patch
    import cli as _cli_module
    import model_tools as _mt
    import tools.mcp_tool as _mcp

    # Use "alibaba" — a known HERMES_OVERLAYS provider slug whose canonical
    # identity is itself (no alias indirection).  The custom_providers entry
    # will override its display model.
    PROVIDER_SLUG = "alibaba"
    RESOLVED_MODEL = "qwen-max-custom"

    # Build a CLI_CONFIG dict that includes custom_providers with our slug.
    cli_config = {
        "display": {"tool_progress": "new"},
        "terminal": {},
        "model": {},
        "custom_providers": {
            PROVIDER_SLUG: {"model": RESOLVED_MODEL},
        },
    }

    captured_model = None

    def _capture_banner(console, model, cwd, **kwargs):
        nonlocal captured_model
        captured_model = model

    with (
        _patch.object(_cli_module, "load_cli_config", return_value=cli_config),
        _patch.object(_cli_module, "CLI_CONFIG", cli_config),
        _patch.object(_cli_module, "get_tool_definitions", return_value=[]),
        _patch.object(_cli_module, "build_welcome_banner", side_effect=_capture_banner),
        _patch(_cli_module.__name__ + ".HermesCLI._show_tool_availability_warnings"),
        _patch.object(_mt, "check_tool_availability", return_value=(["web"], [])),
        _patch.object(_mcp, "get_mcp_status", return_value=[]),
    ):
        from cli import HermesCLI
        obj = HermesCLI.__new__(HermesCLI)
        obj.model = PROVIDER_SLUG
        obj.enabled_toolsets = ["hermes-core"]
        obj.compact = False
        obj.console = MagicMock()
        obj.session_id = None
        obj.api_key = "test"
        obj.base_url = ""
        obj.provider = "test"
        obj._provider_source = None
        obj.agent = SimpleNamespace(
            context_compressor=SimpleNamespace(context_length=None)
        )

        # show_banner checks terminal width for compact mode
        with _patch("shutil.get_terminal_size", return_value=os.terminal_size((120, 40))):
            obj.show_banner()

    assert captured_model is not None, "build_welcome_banner was never called"
    assert captured_model == RESOLVED_MODEL, (
        f"Expected model={RESOLVED_MODEL!r}, got {captured_model!r} — "
        f"provider slug {PROVIDER_SLUG!r} was not resolved through custom_providers"
    )
