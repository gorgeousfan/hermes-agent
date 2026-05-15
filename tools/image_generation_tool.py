#!/usr/bin/env python3
"""
Image Generation Tools Module

Provides image generation via FAL.ai. Multiple FAL models are supported and
selectable via ``hermes tools`` → Image Generation; the active model is
persisted to ``image_gen.model`` in ``config.yaml``.

Architecture:
- ``FAL_MODELS`` is a catalog of supported models with per-model metadata
  (size-style family, defaults, ``supports`` whitelist, upscaler flag).
- ``_build_fal_payload()`` translates the agent's unified inputs (prompt +
  aspect_ratio) into the model-specific payload and filters to the
  ``supports`` whitelist so models never receive rejected keys.
- Upscaling via FAL's Clarity Upscaler is gated per-model via the ``upscale``
  flag — on for FLUX 2 Pro (backward-compat), off for all faster/newer models
  where upscaling would either hurt latency or add marginal quality.

Pricing shown in UI strings is as-of the initial commit; we accept drift and
update when it's noticed.
"""

import datetime
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import unquote, urlencode, urlparse

# fal_client is imported lazily — see _load_fal_client(). Pulling it
# eagerly added ~64 ms to every CLI cold start because
# discover_builtin_tools() imports this module unconditionally during
# the registry walk, even when image generation is never used.
#
# Tests that monkeypatch this attribute (e.g.
# ``monkeypatch.setattr(image_tool, "fal_client", fake_fal_client)``)
# still work: _load_fal_client() short-circuits when the attribute is
# anything truthy, so a test-installed mock is not overwritten by a
# subsequent real import.
fal_client: Any = None


def _load_fal_client() -> Any:
    """Lazily import fal_client and rebind the module global on first use.

    Idempotent. Returns the (now-loaded) ``fal_client`` module reference.
    Skips the import if the global is already truthy — this preserves the
    test pattern of monkeypatching the module global to install a mock.
    """
    global fal_client
    if fal_client is not None:
        return fal_client
    try:
        from tools.lazy_deps import ensure as _lazy_ensure
        _lazy_ensure("image.fal", prompt=False)
    except ImportError:
        pass
    except Exception as e:
        raise ImportError(str(e))
    import fal_client as _fal_client  # noqa: F811 — module-global rebind
    fal_client = _fal_client
    return fal_client


