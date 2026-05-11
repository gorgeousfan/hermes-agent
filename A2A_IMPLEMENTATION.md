# A2A Protocol Support - Implementation Summary

## Overview

This PR implements Agent Card generation for the A2A (Agent-to-Agent) protocol, enabling Hermes Agent to be discovered by other A2A-compliant agents.

**Related Issue:** https://github.com/NousResearch/hermes-agent/issues/514

## What was implemented

### 1. Agent Card Generation Module (`agent/a2a/`)

- `agent/a2a/__init__.py` - Package entry point
- `agent/a2a/agent_card.py` - Complete Agent Card generation with:
  - A2A-compliant data classes (AgentCard, AgentSkill, AgentCapabilities, AgentInterface)
  - Mapping of Hermes tools to A2A skills
  - Configurable base URL and capabilities
  - JSON serialization support

### 2. API Server Endpoint (`gateway/platforms/api_server.py`)

- Added `GET /.well-known/agent.json` endpoint
- Returns A2A-compliant Agent Card JSON
- Includes CORS headers for cross-origin access
- Cache-Control headers (1 hour) to reduce server load

### 3. Tests (`tests/gateway/test_api_server.py`)

- `TestAgentCard` test class with 4 tests:
  - Agent Card endpoint returns valid JSON
  - CORS headers for cross-origin access
  - Cache-Control headers present
  - Skills list includes core Hermes capabilities

### 4. Dependencies (`pyproject.toml`)

- Added `[a2a]` optional dependency: `a2a-sdk>=1.0.0,<2`
- Added `a2a` to `[all]` extra

## Agent Card Output

```json
{
  "name": "Hermes Agent",
  "description": "Self-improving AI agent with memory, skills, and tool ecosystem...",
  "version": "1.0.0",
  "url": "http://localhost:8642/.well-known/agent.json",
  "capabilities": {
    "streaming": true,
    "push_notifications": false
  },
  "supported_interfaces": [{
    "url": "http://localhost:8642",
    "protocol_binding": "JSONRPC",
    "protocol_version": "1.0"
  }],
  "skills": [
    {"id": "hermes_memory", "name": "Persistent Memory", ...},
    {"id": "delegate_task", "name": "Task Delegation", ...},
    ...
  ],
  "provider": {
    "name": "Nous Research",
    "url": "https://hermes-agent.nousresearch.com"
  }
}
```

## Testing

```bash
# Run agent card tests
pytest tests/gateway/test_api_server.py::TestAgentCard -v

# Generate agent card directly
python -c "from agent.a2a import generate_agent_card; print(generate_agent_card().to_json())"
```

## Next Steps (Future PRs)

### Phase 2: A2A Client
- Implement `tools/a2a_tool.py` for discovering and calling remote A2A agents
- Agent Card resolution from remote URLs
- Task management (send message, get task, list tasks)

### Phase 3: A2A Server (Full)
- Implement JSON-RPC endpoints for A2A protocol operations
- Task lifecycle management (create, update, complete, cancel)
- Streaming support via Server-Sent Events
- Push notification support

## References

- A2A Protocol Specification: https://a2a-protocol.org/latest/specification/
- A2A Python SDK: https://github.com/a2aproject/a2a-python
- Hermes Issue #514: https://github.com/NousResearch/hermes-agent/issues/514