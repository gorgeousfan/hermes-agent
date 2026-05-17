---
title: "OAuth Broker Credentials"
description: "Use an external service to refresh OAuth tokens for Hermes credential-pool entries"
---

# OAuth Broker Credentials

Hermes can use an externally managed OAuth broker for credential-pool entries.
This is useful when refresh tokens rotate and must have a single owner, or when a
deployment platform wants to centralize OAuth storage and policy outside Hermes.

Add a credential-pool entry with `source: oauth_broker`:

```json
{
  "credential_pool": {
    "openai-codex": [
      {
        "id": "team-codex",
        "label": "Team Codex",
        "auth_type": "oauth",
        "source": "oauth_broker",
        "access_token": "",
        "broker_url": "https://example.com/hermes/oauth-token",
        "broker_headers_env": "HERMES_OAUTH_BROKER_HEADERS",
        "broker_subject": "connection_123"
      }
    ]
  }
}
```

`broker_headers_env` is optional. When set, it must name an environment variable
containing a JSON object of HTTP headers:

```bash
export HERMES_OAUTH_BROKER_HEADERS='{"Authorization":"Bearer broker-runtime-token"}'
```

When Hermes needs a token, it posts JSON to `broker_url`:

```json
{
  "provider": "openai-codex",
  "credential_id": "team-codex",
  "subject": "connection_123",
  "force": false
}
```

The broker response must include an `access_token` or `api_key`. It may also
include `base_url`, `expires_at`, `expires_at_ms`, `inference_base_url`,
`agent_key`, `agent_key_expires_at`, `refresh_after`, or `expires_in`.

Hermes caches the returned token in `auth.json` but never stores a refresh token
for `oauth_broker` entries. On a forced retry after an auth failure, Hermes calls
the broker again with `force: true`.
