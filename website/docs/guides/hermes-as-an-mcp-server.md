---
sidebar_position: 7
title: "Run Hermes as an MCP server"
description: "Expose Hermes Agent conversations, messaging, approvals, and events to MCP clients"
---

# Run Hermes as an MCP server

Hermes can run as a local MCP server so other MCP-capable clients can talk to a running Hermes workspace.

Use this when you want another agent or IDE to:

- list and inspect Hermes conversations
- send messages through Hermes-supported platforms
- poll gateway events
- hand work to Hermes without leaving your MCP client
- keep Hermes' memory, skills, cron jobs, and approval flows as the operating layer

## Start the server

From a machine with Hermes installed by the standard installer, MCP support is already included.
If you installed from PyPI manually, include the MCP extra:

```bash
pip install "hermes-agent[mcp]"
```

Then start the stdio server:

```bash
hermes mcp serve
```

For debugging:

```bash
hermes mcp serve --verbose
```

The server uses stdio transport. Configure your MCP client to launch `hermes mcp serve` as the command.

## Example client configuration

```json
{
  "mcpServers": {
    "hermes": {
      "command": "hermes",
      "args": ["mcp", "serve"]
    }
  }
}
```

If Hermes is installed in a virtual environment or custom location, use the absolute path to the `hermes` executable.

## Package registry metadata

This repository includes `server.json` for MCP registry discovery. The package is published on PyPI as `hermes-agent`; registry metadata includes the `mcp` extra because the server depends on the MCP SDK. The intended runtime command is:

```bash
hermes mcp serve
```

If an MCP client does not yet understand PyPI extras in `server.json`, install Hermes with `pip install "hermes-agent[mcp]"` first and configure the client with the explicit command shown above.

When publishing or validating registry metadata, use the server name:

```text
io.github.NousResearch/hermes-agent
```

## Safety notes

Hermes can bridge to real messaging platforms and tools. Treat an MCP client connected to Hermes as a privileged operator:

- only connect trusted MCP clients
- keep command approvals enabled for risky actions
- prefer dry-run or preview flows before sending messages
- review outbound communication policies for shared team workspaces
- avoid exposing secrets through environment variables unless they are required

## Commercial support

Teams that want Hermes deployed with MCP, messaging gateways, internal tools, or custom skills can use the optional [commercial support](https://github.com/NousResearch/hermes-agent/blob/main/COMMERCIAL_SUPPORT.md) path described in the repository.
