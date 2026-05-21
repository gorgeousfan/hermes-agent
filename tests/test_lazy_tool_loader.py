import json

import model_tools
from tools.lazy_tool_loader import TOOL_PACKS, ToolPack, load_tool_pack_for_agent
from tools.registry import registry


def _dummy_handler(args, **kwargs):
    return "{}"


def _make_schema(name: str):
    return {
        "name": name,
        "description": f"{name} test tool",
        "parameters": {"type": "object", "properties": {}},
    }


def test_visible_tools_env_filters_schemas_without_deregistering(monkeypatch):
    shown = "test_visible_tools_alpha"
    hidden = "test_visible_tools_beta"
    try:
        registry.register(
            name=shown,
            toolset="test-visible-tools",
            schema=_make_schema(shown),
            handler=_dummy_handler,
        )
        registry.register(
            name=hidden,
            toolset="test-visible-tools",
            schema=_make_schema(hidden),
            handler=_dummy_handler,
        )
        model_tools._clear_tool_defs_cache()
        monkeypatch.delenv("HERMES_TUI_VISIBLE_TOOLS", raising=False)
        monkeypatch.setenv("HERMES_VISIBLE_TOOLS", shown)

        definitions = model_tools.get_tool_definitions(
            enabled_toolsets=["test-visible-tools"],
            quiet_mode=True,
        )

        assert [definition["function"]["name"] for definition in definitions] == [shown]
        assert registry.get_entry(hidden) is not None
    finally:
        registry.deregister(shown)
        registry.deregister(hidden)
        model_tools._clear_tool_defs_cache()


def test_tui_visible_tools_env_takes_precedence(monkeypatch):
    global_tool = "test_visible_tools_global"
    tui_tool = "test_visible_tools_tui"
    try:
        registry.register(
            name=global_tool,
            toolset="test-visible-tools-precedence",
            schema=_make_schema(global_tool),
            handler=_dummy_handler,
        )
        registry.register(
            name=tui_tool,
            toolset="test-visible-tools-precedence",
            schema=_make_schema(tui_tool),
            handler=_dummy_handler,
        )
        model_tools._clear_tool_defs_cache()
        monkeypatch.setenv("HERMES_VISIBLE_TOOLS", global_tool)
        monkeypatch.setenv("HERMES_TUI_VISIBLE_TOOLS", tui_tool)

        definitions = model_tools.get_tool_definitions(
            enabled_toolsets=["test-visible-tools-precedence"],
            quiet_mode=True,
        )

        assert [definition["function"]["name"] for definition in definitions] == [tui_tool]
    finally:
        registry.deregister(global_tool)
        registry.deregister(tui_tool)
        model_tools._clear_tool_defs_cache()


def test_load_tool_pack_adds_registered_schemas_to_agent():
    tool_name = "mcp_botparlor_get_avatar_inventory"

    class Agent:
        def __init__(self):
            self.tools = []
            self.valid_tool_names = {"load_tool_pack"}

    try:
        registry.register(
            name=tool_name,
            toolset="mcp-botparlor",
            schema=_make_schema(tool_name),
            handler=_dummy_handler,
        )

        agent = Agent()
        result = json.loads(load_tool_pack_for_agent(agent, "avatar"))

        assert result["success"] is True
        assert tool_name in result["loaded"]
        assert tool_name in agent.valid_tool_names
        assert any(
            definition["function"]["name"] == tool_name
            for definition in agent.tools
        )
        assert "mcp_botparlor_create_avatar" in result["unavailable"]
        assert result["suggested_skills"] == ["botparlor-avatar-maintenance"]
    finally:
        registry.deregister(tool_name)


def test_assistant_pack_returns_skill_hints_and_loads_available_tools():
    tool_name = "test_assistant_pack_tool"
    pack_name = "test_assistant_pack"

    class Agent:
        def __init__(self):
            self.tools = []
            self.valid_tool_names = {"load_tool_pack"}

    try:
        TOOL_PACKS[pack_name] = ToolPack(
            description="Assistant pack test.",
            tools=[tool_name],
            suggested_skills=["codex", "test-driven-development"],
        )
        registry.register(
            name=tool_name,
            toolset="test-assistant-pack",
            schema=_make_schema(tool_name),
            handler=_dummy_handler,
        )

        agent = Agent()
        result = json.loads(load_tool_pack_for_agent(agent, pack_name))

        assert result["success"] is True
        assert tool_name in result["loaded"]
        assert tool_name in agent.valid_tool_names
        assert "codex" in result["suggested_skills"]
        assert "test-driven-development" in result["suggested_skills"]
    finally:
        registry.deregister(tool_name)
        TOOL_PACKS.pop(pack_name, None)
