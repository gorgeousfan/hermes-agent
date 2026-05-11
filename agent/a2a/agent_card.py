"""
Agent Card Generation for A2A Protocol.

Generates A2A Agent Cards (/.well-known/agent.json) that describe Hermes Agent's
capabilities, skills, and endpoints for inter-agent discovery.

A2A Protocol Reference: https://a2a-protocol.org/latest/specification/
Hermes Issue: https://github.com/NousResearch/hermes-agent/issues/514
"""

import json
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

# A2A Protocol version we support
A2A_PROTOCOL_VERSION = "1.0"

# Default transport binding
DEFAULT_TRANSPORT = "JSONRPC"  # JSON-RPC 2.0 over HTTP

# Default input/output modes
DEFAULT_INPUT_MODES = ["text/plain"]
DEFAULT_OUTPUT_MODES = ["text/plain"]


@dataclass
class AgentSkill:
    """
    Describes a specific capability or function the agent can perform.
    
    Maps to A2A AgentSkill protobuf message.
    """
    id: str
    name: str
    description: str
    tags: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    input_modes: List[str] = field(default_factory=list)
    output_modes: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
        }
        if self.tags:
            result["tags"] = self.tags
        if self.examples:
            result["examples"] = self.examples
        if self.input_modes:
            result["input_modes"] = self.input_modes
        if self.output_modes:
            result["output_modes"] = self.output_modes
        return result


@dataclass
class AgentCapabilities:
    """
    Describes the capabilities supported by the agent.
    
    Maps to A2A AgentCapabilities protobuf message.
    """
    streaming: bool = True
    push_notifications: bool = False
    extensions: List[Dict[str, Any]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "streaming": self.streaming,
            "push_notifications": self.push_notifications,
        }
        if self.extensions:
            result["extensions"] = self.extensions
        return result


@dataclass
class AgentInterface:
    """
    Describes a transport interface for the agent.
    
    Maps to A2A AgentInterface protobuf message.
    """
    url: str
    protocol_binding: str = "JSONRPC"  # JSONRPC, GRPC, or HTTP_JSON
    protocol_version: str = A2A_PROTOCOL_VERSION
    tenant: str = ""  # Optional tenant for multi-tenant
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "url": self.url,
            "protocol_binding": self.protocol_binding,
            "protocol_version": self.protocol_version,
        }
        if self.tenant:
            result["tenant"] = self.tenant
        return result


@dataclass
class AgentProvider:
    """
    Information about the agent's provider/organization.
    
    Maps to A2A AgentProvider protobuf message.
    """
    name: str
    url: str = ""
    version: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        result = {"name": self.name}
        if self.url:
            result["url"] = self.url
        if self.version:
            result["version"] = self.version
        return result


@dataclass
class SecurityScheme:
    """
    Describes a security scheme for authentication.
    
    A2A uses standard OpenAPI-style security schemes.
    """
    id: str
    type: str  # "apiKey", "http", "oauth2", "openIdConnect"
    description: str = ""
    name: str = ""  # For apiKey type
    in_: str = ""  # "header", "query", "cookie" for apiKey
    scheme: str = ""  # For http type (e.g., "bearer")
    flows: Dict[str, Any] = field(default_factory=dict)  # For oauth2
    
    def to_dict(self) -> Dict[str, Any]:
        result = {"id": self.id, "type": self.type}
        if self.description:
            result["description"] = self.description
        if self.type == "apiKey":
            if self.name:
                result["name"] = self.name
            if self.in_:
                result["in"] = self.in_
        elif self.type == "http":
            if self.scheme:
                result["scheme"] = self.scheme
        elif self.type == "oauth2":
            if self.flows:
                result["flows"] = self.flows
        return result


