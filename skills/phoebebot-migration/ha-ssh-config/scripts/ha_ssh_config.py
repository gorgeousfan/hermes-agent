#!/usr/bin/env python3
"""
HA SSH Config Helper — Safe YAML manipulation for Home Assistant config files.

Usage:
    python3 ha_ssh_config.py add <name> <command> [--dry-run]
    python3 ha_ssh_config.py remove <name> [--dry-run]
    python3 ha_ssh_config.py list
    python3 ha_ssh_config.py check
    python3 ha_ssh_config.py reload

Environment:
#     HASS_URL  - Home Assistant URL (default: http://homeassistant.local:8123)
    HASS_TOKEN - Home Assistant Long-Lived Access Token
    SSH_ALIAS - SSH alias for HA host (default: ha)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML, YAMLError
from ruamel.yaml.comments import CommentedMap

# ── Configuration ──────────────────────────────────────────────────────────

HASS_URL = os.getenv("HASS_URL", "http://homeassistant.local:8123")
HASS_TOKEN = os.getenv("HASS_TOKEN")
SSH_ALIAS = os.getenv("SSH_ALIAS", "ha")
CONFIG_FILE = "/config/configuration.yaml"
BACKUP_DIR = "/config"
MAX_BACKUPS = 10

# ── SSH Helpers ─────────────────────────────────────────────────────────────


def ssh_run(command: str) -> subprocess.CompletedProcess:
    """Run a command via SSH with fail-fast (1 attempt, clear error)."""
    try:
        result = subprocess.run(
            f"ssh {SSH_ALIAS} '{command}'",
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result
    except subprocess.TimeoutExpired:
        print(json.dumps({"status": "error", "message": "SSH command timed out"}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"SSH failed: {e}"}))
        sys.exit(1)


def ssh_read_file(filepath: str) -> str:
    """Read a file from the HA host via SSH."""
    result = ssh_run(f"cat {filepath}")
    if result.returncode != 0:
        print(json.dumps({"status": "error", "message": f"Failed to read {filepath}: {result.stderr.strip()}"}))
        sys.exit(1)
    return result.stdout


def ssh_write_file(filepath: str, content: str) -> None:
    """Write a file on the HA host via SSH using stdin pipe."""
    result = subprocess.run(
        f"ssh {SSH_ALIAS} 'cat > {filepath}'",
        shell=True,
        input=content,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        print(json.dumps({"status": "error", "message": f"Failed to write {filepath}: {result.stderr.strip()}"}))
        sys.exit(1)


# ── Backup Helpers ─────────────────────────────────────────────────────────


def create_backup() -> str:
    """Create a backup of configuration.yaml, keeping last MAX_BACKUPS."""
    timestamp = int(time.time())
    backup_name = f"configuration.yaml.bak.{timestamp}"
    backup_path = f"{BACKUP_DIR}/{backup_name}"

    # Copy current config to backup
    ssh_run(f"cp {CONFIG_FILE} {backup_path}")

    # Prune old backups (keep last MAX_BACKUPS)
    result = ssh_run(f"ls -t {BACKUP_DIR}/configuration.yaml.bak.* 2>/dev/null | tail -n +{MAX_BACKUPS + 1} | xargs rm -f 2>/dev/null")

    return backup_path


# ── YAML Helpers ───────────────────────────────────────────────────────────

_yaml = YAML(typ="rt")
_yaml.preserve_quotes = True


def parse_yaml(content: str) -> CommentedMap:
    """Parse YAML content, preserving HA custom tags (!include, !secret, etc) and comments."""
    try:
        data = _yaml.load(StringIO(content))
        return data if data is not None else CommentedMap()
    except YAMLError as e:
        print(json.dumps({"status": "error", "message": f"YAML parse error: {e}"}))
        sys.exit(1)


def serialize_yaml(data) -> str:
    """Serialize back to YAML, preserving HA custom tags and comments."""
    try:
        stream = StringIO()
        _yaml.dump(data, stream)
        return stream.getvalue()
    except YAMLError as e:
        print(json.dumps({"status": "error", "message": f"YAML serialize error: {e}"}))
        sys.exit(1)


def ensure_shell_command_section(data: CommentedMap) -> CommentedMap:
    """Ensure shell_command section exists in config data."""
    if "shell_command" not in data or not isinstance(data.get("shell_command"), (dict, CommentedMap)):
        data["shell_command"] = CommentedMap()
    return data


# ── Core Operations ────────────────────────────────────────────────────────


def add_shell_command(name: str, command: str, dry_run: bool = False) -> dict:
    """Add a shell_command entry."""
    # Validate name format
    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        return {
            "status": "error",
            "message": f"Invalid shell_command name: '{name}'. Must be lowercase letters, numbers, underscores (start with letter)."
        }

    # Read existing config
    content = ssh_read_file(CONFIG_FILE)
    data = parse_yaml(content)

    # Check if already exists (idempotency)
    shell_cmds = data.get("shell_command", {})
    if name in shell_cmds:
        return {"status": "exists", "name": name, "message": f"shell_command '{name}' already exists"}

    # Modify
    data = ensure_shell_command_section(data)
    data["shell_command"][name] = command

    # Serialize
    new_content = serialize_yaml(data)

    if dry_run:
        return {
            "status": "dry_run",
            "name": name,
            "command": command,
            "preview": new_content
        }

    # Backup first
    backup = create_backup()

    # Write
    ssh_write_file(CONFIG_FILE, new_content)

    # Verify via ha core check
    check_result = ha_core_check()
    if check_result.get("status") != "ok":
        # Restore backup on failure
        ssh_run(f"cp {backup} {CONFIG_FILE}")
        return {
            "status": "error",
            "name": name,
            "message": "ha core check failed after write. Config restored from backup.",
            "check_error": check_result.get("message", ""),
            "backup": backup
        }

    return {"status": "added", "name": name, "backup": backup}


def remove_shell_command(name: str, dry_run: bool = False) -> dict:
    """Remove a shell_command entry."""
    # Read existing config
    content = ssh_read_file(CONFIG_FILE)
    data = parse_yaml(content)

    # Check if exists
    shell_cmds = data.get("shell_command", {})
    if name not in shell_cmds:
        return {"status": "not_found", "name": name, "message": f"shell_command '{name}' not found"}

    # Modify
    if "shell_command" in data:
        del data["shell_command"][name]
        # Remove section if empty
        if not data["shell_command"]:
            del data["shell_command"]

    # Serialize
    new_content = serialize_yaml(data)

    if dry_run:
        return {
            "status": "dry_run",
            "name": name,
            "preview": new_content
        }

    # Backup first
    backup = create_backup()

    # Write
    ssh_write_file(CONFIG_FILE, new_content)

    # Verify via ha core check
    check_result = ha_core_check()
    if check_result.get("status") != "ok":
        # Restore backup on failure
        ssh_run(f"cp {backup} {CONFIG_FILE}")
        return {
            "status": "error",
            "name": name,
            "message": "ha core check failed after write. Config restored from backup.",
            "check_error": check_result.get("message", ""),
            "backup": backup
        }

    return {"status": "removed", "name": name, "backup": backup}


def list_shell_commands() -> dict:
    """List all shell_command entries."""
    content = ssh_read_file(CONFIG_FILE)
    data = parse_yaml(content)

    shell_cmds = data.get("shell_command", {})
    if not isinstance(shell_cmds, dict):
        shell_cmds = {}

    return {
        "status": "ok",
        "count": len(shell_cmds),
        "entries": shell_cmds
    }


# ── HA Core Operations ─────────────────────────────────────────────────────


def ha_core_check() -> dict:
    """Run ha core check."""
    result = ssh_run("ha core check")
    if result.returncode != 0:
        return {
            "status": "error",
            "message": result.stderr.strip() or result.stdout.strip()
        }
    return {"status": "ok", "message": "Configuration valid"}


def ha_core_reload() -> dict:
    """Reload core config via HA API."""
    try:
        result = subprocess.run(
            f"curl -s -X POST -H 'Authorization: Bearer {HASS_TOKEN}' '{HASS_URL}/api/services/homeassistant/reload_core_config'",
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return {"status": "reloaded", "code": 200}
        return {"status": "error", "message": result.stderr.strip() or result.stdout.strip()}
    except Exception as e:
        return {"status": "error", "message": f"Reload failed: {e}"}


# ── CLI Interface ──────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="HA SSH Config Helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # add
    add_parser = subparsers.add_parser("add", help="Add a shell_command")
    add_parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    add_parser.add_argument("name", help="Command name")
    add_parser.add_argument("shell_command", nargs="+", help="Shell command (quote if contains spaces)")

    # remove
    remove_parser = subparsers.add_parser("remove", help="Remove a shell_command")
    remove_parser.add_argument("name", help="Command name")
    remove_parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")

    # list
    subparsers.add_parser("list", help="List all shell_commands")

    # check
    subparsers.add_parser("check", help="Run ha core check")

    # reload
    subparsers.add_parser("reload", help="Reload core config")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Dispatch
    if args.command == "add":
        result = add_shell_command(args.name, " ".join(args.shell_command), dry_run=args.dry_run)
    elif args.command == "remove":
        result = remove_shell_command(args.name, dry_run=args.dry_run)
    elif args.command == "list":
        result = list_shell_commands()
    elif args.command == "check":
        result = ha_core_check()
    elif args.command == "reload":
        result = ha_core_reload()
    else:
        parser.print_help()
        sys.exit(1)

    # Output JSON
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