from tools.debug_helpers import DebugSession
from tools.managed_tool_gateway import resolve_managed_tool_gateway
from tools.tool_backend_helpers import (
    fal_key_is_configured,
    managed_nous_tools_enabled,
    prefers_gateway,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FAL model catalog
# ---------------------------------------------------------------------------
#
# Each entry declares how to translate our unified inputs into the model's
# native payload shape. Size specification falls into three families:
#
#   "image_size_preset" — preset enum ("square_hd", "landscape_16_9", ...)
#                          used by the flux family, z-image, qwen, recraft,
#                          ideogram.
#   "aspect_ratio"      — aspect ratio enum ("16:9", "1:1", ...) used by
#                          nano-banana (Gemini).
#   "gpt_literal"       — literal dimension strings ("1024x1024", etc.)
#                          used by gpt-image-1.5.
#
# ``supports`` is a whitelist of keys allowed in the outgoing payload — any
# key outside this set is stripped before submission so models never receive
# rejected parameters (each FAL model rejects unknown keys differently).
#
# ``upscale`` controls whether to chain Clarity Upscaler after generation.

FAL_MODELS: Dict[str, Dict[str, Any]] = {
    "fal-ai/flux-2/klein/9b": {
        "display": "FLUX 2 Klein 9B",
        "speed": "<1s",
        "strengths": "Fast, crisp text",
        "price": "$0.006/MP",
        "size_style": "image_size_preset",
        "sizes": {
            "landscape": "landscape_16_9",
            "square": "square_hd",
            "portrait": "portrait_16_9",
        },
        "defaults": {
            "num_inference_steps": 4,
            "output_format": "png",
            "enable_safety_checker": False,
        },
        "supports": {
            "prompt", "image_size", "num_inference_steps", "seed",
            "output_format", "enable_safety_checker",
        },
        "upscale": False,
    },
    "fal-ai/flux-2-pro": {
        "display": "FLUX 2 Pro",
        "speed": "~6s",
        "strengths": "Studio photorealism",
        "price": "$0.03/MP",
        "size_style": "image_size_preset",
        "sizes": {
            "landscape": "landscape_16_9",
            "square": "square_hd",
            "portrait": "portrait_16_9",
        },
        "defaults": {
            "num_inference_steps": 50,
            "guidance_scale": 4.5,
            "num_images": 1,
            "output_format": "png",
            "enable_safety_checker": False,
            "safety_tolerance": "5",
            "sync_mode": True,
        },
        "supports": {
            "prompt", "image_size", "num_inference_steps", "guidance_scale",
            "num_images", "output_format", "enable_safety_checker",
            "safety_tolerance", "sync_mode", "seed",
        },
        "upscale": True,   # Backward-compat: current default behavior.
    },
    "fal-ai/z-image/turbo": {
        "display": "Z-Image Turbo",
        "speed": "~2s",
        "strengths": "Bilingual EN/CN, 6B",
        "price": "$0.005/MP",
        "size_style": "image_size_preset",
        "sizes": {
            "landscape": "landscape_16_9",
            "square": "square_hd",
            "portrait": "portrait_16_9",
        },
        "defaults": {
            "num_inference_steps": 8,
            "num_images": 1,
            "output_format": "png",
            "enable_safety_checker": False,
            "enable_prompt_expansion": False,  # avoid the extra per-request charge
        },
        "supports": {
            "prompt", "image_size", "num_inference_steps", "num_images",
            "seed", "output_format", "enable_safety_checker",
            "enable_prompt_expansion",
        },
        "upscale": False,
    },
    "fal-ai/nano-banana-pro": {
        "display": "Nano Banana Pro (Gemini 3 Pro Image)",
        "speed": "~8s",
        "strengths": "Gemini 3 Pro, reasoning depth, text rendering",
        "price": "$0.15/image (1K)",
        "size_style": "aspect_ratio",
        "sizes": {
            "landscape": "16:9",
            "square": "1:1",
            "portrait": "9:16",
        },
        "defaults": {
            "num_images": 1,
            "output_format": "png",
            "safety_tolerance": "5",
            # "1K" is the cheapest tier; 4K doubles the per-image cost.
            # Users on Nous Subscription should stay at 1K for predictable billing.
            "resolution": "1K",
        },
        "supports": {
            "prompt", "aspect_ratio", "num_images", "output_format",
            "safety_tolerance", "seed", "sync_mode", "resolution",
            "enable_web_search", "limit_generations",
        },
        "upscale": False,
    },
    "fal-ai/gpt-image-1.5": {
        "display": "GPT Image 1.5",
        "speed": "~15s",
        "strengths": "Prompt adherence",
        "price": "$0.034/image",
        "size_style": "gpt_literal",
        "sizes": {
            "landscape": "1536x1024",
            "square": "1024x1024",
            "portrait": "1024x1536",
        },
        "defaults": {
            # Quality is pinned to medium to keep portal billing predictable
            # across all users (low is too rough, high is 4-6x more expensive).
            "quality": "medium",
            "num_images": 1,
            "output_format": "png",
        },
        "supports": {
            "prompt", "image_size", "quality", "num_images", "output_format",
            "background", "sync_mode",
        },
        "upscale": False,
    },
    "openai/gpt-image-2": {
        "display": "GPT Image 2",
        "speed": "~20s",
        "strengths": "SOTA text rendering + CJK, world-aware photorealism",
        "price": "$0.04–0.06/image",
        # GPT Image 2 uses FAL's standard preset enum (unlike 1.5's literal
        # dimensions). The FAL schema exposes both 4:3 and 16:9 presets; keep
        # Hermes' landscape/portrait aliases consistent with the rest of the
        # image tool surface.
        "size_style": "image_size_preset",
        "sizes": {
            "landscape": "landscape_16_9",  # 1024x576
            "square": "square_hd",            # 1024x1024
            "portrait": "portrait_16_9",       # 576x1024
        },
        "defaults": {
            # Same quality pinning as gpt-image-1.5: medium keeps Nous
            # Portal billing predictable. "high" is 3-4x the per-image
            # cost at the same size; "low" is too rough for production use.
            "quality": "medium",
            "num_images": 1,
            "output_format": "png",
        },
        "supports": {
            "prompt", "image_size", "quality", "num_images", "output_format",
            "sync_mode",
            # openai_api_key (BYOK) intentionally omitted — all users go
            # through the shared FAL billing path.
        },
        "upscale": False,
    },
    "fal-ai/nano-banana-2": {
        "display": "Nano Banana 2",
        "speed": "~5-15s",
        "strengths": "Fast Gemini Flash image generation, vibrant output, strong text",
        "price": "$0.039/image (1K)",
        "size_style": "aspect_ratio",
        "sizes": {
            "landscape": "16:9",
            "square": "1:1",
            "portrait": "9:16",
        },
        "defaults": {
            "num_images": 1,
            "output_format": "png",
            "safety_tolerance": "4",
            "sync_mode": False,
            "resolution": "1K",
            "limit_generations": True,
        },
        "supports": {
            "prompt", "aspect_ratio", "num_images", "output_format",
            "safety_tolerance", "seed", "sync_mode", "resolution",
            "enable_web_search", "limit_generations", "thinking_level",
        },
        "upscale": False,
    },
    "fal-ai/ideogram/v3": {
        "display": "Ideogram V3",
        "speed": "~5s",
        "strengths": "Best typography",
        "price": "$0.03-0.09/image",
        "size_style": "image_size_preset",
        "sizes": {
            "landscape": "landscape_16_9",
            "square": "square_hd",
            "portrait": "portrait_16_9",
        },
        "defaults": {
            "rendering_speed": "BALANCED",
            "expand_prompt": True,
            "style": "AUTO",
        },
        "supports": {
            "prompt", "image_size", "rendering_speed", "expand_prompt",
            "style", "seed",
        },
        "upscale": False,
    },
    "fal-ai/recraft/v4/pro/text-to-image": {
        "display": "Recraft V4 Pro",
        "speed": "~8s",
        "strengths": "Design, brand systems, production-ready",
        "price": "$0.25/image",
        "size_style": "image_size_preset",
        "sizes": {
            "landscape": "landscape_16_9",
            "square": "square_hd",
            "portrait": "portrait_16_9",
        },
        "defaults": {
            # V4 Pro dropped V3's required `style` enum — defaults handle taste now.
            "enable_safety_checker": False,
        },
        "supports": {
            "prompt", "image_size", "enable_safety_checker",
            "colors", "background_color",
        },
        "upscale": False,
    },
    "fal-ai/qwen-image": {
        "display": "Qwen Image",
        "speed": "~12s",
        "strengths": "LLM-based, complex text",
        "price": "$0.02/MP",
        "size_style": "image_size_preset",
        "sizes": {
            "landscape": "landscape_16_9",
            "square": "square_hd",
            "portrait": "portrait_16_9",
        },
        "defaults": {
            "num_inference_steps": 30,
            "guidance_scale": 2.5,
            "num_images": 1,
            "output_format": "png",
            "acceleration": "regular",
        },
        "supports": {
            "prompt", "image_size", "num_inference_steps", "guidance_scale",
            "num_images", "output_format", "acceleration", "seed", "sync_mode",
        },
        "upscale": False,
    },
}

# Default model is the fastest reasonable option. Kept cheap and sub-1s.
DEFAULT_MODEL = "fal-ai/flux-2/klein/9b"

DEFAULT_ASPECT_RATIO = "landscape"
VALID_ASPECT_RATIOS = ("landscape", "square", "portrait")

FAL_MODEL_ALIASES = {
    "gpt-image-2": "openai/gpt-image-2",
    "fal-ai/gpt-image-2": "openai/gpt-image-2",
    "openai/gpt-image-2": "openai/gpt-image-2",
    "nano-banana-2": "fal-ai/nano-banana-2",
    "nano-banana-2-generate": "fal-ai/nano-banana-2",
}


# ---------------------------------------------------------------------------
# FAL image editing / img2img catalog
# ---------------------------------------------------------------------------
#
# Editing endpoints are separate from text-to-image endpoints. The tool
# exposes a compact surface and these entries translate it into each model's
# native payload shape.

FAL_IMAGE_EDIT_ASPECTS = (
    "auto", "21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4",
    "2:3", "9:16", "9:21", "4:1", "1:4", "8:1", "1:8",
)
FAL_IMAGE_EDIT_STANDARD_ASPECTS = (
    "auto", "21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4",
    "2:3", "9:16",
)

GPT_IMAGE_2_SIZES = (
    "auto", "square_hd", "square", "portrait_4_3", "portrait_16_9",
    "landscape_4_3", "landscape_16_9",
)

FAL_IMAGE_EDIT_MODELS: Dict[str, Dict[str, Any]] = {
    "fal-ai/nano-banana/edit": {
        "display": "Nano Banana Edit",
        "speed": "~5-15s",
        "strengths": "Fast natural-language edits, multi-image context",
        "price": "$0.039/image",
        "image_param": "image_urls",
        "max_images": 14,
        "defaults": {
            "num_images": 1,
            "aspect_ratio": "auto",
            "output_format": "png",
            "safety_tolerance": "4",
            "sync_mode": False,
            "limit_generations": False,
        },
        "supports": {
            "prompt", "num_images", "seed", "aspect_ratio", "output_format",
            "safety_tolerance", "sync_mode", "image_urls", "limit_generations",
        },
        "aspect_ratios": FAL_IMAGE_EDIT_STANDARD_ASPECTS,
        "output_formats": ("jpeg", "png", "webp"),
        "resolutions": None,
    },
    "fal-ai/nano-banana-2/edit": {
        "display": "Nano Banana 2 Edit",
        "speed": "~5-15s",
        "strengths": "Fast high-value Gemini Flash edits, multi-image context",
        "price": "$0.039/image (1K)",
        "image_param": "image_urls",
        "max_images": 14,
        "defaults": {
            "num_images": 1,
            "aspect_ratio": "auto",
            "output_format": "png",
            "safety_tolerance": "4",
            "sync_mode": False,
            "resolution": "1K",
            "limit_generations": True,
            "enable_web_search": False,
        },
        "supports": {
            "prompt", "num_images", "seed", "aspect_ratio", "output_format",
            "safety_tolerance", "sync_mode", "image_urls", "resolution",
            "limit_generations", "enable_web_search", "thinking_level",
        },
        "aspect_ratios": FAL_IMAGE_EDIT_ASPECTS,
        "output_formats": ("jpeg", "png", "webp"),
        "resolutions": ("0.5K", "1K", "2K", "4K"),
        "thinking_levels": ("minimal", "high"),
    },
    "fal-ai/nano-banana-pro/edit": {
        "display": "Nano Banana Pro Edit",
        "speed": "~10-30s",
        "strengths": "Premium reasoning edits, typography, high-resolution outputs",
        "price": "$0.15/image",
        "image_param": "image_urls",
        "max_images": 14,
        "defaults": {
            "num_images": 1,
            "aspect_ratio": "auto",
            "output_format": "png",
            "safety_tolerance": "4",
            "sync_mode": False,
            "resolution": "1K",
            "limit_generations": False,
            "enable_web_search": False,
        },
        "supports": {
            "prompt", "num_images", "seed", "aspect_ratio", "output_format",
            "safety_tolerance", "sync_mode", "image_urls", "resolution",
            "limit_generations", "enable_web_search",
        },
        "aspect_ratios": FAL_IMAGE_EDIT_STANDARD_ASPECTS,
        "output_formats": ("jpeg", "png", "webp"),
        "resolutions": ("1K", "2K", "4K"),
    },
    "openai/gpt-image-2/edit": {
        "display": "GPT Image 2 Edit",
        "speed": "~15-30s",
        "strengths": "Fine-grained OpenAI image edits, typography, optional mask control",
        "price": "$0.04-0.06/image",
        "image_param": "image_urls",
        "max_images": 8,
        "defaults": {
            "image_size": "auto",
            "quality": "high",
            "num_images": 1,
            "output_format": "png",
            "sync_mode": False,
        },
        "supports": {
            "prompt", "image_urls", "image_size", "quality", "num_images",
            "output_format", "sync_mode", "mask_url",
        },
        "image_sizes": GPT_IMAGE_2_SIZES,
        "quality_levels": ("auto", "low", "medium", "high"),
        "output_formats": ("jpeg", "png", "webp"),
        "resolutions": None,
    },
    "fal-ai/gemini-3-pro-image-preview/edit": {
        "display": "Gemini 3 Pro Image Preview Edit",
        "speed": "~10-30s",
        "strengths": "Premium Gemini/Nano Banana Pro editing endpoint",
        "price": "$0.15/image",
        "image_param": "image_urls",
        "max_images": 14,
        "defaults": {
            "num_images": 1,
            "aspect_ratio": "auto",
            "output_format": "png",
            "safety_tolerance": "4",
            "sync_mode": False,
            "resolution": "1K",
            "limit_generations": False,
            "enable_web_search": False,
        },
        "supports": {
            "prompt", "num_images", "seed", "aspect_ratio", "output_format",
            "safety_tolerance", "sync_mode", "image_urls", "resolution",
            "limit_generations", "enable_web_search",
        },
        "aspect_ratios": FAL_IMAGE_EDIT_STANDARD_ASPECTS,
        "output_formats": ("jpeg", "png", "webp"),
        "resolutions": ("1K", "2K", "4K"),
    },
    "fal-ai/flux-pro/kontext": {
        "display": "FLUX.1 Kontext Pro",
        "speed": "~5-15s",
        "strengths": "Targeted local/global edits with strong character consistency",
        "price": "$0.04/image",
        "image_param": "image_url",
        "max_images": 1,
        "defaults": {
            "guidance_scale": 3.5,
            "sync_mode": False,
            "num_images": 1,
            "output_format": "jpeg",
            "safety_tolerance": "2",
            "enhance_prompt": False,
        },
        "supports": {
            "prompt", "seed", "guidance_scale", "sync_mode", "num_images",
            "output_format", "safety_tolerance", "enhance_prompt",
            "aspect_ratio", "image_url",
        },
        "aspect_ratios": ("21:9", "16:9", "4:3", "3:2", "1:1", "2:3", "3:4", "9:16", "9:21"),
        "output_formats": ("jpeg", "png"),
        "resolutions": None,
    },
    "fal-ai/flux-pro/kontext/multi": {
        "display": "FLUX.1 Kontext Pro Multi",
        "speed": "~5-20s",
        "strengths": "Multi-reference FLUX Kontext edits",
        "price": "$0.04/image",
        "image_param": "image_urls",
        "max_images": 8,
        "defaults": {
            "guidance_scale": 3.5,
            "sync_mode": False,
            "num_images": 1,
            "output_format": "jpeg",
            "safety_tolerance": "2",
            "enhance_prompt": False,
        },
        "supports": {
            "prompt", "seed", "guidance_scale", "sync_mode", "num_images",
            "output_format", "safety_tolerance", "enhance_prompt",
            "aspect_ratio", "image_urls",
        },
        "aspect_ratios": ("21:9", "16:9", "4:3", "3:2", "1:1", "2:3", "3:4", "9:16", "9:21"),
        "output_formats": ("jpeg", "png"),
        "resolutions": None,
    },
}

DEFAULT_IMAGE_EDIT_MODEL = "fal-ai/nano-banana/edit"

FAL_IMAGE_EDIT_MODEL_ALIASES = {
    "nano-banana": "fal-ai/nano-banana/edit",
    "nano-banana-edit": "fal-ai/nano-banana/edit",
    "nano-banana-2": "fal-ai/nano-banana-2/edit",
    "nano-banana-2-edit": "fal-ai/nano-banana-2/edit",
    "nano-banana-pro": "fal-ai/nano-banana-pro/edit",
    "nano-banana-pro-edit": "fal-ai/nano-banana-pro/edit",
    "gpt-image-2": "openai/gpt-image-2/edit",
    "gpt-image-2-edit": "openai/gpt-image-2/edit",
    "openai/gpt-image-2": "openai/gpt-image-2/edit",
    "openai/gpt-image-2/edit": "openai/gpt-image-2/edit",
    "fal-ai/gpt-image-2": "openai/gpt-image-2/edit",
    "fal-ai/gpt-image-2/edit": "openai/gpt-image-2/edit",
    "gemini-3-pro-image-preview": "fal-ai/gemini-3-pro-image-preview/edit",
    "gemini-3-pro-image-preview-edit": "fal-ai/gemini-3-pro-image-preview/edit",
    "flux-kontext": "fal-ai/flux-pro/kontext",
    "flux-pro-kontext": "fal-ai/flux-pro/kontext",
    "kontext": "fal-ai/flux-pro/kontext",
    "flux-kontext-multi": "fal-ai/flux-pro/kontext/multi",
    "flux-pro-kontext-multi": "fal-ai/flux-pro/kontext/multi",
    "kontext-multi": "fal-ai/flux-pro/kontext/multi",
    "fal-ai/nano-banana": "fal-ai/nano-banana/edit",
    "fal-ai/nano-banana-2": "fal-ai/nano-banana-2/edit",
    "fal-ai/nano-banana-pro": "fal-ai/nano-banana-pro/edit",
    "fal-ai/gemini-3-pro-image-preview": "fal-ai/gemini-3-pro-image-preview/edit",
}


# ---------------------------------------------------------------------------
# Upscaler (Clarity Upscaler — unchanged from previous implementation)
# ---------------------------------------------------------------------------
UPSCALER_MODEL = "fal-ai/clarity-upscaler"
UPSCALER_FACTOR = 2
UPSCALER_SAFETY_CHECKER = False
UPSCALER_DEFAULT_PROMPT = "masterpiece, best quality, highres"
UPSCALER_NEGATIVE_PROMPT = "(worst quality, low quality, normal quality:2)"
UPSCALER_CREATIVITY = 0.35
UPSCALER_RESEMBLANCE = 0.6
UPSCALER_GUIDANCE_SCALE = 4
UPSCALER_NUM_INFERENCE_STEPS = 18


_debug = DebugSession("image_tools", env_var="IMAGE_TOOLS_DEBUG")
_managed_fal_client = None
_managed_fal_client_config = None
_managed_fal_client_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Managed FAL gateway (Nous Subscription)
# ---------------------------------------------------------------------------
def _resolve_managed_fal_gateway():
    """Return managed fal-queue gateway config when the user prefers the gateway
    or direct FAL credentials are absent."""
    if fal_key_is_configured() and not prefers_gateway("image_gen"):
        return None
    return resolve_managed_tool_gateway("fal-queue")


def _normalize_fal_queue_url_format(queue_run_origin: str) -> str:
    normalized_origin = str(queue_run_origin or "").strip().rstrip("/")
    if not normalized_origin:
        raise ValueError("Managed FAL queue origin is required")
    return f"{normalized_origin}/"


class _ManagedFalSyncClient:
    """Small per-instance wrapper around fal_client.SyncClient for managed queue hosts."""

    def __init__(self, *, key: str, queue_run_origin: str):
        # Trigger the lazy import on first construction. Idempotent — the
        # placeholder is overwritten with the real module on first call.
        _load_fal_client()
        sync_client_class = getattr(fal_client, "SyncClient", None)
        if sync_client_class is None:
            raise RuntimeError("fal_client.SyncClient is required for managed FAL gateway mode")

        client_module = getattr(fal_client, "client", None)
        if client_module is None:
            raise RuntimeError("fal_client.client is required for managed FAL gateway mode")

        self._queue_url_format = _normalize_fal_queue_url_format(queue_run_origin)
        self._sync_client = sync_client_class(key=key)
        self._http_client = getattr(self._sync_client, "_client", None)
        self._maybe_retry_request = getattr(client_module, "_maybe_retry_request", None)
        self._raise_for_status = getattr(client_module, "_raise_for_status", None)
        self._request_handle_class = getattr(client_module, "SyncRequestHandle", None)
        self._add_hint_header = getattr(client_module, "add_hint_header", None)
        self._add_priority_header = getattr(client_module, "add_priority_header", None)
        self._add_timeout_header = getattr(client_module, "add_timeout_header", None)

        if self._http_client is None:
            raise RuntimeError("fal_client.SyncClient._client is required for managed FAL gateway mode")
        if self._maybe_retry_request is None or self._raise_for_status is None:
            raise RuntimeError("fal_client.client request helpers are required for managed FAL gateway mode")
        if self._request_handle_class is None:
            raise RuntimeError("fal_client.client.SyncRequestHandle is required for managed FAL gateway mode")

    def submit(
        self,
        application: str,
        arguments: Dict[str, Any],
        *,
        path: str = "",
        hint: Optional[str] = None,
        webhook_url: Optional[str] = None,
        priority: Any = None,
        headers: Optional[Dict[str, str]] = None,
        start_timeout: Optional[Union[int, float]] = None,
    ):
        url = self._queue_url_format + application
        if path:
            url += "/" + path.lstrip("/")
        if webhook_url is not None:
            url += "?" + urlencode({"fal_webhook": webhook_url})

        request_headers = dict(headers or {})
        if hint is not None and self._add_hint_header is not None:
            self._add_hint_header(hint, request_headers)
        if priority is not None:
            if self._add_priority_header is None:
                raise RuntimeError("fal_client.client.add_priority_header is required for priority requests")
            self._add_priority_header(priority, request_headers)
        if start_timeout is not None:
            if self._add_timeout_header is None:
                raise RuntimeError("fal_client.client.add_timeout_header is required for timeout requests")
            self._add_timeout_header(start_timeout, request_headers)

        response = self._maybe_retry_request(
            self._http_client,
            "POST",
            url,
            json=arguments,
            timeout=getattr(self._sync_client, "default_timeout", 120.0),
            headers=request_headers,
        )
        self._raise_for_status(response)

        data = response.json()
        return self._request_handle_class(
            request_id=data["request_id"],
            response_url=data["response_url"],
            status_url=data["status_url"],
            cancel_url=data["cancel_url"],
            client=self._http_client,
        )


def _get_managed_fal_client(managed_gateway):
    """Reuse the managed FAL client so its internal httpx.Client is not leaked per call."""
    global _managed_fal_client, _managed_fal_client_config

    client_config = (
        managed_gateway.gateway_origin.rstrip("/"),
        managed_gateway.nous_user_token,
    )
    with _managed_fal_client_lock:
        if _managed_fal_client is not None and _managed_fal_client_config == client_config:
            return _managed_fal_client

        _managed_fal_client = _ManagedFalSyncClient(
            key=managed_gateway.nous_user_token,
            queue_run_origin=managed_gateway.gateway_origin,
        )
        _managed_fal_client_config = client_config
        return _managed_fal_client


def _submit_fal_request(model: str, arguments: Dict[str, Any]):
    """Submit a FAL request using direct credentials or the managed queue gateway."""
    # Trigger the lazy import on first call. Idempotent.
    _load_fal_client()
    request_headers = {"x-idempotency-key": str(uuid.uuid4())}
    managed_gateway = _resolve_managed_fal_gateway()
    if managed_gateway is None:
        return fal_client.submit(model, arguments=arguments, headers=request_headers)

    managed_client = _get_managed_fal_client(managed_gateway)
    try:
        return managed_client.submit(
            model,
            arguments=arguments,
            headers=request_headers,
        )
    except Exception as exc:
        # 4xx from the managed gateway typically means the portal doesn't
        # currently proxy this model (allowlist miss, billing gate, etc.)
        # — surface a clearer message with actionable remediation instead
        # of a raw HTTP error from httpx.
        status = _extract_http_status(exc)
        if status is not None and 400 <= status < 500:
            raise ValueError(
                f"Nous Subscription gateway rejected model '{model}' "
                f"(HTTP {status}). This model may not yet be enabled on "
                f"the Nous Portal's FAL proxy. Either:\n"
                f"  • Set FAL_KEY in your environment to use FAL.ai directly, or\n"
                f"  • Pick a different model via `hermes tools` → Image Generation."
            ) from exc
        raise


def _extract_http_status(exc: BaseException) -> Optional[int]:
    """Return an HTTP status code from httpx/fal exceptions, else None.

    Defensive across exception shapes — httpx.HTTPStatusError exposes
    ``.response.status_code`` while fal_client wrappers may expose
    ``.status_code`` directly.
    """
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    return None


# ---------------------------------------------------------------------------
# Model resolution + payload construction
# ---------------------------------------------------------------------------
def _resolve_fal_model() -> tuple:
    """Resolve the active FAL model from config.yaml (primary) or default.

    Returns (model_id, metadata_dict). Falls back to DEFAULT_MODEL if the
    configured model is unknown (logged as a warning).
    """
    model_id = ""
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        img_cfg = cfg.get("image_gen") if isinstance(cfg, dict) else None
        if isinstance(img_cfg, dict):
            raw = img_cfg.get("model")
            if isinstance(raw, str):
                model_id = raw.strip()
    except Exception as exc:
        logger.debug("Could not load image_gen.model from config: %s", exc)

    # Env var escape hatch (undocumented; backward-compat for tests/scripts).
    if not model_id:
        model_id = os.getenv("FAL_IMAGE_MODEL", "").strip()

    if not model_id:
        return DEFAULT_MODEL, FAL_MODELS[DEFAULT_MODEL]

    model_id = FAL_MODEL_ALIASES.get(model_id, FAL_MODEL_ALIASES.get(model_id.lower(), model_id))

    if model_id not in FAL_MODELS:
        logger.warning(
            "Unknown FAL model '%s' in config; falling back to %s",
            model_id, DEFAULT_MODEL,
        )
        return DEFAULT_MODEL, FAL_MODELS[DEFAULT_MODEL]

    return model_id, FAL_MODELS[model_id]


def _build_fal_payload(
    model_id: str,
    prompt: str,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    seed: Optional[int] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a FAL request payload for `model_id` from unified inputs.

    Translates aspect_ratio into the model's native size spec (preset enum,
    aspect-ratio enum, or GPT literal string), merges model defaults, applies
    caller overrides, then filters to the model's ``supports`` whitelist.
    """
    model_id = FAL_MODEL_ALIASES.get(model_id, FAL_MODEL_ALIASES.get(model_id.lower(), model_id))
    meta = FAL_MODELS[model_id]
    size_style = meta["size_style"]
    sizes = meta["sizes"]

    aspect = (aspect_ratio or DEFAULT_ASPECT_RATIO).lower().strip()
    if aspect not in sizes:
        aspect = DEFAULT_ASPECT_RATIO

    payload: Dict[str, Any] = dict(meta.get("defaults", {}))
    payload["prompt"] = (prompt or "").strip()

    if size_style in {"image_size_preset", "gpt_literal"}:
        payload["image_size"] = sizes[aspect]
    elif size_style == "aspect_ratio":
        payload["aspect_ratio"] = sizes[aspect]
    else:
        raise ValueError(f"Unknown size_style: {size_style!r}")

    if seed is not None and isinstance(seed, int):
        payload["seed"] = seed

    if overrides:
        for k, v in overrides.items():
            if v is not None:
                payload[k] = v

    supports = meta["supports"]
    return {k: v for k, v in payload.items() if k in supports}


def _canonical_fal_edit_model(value: Optional[str]) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw in FAL_IMAGE_EDIT_MODELS:
        return raw
    lowered = raw.lower()
    return FAL_IMAGE_EDIT_MODEL_ALIASES.get(lowered)


def _resolve_fal_edit_model(explicit: Optional[str] = None) -> tuple[str, Dict[str, Any]]:
    """Resolve the active FAL image editing model.

    Selection order:
    1. tool-call ``model`` argument
    2. ``FAL_IMAGE_EDIT_MODEL`` env var
    3. ``image_gen.edit_model`` in config.yaml
    4. ``image_gen.model`` when it maps cleanly to an edit endpoint
    5. ``DEFAULT_IMAGE_EDIT_MODEL``
    """
    candidates: List[Optional[str]] = [explicit, os.getenv("FAL_IMAGE_EDIT_MODEL")]

    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        img_cfg = cfg.get("image_gen") if isinstance(cfg, dict) else None
        if isinstance(img_cfg, dict):
            edit_model = img_cfg.get("edit_model")
            if isinstance(edit_model, str):
                candidates.append(edit_model)
            gen_model = img_cfg.get("model")
            if isinstance(gen_model, str):
                candidates.append(gen_model)
    except Exception as exc:
        logger.debug("Could not load image_gen edit model config: %s", exc)

    for candidate in candidates:
        canonical = _canonical_fal_edit_model(candidate)
        if canonical:
            return canonical, FAL_IMAGE_EDIT_MODELS[canonical]

    return DEFAULT_IMAGE_EDIT_MODEL, FAL_IMAGE_EDIT_MODELS[DEFAULT_IMAGE_EDIT_MODEL]


def _normalize_image_edit_urls(
    image_urls: Any = None,
    image_url: Any = None,
    image_paths: Any = None,
    image_path: Any = None,
) -> List[str]:
    """Normalize single and multi-image inputs into a clean source list.

    Sources may already be public HTTP(S) URLs or local filesystem paths.
    Local paths are uploaded later, after provider/auth checks have run.
    """
    values: List[Any] = []
    if isinstance(image_url, str) and image_url.strip():
        values.append(image_url)
    if isinstance(image_urls, str):
        values.extend(part.strip() for part in image_urls.split(","))
    elif isinstance(image_urls, (list, tuple)):
        values.extend(image_urls)
    if isinstance(image_path, str) and image_path.strip():
        values.append(image_path)
    if isinstance(image_paths, str):
        values.extend(part.strip() for part in image_paths.split(","))
    elif isinstance(image_paths, (list, tuple)):
        values.extend(image_paths)

    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        clean = value.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def _is_http_image_source(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _local_image_source_path(value: str) -> Path:
    parsed = urlparse(value)
    if parsed.scheme.lower() == "file":
        return Path(unquote(parsed.path)).expanduser()
    return Path(value).expanduser()


def _upload_fal_local_file(path_value: str) -> str:
    """Upload a local image file to FAL storage and return its public URL."""
    path = _local_image_source_path(path_value)
    if not path.is_file():
        raise ValueError(f"Local image file not found: {path_value}")
    if not fal_key_is_configured():
        raise ValueError(
            "Local image file upload requires FAL_KEY. Pass an existing "
            "public image URL, or configure FAL_KEY so Hermes can upload "
            "the file to FAL storage before editing."
        )

    _load_fal_client()
    upload_file = getattr(fal_client, "upload_file", None)
    if upload_file is None:
        raise RuntimeError(
            "fal_client.upload_file is unavailable; upgrade fal-client or "
            "pass a public image URL."
        )
    return upload_file(path)


def _prepare_image_edit_urls(sources: List[str]) -> List[str]:
    """Convert local image paths to public FAL URLs; preserve HTTP(S) URLs."""
    prepared: List[str] = []
    for source in sources:
        if _is_http_image_source(source):
            prepared.append(source)
        else:
            prepared.append(_upload_fal_local_file(source))
    return prepared


def _normalize_fal_edit_aspect_ratio(value: Optional[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        return "auto"
    normalized = value.strip().lower()
    aliases = {
        "landscape": "16:9",
        "wide": "16:9",
        "portrait": "9:16",
        "vertical": "9:16",
        "square": "1:1",
    }
    return aliases.get(normalized, normalized)


def _coerce_positive_int(value: Any, *, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _coerce_nonnegative_int(value: Any, *, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _coerce_float(value: Any, *, default: Optional[float] = None) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, *, default: Optional[bool] = None) -> Optional[bool]:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return default


def _build_fal_edit_payload(
    model_id: str,
    *,
    prompt: str,
    image_urls: List[str],
    aspect_ratio: Optional[str] = None,
    output_format: Optional[str] = None,
    num_images: Optional[int] = None,
    seed: Optional[int] = None,
    guidance_scale: Optional[float] = None,
    resolution: Optional[str] = None,
    limit_generations: Optional[bool] = None,
    enable_web_search: Optional[bool] = None,
    image_size: Optional[str] = None,
    quality: Optional[str] = None,
    mask_url: Optional[str] = None,
    thinking_level: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a FAL edit/img2img payload for ``model_id``.

    The returned payload is filtered through the model's ``supports`` set so
    unsupported convenience args never reach FAL.
    """
    meta = FAL_IMAGE_EDIT_MODELS[model_id]
    payload: Dict[str, Any] = dict(meta.get("defaults", {}))
    payload["prompt"] = (prompt or "").strip()

    max_images = int(meta.get("max_images") or len(image_urls) or 1)
    limited_urls = image_urls[:max_images]
    if meta.get("image_param") == "image_url":
        payload["image_url"] = limited_urls[0]
    else:
        payload["image_urls"] = limited_urls

    if num_images is not None:
        payload["num_images"] = max(1, min(4, num_images))
    if seed is not None:
        payload["seed"] = seed
    if guidance_scale is not None:
        payload["guidance_scale"] = max(1.0, min(20.0, guidance_scale))
    if limit_generations is not None:
        payload["limit_generations"] = bool(limit_generations)
    if enable_web_search is not None:
        payload["enable_web_search"] = bool(enable_web_search)
    if isinstance(mask_url, str) and mask_url.strip():
        payload["mask_url"] = mask_url.strip()

    aspect = _normalize_fal_edit_aspect_ratio(aspect_ratio)
    allowed_aspects = meta.get("aspect_ratios")
    if allowed_aspects and aspect in allowed_aspects:
        payload["aspect_ratio"] = aspect

    image_sizes = meta.get("image_sizes")
    if isinstance(image_size, str) and image_size.strip() and image_sizes:
        size = image_size.strip()
        size_aliases = {
            "landscape": "landscape_16_9",
            "wide": "landscape_16_9",
            "standard_landscape": "landscape_4_3",
            "portrait": "portrait_16_9",
            "vertical": "portrait_16_9",
            "standard_portrait": "portrait_4_3",
            "square": "square_hd",
        }
        size = size_aliases.get(size.lower(), size)
        if size in image_sizes:
            payload["image_size"] = size

    quality_levels = meta.get("quality_levels")
    if isinstance(quality, str) and quality.strip() and quality_levels:
        level = quality.strip().lower()
        if level in quality_levels:
            payload["quality"] = level

    formats = meta.get("output_formats")
    if isinstance(output_format, str) and output_format.strip():
        fmt = output_format.strip().lower()
        if not formats or fmt in formats:
            payload["output_format"] = fmt

    resolutions = meta.get("resolutions")
    if isinstance(resolution, str) and resolution.strip() and resolutions:
        res = resolution.strip().upper()
        if res in resolutions:
            payload["resolution"] = res

    thinking_levels = meta.get("thinking_levels")
    if isinstance(thinking_level, str) and thinking_level.strip() and thinking_levels:
        level = thinking_level.strip().lower()
        if level in thinking_levels:
            payload["thinking_level"] = level

    supports = meta["supports"]
    return {k: v for k, v in payload.items() if k in supports}


# ---------------------------------------------------------------------------
# Upscaler
# ---------------------------------------------------------------------------
def _upscale_image(image_url: str, original_prompt: str) -> Optional[Dict[str, Any]]:
    """Upscale an image using FAL.ai's Clarity Upscaler.

    Returns upscaled image dict, or None on failure (caller falls back to
    the original image).
    """
    try:
        logger.info("Upscaling image with Clarity Upscaler...")

        upscaler_arguments = {
            "image_url": image_url,
            "prompt": f"{UPSCALER_DEFAULT_PROMPT}, {original_prompt}",
            "upscale_factor": UPSCALER_FACTOR,
            "negative_prompt": UPSCALER_NEGATIVE_PROMPT,
            "creativity": UPSCALER_CREATIVITY,
            "resemblance": UPSCALER_RESEMBLANCE,
            "guidance_scale": UPSCALER_GUIDANCE_SCALE,
            "num_inference_steps": UPSCALER_NUM_INFERENCE_STEPS,
            "enable_safety_checker": UPSCALER_SAFETY_CHECKER,
        }

        handler = _submit_fal_request(UPSCALER_MODEL, arguments=upscaler_arguments)
        result = handler.get()

        if result and "image" in result:
            upscaled_image = result["image"]
            logger.info(
                "Image upscaled successfully to %sx%s",
                upscaled_image.get("width", "unknown"),
                upscaled_image.get("height", "unknown"),
            )
            return {
                "url": upscaled_image["url"],
                "width": upscaled_image.get("width", 0),
                "height": upscaled_image.get("height", 0),
                "upscaled": True,
                "upscale_factor": UPSCALER_FACTOR,
            }
        logger.error("Upscaler returned invalid response")
        return None

    except Exception as e:
        logger.error("Error upscaling image: %s", e, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------
def image_generate_tool(
    prompt: str,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    num_inference_steps: Optional[int] = None,
    guidance_scale: Optional[float] = None,
    num_images: Optional[int] = None,
    output_format: Optional[str] = None,
    seed: Optional[int] = None,
) -> str:
    """Generate an image from a text prompt using the configured FAL model.

    The agent-facing schema exposes only ``prompt`` and ``aspect_ratio``; the
    remaining kwargs are overrides for direct Python callers and are filtered
    per-model via the ``supports`` whitelist (unsupported overrides are
    silently dropped so legacy callers don't break when switching models).

    Returns a JSON string with ``{"success": bool, "image": url | None,
    "error": str, "error_type": str}``.
    """
    model_id, meta = _resolve_fal_model()

    debug_call_data = {
        "model": model_id,
        "parameters": {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "num_inference_steps": num_inference_steps,
            "guidance_scale": guidance_scale,
            "num_images": num_images,
            "output_format": output_format,
            "seed": seed,
        },
        "error": None,
        "success": False,
        "images_generated": 0,
        "generation_time": 0,
    }

    start_time = datetime.datetime.now()

    try:
        if not prompt or not isinstance(prompt, str) or len(prompt.strip()) == 0:
            raise ValueError("Prompt is required and must be a non-empty string")

        if not (fal_key_is_configured() or _resolve_managed_fal_gateway()):
            raise ValueError(_build_no_backend_setup_message())

        aspect_lc = (aspect_ratio or DEFAULT_ASPECT_RATIO).lower().strip()
        if aspect_lc not in VALID_ASPECT_RATIOS:
            logger.warning(
                "Invalid aspect_ratio '%s', defaulting to '%s'",
                aspect_ratio, DEFAULT_ASPECT_RATIO,
            )
            aspect_lc = DEFAULT_ASPECT_RATIO

        overrides: Dict[str, Any] = {}
        if num_inference_steps is not None:
            overrides["num_inference_steps"] = num_inference_steps
        if guidance_scale is not None:
            overrides["guidance_scale"] = guidance_scale
        if num_images is not None:
            overrides["num_images"] = num_images
        if output_format is not None:
            overrides["output_format"] = output_format

        arguments = _build_fal_payload(
            model_id, prompt, aspect_lc, seed=seed, overrides=overrides,
        )

        logger.info(
            "Generating image with %s (%s) — prompt: %s",
            meta.get("display", model_id), model_id, prompt[:80],
        )

        handler = _submit_fal_request(model_id, arguments=arguments)
        result = handler.get()

        generation_time = (datetime.datetime.now() - start_time).total_seconds()

        if not result or "images" not in result:
            raise ValueError("Invalid response from FAL.ai API — no images returned")

        images = result.get("images", [])
        if not images:
            raise ValueError("No images were generated")

        should_upscale = bool(meta.get("upscale", False))

        formatted_images = []
        for img in images:
            if not (isinstance(img, dict) and "url" in img):
                continue
            original_image = {
                "url": img["url"],
                "width": img.get("width", 0),
                "height": img.get("height", 0),
            }

            if should_upscale:
                upscaled_image = _upscale_image(img["url"], prompt.strip())
                if upscaled_image:
                    formatted_images.append(upscaled_image)
                    continue
                logger.warning("Using original image as fallback (upscale failed)")

            original_image["upscaled"] = False
            formatted_images.append(original_image)

        if not formatted_images:
            raise ValueError("No valid image URLs returned from API")

        upscaled_count = sum(1 for img in formatted_images if img.get("upscaled"))
        logger.info(
            "Generated %s image(s) in %.1fs (%s upscaled) via %s",
            len(formatted_images), generation_time, upscaled_count, model_id,
        )

        response_data = {
            "success": True,
            "image": formatted_images[0]["url"] if formatted_images else None,
        }

        debug_call_data["success"] = True
        debug_call_data["images_generated"] = len(formatted_images)
        debug_call_data["generation_time"] = generation_time
        _debug.log_call("image_generate_tool", debug_call_data)
        _debug.save()

        return json.dumps(response_data, indent=2, ensure_ascii=False)

    except Exception as e:
        generation_time = (datetime.datetime.now() - start_time).total_seconds()
        error_msg = f"Error generating image: {str(e)}"
        logger.error("%s", error_msg, exc_info=True)

        response_data = {
            "success": False,
            "image": None,
            "error": str(e),
            "error_type": type(e).__name__,
        }

        debug_call_data["error"] = error_msg
        debug_call_data["generation_time"] = generation_time
        _debug.log_call("image_generate_tool", debug_call_data)
        _debug.save()

        return json.dumps(response_data, indent=2, ensure_ascii=False)


def _format_fal_image_outputs(images: Any) -> List[Dict[str, Any]]:
    formatted: List[Dict[str, Any]] = []
    if not isinstance(images, list):
        return formatted
    for img in images:
        if isinstance(img, dict) and img.get("url"):
            formatted.append({
                "url": img["url"],
                "width": img.get("width"),
                "height": img.get("height"),
                "content_type": img.get("content_type"),
                "file_name": img.get("file_name"),
                "file_size": img.get("file_size"),
            })
        elif isinstance(img, str) and img.strip():
            formatted.append({"url": img.strip()})
    return formatted


def image_edit_tool(
    prompt: str,
    image_urls: List[str],
    *,
    aspect_ratio: str = "auto",
    output_format: str = "png",
    num_images: Optional[int] = None,
    seed: Optional[int] = None,
    guidance_scale: Optional[float] = None,
    resolution: Optional[str] = None,
    model: Optional[str] = None,
    limit_generations: Optional[bool] = None,
    enable_web_search: Optional[bool] = None,
    image_size: Optional[str] = None,
    quality: Optional[str] = None,
    mask_url: Optional[str] = None,
    thinking_level: Optional[str] = None,
) -> str:
    """Edit one or more images with FAL image-to-image/edit endpoints.

    Returns a JSON string with the first edited image in ``image`` and all
    returned outputs in ``images``.
    """
    start_time = datetime.datetime.now()
    model_id, meta = _resolve_fal_edit_model(model)

    debug_call_data = {
        "model": model_id,
        "parameters": {
            "prompt": prompt,
            "image_urls": image_urls,
            "aspect_ratio": aspect_ratio,
            "output_format": output_format,
            "num_images": num_images,
            "seed": seed,
            "guidance_scale": guidance_scale,
            "resolution": resolution,
            "image_size": image_size,
            "quality": quality,
            "mask_url": mask_url,
            "thinking_level": thinking_level,
        },
        "error": None,
        "success": False,
        "images_generated": 0,
        "generation_time": 0,
    }

    try:
        if not prompt or not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt is required and must be a non-empty string")
        if not image_urls:
            raise ValueError("At least one image URL or local path is required for image editing")
        if not (fal_key_is_configured() or _resolve_managed_fal_gateway()):
            message = "FAL_KEY environment variable not set"
            if managed_nous_tools_enabled():
                message += " and managed FAL gateway is unavailable"
            raise ValueError(message)

        image_urls = _prepare_image_edit_urls(image_urls)
        if isinstance(mask_url, str) and mask_url.strip():
            mask_url = _prepare_image_edit_urls([mask_url.strip()])[0]

        arguments = _build_fal_edit_payload(
            model_id,
            prompt=prompt,
            image_urls=image_urls,
            aspect_ratio=aspect_ratio,
            output_format=output_format,
            num_images=num_images,
            seed=seed,
            guidance_scale=guidance_scale,
            resolution=resolution,
            limit_generations=limit_generations,
            enable_web_search=enable_web_search,
            image_size=image_size,
            quality=quality,
            mask_url=mask_url,
            thinking_level=thinking_level,
        )

        logger.info(
            "Editing image with %s (%s) — prompt: %s",
            meta.get("display", model_id), model_id, prompt[:80],
        )

        handler = _submit_fal_request(model_id, arguments=arguments)
        result = handler.get()
        generation_time = (datetime.datetime.now() - start_time).total_seconds()

        formatted_images = _format_fal_image_outputs((result or {}).get("images"))
        if not formatted_images:
            raise ValueError("Invalid response from FAL.ai API — no images returned")

        response_data = {
            "success": True,
            "image": formatted_images[0]["url"],
            "images": formatted_images,
            "model": model_id,
            "provider": "fal",
            "prompt": prompt.strip(),
            "source_images": image_urls,
            "aspect_ratio": arguments.get("aspect_ratio", ""),
        }
        if isinstance(result, dict):
            if result.get("description"):
                response_data["description"] = result["description"]
            if result.get("seed") is not None:
                response_data["seed"] = result["seed"]

        debug_call_data["success"] = True
        debug_call_data["images_generated"] = len(formatted_images)
        debug_call_data["generation_time"] = generation_time
        _debug.log_call("image_edit_tool", debug_call_data)
        _debug.save()

        return json.dumps(response_data, indent=2, ensure_ascii=False)

    except Exception as e:
        generation_time = (datetime.datetime.now() - start_time).total_seconds()
        error_msg = f"Error editing image: {str(e)}"
        logger.error("%s", error_msg, exc_info=True)

        response_data = {
            "success": False,
            "image": None,
            "error": str(e),
            "error_type": type(e).__name__,
            "model": model_id,
            "provider": "fal",
        }

        debug_call_data["error"] = error_msg
        debug_call_data["generation_time"] = generation_time
        _debug.log_call("image_edit_tool", debug_call_data)
        _debug.save()

        return json.dumps(response_data, indent=2, ensure_ascii=False)


def check_fal_api_key() -> bool:
    """True if the FAL.ai API key (direct or managed gateway) is available."""
    return bool(fal_key_is_configured() or _resolve_managed_fal_gateway())


def check_fal_image_edit_requirements() -> bool:
    """True when the in-tree FAL edit backend can service image_edit calls."""
    try:
        if check_fal_api_key():
            _load_fal_client()
            return True
    except ImportError:
        return False
    return False


def _build_no_backend_setup_message() -> str:
    """Build an actionable error string when no FAL backend is reachable.

    Used by the in-tree FAL path. Mentions:
      - FAL_KEY signup link
      - managed-gateway status (if Nous tools are enabled)
      - plugin alternative pointer (so users on a stale ``image_gen.provider``
        know the registry exists and how to inspect it)
    """
    lines = ["Image generation is unavailable in this environment.", ""]
    lines.append("Missing requirements:")
    if managed_nous_tools_enabled():
        lines.append(
            "  - FAL_KEY is not set and the managed FAL gateway is unreachable"
        )
    else:
        lines.append("  - FAL_KEY environment variable is not set")
    lines.append("")
    lines.append("To enable image generation, do one of:")
    lines.append(
        "  1. Get a free API key at https://fal.ai and set "
        "FAL_KEY=<your-key> (then restart the session)"
    )
    if managed_nous_tools_enabled():
        lines.append(
            "  2. Sign in to a Nous account that has the managed FAL "
            "gateway enabled (`hermes setup`)"
        )
    lines.append(
        "  3. Configure a different image_gen provider via `hermes tools` "
        "→ Image Generation (run `hermes plugins list` to see installed "
        "backends)"
    )
    return "\n".join(lines)


def check_image_generation_requirements() -> bool:
    """True if any image gen backend is available.

    Providers are considered in this order:

    1. The in-tree FAL backend (FAL_KEY or managed gateway).
    2. Any plugin-registered provider whose ``is_available()`` returns True.

    Plugins win only when the in-tree FAL path is NOT ready, which matches
    the historical behavior: shipping hermes with a FAL key configured
    should still expose the tool. The active selection among ready
    providers is resolved per-call by ``image_gen.provider``.
    """
    try:
        if check_fal_api_key():
            # Trigger the lazy fal_client import here as the SDK presence
            # check. Raises ImportError if the optional ``fal-client``
            # package isn't installed; the caller's except ImportError
            # below catches that and continues to plugin probing.
            _load_fal_client()
            return True
    except ImportError:
        pass

    # Probe plugin providers. Discovery is idempotent and cheap.
    try:
        from agent.image_gen_registry import list_providers
        from hermes_cli.plugins import _ensure_plugins_discovered

        _ensure_plugins_discovered()
        for provider in list_providers():
            try:
                if provider.is_available():
                    return True
            except Exception:
                continue
    except Exception:
        pass

    return False


# ---------------------------------------------------------------------------
# Demo / CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("🎨 Image Generation Tools — FAL.ai multi-model support")
    print("=" * 60)

    if not check_fal_api_key():
        print("❌ FAL_KEY environment variable not set")
        print("   Set it via: export FAL_KEY='your-key-here'")
        print("   Get a key: https://fal.ai/")
        raise SystemExit(1)
    print("✅ FAL.ai API key found")

    try:
        import fal_client  # noqa: F401
        print("✅ fal_client library available")
    except ImportError:
        print("❌ fal_client library not found — pip install fal-client")
        raise SystemExit(1)

    model_id, meta = _resolve_fal_model()
    print(f"🤖 Active model: {meta.get('display', model_id)} ({model_id})")
    print(f"   Speed: {meta.get('speed', '?')}  ·  Price: {meta.get('price', '?')}")
    print(f"   Upscaler: {'on' if meta.get('upscale') else 'off'}")

    print("\nAvailable models:")
    for mid, m in FAL_MODELS.items():
        marker = " ← active" if mid == model_id else ""
        print(f"  {mid:<32}  {m.get('speed', '?'):<6}  {m.get('price', '?')}{marker}")

    if _debug.active:
        print(f"\n🐛 Debug mode enabled — session {_debug.session_id}")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
from tools.registry import registry, tool_error

IMAGE_GENERATE_SCHEMA = {
    "name": "image_generate",
    "description": (
        "Generate high-quality images from text prompts. The underlying "
        "backend (FAL, OpenAI, etc.) and model are user-configured and not "
        "selectable by the agent. Returns either a URL or an absolute file "
        "path in the `image` field; display it with markdown "
        "![description](url-or-path) and the gateway will deliver it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The text prompt describing the desired image. Be detailed and descriptive.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": list(VALID_ASPECT_RATIOS),
                "description": "The aspect ratio of the generated image. 'landscape' is 16:9 wide, 'portrait' is 16:9 tall, 'square' is 1:1.",
                "default": DEFAULT_ASPECT_RATIO,
            },
        },
        "required": ["prompt"],
    },
}

