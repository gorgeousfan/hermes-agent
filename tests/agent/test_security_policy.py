import json

from agent.security_policy import audit_security_policy


def test_security_policy_audit_is_metadata_only_and_flags_risky_routes():
    config = {
        "security": {
            "redact_secrets": False,
            "tirith_enabled": False,
            "tirith_fail_open": True,
            "allow_private_urls": True,
            "allow_lazy_installs": True,
            "website_blocklist": {
                "enabled": True,
                "domains": ["private.example"],
                "shared_files": ["/very/private/path.txt"],
            },
        },
        "browser": {
            "allow_private_urls": True,
            "auto_local_for_private_urls": False,
        },
        "approvals": {
            "mode": "off",
            "cron_mode": "approve",
            "destructive_slash_confirm": False,
            "mcp_reload_confirm": False,
        },
        "terminal": {
            "backend": "local",
            "docker_volumes": ["/host/private:/workspace/private"],
            "docker_mount_cwd_to_workspace": True,
        },
        "hooks_auto_accept": True,
        "command_allowlist": ["rm -rf /tmp/scratch"],
        "lsp": {"install_strategy": "auto"},
        "mcp_servers": {
            "private-mcp-name": {
                "url": "https://private.internal.example/mcp?api_key=sk-should-not-leak",
                "headers": {"Authorization": "Bearer should-not-leak"},
                "env": {"PRIVATE_API_KEY": "sk-should-not-leak"},
            },
        },
    }
    environ = {
        "HERMES_YOLO_MODE": "1",
        "HERMES_ACCEPT_HOOKS": "1",
        "HERMES_TERMINAL_SECURITY_MODE": "full-access",
        "GATEWAY_ALLOW_ALL_USERS": "true",
        "API_SERVER_ENABLED": "true",
        "WEBHOOK_ENABLED": "true",
        "TELEGRAM_BOT_TOKEN": "123456:should-not-leak",
    }

    audit = audit_security_policy(config=config, environ=environ)

    codes = {issue["code"] for issue in audit["issues"]}
    assert audit["content_policy"] == "metadata_only"
    assert audit["mode"] == "audit_only_no_side_effects"
    assert audit["highest_severity"] == "critical"
    assert {
        "redaction_disabled",
        "approval_bypass_enabled",
        "approval_mode_off",
        "cron_auto_approve_enabled",
        "host_execution_with_approval_bypass",
        "codex_full_access_permission_profile",
        "gateway_allow_all_enabled",
        "api_server_enabled_without_key",
        "webhook_enabled_without_credential",
        "mcp_credential_bindings_present",
    } <= codes
    assert audit["checks"]["gateway"]["credential_binding_count"] == 1
    assert audit["checks"]["mcp"]["server_count"] == 1
    assert audit["checks"]["mcp"]["credential_binding_count"] == 2
    assert audit["credential_inventory"]["raw_values_returned"] is False

    raw = json.dumps(audit, sort_keys=True)
    assert "should-not-leak" not in raw
    assert "sk-" not in raw
    assert "Bearer" not in raw
    assert "private.internal.example" not in raw
    assert "private-mcp-name" not in raw
    assert "/very/private/path.txt" not in raw
    assert "/host/private" not in raw


def test_security_policy_audit_safe_config_has_no_high_risk_issues():
    config = {
        "security": {
            "redact_secrets": True,
            "tirith_enabled": True,
            "tirith_fail_open": False,
            "allow_private_urls": False,
            "allow_lazy_installs": False,
            "website_blocklist": {"enabled": False, "domains": [], "shared_files": []},
        },
        "browser": {"allow_private_urls": False, "auto_local_for_private_urls": True},
        "approvals": {
            "mode": "manual",
            "cron_mode": "deny",
            "destructive_slash_confirm": True,
            "mcp_reload_confirm": True,
        },
        "terminal": {"backend": "docker", "docker_volumes": []},
        "hooks_auto_accept": False,
        "command_allowlist": [],
        "lsp": {"install_strategy": "manual"},
        "mcp_servers": {},
    }
    environ = {"API_SERVER_KEY": "present-but-not-returned", "GATEWAY_ALLOWED_USERS": "configured"}

    audit = audit_security_policy(config=config, environ=environ)

    assert audit["issue_count"] == 0
    assert audit["highest_severity"] == "none"
    assert audit["checks"]["terminal"]["host_execution"] is False
    assert audit["checks"]["gateway"]["api_server_key_present"] is True
    assert "present-but-not-returned" not in json.dumps(audit, sort_keys=True)


