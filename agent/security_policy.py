"""Metadata-only security policy audit surface for Hermes hardening Tier 7.

This module intentionally does not enforce policy or read/write external state.
It summarizes the security posture in structural terms so dashboard/doctor/harness
surfaces can answer "what risky routes are open?" without exposing credential
values, connection strings, prompt content, raw paths, or private endpoint URLs.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import os
import re
from typing import Any

SCHEMA_VERSION = 1
CONTENT_POLICY = "metadata_only"
MODE = "audit_only_no_side_effects"

_TRUE_VALUES = {"1", "true", "yes", "on", "y"}
_FALSE_VALUES = {"0", "false", "no", "off", "n"}
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|password|secret|credential|authorization|cookie)",
    re.IGNORECASE,
)

_GATEWAY_ALLOWED_ENV_VARS = (
    "TELEGRAM_ALLOWED_USERS",
    "DISCORD_ALLOWED_USERS",
    "WHATSAPP_ALLOWED_USERS",
    "SLACK_ALLOWED_USERS",
    "SIGNAL_ALLOWED_USERS",
    "SIGNAL_GROUP_ALLOWED_USERS",
    "TELEGRAM_GROUP_ALLOWED_USERS",
    "TELEGRAM_GROUP_ALLOWED_CHATS",
    "EMAIL_ALLOWED_USERS",
    "SMS_ALLOWED_USERS",
    "MATTERMOST_ALLOWED_USERS",
    "MATRIX_ALLOWED_USERS",
    "DINGTALK_ALLOWED_USERS",
    "FEISHU_ALLOWED_USERS",
    "WECOM_ALLOWED_USERS",
    "WECOM_CALLBACK_ALLOWED_USERS",
    "WEIXIN_ALLOWED_USERS",
    "BLUEBUBBLES_ALLOWED_USERS",
    "QQ_ALLOWED_USERS",
    "YUANBAO_ALLOWED_USERS",
    "GATEWAY_ALLOWED_USERS",
)
_GATEWAY_ALLOW_ALL_ENV_VARS = (
    "GATEWAY_ALLOW_ALL_USERS",
    "TELEGRAM_ALLOW_ALL_USERS",
    "DISCORD_ALLOW_ALL_USERS",
    "WHATSAPP_ALLOW_ALL_USERS",
    "SLACK_ALLOW_ALL_USERS",
    "SIGNAL_ALLOW_ALL_USERS",
    "EMAIL_ALLOW_ALL_USERS",
    "SMS_ALLOW_ALL_USERS",
    "MATTERMOST_ALLOW_ALL_USERS",
    "MATRIX_ALLOW_ALL_USERS",
    "DINGTALK_ALLOW_ALL_USERS",
    "FEISHU_ALLOW_ALL_USERS",
    "WECOM_ALLOW_ALL_USERS",
    "WECOM_CALLBACK_ALLOW_ALL_USERS",
    "WEIXIN_ALLOW_ALL_USERS",
    "BLUEBUBBLES_ALLOW_ALL_USERS",
    "QQ_ALLOW_ALL_USERS",
    "YUANBAO_ALLOW_ALL_USERS",
)
_GATEWAY_CREDENTIAL_ENV_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "DISCORD_BOT_TOKEN",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "WHATSAPP_ACCESS_TOKEN",
    "SIGNAL_SERVICE_PASSWORD",
    "EMAIL_PASSWORD",
    "SMS_AUTH_TOKEN",
    "MATRIX_ACCESS_TOKEN",
    "DINGTALK_APP_SECRET",
    "FEISHU_APP_SECRET",
    "WECOM_SECRET",
    "WEIXIN_TOKEN",
    "BLUEBUBBLES_PASSWORD",
    "QQ_CLIENT_SECRET",
    "YUANBAO_ACCESS_TOKEN",
    "WEBHOOK_SECRET",
    "API_SERVER_KEY",
)
_INGRESS_ENABLE_ENV_VARS = (
    "API_SERVER_ENABLED",
    "WEBHOOK_ENABLED",
    "MSGRAPH_WEBHOOK_ENABLED",
    "WECOM_CALLBACK_ENABLED",
    "GATEWAY_PROXY_URL",
)
_SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in _TRUE_VALUES


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    lowered = str(value).strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    return default


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _safe_enum(value: Any, allowed: set[str], default: str) -> str:
    """Return a bounded enum label without echoing arbitrary config/env values."""
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    return text if text in allowed else "custom"


def _env_bool(environ: Mapping[str, Any], name: str, default: bool) -> bool:
    if name in environ and _present(environ.get(name)):
        return _as_bool(environ.get(name), default=default)
    return default


def _approval_mode(value: Any) -> str:
    # Match tools.approval._normalize_approval_mode: bare YAML `off` can arrive
    # as boolean False and must still mean approval bypass.
    if isinstance(value, bool):
        return "off" if value is False else "manual"
    return _safe_enum(value, {"manual", "smart", "off"}, "manual")


def _cron_mode(value: Any) -> str:
    # Match tools.approval._get_cron_approval_mode. Unknown values fail closed.
    text = str(value if value is not None else "deny").strip().lower()
    return "approve" if text in {"approve", "off", "allow", "yes"} else "deny"


def _count_present(environ: Mapping[str, Any], names: tuple[str, ...]) -> int:
    return sum(1 for name in names if _present(environ.get(name)))


def _count_truthy(environ: Mapping[str, Any], names: tuple[str, ...]) -> int:
    return sum(1 for name in names if _is_truthy(environ.get(name)))


def _issue(code: str, severity: str, surface: str, detail: str) -> dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "surface": surface,
        "detail": detail,
    }


def _highest_severity(issues: list[dict[str, str]]) -> str:
    if not issues:
        return "none"
    return max(
        (str(issue.get("severity") or "info") for issue in issues),
        key=lambda item: _SEVERITY_ORDER.get(item, 0),
    )


def _gateway_plugin_env_vars() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return plugin-declared gateway allowlist env names without loading secrets."""
    try:
        from gateway.platform_registry import platform_registry

        allowed = []
        allow_all = []
        for entry in platform_registry.plugin_entries():
            if getattr(entry, "allowed_users_env", None):
                allowed.append(str(entry.allowed_users_env))
            if getattr(entry, "allow_all_env", None):
                allow_all.append(str(entry.allow_all_env))
        return tuple(allowed), tuple(allow_all)
    except Exception:
        return (), ()