IMAGE_EDIT_SCHEMA = {
    "name": "image_edit",
    "description": (
        "Edit or transform existing images with FAL image-to-image models. "
        "Use this after `image_generate` to refine a concept image, combine "
        "references, change scene details, restyle, or create a start frame "
        "for `video_generate`. Provide `image_url` for one source image or "
        "`image_urls` for multiple references. Local files can be passed as "
        "`image_path` or `image_paths`; Hermes uploads them to FAL storage "
        "before editing. Returns the edited image URL in `image`; display "
        "it with markdown ![description](url-or-path)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Natural-language edit instruction. Be explicit about what should change and what should remain unchanged.",
            },
            "image_url": {
                "type": "string",
                "description": "Single source image URL to edit. Use image_urls instead when combining multiple references.",
            },
            "image_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "One or more source/reference image URLs for image editing or img2img generation.",
            },
            "image_path": {
                "type": "string",
                "description": "Single local image file path to upload to FAL storage before editing.",
            },
            "image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "One or more local image file paths to upload to FAL storage before editing.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": list(FAL_IMAGE_EDIT_ASPECTS) + ["landscape", "square", "portrait"],
                "description": "Output aspect ratio. Use auto to preserve/infer from source image; landscape/square/portrait are accepted aliases.",
                "default": "auto",
            },
            "output_format": {
                "type": "string",
                "enum": ["png", "jpeg", "webp"],
                "description": "Output image format. Some edit models support only png/jpeg; unsupported values are ignored.",
                "default": "png",
            },
            "num_images": {
                "type": "integer",
                "description": "Number of edited variations to return. FAL edit endpoints clamp to 1-4.",
                "default": 1,
            },
            "seed": {
                "type": "integer",
                "description": "Optional seed for reproducible edits where supported.",
            },
            "guidance_scale": {
                "type": "number",
                "description": "Prompt-adherence strength for FLUX Kontext models. Ignored by models that do not support it.",
            },
            "resolution": {
                "type": "string",
                "enum": ["0.5K", "1K", "2K", "4K"],
                "description": "Nano Banana 2/Pro/Gemini edit resolution. Ignored by other edit models.",
            },
            "image_size": {
                "type": "string",
                "enum": list(GPT_IMAGE_2_SIZES) + ["landscape", "square", "portrait"],
                "description": "GPT Image 2 output size. Use auto to infer from source images; landscape/square/portrait are accepted aliases.",
            },
            "quality": {
                "type": "string",
                "enum": ["auto", "low", "medium", "high"],
                "description": "GPT Image 2 quality setting. Ignored by models that do not support it.",
            },
            "mask_url": {
                "type": "string",
                "description": "Optional mask image URL for GPT Image 2 edits. White regions are the area to edit.",
            },
            "mask_image_url": {
                "type": "string",
                "description": "Alias for mask_url, accepted for compatibility with GPT Image 2 docs/examples.",
            },
            "thinking_level": {
                "type": "string",
                "enum": ["minimal", "high"],
                "description": "Nano Banana 2 thinking level. Omit unless deeper reasoning is needed.",
            },
            "model": {
                "type": "string",
                "description": (
                    "Optional FAL edit model override. Supported IDs include "
                    "openai/gpt-image-2/edit, fal-ai/nano-banana-2/edit, "
                    "fal-ai/nano-banana/edit, fal-ai/nano-banana-pro/edit, "
                    "fal-ai/flux-pro/kontext, and fal-ai/flux-pro/kontext/multi. "
                    "Omit to use image_gen.edit_model config or the default."
                ),
            },
        },
        "required": ["prompt"],
    },
}


