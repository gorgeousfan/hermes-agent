"""Tests for `providers.<name>.extra_body` config-driven request override.

Closes #8160 — provides a way for users to pin per-provider extra_body
fields (e.g. `enable_thinking: false` for DashScope-hosted Qwen3 models)
once in `~/.hermes/config.yaml` instead of threading them through every
call site.
"""

from __future__ import annotations

import pytest

from agent.transports import get_transport
from agent.transports.chat_completions import _load_provider_extra_body_override


@pytest.fixture
def transport():
    import agent.transports.chat_completions  # noqa: F401
    return get_transport("chat_completions")


@pytest.fixture
def stub_load_config(monkeypatch):
    """Replace hermes_cli.config.load_config with a controllable stub."""
    cfg = {}

    def _set(new_cfg):
        cfg.clear()
        cfg.update(new_cfg)

    def _stub_load_config():
        return cfg

    import hermes_cli.config as _config_mod
    monkeypatch.setattr(_config_mod, "load_config", _stub_load_config)
    return _set


class TestLoadProviderExtraBodyOverride:
    def test_returns_empty_when_provider_name_blank(self):
        assert _load_provider_extra_body_override("") == {}

    def test_returns_empty_when_no_providers_section(self, stub_load_config):
        stub_load_config({})
        assert _load_provider_extra_body_override("dashscope") == {}

    def test_returns_empty_when_provider_missing(self, stub_load_config):
        stub_load_config({"providers": {"openrouter": {}}})
        assert _load_provider_extra_body_override("dashscope") == {}

    def test_returns_empty_when_extra_body_missing(self, stub_load_config):
        stub_load_config({"providers": {"dashscope": {"some_other_key": "x"}}})
        assert _load_provider_extra_body_override("dashscope") == {}

    def test_returns_empty_when_extra_body_not_dict(self, stub_load_config):
        stub_load_config({"providers": {"dashscope": {"extra_body": "not a dict"}}})
        assert _load_provider_extra_body_override("dashscope") == {}

    def test_returns_dict_copy_when_present(self, stub_load_config):
        original = {"enable_thinking": False, "max_steps": 10}
        stub_load_config({"providers": {"dashscope": {"extra_body": original}}})
        result = _load_provider_extra_body_override("dashscope")
        assert result == original
        # Mutating the result must not leak back into config
        result["new_key"] = "new"
        assert "new_key" not in original

    def test_swallows_load_config_exceptions(self, monkeypatch):
        import hermes_cli.config as _config_mod
        def _raise(*_a, **_kw):
            raise RuntimeError("config corrupted")
        monkeypatch.setattr(_config_mod, "load_config", _raise)
        # Must not propagate; returns empty dict so caller can merge safely.
        assert _load_provider_extra_body_override("dashscope") == {}


class TestExtraBodyConfigOverrideAppliedInBuildKwargs:
    def test_dashscope_enable_thinking_false_is_applied(self, transport, stub_load_config):
        """The use case from #8160: a DashScope user wants to default
        `enable_thinking: false` for every call to that provider."""
        stub_load_config({
            "providers": {
                "alibaba-coding-plan": {"extra_body": {"enable_thinking": False}},
            },
        })
        kw = transport.build_kwargs(
            model="qwen3-coder",
            messages=[{"role": "user", "content": "hi"}],
            provider_name="alibaba-coding-plan",
        )
        assert kw["extra_body"]["enable_thinking"] is False

    def test_config_override_wins_over_caller_extra_body_additions(
        self, transport, stub_load_config
    ):
        """Config is the user's standing intent; caller passes per-call
        additions. Per the design, config overrides win — they're applied
        last so the user's "I always want this for this provider" holds."""
        stub_load_config({
            "providers": {
                "dashscope": {"extra_body": {"enable_thinking": False}},
            },
        })
        kw = transport.build_kwargs(
            model="qwen3-plus",
            messages=[{"role": "user", "content": "hi"}],
            provider_name="dashscope",
            extra_body_additions={"enable_thinking": True},
        )
        assert kw["extra_body"]["enable_thinking"] is False, (
            "config-level extra_body must win over caller-level "
            "extra_body_additions for the same key"
        )

    def test_no_extra_body_section_when_no_config_and_no_other_sources(
        self, transport, stub_load_config
    ):
        stub_load_config({})
        kw = transport.build_kwargs(
            model="custom-model",
            messages=[{"role": "user", "content": "hi"}],
            provider_name="some-provider",
        )
        assert "extra_body" not in kw

    def test_config_override_merges_with_existing_provider_extra_body(
        self, transport, stub_load_config
    ):
        """Existing provider-specific extra_body (e.g. Nous tags) and the
        config override should both end up in the final dict, with config
        having the final word for any overlapping keys."""
        stub_load_config({
            "providers": {
                "nous": {"extra_body": {"custom_field": "from-config"}},
            },
        })
        from providers import get_provider_profile
        profile = get_provider_profile("nous")
        kw = transport.build_kwargs(
            model="any",
            messages=[{"role": "user", "content": "hi"}],
            provider_profile=profile,
            provider_name="nous",
        )
        # Both fields present
        assert kw["extra_body"]["custom_field"] == "from-config"
        assert kw["extra_body"]["tags"] == ["product=hermes-agent"]

    def test_config_override_does_not_crash_on_unknown_provider(
        self, transport, stub_load_config
    ):
        stub_load_config({
            "providers": {
                "alibaba-coding-plan": {"extra_body": {"x": 1}},
            },
        })
        # provider_name doesn't match the config key — must not raise
        kw = transport.build_kwargs(
            model="m",
            messages=[{"role": "user", "content": "hi"}],
            provider_name="some-other-provider",
        )
        # No extra_body emitted because nothing matched
        assert "extra_body" not in kw or "x" not in kw.get("extra_body", {})