@dataclass
class AgentCard:
    """
    A2A Agent Card - the "business card" for agent discovery.
    
    This is the complete AgentCard definition that gets served at
    /.well-known/agent.json for A2A protocol compliance.
    
    Reference: https://a2a-protocol.org/latest/specification/#411-agentcard
    """
    name: str
    description: str
    version: str
    url: str
    capabilities: AgentCapabilities
    supported_interfaces: List[AgentInterface]
    default_input_modes: List[str] = field(default_factory=lambda: DEFAULT_INPUT_MODES)
    default_output_modes: List[str] = field(default_factory=lambda: DEFAULT_OUTPUT_MODES)
    skills: List[AgentSkill] = field(default_factory=list)
    provider: Optional[AgentProvider] = None
    documentation_url: str = ""
    icon_url: str = ""
    security_schemes: Dict[str, SecurityScheme] = field(default_factory=dict)
    security_requirements: List[Dict[str, List[str]]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to A2A-compatible dictionary."""
        result = {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "url": self.url,
            "capabilities": self.capabilities.to_dict(),
            "supported_interfaces": [i.to_dict() for i in self.supported_interfaces],
            "default_input_modes": self.default_input_modes,
            "default_output_modes": self.default_output_modes,
            "skills": [s.to_dict() for s in self.skills],
        }
        if self.provider:
            result["provider"] = self.provider.to_dict()
        if self.documentation_url:
            result["documentation_url"] = self.documentation_url
        if self.icon_url:
            result["icon_url"] = self.icon_url
        if self.security_schemes:
            result["security_schemes"] = {
                k: v.to_dict() for k, v in self.security_schemes.items()
            }
        if self.security_requirements:
            result["security_requirements"] = self.security_requirements
        return result
    
    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)


# ---------------------------------------------------------------------------
# Hermes-specific Agent Card generation
# ---------------------------------------------------------------------------

# Map Hermes tool names to human-readable A2A Skills
_HERMES_TOOL_TO_SKILL: Dict[str, AgentSkill] = {
    # Core tools
    "web_search": AgentSkill(
        id="web_search",
        name="Web Search",
        description="Search the web for information using Brave Search API",
        tags=["research", "web", "search"],
    ),
    "web_extract": AgentSkill(
        id="web_extract",
        name="Web Content Extraction",
        description="Extract and parse content from web pages",
        tags=["research", "web", "extraction"],
    ),
    "terminal": AgentSkill(
        id="terminal",
        name="Terminal Execution",
        description="Execute shell commands in a Linux environment",
        tags=["execution", "shell", "commands"],
    ),
    "process": AgentSkill(
        id="process",
        name="Process Management",
        description="Manage background processes started with terminal",
        tags=["execution", "process", "background"],
    ),
    "read_file": AgentSkill(
        id="read_file",
        name="File Reading",
        description="Read text files with line numbers and pagination",
        tags=["file", "read", "content"],
    ),
    "write_file": AgentSkill(
        id="write_file",
        name="File Writing",
        description="Write content to files, creating parent directories automatically",
        tags=["file", "write", "create"],
    ),
    "patch": AgentSkill(
        id="patch",
        name="File Patching",
        description="Apply targeted find-and-replace edits to files",
        tags=["file", "edit", "patch"],
    ),
    "search_files": AgentSkill(
        id="search_files",
        name="File Search",
        description="Search file contents or find files by name using ripgrep",
        tags=["file", "search", "ripgrep"],
    ),
    "vision_analyze": AgentSkill(
        id="vision_analyze",
        name="Image Analysis",
        description="Analyze images from URLs or file paths using vision models",
        tags=["vision", "image", "analysis"],
    ),
    "image_generate": AgentSkill(
        id="image_generate",
        name="Image Generation",
        description="Generate images from text prompts using configured providers",
        tags=["image", "generation", "creative"],
    ),
    "skills_list": AgentSkill(
        id="skills_list",
        name="List Skills",
        description="List available skills (name + description)",
        tags=["skills", "discovery"],
    ),
    "skill_view": AgentSkill(
        id="skill_view",
        name="View Skill",
        description="Load a skill's full content including references and templates",
        tags=["skills", "knowledge"],
    ),
    "skill_manage": AgentSkill(
        id="skill_manage",
        name="Manage Skills",
        description="Create, update, or delete skills",
        tags=["skills", "management"],
    ),
    "todo": AgentSkill(
        id="todo",
        name="Task Management",
        description="Manage a task list for the current session",
        tags=["planning", "tasks", "todo"],
    ),
    "memory": AgentSkill(
        id="memory",
        name="Persistent Memory",
        description="Save durable information to persistent memory that survives across sessions",
        tags=["memory", "persistence", "context"],
    ),
    "session_search": AgentSkill(
        id="session_search",
        name="Session History Search",
        description="Search long-term memory of past conversations",
        tags=["memory", "search", "history"],
    ),
    "clarify": AgentSkill(
        id="clarify",
        name="Clarification",
        description="Ask the user a question when clarification is needed",
        tags=["interaction", "questions"],
    ),
    "execute_code": AgentSkill(
        id="execute_code",
        name="Code Execution",
        description="Run a Python script that can call Hermes tools programmatically",
        tags=["code", "python", "execution"],
    ),
    "delegate_task": AgentSkill(
        id="delegate_task",
        name="Task Delegation",
        description="Spawn one or more subagents to work on tasks in isolated contexts",
        tags=["delegation", "multi-agent", "parallel"],
    ),
    "cronjob": AgentSkill(
        id="cronjob",
        name="Cronjob Management",
        description="Manage scheduled cron jobs with scheduling, running, and monitoring",
        tags=["scheduling", "cron", "automation"],
    ),
    "send_message": AgentSkill(
        id="send_message",
        name="Cross-Platform Messaging",
        description="Send a message to a connected messaging platform (Discord, Telegram, etc.)",
        tags=["messaging", "communication"],
    ),
    "computer_use": AgentSkill(
        id="computer_use",
        name="Computer Use",
        description="Background desktop control via cua-driver (screenshots, mouse, keyboard)",
        tags=["desktop", "automation", "control"],
    ),
}

# Core Hermes skills that define the agent's identity
_HERMES_CORE_SKILLS = [
    AgentSkill(
        id="hermes_memory",
        name="Persistent Memory",
        description="Store and recall user facts, preferences, and context across sessions",
        tags=["memory", "persistence", "personalization"],
    ),
    AgentSkill(
        id="hermes_skills",
        name="Dynamic Skills",
        description="Create, load, and improve reusable skill documents for specialized tasks",
        tags=["skills", "learning", "automation"],
    ),
    AgentSkill(
        id="hermes_delegation",
        name="Task Delegation",
        description="Spawn sub-agents for parallel work with isolated contexts",
        tags=["delegation", "multi-agent", "orchestration"],
    ),
]


def _get_hermes_skills(enabled_toolsets: Optional[List[str]] = None) -> List[AgentSkill]:
    """
    Get list of A2A Skills representing Hermes Agent capabilities.
    
    Args:
        enabled_toolsets: Optional list of enabled toolset names to filter skills.
                        If None, returns all available skills.
    
    Returns:
        List of AgentSkill objects representing Hermes capabilities.
    """
    from toolsets import resolve_toolset, get_all_toolsets
    
    skills = list(_HERMES_CORE_SKILLS)
    
    if enabled_toolsets is None:
        # If no toolsets specified, resolve all to get all tools
        try:
            all_toolsets = get_all_toolsets()
            all_tools = set()
            for ts_name in all_toolsets:
                try:
                    tools = resolve_toolset(ts_name)
                    all_tools.update(tools)
                except Exception:
                    pass
            tool_names = list(all_tools)
        except Exception:
            # Fallback to core tools if toolsets module fails
            tool_names = []
    else:
        # Resolve specific toolsets
        tool_names = []
        try:
            for ts_name in enabled_toolsets:
                tools = resolve_toolset(ts_name)
                tool_names.extend(tools)
            tool_names = list(set(tool_names))  # Dedupe
        except Exception:
            pass
    
    # Map tool names to skills
    for tool_name in tool_names:
        if tool_name in _HERMES_TOOL_TO_SKILL:
            skills.append(_HERMES_TOOL_TO_SKILL[tool_name])
    
    return skills


def _get_base_url(config: Optional[Any] = None) -> str:
    """
    Determine the base URL for the A2A endpoint.
    
    Priority:
    1. Configured a2a.url in config.yaml
    2. API server host:port from config
    3. Default localhost:8642
    
    Args:
        config: Optional Hermes config object
        
    Returns:
        Base URL string for the A2A endpoint.
    """
    # Default
    base_url = "http://localhost:8642"
    
    if config is None:
        return base_url
    
    # Try config values
    try:
        # Check for explicit A2A config
        if hasattr(config, "a2a") and config.a2a:
            if hasattr(config.a2a, "url") and config.a2a.url:
                return config.a2a.url
    except Exception:
        pass
    
    try:
        # Check for API server config
        if hasattr(config, "api_server") and config.api_server:
            host = getattr(config.api_server, "host", "127.0.0.1")
            port = getattr(config.api_server, "port", 8642)
            # Use localhost for 0.0.0.0 binding
            if host in ("0.0.0.0", "::", ""):
                host = "localhost"
            return f"http://{host}:{port}"
    except Exception:
        pass
    
    return base_url


def generate_agent_card(
    config: Optional[Any] = None,
    enabled_toolsets: Optional[List[str]] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    version: Optional[str] = None,
    streaming: bool = True,
    push_notifications: bool = False,
) -> AgentCard:
    """
    Generate an A2A Agent Card for Hermes Agent.
    
    This function creates a standards-compliant Agent Card that describes
    Hermes Agent's capabilities for discovery by other A2A-compliant agents.
    
    Args:
        config: Optional Hermes config object for extracting URL and settings
        enabled_toolsets: Optional list of enabled toolset names
        name: Override agent name (default: "Hermes Agent")
        description: Override agent description
        version: Override agent version
        streaming: Whether streaming is supported (default: True)
        push_notifications: Whether push notifications are supported (default: False)
    
    Returns:
        AgentCard object ready for serialization to JSON.
    
    Example:
        >>> card = generate_agent_card()
        >>> json_string = card.to_json()
        >>> print(json_string)
    """
    # Get version from hermes if possible
    try:
        from hermes_constants import HERMES_VERSION
        hermes_version = HERMES_VERSION
    except ImportError:
        hermes_version = "1.0.0"
    
    base_url = _get_base_url(config)
    a2a_url = f"{base_url.rstrip('/')}/.well-known/agent.json"
    
    # Build capabilities
    capabilities = AgentCapabilities(
        streaming=streaming,
        push_notifications=push_notifications,
    )
    
    # Build interface
    interface = AgentInterface(
        url=base_url,
        protocol_binding=DEFAULT_TRANSPORT,
        protocol_version=A2A_PROTOCOL_VERSION,
    )
    
    # Get skills
    skills = _get_hermes_skills(enabled_toolsets)
    
    # Build provider info
    provider = AgentProvider(
        name="Nous Research",
        url="https://hermes-agent.nousresearch.com",
        version=hermes_version,
    )
    
    # Build the card
    card = AgentCard(
        name=name or "Hermes Agent",
        description=description or (
            "Self-improving AI agent with memory, skills, and tool ecosystem. "
            "Supports persistent memory across sessions, dynamic skill creation, "
            "cross-platform messaging, and task delegation to sub-agents."
        ),
        version=version or hermes_version,
        url=a2a_url,
        capabilities=capabilities,
        supported_interfaces=[interface],
        default_input_modes=DEFAULT_INPUT_MODES,
        default_output_modes=DEFAULT_OUTPUT_MODES,
        skills=skills,
        provider=provider,
        documentation_url="https://hermes-agent.nousresearch.com/docs",
    )
    
    return card


def get_agent_card_json(
    config: Optional[Any] = None,
    enabled_toolsets: Optional[List[str]] = None,
    **kwargs,
) -> str:
    """
    Get the Agent Card as a JSON string.
    
    Convenience wrapper around generate_agent_card() that returns JSON.
    
    Args:
        config: Optional Hermes config object
        enabled_toolsets: Optional list of enabled toolset names
        **kwargs: Additional arguments passed to generate_agent_card()
    
    Returns:
        JSON string of the Agent Card document.
    """
    card = generate_agent_card(
        config=config,
        enabled_toolsets=enabled_toolsets,
        **kwargs,
    )
    return card.to_json()


def get_well_known_agent_card_endpoint() -> str:
    """
    Get the well-known URI path for the Agent Card.
    
    Returns:
        The path component: /.well-known/agent.json
    """
    return "/.well-known/agent.json"


# ---------------------------------------------------------------------------
# Module-level test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Generate and print a sample Agent Card
    card = generate_agent_card()
    print("Hermes Agent A2A Agent Card:")
    print("=" * 60)
    print(card.to_json(indent=2))
    print("=" * 60)
    print(f"\nWell-known endpoint: {get_well_known_agent_card_endpoint()}")