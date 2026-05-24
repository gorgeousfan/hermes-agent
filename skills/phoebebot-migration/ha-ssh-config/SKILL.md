---
name: ha-ssh-config
description: "Safe SSH-based YAML config management for Home Assistant. Handles shell_command entries with parse-modify-serialize via ruamel.yaml (preserves !include + comments), auto-backup, and dry-run mode."
version: "1.0.0"
author: "punchtaylor"
license: "MIT"
---

# HA SSH Config Skill

Safe, idempotent YAML config management for Home Assistant via SSH. Uses ruamel.yaml in round-trip mode for parse-modify-serialize, preserving `!include` directives and comments.

## When to Use

- Adding/removing `shell_command` entries in Home Assistant
- Any time you need to edit HA config files via SSH safely
- When you want dry-run validation before committing changes

## Prerequisites

- Passwordless SSH to HA host (`ha` alias configured)
- ruamel.yaml installed: `pip3 install ruamel.yaml`
- `HASS_URL` and `HASS_TOKEN` env vars set in `~/.hermes/.env`

## Interface

| Command | Description | Example |
|---------|-------------|---------|
| `add <name> <command>` | Add shell_command entry | `add backup_config "cp /config/configuration.yaml /tmp/config_backup.yaml"` |
| `remove <name>` | Remove shell_command entry | `remove backup_config` |
| `list` | List all shell_commands | `list` |
| `check` | Run ha core check | `check` |
| `reload` | Reload core config | `reload` |
| `--dry-run` | Preview without writing | `add test_cmd "echo hello" --dry-run` |

## Usage Pattern

### Adding a shell_command

```bash
# Step 1: Add (with auto-backup + ha core check)
python3 ~/.hermes/skills/ha-ssh-config/scripts/ha_ssh_config.py add my_command "echo hello"

# Step 2: Reload to pick up changes
python3 ~/.hermes/skills/ha-ssh-config/scripts/ha_ssh_config.py reload

# Step 3: Verify via REST
curl -s -H "Authorization: Bearer $HASS_TOKEN" "$HASS_URL/api/services" | grep my_command
```

### Dry-run mode

```bash
# Preview changes without writing
python3 ~/.hermes/skills/ha-ssh-config/scripts/ha_ssh_config.py add test_cmd "echo test" --dry-run
```

### Removing a shell_command

```bash
# Remove + auto-backup + ha core check
python3 ~/.hermes/skills/ha-ssh-config/scripts/ha_ssh_config.py remove my_command

# Reload
python3 ~/.hermes/skills/ha-ssh-config/scripts/ha_ssh_config.py reload
```

## Error Handling

- **YAML errors:** Caught by `ha core check` before reload — config restored from backup if check fails
- **SSH failures:** Fail-fast with clear error message (no retry)
- **Idempotency:** Add/remove operations check-before-write — safe to retry
- **Auto-backup:** Config backed up before every write (last 10 kept)

## Idempotency Story

| Operation | First Call | Second Call |
|-----------|------------|-------------|
| `add` (exists) | Returns `added` | Returns `exists` — no modification |
| `remove` (exists) | Returns `removed` | Returns `not_found` — no modification |
| `check` | Runs check | Runs check (safe) |
| `reload` | Reloads config | Reloads config (safe) |

## Testing Pattern

1. Add entry → verify via REST API
2. Remove entry → verify gone via REST API
3. Config clean (no leftover entries)

## File Locations

- Helper script: `~/.hermes/skills/ha-ssh-config/scripts/ha_ssh_config.py`
- Skill doc: `~/.hermes/skills/ha-ssh-config/SKILL.md`
- Backups: `/config/configuration.yaml.bak.<timestamp>` (on HA host)

## Troubleshooting

- If `ha core check` fails after a write, the script auto-restores from backup. Check the error message for YAML syntax issues.
- If SSH commands time out, verify network connectivity and that the `ha` alias resolves.
- **`!include` tags require ruamel.yaml:** Standard PyYAML's `safe_load` chokes on `!include`, `!include_dir_merge_named`, etc. The script uses ruamel.yaml in round-trip mode to preserve these tags through parse-modify-serialize. Install: `pip3 install ruamel.yaml` — required, no fallback.
- **SSH write pattern for HA OS:** Must use stdin pipe: `ssh ha 'cat > /path'` with `input=content`. Other patterns fail on HA OS: direct `>` redirection gives "No such file or directory", `scp` fails across Docker container filesystems, `mv` fails across mounts, `tee` has quoting issues.
- **Service cache after reload:** `ha core reload` picks up new entries but doesn't clear the cache for removed entries. A full `ha core restart` is needed to purge stale service entries (may take 15-20s).
- **argparse gotcha:** Don't name a positional arg `command` when using subparsers with `dest='command'` — they collide. Use `shell_command` or similar.

## Status

- ✅ Sprint 3 v1 complete (2026-05-15)
- ✅ Full empirical test cycle passed: add → reload → verify via REST → remove → reload → list clean
- ✅ ruamel.yaml preserves `!include` tags and comments correctly
- ✅ SSH stdin pipe write pattern validated on HA OS
- ✅ Auto-backup, dry-run, idempotency all working

## Future (Sprint 4+)

- automations.yaml support
- scripts.yaml support
- scenes.yaml support
- Multi-entry operations
- SSH retry with backoff (if proven needed)
