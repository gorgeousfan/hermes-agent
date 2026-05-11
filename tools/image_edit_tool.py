#!/usr/bin/env python3
"""Image editing tool.

Provides a small, provider-dispatched tool for prompt-guided image-to-image
editing. The first local implementation targets the openai-codex image_gen
backend, which can pass reference images through the Codex Responses API.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from agent.image_gen_provider import DEFAULT_ASPECT_RATIO, VALID_ASPECT_RATIOS
from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)


IMAGE_EDIT_SCHEMA = {
    "name": "image_edit",
    "description": (
        "Edit an existing image using a text instruction and a reference image. "
        "Mandatory: when the user provides, uploads, links, or names any reference/source/product/person image, "
        "use this image-to-image tool rather than image_generate/text-to-image. "
        "The active image backend is user-configured. Currently this is intended "
        "for backends that support image-to-image editing, such as OpenAI Codex "
        "auth with GPT Image 2. Returns either a URL or an absolute file path in "
        "the `image` field; display it with markdown ![description](url-or-path)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Instruction describing the edit to apply while preserving unchanged parts of the source image.",
            },
            "image": {
                "type": "string",
                "description": "Reference image as an HTTP(S) URL, data URL, or local file path under Hermes' image cache.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": list(VALID_ASPECT_RATIOS),
                "description": "Desired output aspect ratio. Use '9:16' for exact vertical TikTok/Reels-style images; legacy 'portrait' is 2:3 tall.",
                "default": DEFAULT_ASPECT_RATIO,
            },
            "size": {
                "type": "string",
                "description": "Optional exact output size for OpenAI/OpenAI-Codex GPT Image 2, formatted WIDTHxHEIGHT (e.g. 1024x1824 for 9:16). Overrides aspect_ratio when supported. Dimensions must be multiples of 16, max side < 3840, aspect ratio <= 3:1, and total pixels between 655,360 and 8,294,400.",
            },
            "quality_tier": {
                "type": "string",
                "enum": ["auto", "low", "medium", "high"],
                "description": "Optional GPT Image 2 quality tier override. Use 'auto' to preserve provider/router defaults.",
                "default": "auto",
            },
            "model": {
                "type": "string",
                "enum": ["gpt-image-2-low", "gpt-image-2-medium", "gpt-image-2-high"],
                "description": "Optional explicit GPT Image 2 model/tier override. Takes precedence over quality_tier when both are provided.",
            },
        },
        "required": ["prompt", "image"],
    },
}


def _read_configured_image_provider():
    # Reuse image_generate's provider selection logic so both tools honor the
    # same image_gen.provider setting.
    try:
        from tools.image_generation_tool import _read_configured_image_provider as _reader

        return _reader()
    except Exception as exc:
        logger.debug("Could not read configured image provider for image_edit: %s", exc)
        return None


def _read_configured_image_model():
    # Keep image_edit aligned with image_generate for third-party providers
    # that rely on the tool wrapper to forward the user's selected image model.
    try:
        from tools.image_generation_tool import _read_configured_image_model as _reader

        return _reader()
    except Exception as exc:
        logger.debug("Could not read configured image model for image_edit: %s", exc)
        return None


def _get_active_edit_provider():
    configured = _read_configured_image_provider()
    if not configured or configured == "fal":
        return None, configured

    try:
        from agent.image_gen_registry import get_provider
        from hermes_cli.plugins import _ensure_plugins_discovered

        _ensure_plugins_discovered()
        provider = get_provider(configured)
        if provider is None:
            _ensure_plugins_discovered(force=True)
            provider = get_provider(configured)
        return provider, configured
    except Exception as exc:
        logger.debug("image_edit provider discovery failed: %s", exc)
        return None, configured


def _dispatch_to_plugin_provider(
    prompt: str,
    image: str,
    aspect_ratio: str,
    size: str | None = None,
    *,
    quality_tier: str | None = None,
    model: str | None = None,
) -> str:
    provider, configured = _get_active_edit_provider()
    if configured is None or configured == "fal":
        return json.dumps({
            "success": False,
            "image": None,
            "error": (
                "image_edit requires an image_gen provider that supports editing. "
                "Set image_gen.provider to an edit-capable backend such as 'openai-codex'."
            ),
            "error_type": "provider_not_configured",
        })

    if provider is None:
        return json.dumps({
            "success": False,
            "image": None,
            "error": (
                f"image_gen.provider='{configured}' is set but no plugin registered "
                "that name. Run `hermes plugins list` to see available image gen backends."
            ),
            "error_type": "provider_not_registered",
        })

    if not getattr(provider, "supports_edit", lambda: False)():
        return json.dumps({
            "success": False,
            "image": None,
            "error": f"Image provider '{getattr(provider, 'name', configured)}' does not support image_edit",
            "error_type": "unsupported",
        })

    try:
        configured_model = _read_configured_image_model()
        edit_kwargs = {"size": size}
        if quality_tier and quality_tier != "auto":
            edit_kwargs["quality_tier"] = quality_tier
        if model:
            edit_kwargs["model"] = model
        elif not edit_kwargs.get("quality_tier") and configured_model:
            edit_kwargs["model"] = configured_model
        result = provider.edit(prompt=prompt, image=image, aspect_ratio=aspect_ratio, **edit_kwargs)
    except Exception as exc:
        logger.warning("Image edit provider '%s' raised: %s", getattr(provider, "name", "?"), exc)
        return json.dumps({
            "success": False,
            "image": None,
            "error": f"Provider '{getattr(provider, 'name', '?')}' error: {exc}",
            "error_type": "provider_exception",
        })

    if not isinstance(result, dict):
        return json.dumps({
            "success": False,
            "image": None,
            "error": "Provider returned a non-dict result",
            "error_type": "provider_contract",
        })
    return json.dumps(result)


def check_image_edit_requirements() -> bool:
    provider, configured = _get_active_edit_provider()
    if not configured or provider is None:
        return False
    try:
        return bool(provider.is_available() and provider.supports_edit())
    except Exception:
        return False


def _handle_image_edit(args: Dict[str, Any], **kw):
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return tool_error("prompt is required for image editing")

    image = args.get("image") or args.get("image_path") or args.get("image_url")
    if not isinstance(image, str) or not image.strip():
        return tool_error("image is required for image editing and must be a path or URL")
    image = image.strip()

    aspect_ratio = args.get("aspect_ratio", DEFAULT_ASPECT_RATIO)
    size = args.get("size")
    quality_tier = args.get("quality_tier")
    model = args.get("model")
    return _dispatch_to_plugin_provider(
        prompt,
        image,
        aspect_ratio,
        size,
        quality_tier=quality_tier,
        model=model,
    )


registry.register(
    name="image_edit",
    toolset="image_gen",
    schema=IMAGE_EDIT_SCHEMA,
    handler=_handle_image_edit,
    check_fn=check_image_edit_requirements,
    requires_env=[],
    is_async=False,
    emoji="🖼️",
)
