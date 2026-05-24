"""MiniMax image generation backend.

Exposes MiniMax's ``image-01`` model as an :class:`ImageGenProvider`.

Requires ``MINIMAX_API_KEY`` in the environment.  Endpoint:
``https://api.minimax.io/v1/image_generation``

Selection precedence (first hit wins):

1. ``MINIMAX_IMAGE_MODEL`` env var
2. ``image_gen.minimax.model`` in ``config.yaml``
3. :data:`DEFAULT_MODEL`
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List
from urllib.parse import urlparse

import requests

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    save_b64_image,
    success_response,
)

logger = logging.getLogger(__name__)

API_ENDPOINT = "https://api.minimax.io/v1/image_generation"
DEFAULT_MODEL = "image-01"

_MODELS: Dict[str, Dict[str, Any]] = {
    "image-01": {
        "display": "Image-01",
        "speed": "~10-20s",
        "strengths": "Photorealistic, 9 aspect ratios, 512-2048px",
        "price": "Token Plan (quota-based)",
    },
}

# MiniMax natively supports these aspect ratios
_ASPECT_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"}

# Allowed hosts for image URL download (SSRF guard)
_ALLOWED_IMAGE_HOSTS = {
    "hailuo-image-algeng-data-us.oss-us-east-1.aliyuncs.com",
    "hailuo-image-algeng-data.oss-cn-hangzhou.aliyuncs.com",
    "cdn.minimax.io",
}


def _load_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        mm = section.get("minimax") if isinstance(section, dict) else None
        return mm if isinstance(mm, dict) else {}
    except Exception as exc:
        logger.debug("Could not load image_gen.minimax config: %s", exc)
        return {}


def _resolve_model() -> str:
    env = os.environ.get("MINIMAX_IMAGE_MODEL", "").strip()
    if env and env in _MODELS:
        return env
    cfg = _load_config()
    candidate = cfg.get("model") if isinstance(cfg.get("model"), str) else None
    if candidate and candidate in _MODELS:
        return candidate
    return DEFAULT_MODEL


class MiniMaxImageProvider(ImageGenProvider):
    """MiniMax ``image-01`` image generation backend."""

    @property
    def name(self) -> str:
        return "minimax"

    @property
    def display_name(self) -> str:
        return "MiniMax"

    def is_available(self) -> bool:
        return bool(os.getenv("MINIMAX_API_KEY", "").strip())

    def list_models(self) -> List[Dict[str, Any]]:
        return [{"id": mid, **meta} for mid, meta in _MODELS.items()]

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "MiniMax",
            "badge": "token-plan",
            "tag": "Image-01 — photorealistic, 9 aspect ratios",
            "env_vars": [
                {
                    "key": "MINIMAX_API_KEY",
                    "prompt": "MiniMax API key",
                    "url": "https://platform.minimax.io/user-center/basic-information/interface-key",
                }
            ],
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        api_key = os.getenv("MINIMAX_API_KEY", "").strip()
        if not api_key:
            return error_response(
                error="MINIMAX_API_KEY not set. Get one at https://platform.minimax.io/",
                provider="minimax",
                model=DEFAULT_MODEL,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
            )

        model = _resolve_model()
        # Fall back to 1:1 for any ratio not natively supported
        mm_ratio = aspect_ratio if aspect_ratio in _ASPECT_RATIOS else "1:1"

        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": mm_ratio,
            "n": 1,
            "response_format": "url",
        }

        try:
            resp = requests.post(
                API_ENDPOINT,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            return error_response(
                error=f"MiniMax API request failed: {exc}",
                provider="minimax",
                model=model,
                prompt=prompt,
                aspect_ratio=mm_ratio,
            )
        except ValueError as exc:
            return error_response(
                error=f"MiniMax returned non-JSON response: {exc}",
                provider="minimax",
                model=model,
                prompt=prompt,
                aspect_ratio=mm_ratio,
            )

        base_resp = data.get("base_resp", {})
        if base_resp.get("status_code", -1) != 0:
            return error_response(
                error=f"MiniMax error {base_resp.get('status_code')}: {base_resp.get('status_msg')}",
                provider="minimax",
                model=model,
                prompt=prompt,
                aspect_ratio=mm_ratio,
            )

        image_urls = (data.get("data") or {}).get("image_urls", [])
        if not image_urls:
            return error_response(
                error="MiniMax returned no image URLs",
                provider="minimax",
                model=model,
                prompt=prompt,
                aspect_ratio=mm_ratio,
            )

        image_url = image_urls[0]

        # Validate URL before fetching: must be HTTPS and from an allowed host
        parsed = urlparse(image_url)
        _host_ok = (
            parsed.scheme == "https"
            and any(
                parsed.netloc == h or parsed.netloc.endswith("." + h)
                for h in _ALLOWED_IMAGE_HOSTS
            )
        )

        image_path = image_url  # fallback: return URL as-is
        if _host_ok:
            try:
                import base64
                img_resp = requests.get(image_url, timeout=60)
                img_resp.raise_for_status()
                b64 = base64.b64encode(img_resp.content).decode()
                saved = save_b64_image(b64, prefix="minimax", extension="png")
                image_path = str(saved)
            except Exception as exc:
                logger.debug("MiniMax image download failed, returning URL: %s", exc)
        else:
            logger.warning(
                "MiniMax returned image URL with unexpected host %r — skipping download",
                parsed.netloc,
            )

        return success_response(
            image=image_path,
            model=model,
            prompt=prompt,
            aspect_ratio=mm_ratio,
            provider="minimax",
        )


def register(ctx) -> None:
    ctx.register_image_gen_provider(MiniMaxImageProvider())
