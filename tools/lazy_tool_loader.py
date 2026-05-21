"""Session-local lazy tool loading.

Tools remain registered in the global registry, but this helper can add
selected schemas to a running agent after session start.
"""

import json
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Set

from tools.registry import registry


TOOL_NAME = "load_tool_pack"

@dataclass(frozen=True)
class ToolPack:
    description: str
    tools: List[str]
    suggested_skills: List[str] = field(default_factory=list)


SKILL_READ_TOOLS = ["skills_list", "skill_view"]
SKILL_EDIT_TOOLS = ["skills_list", "skill_view", "skill_manage"]
BROWSER_TOOLS = [
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_scroll",
    "browser_back",
    "browser_press",
    "browser_get_images",
    "browser_vision",
    "browser_console",
    "browser_cdp",
    "browser_dialog",
]
FILE_TOOLS = ["read_file", "write_file", "patch", "search_files"]
TERMINAL_TOOLS = ["terminal", "process"]
HA_TOOLS = ["ha_list_entities", "ha_get_state", "ha_list_services", "ha_call_service"]


TOOL_PACKS: Dict[str, ToolPack] = {
    "avatar": ToolPack(
        description="BotParlor avatar inventory and avatar-generation tools.",
        tools=[
            "mcp_botparlor_get_avatar_inventory",
            "mcp_botparlor_get_outfits",
            "mcp_botparlor_create_avatar",
            "mcp_botparlor_get_avatar_job",
            "mcp_botparlor_get_moods",
        ],
        suggested_skills=["botparlor-avatar-maintenance"],
    ),
    "reminders": ToolPack(
        description="BotParlor reminder and scheduled-message tools.",
        tools=[
            "mcp_botparlor_create_reminder",
            "mcp_botparlor_list_reminders",
            "mcp_botparlor_update_reminder",
            "mcp_botparlor_cancel_reminder",
        ],
    ),
    "media": ToolPack(
        description="BotParlor media display controls.",
        tools=[
            "mcp_botparlor_display_media",
            "mcp_botparlor_close_media",
        ],
    ),
    "botparlor_resources": ToolPack(
        description="BotParlor MCP resources and prompt templates.",
        tools=[
            "mcp_botparlor_list_resources",
            "mcp_botparlor_read_resource",
            "mcp_botparlor_list_prompts",
            "mcp_botparlor_get_prompt",
        ],
        suggested_skills=["botparlor"],
    ),
    "recall": ToolPack(
        description="Past-session search and recall.",
        tools=["session_search"],
    ),
    "skills": ToolPack(
        description="Generic skill listing, viewing, and management.",
        tools=SKILL_EDIT_TOOLS,
    ),
    "coding": ToolPack(
        description="Coding, repository work, debugging, tests, GitHub workflows, and subagents.",
        tools=[
            *SKILL_EDIT_TOOLS,
            *TERMINAL_TOOLS,
            *FILE_TOOLS,
            "todo",
            "execute_code",
            "delegate_task",
        ],
        suggested_skills=[
            "claude-code",
            "codex",
            "github-auth",
            "github-code-review",
            "github-issues",
            "github-pr-workflow",
            "github-repo-management",
            "plan",
            "python-debugpy",
            "subagent-driven-development",
            "systematic-debugging",
            "test-driven-development",
            "writing-plans",
        ],
    ),
    "web_research": ToolPack(
        description="Web search, extraction, browser automation, social/video/PDF research.",
        tools=[
            *SKILL_READ_TOOLS,
            "web_search",
            "web_extract",
            "x_search",
            *BROWSER_TOOLS,
            "vision_analyze",
        ],
        suggested_skills=[
            "web-browsing",
            "youtube-content",
            "x-twitter",
            "nano-pdf",
        ],
    ),
    "local_ops": ToolPack(
        description="Local machine, network, maps, and Home Assistant operations.",
        tools=[
            *SKILL_READ_TOOLS,
            *TERMINAL_TOOLS,
            *HA_TOOLS,
        ],
        suggested_skills=[
            "find-nearby",
            "maps",
            "network-device-discovery",
            "homeassistant",
            "godmode",
        ],
    ),
    "hermes_admin": ToolPack(
        description="Hermes internals, MCP work, skill authoring, and TUI debugging.",
        tools=[
            *SKILL_EDIT_TOOLS,
            *TERMINAL_TOOLS,
            *FILE_TOOLS,
            "execute_code",
            "delegate_task",
        ],
        suggested_skills=[
            "hermes-agent",
            "hermes-agent-communication",
            "debugging-hermes-tui-commands",
            "hermes-agent-skill-authoring",
            "mcporter",
            "native-mcp",
        ],
    ),
    "notes": ToolPack(
        description="Note taking, Obsidian, and local knowledge capture.",
        tools=[
            *SKILL_READ_TOOLS,
            *FILE_TOOLS,
            "memory",
        ],
        suggested_skills=["obsidian"],
    ),
    "design": ToolPack(
        description="Web design references and implementation support.",
        tools=[
            *SKILL_READ_TOOLS,
            *FILE_TOOLS,
            "web_search",
            "web_extract",
            "image_generate",
        ],
        suggested_skills=["popular-web-designs"],
    ),
    "persona": ToolPack(
        description="Bot personality, dogfood, and security-rule skills.",
        tools=SKILL_READ_TOOLS,
        suggested_skills=[
            "dogfood",
            "viper-personality",
            "7-day-security-rule",
        ],
    ),
    "power": ToolPack(
        description="Broad legacy pack for loading most general Hermes tools.",
        tools=[
            "web_search",
            "web_extract",
            *TERMINAL_TOOLS,
            *FILE_TOOLS,
            *BROWSER_TOOLS,
            "vision_analyze",
            "image_generate",
            "text_to_speech",
            "todo",
            "execute_code",
            "delegate_task",
        ],
    ),
}


