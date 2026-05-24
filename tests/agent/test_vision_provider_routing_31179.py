"""Regression tests for issue #31179.

When ``auxiliary.vision.provider`` is set to a non-aggregator provider
(e.g. ``openai`` for direct GPT-4o-mini routing) but the provider can't
be resolved, the previous behaviour silently fell back to the main agent
provider — so vision calls landed on the user's text-only main model
(DeepSeek V4 etc.) and 400'd with::

    unknown variant `image_url`, expected `text`

The fix is two-pronged:

1. ``call_llm`` and ``async_call_llm`` now raise a clear error for
   explicit vision providers that don't resolve, mirroring the
   non-vision branch.  ``auto`` / ``openrouter`` / ``custom`` keep their
   permissive behaviour because they're aggregator/default providers.
2. ``openai`` is now a first-class provider that routes to
   ``api.openai.com`` with ``OPENAI_API_KEY``, so the user's natural
   config (``provider: openai``, ``model: gpt-4o-mini``) just works.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from agent.auxiliary_client import (
    async_call_llm,
    call_llm,
    resolve_provider_client,
    resolve_vision_provider_client,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "OPENROUTER_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def aux_vision_openai_config(monkeypatch):
    """Simulate the user's #31179 config:

        auxiliary:
          vision:
            provider: openai
            model: gpt-4o-mini

    on a host whose main provider is text-only (DeepSeek V4).
    """

    def fake_load_config():
        return {
            "model": {"provider": "deepseek", "default": "deepseek-v4-pro"},
            "auxiliary": {
                "vision": {"provider": "openai", "model": "gpt-4o-mini"}
            },
        }

    monkeypatch.setattr(
        "hermes_cli.config.load_config", fake_load_config, raising=False
    )
    monkeypatch.setattr(
        "agent.auxiliary_client._read_main_provider",
        lambda: "deepseek",
    )
    monkeypatch.setattr(
        "agent.auxiliary_client._read_main_model",
        lambda: "deepseek-v4-pro",
    )


# ---------------------------------------------------------------------------
# Fix 1 — explicit non-resolving vision provider raises instead of routing
# to the main model.
# ---------------------------------------------------------------------------


class TestExplicitVisionProviderFailsLoud:
    """The silent ``auto`` fallback for unresolved explicit vision providers
    is the root cause of #31179 — verify it's gone.
    """

    def test_unknown_vision_provider_raises_runtime_error(self, monkeypatch):
        """An unrecognised provider name must surface as a config error,
        not silently route the image to the main model."""
        monkeypatch.setattr(
            "agent.auxiliary_client._get_auxiliary_task_config",
            lambda task: {"provider": "totally-not-a-provider", "model": "x"},
        )
        # Guard: if the silent fallback path were still alive it would hit
        # _resolve_auto via resolve_vision_provider_client(provider="auto")
        # and route to the main provider's client.  Make any auto fallback
        # explode loudly so the test catches the regression rather than
        # masking it.
        guard = MagicMock(
            side_effect=AssertionError(
                "resolve_vision_provider_client must NOT be called with "
                "provider='auto' when the user explicitly set a non-auto "
                "vision provider — issue #31179."
            )
        )
        original = resolve_vision_provider_client

        def wrapped(*args, **kwargs):
            if kwargs.get("provider") == "auto":
                guard()
            return original(*args, **kwargs)

        monkeypatch.setattr(
            "agent.auxiliary_client.resolve_vision_provider_client", wrapped
        )

        with pytest.raises(RuntimeError, match="totally-not-a-provider"):
            call_llm(
                task="vision",
                messages=[{"role": "user", "content": "hi"}],
            )

    @pytest.mark.asyncio
    async def test_unknown_vision_provider_raises_runtime_error_async(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            "agent.auxiliary_client._get_auxiliary_task_config",
            lambda task: {"provider": "totally-not-a-provider", "model": "x"},
        )
        original = resolve_vision_provider_client

        def wrapped(*args, **kwargs):
            if kwargs.get("provider") == "auto":
                raise AssertionError(
                    "auto-fallback should NOT fire for explicit unresolved "
                    "vision provider (#31179)"
                )
            return original(*args, **kwargs)

        monkeypatch.setattr(
            "agent.auxiliary_client.resolve_vision_provider_client", wrapped
        )

        with pytest.raises(RuntimeError, match="totally-not-a-provider"):
            await async_call_llm(
                task="vision",
                messages=[{"role": "user", "content": "hi"}],
            )

    def test_error_message_names_the_offending_env_var(self, monkeypatch):
        """Error must tell the user which env var to set."""
        monkeypatch.setattr(
            "agent.auxiliary_client._get_auxiliary_task_config",
            lambda task: {"provider": "openai", "model": "gpt-4o-mini"},
        )
        # Make the openai client resolution fail (no key) so we hit the
        # raise.  Patch _get_cached_client so resolve_vision_provider_client
        # returns (provider, None, None).
        monkeypatch.setattr(
            "agent.auxiliary_client._get_cached_client",
            lambda *a, **kw: (None, None),
        )
        with pytest.raises(RuntimeError) as excinfo:
            call_llm(
                task="vision",
                messages=[{"role": "user", "content": "hi"}],
            )
        msg = str(excinfo.value)
        assert "openai" in msg
        # Hyphens in provider names get rewritten to underscores in env
        # vars (e.g. openai-codex → OPENAI_CODEX_API_KEY).
        assert "OPENAI_API_KEY" in msg

    def test_openrouter_keeps_permissive_auto_fallback(self, monkeypatch):
        """``openrouter`` is the aggregator default; if its client doesn't
        resolve we still fall through to the auto chain.  Same behaviour
        for ``custom``.  Guards against over-eager fix.
        """
        monkeypatch.setattr(
            "agent.auxiliary_client._get_auxiliary_task_config",
            lambda task: {"provider": "openrouter"},
        )
        # First call (provider=openrouter) returns no client; second call
        # with provider=auto returns a usable mock.
        fake_async_client = MagicMock(name="auto_fallback_client")
        fake_async_client.base_url = "https://openrouter.ai/api/v1"

        async def fake_create(**kwargs):
            return MagicMock(
                choices=[
                    MagicMock(
                        message=MagicMock(content="ok", role="assistant"),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            )

        fake_async_client.chat.completions.create = fake_create

        calls = []

        def fake_resolve(*args, **kwargs):
            calls.append(kwargs.get("provider"))
            if kwargs.get("provider") == "openrouter":
                return ("openrouter", None, None)
            return ("openrouter", fake_async_client, "google/gemini-3-flash-preview")

        monkeypatch.setattr(
            "agent.auxiliary_client.resolve_vision_provider_client", fake_resolve
        )

        # The call should succeed by falling back to auto — exactly the
        # legacy behaviour we're preserving for aggregator/default names.
        import asyncio

        asyncio.get_event_loop().run_until_complete(
            async_call_llm(
                task="vision",
                messages=[{"role": "user", "content": "hi"}],
            )
        )

        assert "openrouter" in calls
        assert "auto" in calls


# ---------------------------------------------------------------------------
# Fix 2 — ``openai`` is registered as a first-class provider so the user's
# natural config (provider: openai + OPENAI_API_KEY) Just Works.
# ---------------------------------------------------------------------------


class TestOpenAIProviderRegistered:
    """``provider: openai`` must resolve to a real client against
    ``api.openai.com`` using ``OPENAI_API_KEY`` (issue #31179).
    """

    def test_openai_is_in_provider_registry(self):
        """Plugin auto-registration wires openai into PROVIDER_REGISTRY."""
        from hermes_cli.auth import PROVIDER_REGISTRY

        assert "openai" in PROVIDER_REGISTRY, (
            "Expected the openai provider plugin to register itself in "
            "PROVIDER_REGISTRY via providers/__init__.py auto-extension."
        )
        cfg = PROVIDER_REGISTRY["openai"]
        assert cfg.auth_type == "api_key"
        assert "OPENAI_API_KEY" in cfg.api_key_env_vars
        assert "api.openai.com" in cfg.inference_base_url

    def test_openai_profile_attributes(self):
        """The provider profile itself carries the right metadata."""
        from providers import get_provider_profile

        profile = get_provider_profile("openai")
        assert profile is not None
        assert profile.api_mode == "chat_completions"
        assert profile.base_url == "https://api.openai.com/v1"
        assert profile.default_aux_model == "gpt-4o-mini"
        assert "OPENAI_API_KEY" in profile.env_vars

    def test_resolve_provider_client_openai_with_key(self, monkeypatch):
        """resolve_provider_client('openai', model) returns a client
        pointing at api.openai.com when OPENAI_API_KEY is set.
        """
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        client, final_model = resolve_provider_client("openai", "gpt-4o-mini")
        assert client is not None
        assert "api.openai.com" in str(getattr(client, "base_url", ""))
        assert final_model == "gpt-4o-mini"

    def test_resolve_vision_provider_client_routes_to_openai(self, monkeypatch):
        """User config ``auxiliary.vision.provider: openai`` resolves to
        api.openai.com — not to the main model (DeepSeek/etc.).
        """
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        monkeypatch.setattr(
            "agent.auxiliary_client._get_auxiliary_task_config",
            lambda task: {"provider": "openai", "model": "gpt-4o-mini"},
        )
        provider, client, model = resolve_vision_provider_client()
        assert provider == "openai"
        assert client is not None
        assert "api.openai.com" in str(getattr(client, "base_url", ""))
        assert model == "gpt-4o-mini"

    def test_unset_openai_key_raises_clear_error(self, monkeypatch):
        """When provider: openai is set but OPENAI_API_KEY is missing,
        the explicit-provider gate (Fix 1) raises a clear error rather
        than silently falling back to the main provider.
        """
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(
            "agent.auxiliary_client._get_auxiliary_task_config",
            lambda task: {"provider": "openai", "model": "gpt-4o-mini"},
        )
        with pytest.raises(RuntimeError, match="openai"):
            call_llm(
                task="vision",
                messages=[{"role": "user", "content": "hi"}],
            )
