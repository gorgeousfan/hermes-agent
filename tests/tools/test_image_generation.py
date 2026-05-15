"""Tests for tools/image_generation_tool.py — FAL multi-model support.

Covers the pure logic of the new wrapper: catalog integrity, the three size
families (image_size_preset / aspect_ratio / gpt_literal), the supports
whitelist, default merging, GPT quality override, and model resolution
fallback. Does NOT exercise fal_client submission — that's covered by
tests/tools/test_managed_media_gateways.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def image_tool():
    """Fresh import of tools.image_generation_tool per test."""
    import importlib
    import tools.image_generation_tool as mod
    return importlib.reload(mod)


# ---------------------------------------------------------------------------
# Catalog integrity
# ---------------------------------------------------------------------------

class TestFalCatalog:
    """Every FAL_MODELS entry must have a consistent shape."""

    def test_default_model_is_klein(self, image_tool):
        assert image_tool.DEFAULT_MODEL == "fal-ai/flux-2/klein/9b"

    def test_default_model_in_catalog(self, image_tool):
        assert image_tool.DEFAULT_MODEL in image_tool.FAL_MODELS

    def test_all_entries_have_required_keys(self, image_tool):
        required = {
            "display", "speed", "strengths", "price",
            "size_style", "sizes", "defaults", "supports", "upscale",
        }
        for mid, meta in image_tool.FAL_MODELS.items():
            missing = required - set(meta.keys())
            assert not missing, f"{mid} missing required keys: {missing}"

    def test_size_style_is_valid(self, image_tool):
        valid = {"image_size_preset", "aspect_ratio", "gpt_literal"}
        for mid, meta in image_tool.FAL_MODELS.items():
            assert meta["size_style"] in valid, \
                f"{mid} has invalid size_style: {meta['size_style']}"

    def test_sizes_cover_all_aspect_ratios(self, image_tool):
        for mid, meta in image_tool.FAL_MODELS.items():
            assert set(meta["sizes"].keys()) >= {"landscape", "square", "portrait"}, \
                f"{mid} missing a required aspect_ratio key"

    def test_supports_is_a_set(self, image_tool):
        for mid, meta in image_tool.FAL_MODELS.items():
            assert isinstance(meta["supports"], set), \
                f"{mid}.supports must be a set, got {type(meta['supports'])}"

    def test_prompt_is_always_supported(self, image_tool):
        for mid, meta in image_tool.FAL_MODELS.items():
            assert "prompt" in meta["supports"], \
                f"{mid} must support 'prompt'"

    def test_only_flux2_pro_upscales_by_default(self, image_tool):
        """Upscaling should default to False for all new models to preserve
        the <1s / fast-render value prop. Only flux-2-pro stays True for
        backward-compat with the previous default."""
        for mid, meta in image_tool.FAL_MODELS.items():
            if mid == "fal-ai/flux-2-pro":
                assert meta["upscale"] is True, \
                    "flux-2-pro should keep upscale=True for backward-compat"
            else:
                assert meta["upscale"] is False, \
                    f"{mid} should default to upscale=False"


# ---------------------------------------------------------------------------
# Payload building — three size families
# ---------------------------------------------------------------------------

class TestImageSizePresetFamily:
    """Flux, z-image, qwen, recraft, ideogram all use preset enum sizes."""

    def test_klein_landscape_uses_preset(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/flux-2/klein/9b", "hello", "landscape")
        assert p["image_size"] == "landscape_16_9"
        assert "aspect_ratio" not in p

    def test_klein_square_uses_preset(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/flux-2/klein/9b", "hello", "square")
        assert p["image_size"] == "square_hd"

    def test_klein_portrait_uses_preset(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/flux-2/klein/9b", "hello", "portrait")
        assert p["image_size"] == "portrait_16_9"


class TestAspectRatioFamily:
    """Nano-banana uses aspect_ratio enum, NOT image_size."""

    def test_nano_banana_landscape_uses_aspect_ratio(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/nano-banana-pro", "hello", "landscape")
        assert p["aspect_ratio"] == "16:9"
        assert "image_size" not in p

    def test_nano_banana_square_uses_aspect_ratio(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/nano-banana-pro", "hello", "square")
        assert p["aspect_ratio"] == "1:1"

    def test_nano_banana_portrait_uses_aspect_ratio(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/nano-banana-pro", "hello", "portrait")
        assert p["aspect_ratio"] == "9:16"


class TestGptLiteralFamily:
    """GPT-Image 1.5 uses literal size strings."""

    def test_gpt_landscape_is_literal(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/gpt-image-1.5", "hello", "landscape")
        assert p["image_size"] == "1536x1024"

    def test_gpt_square_is_literal(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/gpt-image-1.5", "hello", "square")
        assert p["image_size"] == "1024x1024"

    def test_gpt_portrait_is_literal(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/gpt-image-1.5", "hello", "portrait")
        assert p["image_size"] == "1024x1536"


class TestGptImage2Presets:
    """GPT Image 2 uses preset enum sizes (not literal strings like 1.5).
    FAL's current schema exposes both 4:3 and 16:9 presets, so Hermes'
    landscape/portrait aliases stay consistent with other image models."""

    def test_gpt2_landscape_uses_16_9_preset(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/gpt-image-2", "hello", "landscape")
        assert p["image_size"] == "landscape_16_9"

    def test_gpt2_square_uses_square_hd(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/gpt-image-2", "hello", "square")
        assert p["image_size"] == "square_hd"

    def test_gpt2_portrait_uses_16_9_preset(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/gpt-image-2", "hello", "portrait")
        assert p["image_size"] == "portrait_16_9"

    def test_gpt2_quality_pinned_to_medium(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/gpt-image-2", "hi", "square")
        assert p["quality"] == "medium"

    def test_gpt2_strips_byok_and_unsupported_overrides(self, image_tool):
        """openai_api_key (BYOK) is deliberately not in supports — all users
        route through shared FAL billing. guidance_scale/num_inference_steps
        aren't in the model's API surface either."""
        p = image_tool._build_fal_payload(
            "fal-ai/gpt-image-2", "hi", "square",
            overrides={
                "openai_api_key": "sk-...",
                "guidance_scale": 7.5,
                "num_inference_steps": 50,
            },
        )
        assert "openai_api_key" not in p
        assert "guidance_scale" not in p
        assert "num_inference_steps" not in p

    def test_gpt2_strips_seed_even_if_passed(self, image_tool):
        # seed isn't in the GPT Image 2 API surface either.
        p = image_tool._build_fal_payload("fal-ai/gpt-image-2", "hi", "square", seed=42)
        assert "seed" not in p


# ---------------------------------------------------------------------------
# Supports whitelist — the main safety property
# ---------------------------------------------------------------------------

class TestSupportsFilter:
    """No model should receive keys outside its `supports` set."""

    def test_payload_keys_are_subset_of_supports_for_all_models(self, image_tool):
        for mid, meta in image_tool.FAL_MODELS.items():
            payload = image_tool._build_fal_payload(mid, "test", "landscape", seed=42)
            unsupported = set(payload.keys()) - meta["supports"]
            assert not unsupported, \
                f"{mid} payload has unsupported keys: {unsupported}"

    def test_gpt_image_has_no_seed_even_if_passed(self, image_tool):
        # GPT-Image 1.5 does not support seed — the filter must strip it.
        p = image_tool._build_fal_payload("fal-ai/gpt-image-1.5", "hi", "square", seed=42)
        assert "seed" not in p

    def test_gpt_image_strips_unsupported_overrides(self, image_tool):
        p = image_tool._build_fal_payload(
            "fal-ai/gpt-image-1.5", "hi", "square",
            overrides={"guidance_scale": 7.5, "num_inference_steps": 50},
        )
        assert "guidance_scale" not in p
        assert "num_inference_steps" not in p

    def test_recraft_has_minimal_payload(self, image_tool):
        # Recraft V4 Pro supports prompt, image_size, enable_safety_checker,
        # colors, background_color (no seed, no style — V4 dropped V3's style enum).
        p = image_tool._build_fal_payload("fal-ai/recraft/v4/pro/text-to-image", "hi", "landscape")
        assert set(p.keys()) <= {
            "prompt", "image_size", "enable_safety_checker",
            "colors", "background_color",
        }

    def test_nano_banana_never_gets_image_size(self, image_tool):
        # Common bug: translator accidentally setting both image_size and aspect_ratio.
        p = image_tool._build_fal_payload("fal-ai/nano-banana-pro", "hi", "landscape", seed=1)
        assert "image_size" not in p
        assert p["aspect_ratio"] == "16:9"


# ---------------------------------------------------------------------------
# Default merging
# ---------------------------------------------------------------------------

class TestDefaults:
    """Model-level defaults should carry through unless overridden."""

    def test_klein_default_steps_is_4(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/flux-2/klein/9b", "hi", "square")
        assert p["num_inference_steps"] == 4

    def test_flux_2_pro_default_steps_is_50(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/flux-2-pro", "hi", "square")
        assert p["num_inference_steps"] == 50

    def test_override_replaces_default(self, image_tool):
        p = image_tool._build_fal_payload(
            "fal-ai/flux-2-pro", "hi", "square", overrides={"num_inference_steps": 25}
        )
        assert p["num_inference_steps"] == 25

    def test_none_override_does_not_replace_default(self, image_tool):
        """None values from caller should be ignored (use default)."""
        p = image_tool._build_fal_payload(
            "fal-ai/flux-2-pro", "hi", "square",
            overrides={"num_inference_steps": None},
        )
        assert p["num_inference_steps"] == 50


# ---------------------------------------------------------------------------
# GPT-Image quality is pinned to medium (not user-configurable)
# ---------------------------------------------------------------------------

class TestGptQualityPinnedToMedium:
    """GPT-Image quality is baked into the FAL_MODELS defaults at 'medium'
    and cannot be overridden via config. Pinning keeps Nous Portal billing
    predictable across all users."""

    def test_gpt_payload_always_has_medium_quality(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/gpt-image-1.5", "hi", "square")
        assert p["quality"] == "medium"

    def test_config_quality_setting_is_ignored(self, image_tool):
        """Even if a user manually edits config.yaml and adds quality_setting,
        the payload must still use medium. No code path reads that field."""
        with patch("hermes_cli.config.load_config",
                   return_value={"image_gen": {"quality_setting": "high"}}):
            p = image_tool._build_fal_payload("fal-ai/gpt-image-1.5", "hi", "square")
        assert p["quality"] == "medium"

    def test_non_gpt_model_never_gets_quality(self, image_tool):
        """quality is only meaningful for GPT-Image models (1.5, 2) — other
        models should never have it in their payload."""
        gpt_models = {"fal-ai/gpt-image-1.5", "fal-ai/gpt-image-2", "openai/gpt-image-2"}
        for mid in image_tool.FAL_MODELS:
            if mid in gpt_models:
                continue
            p = image_tool._build_fal_payload(mid, "hi", "square")
            assert "quality" not in p, f"{mid} unexpectedly has 'quality' in payload"

    def test_honors_quality_setting_flag_is_removed(self, image_tool):
        """The honors_quality_setting flag was the old override trigger.
        It must not be present on any model entry anymore."""
        for mid, meta in image_tool.FAL_MODELS.items():
            assert "honors_quality_setting" not in meta, (
                f"{mid} still has honors_quality_setting; "
                f"remove it — quality is pinned to medium"
            )

    def test_resolve_gpt_quality_function_is_gone(self, image_tool):
        """The _resolve_gpt_quality() helper was removed — quality is now
        a static default, not a runtime lookup."""
        assert not hasattr(image_tool, "_resolve_gpt_quality"), (
            "_resolve_gpt_quality should not exist — quality is pinned"
        )


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

class TestModelResolution:

    def test_no_config_falls_back_to_default(self, image_tool):
        with patch("hermes_cli.config.load_config", return_value={}):
            mid, meta = image_tool._resolve_fal_model()
        assert mid == "fal-ai/flux-2/klein/9b"

    def test_valid_config_model_is_used(self, image_tool):
        with patch("hermes_cli.config.load_config",
                   return_value={"image_gen": {"model": "fal-ai/flux-2-pro"}}):
            mid, meta = image_tool._resolve_fal_model()
        assert mid == "fal-ai/flux-2-pro"
        assert meta["upscale"] is True  # flux-2-pro keeps backward-compat upscaling

    def test_unknown_model_falls_back_to_default_with_warning(self, image_tool, caplog):
        with patch("hermes_cli.config.load_config",
                   return_value={"image_gen": {"model": "fal-ai/nonexistent-9000"}}):
            mid, _ = image_tool._resolve_fal_model()
        assert mid == "fal-ai/flux-2/klein/9b"

    def test_env_var_fallback_when_no_config(self, image_tool, monkeypatch):
        monkeypatch.setenv("FAL_IMAGE_MODEL", "fal-ai/z-image/turbo")
        with patch("hermes_cli.config.load_config", return_value={}):
            mid, _ = image_tool._resolve_fal_model()
        assert mid == "fal-ai/z-image/turbo"

    def test_config_wins_over_env_var(self, image_tool, monkeypatch):
        monkeypatch.setenv("FAL_IMAGE_MODEL", "fal-ai/z-image/turbo")
        with patch("hermes_cli.config.load_config",
                   return_value={"image_gen": {"model": "fal-ai/nano-banana-pro"}}):
            mid, _ = image_tool._resolve_fal_model()
        assert mid == "fal-ai/nano-banana-pro"

    def test_gpt_image_2_alias_resolves_to_openai_endpoint(self, image_tool):
        with patch("hermes_cli.config.load_config",
                   return_value={"image_gen": {"model": "fal-ai/gpt-image-2"}}):
            mid, _ = image_tool._resolve_fal_model()
        assert mid == "openai/gpt-image-2"


# ---------------------------------------------------------------------------
# Aspect ratio handling
# ---------------------------------------------------------------------------

class TestAspectRatioNormalization:

    def test_invalid_aspect_defaults_to_landscape(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/flux-2/klein/9b", "hi", "cinemascope")
        assert p["image_size"] == "landscape_16_9"

    def test_uppercase_aspect_is_normalized(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/flux-2/klein/9b", "hi", "PORTRAIT")
        assert p["image_size"] == "portrait_16_9"

    def test_empty_aspect_defaults_to_landscape(self, image_tool):
        p = image_tool._build_fal_payload("fal-ai/flux-2/klein/9b", "hi", "")
        assert p["image_size"] == "landscape_16_9"


# ---------------------------------------------------------------------------
# Schema + registry integrity
# ---------------------------------------------------------------------------

class TestRegistryIntegration:

    def test_schema_exposes_only_prompt_and_aspect_ratio_to_agent(self, image_tool):
        """The agent-facing schema must stay tight — model selection is a
        user-level config choice, not an agent-level arg."""
        props = image_tool.IMAGE_GENERATE_SCHEMA["parameters"]["properties"]
        assert set(props.keys()) == {"prompt", "aspect_ratio"}

    def test_aspect_ratio_enum_is_three_values(self, image_tool):
        enum = image_tool.IMAGE_GENERATE_SCHEMA["parameters"]["properties"]["aspect_ratio"]["enum"]
        assert set(enum) == {"landscape", "square", "portrait"}

    def test_image_edit_schema_exposes_source_image_inputs(self, image_tool):
        props = image_tool.IMAGE_EDIT_SCHEMA["parameters"]["properties"]
        assert {"prompt", "image_url", "image_urls"}.issubset(props)
        assert {"image_path", "image_paths"}.issubset(props)
        assert "model" in props


# ---------------------------------------------------------------------------
# FAL image editing / img2img
# ---------------------------------------------------------------------------

class TestFalImageEditCatalog:
    def test_default_edit_model_is_available(self, image_tool):
        assert image_tool.DEFAULT_IMAGE_EDIT_MODEL in image_tool.FAL_IMAGE_EDIT_MODELS

    def test_required_edit_models_present(self, image_tool):
        expected = {
            "openai/gpt-image-2/edit",
            "fal-ai/nano-banana/edit",
            "fal-ai/nano-banana-2/edit",
            "fal-ai/nano-banana-pro/edit",
            "fal-ai/flux-pro/kontext",
            "fal-ai/flux-pro/kontext/multi",
        }
        assert expected.issubset(set(image_tool.FAL_IMAGE_EDIT_MODELS))

    def test_edit_model_aliases(self, image_tool):
        assert image_tool._canonical_fal_edit_model("gpt-image-2") == "openai/gpt-image-2/edit"
        assert image_tool._canonical_fal_edit_model("nano-banana-2") == "fal-ai/nano-banana-2/edit"
        assert image_tool._canonical_fal_edit_model("nano-banana-pro") == "fal-ai/nano-banana-pro/edit"
        assert image_tool._canonical_fal_edit_model("kontext") == "fal-ai/flux-pro/kontext"


class TestFalImageEditPayload:
    def test_nano_banana_edit_uses_image_urls(self, image_tool):
        payload = image_tool._build_fal_edit_payload(
            "fal-ai/nano-banana/edit",
            prompt="change shirt to blue",
            image_urls=["https://example.com/a.png", "https://example.com/b.png"],
            aspect_ratio="landscape",
            output_format="webp",
            num_images=9,
            seed=0,
        )
        assert payload["image_urls"] == ["https://example.com/a.png", "https://example.com/b.png"]
        assert payload["aspect_ratio"] == "16:9"
        assert payload["output_format"] == "webp"
        assert payload["num_images"] == 4
        assert payload["seed"] == 0
        assert "image_url" not in payload

    def test_flux_kontext_uses_single_image_url(self, image_tool):
        payload = image_tool._build_fal_edit_payload(
            "fal-ai/flux-pro/kontext",
            prompt="put a donut beside the flour",
            image_urls=["https://example.com/a.png", "https://example.com/b.png"],
            aspect_ratio="1:1",
            output_format="webp",
            guidance_scale=50,
        )
        assert payload["image_url"] == "https://example.com/a.png"
        assert "image_urls" not in payload
        assert payload["aspect_ratio"] == "1:1"
        assert payload["guidance_scale"] == 20.0
        assert payload["output_format"] == "jpeg"

    def test_gpt_image_2_edit_supports_mask_and_quality(self, image_tool):
        payload = image_tool._build_fal_edit_payload(
            "openai/gpt-image-2/edit",
            prompt="replace the logo only",
            image_urls=["https://example.com/product.png"],
            image_size="landscape",
            output_format="webp",
            quality="medium",
            mask_url="https://example.com/mask.png",
            num_images=2,
        )
        assert payload["image_urls"] == ["https://example.com/product.png"]
        assert payload["image_size"] == "landscape_16_9"
        assert payload["quality"] == "medium"
        assert payload["output_format"] == "webp"
        assert payload["mask_url"] == "https://example.com/mask.png"
        assert payload["num_images"] == 2

    def test_nano_banana_2_edit_supports_resolution_and_thinking(self, image_tool):
        payload = image_tool._build_fal_edit_payload(
            "fal-ai/nano-banana-2/edit",
            prompt="make the sign read HERMES",
            image_urls=["https://example.com/sign.png"],
            aspect_ratio="4:1",
            resolution="0.5k",
            thinking_level="high",
        )
        assert payload["image_urls"] == ["https://example.com/sign.png"]
        assert payload["aspect_ratio"] == "4:1"
        assert payload["resolution"] == "0.5K"
        assert payload["thinking_level"] == "high"

    def test_normalize_image_edit_urls_accepts_single_and_list(self, image_tool):
        urls = image_tool._normalize_image_edit_urls(
            image_url=" https://example.com/a.png ",
            image_urls=["https://example.com/a.png", "https://example.com/b.png"],
        )
        assert urls == ["https://example.com/a.png", "https://example.com/b.png"]

    def test_normalize_image_edit_urls_accepts_local_paths(self, image_tool):
        sources = image_tool._normalize_image_edit_urls(
            image_url="https://example.com/a.png",
            image_path="/tmp/source.png",
            image_paths=["/tmp/source.png", "/tmp/second.png"],
        )
        assert sources == [
            "https://example.com/a.png",
            "/tmp/source.png",
            "/tmp/second.png",
        ]

    def test_prepare_image_edit_urls_uploads_local_paths(self, image_tool, tmp_path: Path, monkeypatch):
        local = tmp_path / "source.png"
        local.write_bytes(b"fake image bytes")
        captured = {}

        class FakeFalClient:
            @staticmethod
            def upload_file(path):
                captured["path"] = path
                return "https://v3.fal.media/uploaded/source.png"

        monkeypatch.setattr(image_tool, "fal_key_is_configured", lambda: True)
        monkeypatch.setattr(image_tool, "_load_fal_client", lambda: FakeFalClient)
        monkeypatch.setattr(image_tool, "fal_client", FakeFalClient)

        prepared = image_tool._prepare_image_edit_urls([
            "https://example.com/existing.png",
            str(local),
        ])
        assert prepared == [
            "https://example.com/existing.png",
            "https://v3.fal.media/uploaded/source.png",
        ]
        assert captured["path"] == local


class TestFalImageEditDispatch:
    def test_image_edit_handler_submits_to_fal_and_returns_first_image(self, image_tool, monkeypatch):
        captured = {}

        class Handle:
            def get(self):
                return {
                    "images": [
                        {
                            "url": "https://fal.example/edited.png",
                            "width": 1024,
                            "height": 1024,
                            "content_type": "image/png",
                        }
                    ],
                    "description": "edited",
                    "seed": 123,
                }

        def fake_submit(model, arguments):
            captured["model"] = model
            captured["arguments"] = arguments
            return Handle()

        monkeypatch.setattr(image_tool, "fal_key_is_configured", lambda: True)
        monkeypatch.setattr(image_tool, "_resolve_managed_fal_gateway", lambda: None)
        monkeypatch.setattr(image_tool, "_read_configured_image_provider", lambda: None)
        monkeypatch.setattr(image_tool, "_submit_fal_request", fake_submit)

        raw = image_tool._handle_image_edit({
            "prompt": "make the car red",
            "image_url": "https://example.com/car.png",
            "model": "nano-banana",
        })
        result = json.loads(raw)

        assert result["success"] is True
        assert result["image"] == "https://fal.example/edited.png"
        assert result["provider"] == "fal"
        assert captured["model"] == "fal-ai/nano-banana/edit"
        assert captured["arguments"]["image_urls"] == ["https://example.com/car.png"]

    def test_image_edit_uploads_local_mask_before_submit(self, image_tool, tmp_path: Path, monkeypatch):
        source = tmp_path / "source.png"
        mask = tmp_path / "mask.png"
        source.write_bytes(b"fake source")
        mask.write_bytes(b"fake mask")
        captured = {}

        class Handle:
            def get(self):
                return {"images": [{"url": "https://fal.example/edited.png"}]}

        class FakeFalClient:
            @staticmethod
            def upload_file(path):
                return f"https://v3.fal.media/uploaded/{path.name}"

        def fake_submit(model, arguments):
            captured["model"] = model
            captured["arguments"] = arguments
            return Handle()

        monkeypatch.setattr(image_tool, "fal_key_is_configured", lambda: True)
        monkeypatch.setattr(image_tool, "_resolve_managed_fal_gateway", lambda: None)
        monkeypatch.setattr(image_tool, "_read_configured_image_provider", lambda: None)
        monkeypatch.setattr(image_tool, "_load_fal_client", lambda: FakeFalClient)
        monkeypatch.setattr(image_tool, "fal_client", FakeFalClient)
        monkeypatch.setattr(image_tool, "_submit_fal_request", fake_submit)

        raw = image_tool._handle_image_edit({
            "prompt": "change only the masked area",
            "image_path": str(source),
            "mask_url": str(mask),
            "model": "openai/gpt-image-2/edit",
            "quality": "low",
            "image_size": "portrait",
        })
        result = json.loads(raw)

        assert result["success"] is True
        assert captured["model"] == "openai/gpt-image-2/edit"
        assert captured["arguments"]["image_urls"] == ["https://v3.fal.media/uploaded/source.png"]
        assert captured["arguments"]["mask_url"] == "https://v3.fal.media/uploaded/mask.png"
        assert captured["arguments"]["quality"] == "low"
        assert captured["arguments"]["image_size"] == "portrait_16_9"

    def test_image_edit_rejects_non_fal_configured_provider(self, image_tool, monkeypatch):
        monkeypatch.setattr(image_tool, "_read_configured_image_provider", lambda: "openai")
        raw = image_tool._handle_image_edit({
            "prompt": "edit this",
            "image_url": "https://example.com/i.png",
        })
        result = json.loads(raw)
        assert result["success"] is False
        assert result["error_type"] == "provider_unsupported"


# ---------------------------------------------------------------------------
# Managed gateway 4xx translation
# ---------------------------------------------------------------------------

class _MockResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _MockHttpxError(Exception):
    """Simulates httpx.HTTPStatusError which exposes .response.status_code."""
    def __init__(self, status_code: int, message: str = "Bad Request"):
        super().__init__(message)
        self.response = _MockResponse(status_code)


class TestExtractHttpStatus:
    """Status-code extraction should work across exception shapes."""

    def test_extracts_from_response_attr(self, image_tool):
        exc = _MockHttpxError(403)
        assert image_tool._extract_http_status(exc) == 403

    def test_extracts_from_status_code_attr(self, image_tool):
        exc = Exception("fail")
        exc.status_code = 404  # type: ignore[attr-defined]
        assert image_tool._extract_http_status(exc) == 404

    def test_returns_none_for_non_http_exception(self, image_tool):
        assert image_tool._extract_http_status(ValueError("nope")) is None
        assert image_tool._extract_http_status(RuntimeError("nope")) is None

    def test_response_attr_without_status_code_returns_none(self, image_tool):
        class OddResponse:
            pass
        exc = Exception("weird")
        exc.response = OddResponse()  # type: ignore[attr-defined]
        assert image_tool._extract_http_status(exc) is None


class TestManagedGatewayErrorTranslation:
    """4xx from the Nous managed gateway should be translated to a user-actionable message."""

    def test_4xx_translates_to_value_error_with_remediation(self, image_tool, monkeypatch):
        """403 from managed gateway → ValueError mentioning FAL_KEY + hermes tools."""
        from unittest.mock import MagicMock

        # Simulate: managed mode active, managed submit raises 4xx.
        managed_gateway = MagicMock()
        managed_gateway.gateway_origin = "https://fal-queue-gateway.example.com"
        managed_gateway.nous_user_token = "test-token"
        monkeypatch.setattr(image_tool, "_resolve_managed_fal_gateway",
                            lambda: managed_gateway)

        bad_request = _MockHttpxError(403, "Forbidden")
        mock_managed_client = MagicMock()
        mock_managed_client.submit.side_effect = bad_request
        monkeypatch.setattr(image_tool, "_get_managed_fal_client",
                            lambda gw: mock_managed_client)

        with pytest.raises(ValueError) as exc_info:
            image_tool._submit_fal_request("fal-ai/nano-banana-pro", {"prompt": "x"})

        msg = str(exc_info.value)
        assert "fal-ai/nano-banana-pro" in msg
        assert "403" in msg
        assert "FAL_KEY" in msg
        assert "hermes tools" in msg
        # Original exception chained for debugging
        assert exc_info.value.__cause__ is bad_request

    def test_5xx_is_not_translated(self, image_tool, monkeypatch):
        """500s are real outages, not model-availability issues — don't rewrite them."""
        from unittest.mock import MagicMock

        managed_gateway = MagicMock()
        monkeypatch.setattr(image_tool, "_resolve_managed_fal_gateway",
                            lambda: managed_gateway)

        server_error = _MockHttpxError(502, "Bad Gateway")
        mock_managed_client = MagicMock()
        mock_managed_client.submit.side_effect = server_error
        monkeypatch.setattr(image_tool, "_get_managed_fal_client",
                            lambda gw: mock_managed_client)

        with pytest.raises(_MockHttpxError):
            image_tool._submit_fal_request("fal-ai/flux-2-pro", {"prompt": "x"})

    def test_direct_fal_errors_are_not_translated(self, image_tool, monkeypatch):
        """When user has direct FAL_KEY (managed gateway returns None), raw
        errors from fal_client bubble up unchanged — fal_client already
        provides reasonable error messages for direct usage."""
        from unittest.mock import MagicMock

        monkeypatch.setattr(image_tool, "_resolve_managed_fal_gateway",
                            lambda: None)

        direct_error = _MockHttpxError(403, "Forbidden")
        fake_fal_client = MagicMock()
        fake_fal_client.submit.side_effect = direct_error
        monkeypatch.setattr(image_tool, "fal_client", fake_fal_client)

        with pytest.raises(_MockHttpxError):
            image_tool._submit_fal_request("fal-ai/flux-2-pro", {"prompt": "x"})

    def test_non_http_exception_from_managed_bubbles_up(self, image_tool, monkeypatch):
        """Connection errors, timeouts, etc. from managed mode aren't 4xx —
        they should bubble up unchanged so callers can retry or diagnose."""
        from unittest.mock import MagicMock

        managed_gateway = MagicMock()
        monkeypatch.setattr(image_tool, "_resolve_managed_fal_gateway",
                            lambda: managed_gateway)

        conn_error = ConnectionError("network down")
        mock_managed_client = MagicMock()
        mock_managed_client.submit.side_effect = conn_error
        monkeypatch.setattr(image_tool, "_get_managed_fal_client",
                            lambda gw: mock_managed_client)

        with pytest.raises(ConnectionError):
            image_tool._submit_fal_request("fal-ai/flux-2-pro", {"prompt": "x"})
