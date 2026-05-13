"""Webhook must surface in ``hermes gateway setup`` (Issue #24911).

Two regressions to lock in:

1. ``_all_platforms()`` must include a ``"webhook"`` entry so the
   interactive picker lists it alongside the other messaging
   platforms. Before the fix, the runtime supported the webhook
   platform (``Platform.WEBHOOK`` + env-driven config bridge in
   ``gateway/config.py``) but ``_PLATFORMS`` had no entry, so the
   menu silently omitted it.

2. ``_builtin_setup_fn("webhook")`` must resolve to
   ``hermes_cli.setup._setup_webhooks``. Before the fix the
   dispatch table only had a ``"webhooks"`` (plural) key, which
   never matched any platform key, so even if a caller added a
   ``"webhook"`` entry to the menu the setup flow would not run.
"""

from __future__ import annotations


class TestWebhookSetupSurfaces:
    def test_webhook_present_in_gateway_setup_menu(self):
        from hermes_cli.gateway import _all_platforms

        keys = {p["key"] for p in _all_platforms()}
        assert "webhook" in keys, (
            "webhook missing from gateway setup picker — runtime "
            "supports Platform.WEBHOOK but the menu won't surface it"
        )

    def test_webhook_has_builtin_setup_fn(self):
        from hermes_cli.gateway import _builtin_setup_fn
        from hermes_cli.setup import _setup_webhooks

        fn = _builtin_setup_fn("webhook")
        assert fn is _setup_webhooks, (
            "_builtin_setup_fn('webhook') must dispatch to "
            "hermes_cli.setup._setup_webhooks"
        )

    def test_webhook_entry_has_token_var(self):
        """``_platform_status`` keys off ``token_var`` to report
        'configured' vs 'not configured' — without it the picker
        would show webhook as permanently unconfigured even after
        ``WEBHOOK_ENABLED=true``.
        """
        from hermes_cli.gateway import _all_platforms

        webhook = next(p for p in _all_platforms() if p["key"] == "webhook")
        assert webhook.get("token_var") == "WEBHOOK_ENABLED", (
            "webhook entry must use WEBHOOK_ENABLED as its sentinel "
            "env var to match the gateway/config.py bridge"
        )