def _mcp_inventory(config: Mapping[str, Any]) -> dict[str, Any]:
    servers = config.get("mcp_servers")
    if not isinstance(servers, Mapping):
        servers = {}

    counts = {
        "server_count": 0,
        "stdio_count": 0,
        "http_count": 0,
        "disabled_count": 0,
        "env_binding_count": 0,
        "header_binding_count": 0,
        "oauth_binding_count": 0,
        "credential_binding_count": 0,
    }
    for raw_cfg in servers.values():
        if not isinstance(raw_cfg, Mapping):
            continue
        counts["server_count"] += 1
        if _as_bool(raw_cfg.get("disabled"), default=False):
            counts["disabled_count"] += 1
        if _present(raw_cfg.get("url")):
            counts["http_count"] += 1
        else:
            counts["stdio_count"] += 1
        env = raw_cfg.get("env")
        if isinstance(env, Mapping):
            counts["env_binding_count"] += len(env)
            counts["credential_binding_count"] += sum(
                1 for key in env.keys() if _SECRET_KEY_RE.search(str(key))
            )
        headers = raw_cfg.get("headers")
        if isinstance(headers, Mapping):
            counts["header_binding_count"] += len(headers)
            counts["credential_binding_count"] += sum(
                1 for key in headers.keys() if _SECRET_KEY_RE.search(str(key))
            )
        oauth_cfg = raw_cfg.get("oauth")
        auth_mode = str(raw_cfg.get("auth") or "").strip().lower()
        if auth_mode == "oauth" or isinstance(oauth_cfg, Mapping):
            counts["oauth_binding_count"] += 1
            counts["credential_binding_count"] += 1
        if isinstance(oauth_cfg, Mapping):
            counts["credential_binding_count"] += sum(
                1 for key in oauth_cfg.keys() if _SECRET_KEY_RE.search(str(key))
            )
        counts["credential_binding_count"] += sum(
            1
            for key in raw_cfg.keys()
            if _SECRET_KEY_RE.search(str(key)) and key not in {"env", "headers", "oauth"}
        )

    return counts


def _codex_permission_profile(terminal_security_mode: str) -> str:
    mode = (terminal_security_mode or "auto").strip().lower()
    if mode in {"auto", ""}:
        return "workspace-write"
    if mode in {"off", "none", "full", "full-access", "full_access", "unsafe"}:
        return "full-access"
    if mode in {"readonly", "read-only", "read_only"}:
        return "read-only"
    if mode in {"workspace", "workspace-write", "workspace_write"}:
        return "workspace-write"
    return "custom"


def _load_default_config() -> Mapping[str, Any]:
    try:
        from hermes_cli.config import load_config

        loaded = load_config()
        return loaded if isinstance(loaded, Mapping) else {}
    except Exception:
        return {}


