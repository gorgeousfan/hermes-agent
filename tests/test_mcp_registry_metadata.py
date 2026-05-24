"""Regression tests for MCP registry metadata."""

from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_server_json() -> dict:
    with (REPO_ROOT / "server.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def test_server_json_launches_hermes_mcp_server() -> None:
    metadata = _load_server_json()

    assert metadata["name"] == "io.github.nousresearch/hermes-agent"
    assert metadata["repository"]["url"] == "https://github.com/NousResearch/hermes-agent"

    package = metadata["packages"][0]
    assert package["registryType"] == "pypi"
    assert package["registryBaseUrl"] == "https://pypi.org"
    assert package["identifier"] == "hermes-agent"
    assert package["transport"] == {"type": "stdio"}
    assert package["runtimeHint"] == "uvx"
    assert package["packageArguments"] == [
        {"type": "positional", "value": "mcp"},
        {"type": "positional", "value": "serve"},
    ]


def test_server_json_documents_mcp_extra_fallback() -> None:
    metadata = _load_server_json()
    publisher_meta = metadata["_meta"]["io.modelcontextprotocol.registry/publisher-provided"]
    hermes_meta = publisher_meta["io.github.nousresearch.hermes-agent"]

    assert "hermes-agent[mcp]" in hermes_meta["fallbackInstall"]
    assert hermes_meta["fallbackCommand"] == "hermes mcp serve"
    assert "messages_send" in hermes_meta["knownTools"]
    assert "permissions_respond" in hermes_meta["knownTools"]


def test_readme_contains_mcp_registry_ownership_marker() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "mcp-name: io.github.nousresearch/hermes-agent" in readme