def test_security_policy_audit_bounds_untrusted_enum_values():
    config = {
        "security": {"redact_secrets": True, "tirith_enabled": True, "tirith_fail_open": False, "allow_lazy_installs": False},
        "approvals": {"mode": "manual", "cron_mode": "deny"},
        "terminal": {"backend": "backend-sk-should-not-leak", "security_mode": "mode-sk-should-not-leak"},
        "lsp": {"install_strategy": "install-sk-should-not-leak"},
    }
    environ = {"HERMES_TERMINAL_SECURITY_MODE": "env-sk-should-not-leak"}

    audit = audit_security_policy(config=config, environ=environ)

    assert audit["checks"]["terminal"]["backend"] == "custom"
    assert audit["checks"]["terminal"]["security_mode"] == "custom"
    assert audit["checks"]["supply_chain"]["lsp_install_strategy"] == "custom"
    raw = json.dumps(audit, sort_keys=True)
    assert "sk-should-not-leak" not in raw


def test_security_policy_matches_yaml_boolean_approval_off():
    audit = audit_security_policy(
        config={
            "security": {"redact_secrets": True, "tirith_enabled": True, "tirith_fail_open": False, "allow_lazy_installs": False},
            "approvals": {"mode": False, "cron_mode": "deny"},
            "terminal": {"backend": "local"},
            "lsp": {"install_strategy": "manual"},
        },
        environ={},
    )

    codes = {issue["code"] for issue in audit["issues"]}
    assert audit["checks"]["approvals"]["mode"] == "off"
    assert "approval_mode_off" in codes
    assert "host_execution_with_approval_bypass" in codes


def test_security_policy_honors_redaction_and_tirith_env_overrides():
    config = {
        "security": {
            "redact_secrets": True,
            "tirith_enabled": True,
            "tirith_fail_open": False,
            "allow_lazy_installs": False,
        },
        "approvals": {"mode": "manual", "cron_mode": "deny"},
        "terminal": {"backend": "docker"},
        "lsp": {"install_strategy": "manual"},
    }

    audit = audit_security_policy(
        config=config,
        environ={
            "HERMES_REDACT_SECRETS": "false",
            "TIRITH_ENABLED": "false",
            "TIRITH_FAIL_OPEN": "true",
        },
    )

    codes = {issue["code"] for issue in audit["issues"]}
    assert audit["checks"]["redaction"]["enabled"] is False
    assert audit["checks"]["preexec_scanner"]["tirith_enabled"] is False
    assert audit["checks"]["preexec_scanner"]["tirith_fail_open"] is True
    assert "redaction_disabled" in codes
    assert "tirith_disabled" in codes


def test_security_policy_counts_mcp_oauth_credential_bindings_without_values():
    config = {
        "security": {"redact_secrets": True, "tirith_enabled": True, "tirith_fail_open": False, "allow_lazy_installs": False},
        "approvals": {"mode": "manual", "cron_mode": "deny"},
        "terminal": {"backend": "docker"},
        "lsp": {"install_strategy": "manual"},
        "mcp_servers": {
            "sensitive-oauth-server": {
                "auth": "oauth",
                "oauth": {
                    "client_id": "client-id-should-not-leak",
                    "client_secret": "client-secret-should-not-leak",
                },
            }
        },
    }

    audit = audit_security_policy(config=config, environ={})

    codes = {issue["code"] for issue in audit["issues"]}
    assert audit["checks"]["mcp"]["oauth_binding_count"] == 1
    assert audit["checks"]["mcp"]["credential_binding_count"] >= 2
    assert "mcp_credential_bindings_present" in codes
    raw = json.dumps(audit, sort_keys=True)
    assert "client-secret-should-not-leak" not in raw
    assert "client-id-should-not-leak" not in raw
    assert "sensitive-oauth-server" not in raw
