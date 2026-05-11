"""
A2A (Agent-to-Agent) Protocol Support for Hermes Agent.

This module provides A2A protocol capabilities:
- Agent Card generation (/.well-known/agent.json)
- A2A client functionality (future)
- A2A server functionality (future)

A2A is an open standard (Apache 2.0, Linux Foundation) for inter-agent communication.
It complements MCP: MCP connects agents to tools, A2A connects agents to agents.

Related: https://github.com/NousResearch/hermes-agent/issues/514
"""

from agent.a2a.agent_card import (
    generate_agent_card,
    get_agent_card_json,
    get_well_known_agent_card_endpoint,
)

__all__ = [
    "generate_agent_card",
    "get_agent_card_json",
    "get_well_known_agent_card_endpoint",
]