def audit_security_policy(
    *,
    config: Mapping[str, Any] | None = None,
    environ: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a content-safe Tier 7 security policy/check summary.

    The returned object is intentionally structural: counts, booleans, modes,
    and issue codes only.  It never includes raw credential values, raw URLs,
    raw paths, MCP server names, gateway chat IDs, user IDs, commands, prompts,
    or connection strings.
    """

    cfg = config if isinstance(config, Mapping) else _load_default_config()
    env = environ if isinstance(environ, Mapping) else os.environ

    security_cfg = _as_mapping(cfg.get("security"))
    browser_cfg = _as_mapping(cfg.get("browser"))
    terminal_cfg = _as_mapping(cfg.get("terminal"))
    approvals_cfg = _as_mapping(cfg.get("approvals"))
    lsp_cfg = _as_mapping(cfg.get("lsp"))

    issues: list[dict[str, str]] = []

    redaction_enabled = _env_bool(
        env,
        "HERMES_REDACT_SECRETS",
        _as_bool(security_cfg.get("redact_secrets"), default=True),
    )
    tirith_enabled = _env_bool(
        env,
        "TIRITH_ENABLED",
        _as_bool(security_cfg.get("tirith_enabled"), default=True),
    )
    tirith_fail_open = _env_bool(
        env,
        "TIRITH_FAIL_OPEN",
        _as_bool(security_cfg.get("tirith_fail_open"), default=True),
    )
    security_private_urls = _as_bool(security_cfg.get("allow_private_urls"), default=False)
    browser_private_urls = _as_bool(browser_cfg.get("allow_private_urls"), default=False)
    browser_auto_local = _as_bool(browser_cfg.get("auto_local_for_private_urls"), default=True)
    lazy_installs = _as_bool(security_cfg.get("allow_lazy_installs"), default=True)

    approvals_mode = _approval_mode(approvals_cfg.get("mode"))
    cron_mode = _cron_mode(approvals_cfg.get("cron_mode"))
    yolo_enabled = _is_truthy(env.get("HERMES_YOLO_MODE"))
    hooks_auto_accept = _as_bool(cfg.get("hooks_auto_accept"), default=False) or _is_truthy(
        env.get("HERMES_ACCEPT_HOOKS")
    )
    destructive_slash_confirm = _as_bool(
        approvals_cfg.get("destructive_slash_confirm"),
        default=True,
    )
    mcp_reload_confirm = _as_bool(approvals_cfg.get("mcp_reload_confirm"), default=True)
    command_allowlist_count = len(cfg.get("command_allowlist") or []) if isinstance(cfg.get("command_allowlist"), list) else 0

    terminal_backend = _safe_enum(
        terminal_cfg.get("backend"),
        {"local", "docker", "singularity", "ssh", "modal", "daytona", "vercel_sandbox"},
        "local",
    )
    terminal_security_mode = _safe_enum(
        env.get("HERMES_TERMINAL_SECURITY_MODE") or terminal_cfg.get("security_mode"),
        {
            "auto",
            "off",
            "none",
            "full",
            "full-access",
            "full_access",
            "unsafe",
            "readonly",
            "read-only",
            "read_only",
            "workspace",
            "workspace-write",
            "workspace_write",
        },
        "auto",
    )
    codex_permission = _codex_permission_profile(terminal_security_mode)
    host_execution = terminal_backend in {"local", "ssh"}
    docker_volumes = terminal_cfg.get("docker_volumes")
    docker_volume_count = len(docker_volumes) if isinstance(docker_volumes, list) else 0
    docker_mount_cwd = _as_bool(terminal_cfg.get("docker_mount_cwd_to_workspace"), default=False)

    website_blocklist = _as_mapping(security_cfg.get("website_blocklist"))
    lsp_install_strategy = _safe_enum(
        lsp_cfg.get("install_strategy"),
        {"auto", "manual", "off"},
        "auto",
    )

    plugin_allowed, plugin_allow_all = _gateway_plugin_env_vars()
    allowed_env_vars = _GATEWAY_ALLOWED_ENV_VARS + plugin_allowed
    allow_all_env_vars = _GATEWAY_ALLOW_ALL_ENV_VARS + plugin_allow_all
    gateway_allowlist_count = _count_present(env, allowed_env_vars)
    gateway_allow_all_count = _count_truthy(env, allow_all_env_vars)
    gateway_credential_count = _count_present(env, _GATEWAY_CREDENTIAL_ENV_VARS)
    ingress_enabled_count = _count_present(env, _INGRESS_ENABLE_ENV_VARS)
    api_server_enabled = _is_truthy(env.get("API_SERVER_ENABLED")) or _present(env.get("API_SERVER_KEY"))
    api_server_key_present = _present(env.get("API_SERVER_KEY"))
    webhook_enabled = _is_truthy(env.get("WEBHOOK_ENABLED"))
    webhook_credential_present = _present(env.get("WEBHOOK_SECRET"))

    mcp = _mcp_inventory(cfg)

    if not redaction_enabled:
        issues.append(_issue(
            "redaction_disabled",
            "critical",
            "secrets",
            "Global redaction is disabled; raw credential-like values may appear in logs or chat surfaces.",
        ))
    if yolo_enabled:
        issues.append(_issue(
            "approval_bypass_enabled",
            "critical",
            "approvals",
            "YOLO mode bypasses dangerous-command approval prompts.",
        ))
    if approvals_mode == "off":
        issues.append(_issue(
            "approval_mode_off",
            "critical",
            "approvals",
            "Dangerous command approvals are globally disabled.",
        ))
    if cron_mode == "approve":
        issues.append(_issue(
            "cron_auto_approve_enabled",
            "high",
            "approvals",
            "Cron jobs auto-approve dangerous commands.",
        ))
    if hooks_auto_accept:
        issues.append(_issue(
            "hooks_auto_accept_enabled",
            "medium",
            "hooks",
            "Shell-hook registration can be accepted without an interactive prompt.",
        ))
    if not destructive_slash_confirm:
        issues.append(_issue(
            "destructive_slash_confirm_disabled",
            "medium",
            "approvals",
            "Destructive session slash commands do not require confirmation.",
        ))
    if not mcp_reload_confirm:
        issues.append(_issue(
            "mcp_reload_confirm_disabled",
            "low",
            "approvals",
            "MCP reloads skip the cache/cost confirmation prompt.",
        ))
    if command_allowlist_count:
        issues.append(_issue(
            "dangerous_command_allowlist_present",
            "high",
            "terminal",
            "Permanent dangerous-command approvals are configured.",
        ))
    if not tirith_enabled:
        issues.append(_issue(
            "tirith_disabled",
            "high",
            "preexec_scanner",
            "Pre-exec security scanning is disabled.",
        ))
    elif tirith_fail_open:
        issues.append(_issue(
            "tirith_fail_open",
            "medium",
            "preexec_scanner",
            "Pre-exec security scanning fails open if the scanner is unavailable.",
        ))
    if security_private_urls:
        issues.append(_issue(
            "private_url_access_enabled",
            "high",
            "network",
            "Web requests may target private/internal network addresses.",
        ))
    if browser_private_urls:
        issues.append(_issue(
            "browser_private_url_access_enabled",
            "medium",
            "browser",
            "Browser navigation may target private/internal network addresses.",
        ))
    if lazy_installs:
        issues.append(_issue(
            "lazy_installs_enabled",
            "medium",
            "supply_chain",
            "Optional backend packages may be installed at runtime.",
        ))
    if lsp_install_strategy == "auto":
        issues.append(_issue(
            "lsp_auto_install_enabled",
            "medium",
            "supply_chain",
            "Language-server binaries may be installed automatically.",
        ))
    if host_execution and (yolo_enabled or approvals_mode == "off"):
        issues.append(_issue(
            "host_execution_with_approval_bypass",
            "critical",
            "terminal",
            "Host-capable terminal backend is paired with approval bypass.",
        ))
    if codex_permission == "full-access":
        issues.append(_issue(
            "codex_full_access_permission_profile",
            "high",
            "codex_app_server",
            "Codex app-server permission profile permits full filesystem access.",
        ))
    if docker_volume_count or docker_mount_cwd:
        issues.append(_issue(
            "container_host_mounts_configured",
            "medium",
            "terminal",
            "Containerized terminal backends have host mounts configured.",
        ))
    if gateway_allow_all_count:
        issues.append(_issue(
            "gateway_allow_all_enabled",
            "high",
            "gateway",
            "One or more gateway surfaces allow all users without an allowlist.",
        ))
    if api_server_enabled and not api_server_key_present:
        issues.append(_issue(
            "api_server_enabled_without_key",
            "critical",
            "gateway",
            "OpenAI-compatible API server ingress is enabled without an API key.",
        ))
    if webhook_enabled and not webhook_credential_present:
        issues.append(_issue(
            "webhook_enabled_without_credential",
            "high",
            "gateway",
            "Webhook ingress is enabled without a signing credential.",
        ))
    if mcp["credential_binding_count"]:
        issues.append(_issue(
            "mcp_credential_bindings_present",
            "info",
            "mcp",
            "MCP server configuration includes credential-bearing env/header bindings; values are omitted from this audit.",
        ))

    dangerous_prompt_policy = "bypass" if yolo_enabled or approvals_mode == "off" else approvals_mode
    approval_matrix = [
        {
            "surface": "cli",
            "dangerous_commands": dangerous_prompt_policy,
            "external_side_effects": "approval_required" if dangerous_prompt_policy not in {"off", "bypass"} else "approval_bypassed",
        },
        {
            "surface": "cron",
            "dangerous_commands": cron_mode,
            "external_side_effects": "denied_by_default" if cron_mode == "deny" else "auto_approved",
        },
        {
            "surface": "gateway",
            "dangerous_commands": dangerous_prompt_policy,
            "destructive_session_commands": "confirm_required" if destructive_slash_confirm else "confirmation_disabled",
        },
        {
            "surface": "mcp_reload",
            "cache_cost_confirmation": "confirm_required" if mcp_reload_confirm else "confirmation_disabled",
        },
    ]

    permission_matrix = [
        {
            "profile": "active",
            "terminal_backend": terminal_backend,
            "process_scope": "host" if host_execution else "sandbox",
            "codex_permission_profile": codex_permission,
            "public_remote_shell": "blocked_by_allowlist_or_key" if not gateway_allow_all_count and (gateway_allowlist_count or api_server_key_present) else "review_required",
        }
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "content_policy": CONTENT_POLICY,
        "mode": MODE,
        "generated_at": _now_iso(),
        "policy": {
            "credential_values": "never_returned",
            "external_irreversible_side_effects": "approval_required",
            "public_ingress": "allowlisted_or_keyed",
            "remote_shell": "not_public_without_explicit_opt_in",
        },
        "checks": {
            "redaction": {
                "enabled": redaction_enabled,
                "forced_boundary_redaction": True,
            },
            "approvals": {
                "mode": approvals_mode,
                "cron_mode": cron_mode,
                "yolo_enabled": yolo_enabled,
                "destructive_slash_confirm": destructive_slash_confirm,
                "mcp_reload_confirm": mcp_reload_confirm,
                "command_allowlist_count": command_allowlist_count,
            },
            "preexec_scanner": {
                "tirith_enabled": tirith_enabled,
                "tirith_fail_open": tirith_fail_open,
            },
            "terminal": {
                "backend": terminal_backend,
                "host_execution": host_execution,
                "security_mode": terminal_security_mode,
                "docker_volume_count": docker_volume_count,
                "docker_mount_cwd_to_workspace": docker_mount_cwd,
            },
            "codex_app_server": {
                "permission_profile": codex_permission,
                "inherits_terminal_security_mode": True,
            },
            "network": {
                "allow_private_urls": security_private_urls,
                "browser_allow_private_urls": browser_private_urls,
                "browser_auto_local_for_private_urls": browser_auto_local,
                "website_blocklist_enabled": _as_bool(website_blocklist.get("enabled"), default=False),
                "website_blocklist_domain_count": len(website_blocklist.get("domains") or []) if isinstance(website_blocklist.get("domains"), list) else 0,
                "website_blocklist_shared_file_count": len(website_blocklist.get("shared_files") or []) if isinstance(website_blocklist.get("shared_files"), list) else 0,
            },
            "gateway": {
                "allowlist_binding_count": gateway_allowlist_count,
                "allow_all_binding_count": gateway_allow_all_count,
                "credential_binding_count": gateway_credential_count,
                "ingress_enabled_count": ingress_enabled_count,
                "api_server_enabled": api_server_enabled,
                "api_server_key_present": api_server_key_present,
                "webhook_enabled": webhook_enabled,
                "webhook_credential_present": webhook_credential_present,
            },
            "mcp": mcp,
            "supply_chain": {
                "allow_lazy_installs": lazy_installs,
                "lsp_install_strategy": lsp_install_strategy,
            },
            "hooks": {
                "configured_event_count": len(cfg.get("hooks") or {}) if isinstance(cfg.get("hooks"), Mapping) else 0,
                "auto_accept": hooks_auto_accept,
            },
        },
        "approval_matrix": approval_matrix,
        "profile_permission_matrix": permission_matrix,
        "credential_inventory": {
            "gateway_credential_binding_count": gateway_credential_count,
            "mcp_credential_binding_count": mcp["credential_binding_count"],
            "raw_values_returned": False,
        },
        "issues": issues,
        "issue_count": len(issues),
        "highest_severity": _highest_severity(issues),
    }


__all__ = ["audit_security_policy"]
