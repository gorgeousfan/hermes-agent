# Security policy checks

Hermes Tier 7 adds a metadata-only security policy audit surface. Its job is to make risky autonomy routes explicit without exposing the sensitive values that make those routes work.

## Surface

- Harness API: `HermesHarness().control_plane.security_policy()`
- Dashboard/API endpoint: `GET /api/harness/security-policy`
- Mode: `audit_only_no_side_effects`
- Content policy: `metadata_only`

The audit does **not** enforce policy, create jobs, mutate config, connect to external services, or print raw credential material. It returns booleans, counts, modes, and issue codes.

## What it checks

The audit summarizes:

- credential redaction posture, including `HERMES_REDACT_SECRETS` override state
- approval mode, YOLO mode, cron dangerous-command policy, destructive slash confirmation, and MCP reload confirmation
- Tirith pre-exec scanner state and `TIRITH_ENABLED` / `TIRITH_FAIL_OPEN` override behavior
- terminal backend class, host execution exposure, Docker host mounts, and Codex app-server permission profile
- private/internal URL access for web/browser paths
- gateway ingress posture: allowlists, allow-all flags, API server key presence, webhook credential presence, and enabled ingress count
- MCP server inventory by transport and credential-binding counts
- runtime install/supply-chain posture for lazy installs and LSP auto-install
- shell hook auto-accept posture

## Output contract

The surface returns structural fields only:

```json
{
  "schema_version": 1,
  "content_policy": "metadata_only",
  "mode": "audit_only_no_side_effects",
  "policy": {...},
  "checks": {...},
  "approval_matrix": [...],
  "profile_permission_matrix": [...],
  "credential_inventory": {...},
  "issues": [...],
  "issue_count": 0,
  "highest_severity": "none"
}
```

It must not include:

- API keys or bearer values
- OAuth credentials
- passwords
- webhook signing credentials
- connection strings
- raw MCP server names, URLs, headers, commands, env values, or OAuth credential values
- private filesystem paths
- user IDs, chat IDs, channel IDs, or prompt content

## Issue severity

Common issue codes include:

| Code | Severity | Meaning |
| --- | --- | --- |
| `redaction_disabled` | critical | Global credential redaction is disabled. |
| `approval_bypass_enabled` | critical | YOLO mode bypasses dangerous-command prompts. |
| `approval_mode_off` | critical | Dangerous command approvals are globally disabled. |
| `host_execution_with_approval_bypass` | critical | Host-capable terminal execution is paired with approval bypass. |
| `api_server_enabled_without_key` | critical | OpenAI-compatible API ingress is enabled without an API key. |
| `cron_auto_approve_enabled` | high | Cron jobs auto-approve dangerous commands. |
| `tirith_disabled` | high | Pre-exec command scanning is disabled. |
| `private_url_access_enabled` | high | Web tools may target private/internal network addresses. |
| `gateway_allow_all_enabled` | high | One or more gateway surfaces accept all users. |
| `webhook_enabled_without_credential` | high | Webhook ingress is enabled without a signing credential. |
| `codex_full_access_permission_profile` | high | Codex app-server is mapped to full filesystem access. |
| `lazy_installs_enabled` | medium | Optional backend packages may install at runtime. |
| `lsp_auto_install_enabled` | medium | Language server binaries may install automatically. |
| `mcp_credential_bindings_present` | info | MCP config has credential-bearing bindings; values are omitted. |

## Approval matrix

The `approval_matrix` makes side-effect rules inspectable per surface:

- CLI dangerous commands
- cron dangerous commands
- gateway dangerous commands and destructive session commands
- MCP reload cache/cost confirmation

A healthy production-like profile should keep dangerous commands on `manual` or `smart`, keep cron on `deny`, and keep destructive slash/MCP reload confirmations enabled unless there is an explicit operational reason.

## Profile permission matrix

The `profile_permission_matrix` summarizes the active profile’s execution class:

- terminal backend
- process scope: `host` or `sandbox`
- Codex app-server permission profile
- whether public remote shell exposure is keyed/allowlisted or needs review

This is intentionally a summary. It does not include working directories, mount paths, SSH hosts, gateway chat IDs, or API endpoint URLs.

## Security acceptance

Tier 7 is considered clean when:

1. risky routes are explicit as issue codes or check fields;
2. external/irreversible side effects remain approval-gated unless an explicit bypass is visible;
3. credential values never appear in audit output;
4. public/gateway routes cannot silently become broad remote shell access;
5. focused tests pass via `scripts/run_tests.sh`; and
6. independent review confirms no sensitive data leaks and no unrelated dirty files are staged.