def _read_configured_image_model():
    """Return the value of ``image_gen.model`` from config.yaml, or None."""
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        if isinstance(section, dict):
            value = section.get("model")
            if isinstance(value, str) and value.strip():
                return value.strip()
    except Exception as exc:
        logger.debug("Could not read image_gen.model: %s", exc)
    return None


def _read_configured_image_provider():
    """Return the value of ``image_gen.provider`` from config.yaml, or None.

    We only consult the plugin registry when this is explicitly set — an
    unset value keeps users on the legacy in-tree FAL path even when other
    providers happen to be registered (e.g. a user has OPENAI_API_KEY set
    for other features but never asked for OpenAI image gen).
    """
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        if isinstance(section, dict):
            value = section.get("provider")
            if isinstance(value, str) and value.strip():
                return value.strip()
    except Exception as exc:
        logger.debug("Could not read image_gen.provider: %s", exc)
    return None


def _dispatch_to_plugin_provider(prompt: str, aspect_ratio: str):
    """Route the call to a plugin-registered provider when one is selected.

    Returns a JSON string on dispatch, or ``None`` to fall through to the
    built-in FAL path.

    Dispatch only fires when ``image_gen.provider`` is explicitly set AND
    it does not point to ``fal`` (FAL still lives in-tree in this PR;
    a later PR ports it into ``plugins/image_gen/fal/``). Any other value
    that matches a registered plugin provider wins.
    """
    configured = _read_configured_image_provider()
    if not configured or configured == "fal":
        return None

    # Also read configured model so we can pass it to the plugin
    configured_model = _read_configured_image_model()

    try:
        # Import locally so plugin discovery isn't triggered just by
        # importing this module (tests rely on that).
        from agent.image_gen_registry import get_provider
        from hermes_cli.plugins import _ensure_plugins_discovered

        _ensure_plugins_discovered()
        provider = get_provider(configured)
    except Exception as exc:
        logger.debug("image_gen plugin dispatch skipped: %s", exc)
        return None

    if provider is None:
        try:
            # Long-lived sessions may have discovered plugins before a bundled
            # backend was patched in or before config changed. Retry once with
            # a forced refresh before surfacing a missing-provider error.
            _ensure_plugins_discovered(force=True)
            provider = get_provider(configured)
        except Exception as exc:
            logger.debug("image_gen plugin force-refresh skipped: %s", exc)

    if provider is None:
        return json.dumps({
            "success": False,
            "image": None,
            "error": (
                f"image_gen.provider='{configured}' is set but no plugin "
                f"registered that name. Run `hermes plugins list` to see "
                f"available image gen backends."
            ),
            "error_type": "provider_not_registered",
        })

    try:
        kwargs = {"prompt": prompt, "aspect_ratio": aspect_ratio}
        if configured_model:
            kwargs["model"] = configured_model
        result = provider.generate(**kwargs)
    except Exception as exc:
        logger.warning(
            "Image gen provider '%s' raised: %s",
            getattr(provider, "name", "?"), exc,
        )
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