def _unique_tool_names(names: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for name in names:
        clean = str(name or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _current_agent_tool_names(agent) -> Set[str]:
    current = set(getattr(agent, "valid_tool_names", set()) or set())
    for tool_def in getattr(agent, "tools", []) or []:
        name = tool_def.get("function", {}).get("name")
        if name:
            current.add(name)
    return current


def load_tool_pack_for_agent(agent, pack: str) -> str:
    """Add schemas from *pack* to a running agent and return a JSON result."""
    pack_name = str(pack or "").strip()
    if pack_name not in TOOL_PACKS:
        return json.dumps(
            {
                "success": False,
                "error": f"Unknown tool pack: {pack_name}",
                "available_packs": sorted(TOOL_PACKS),
            }
        )

    tool_pack = TOOL_PACKS[pack_name]
    requested = _unique_tool_names(tool_pack.tools)
    current_names = _current_agent_tool_names(agent)
    definitions = registry.get_definitions(set(requested), quiet=True)
    definitions_by_name = {
        definition["function"]["name"]: definition
        for definition in definitions
        if definition.get("function", {}).get("name")
    }

    existing_tools = list(getattr(agent, "tools", []) or [])
    loaded: List[str] = []
    already_available: List[str] = []
    unavailable: List[str] = []

    for name in requested:
        definition = definitions_by_name.get(name)
        if definition is None:
            unavailable.append(name)
            continue
        if name in current_names:
            already_available.append(name)
            continue
        existing_tools.append(definition)
        current_names.add(name)
        loaded.append(name)

    agent.tools = existing_tools
    agent.valid_tool_names = current_names

    return json.dumps(
        {
            "success": True,
            "pack": pack_name,
            "description": tool_pack.description,
            "loaded": loaded,
            "already_available": already_available,
            "unavailable": unavailable,
            "suggested_skills": tool_pack.suggested_skills,
            "message": "Loaded tools are available on the next model call.",
        }
    )


def _load_tool_pack_handler(args, **kwargs):
    return json.dumps(
        {
            "success": False,
            "error": "load_tool_pack requires an active agent session.",
        }
    )


registry.register(
    name=TOOL_NAME,
    toolset="tool_loader",
    schema={
        "name": TOOL_NAME,
        "description": (
            "Load optional tools into this session by pack name. The result may "
            "include suggested_skills that can be loaded with skill_view."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pack": {
                    "type": "string",
                    "enum": sorted(TOOL_PACKS),
                    "description": "Tool pack to load.",
                }
            },
            "required": ["pack"],
        },
    },
    handler=_load_tool_pack_handler,
    description=(
        "Load optional tools into this session by pack name. The result may "
        "include suggested_skills that can be loaded with skill_view."
    ),
)