def _handle_image_generate(args, **kw):
    prompt = args.get("prompt", "")
    if not prompt:
        return tool_error("prompt is required for image generation")
    aspect_ratio = args.get("aspect_ratio", DEFAULT_ASPECT_RATIO)

    # Route to a plugin-registered provider if one is active (and it's
    # not the in-tree FAL path).
    dispatched = _dispatch_to_plugin_provider(prompt, aspect_ratio)
    if dispatched is not None:
        return dispatched

    return image_generate_tool(
        prompt=prompt,
        aspect_ratio=aspect_ratio,
    )


def _handle_image_edit(args, **kw):
    prompt = args.get("prompt", "")
    if not prompt:
        return tool_error("prompt is required for image editing")

    image_urls = _normalize_image_edit_urls(
        image_urls=args.get("image_urls"),
        image_url=args.get("image_url"),
        image_paths=args.get("image_paths"),
        image_path=args.get("image_path"),
    )
    if not image_urls:
        return tool_error(
            "image_url, image_urls, image_path, or image_paths is required "
            "for image editing"
        )

    configured_provider = _read_configured_image_provider()
    if configured_provider and configured_provider != "fal":
        return json.dumps({
            "success": False,
            "image": None,
            "error": (
                "image_edit currently supports the in-tree FAL backend. "
                f"image_gen.provider is set to {configured_provider!r}; "
                "switch Image Generation to FAL in `hermes tools` to use it."
            ),
            "error_type": "provider_unsupported",
            "provider": configured_provider,
        })

    return image_edit_tool(
        prompt=prompt,
        image_urls=image_urls,
        aspect_ratio=args.get("aspect_ratio", "auto"),
        output_format=args.get("output_format", "png"),
        num_images=_coerce_positive_int(args.get("num_images")),
        seed=_coerce_nonnegative_int(args.get("seed")),
        guidance_scale=_coerce_float(args.get("guidance_scale")),
        resolution=args.get("resolution"),
        model=args.get("model"),
        limit_generations=_coerce_bool(args.get("limit_generations")),
        enable_web_search=_coerce_bool(args.get("enable_web_search")),
        image_size=args.get("image_size"),
        quality=args.get("quality"),
        mask_url=args.get("mask_url") or args.get("mask_image_url"),
        thinking_level=args.get("thinking_level"),
    )


registry.register(
    name="image_generate",
    toolset="image_gen",
    schema=IMAGE_GENERATE_SCHEMA,
    handler=_handle_image_generate,
    check_fn=check_image_generation_requirements,
    requires_env=[],
    is_async=False,   # sync fal_client API to avoid "Event loop is closed" in gateway
    emoji="🎨",
)

registry.register(
    name="image_edit",
    toolset="image_gen",
    schema=IMAGE_EDIT_SCHEMA,
    handler=_handle_image_edit,
    check_fn=check_fal_image_edit_requirements,
    requires_env=[],
    is_async=False,
    emoji="🖼️",
)
