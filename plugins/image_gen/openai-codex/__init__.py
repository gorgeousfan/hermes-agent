"""OpenAI image generation backend — ChatGPT/Codex OAuth variant.

Identical model catalog and tier semantics to the ``openai`` image-gen plugin
(``gpt-image-2`` at low/medium/high quality), but routes the request through
the Codex Responses API ``image_generation`` tool instead of the
``images.generate`` REST endpoint. This lets users who are already
authenticated with Codex/ChatGPT generate images without configuring a
separate ``OPENAI_API_KEY``.

Selection precedence for the tier (first hit wins):

1. ``OPENAI_IMAGE_MODEL`` env var (escape hatch for scripts / tests)
2. ``image_gen.openai-codex.model`` in ``config.yaml``
3. ``image_gen.model`` in ``config.yaml`` (when it's one of our tier IDs)
4. :data:`DEFAULT_MODEL` — ``gpt-image-2-medium``

Output is saved under ``$HERMES_HOME/cache/images/`` using the requested
``output_format`` (PNG by default).
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    save_b64_image,
    success_response,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model catalog — mirrors the ``openai`` plugin so the picker UX is identical.
# ---------------------------------------------------------------------------

API_MODEL = "gpt-image-2"

_MODELS: Dict[str, Dict[str, Any]] = {
    "gpt-image-2-low": {
        "display": "GPT Image 2 (Low)",
        "speed": "~15s",
        "strengths": "Fast iteration, lowest cost",
        "quality": "low",
    },
    "gpt-image-2-medium": {
        "display": "GPT Image 2 (Medium)",
        "speed": "~40s",
        "strengths": "Balanced — default",
        "quality": "medium",
    },
    "gpt-image-2-high": {
        "display": "GPT Image 2 (High)",
        "speed": "~2min",
        "strengths": "Highest fidelity, strongest prompt adherence",
        "quality": "high",
    },
}

DEFAULT_MODEL = "gpt-image-2-medium"

_SIZES = {
    "landscape": "1536x1024",
    "square": "1024x1024",
    "portrait": "1024x1536",
}

_VALID_QUALITIES = {"low", "medium", "high", "auto"}
_VALID_SIZES = {"auto", *_SIZES.values()}
_VALID_OUTPUT_FORMATS = {"png", "jpeg", "webp"}
_MAX_LOCAL_REFERENCE_BYTES = 25 * 1024 * 1024

# Codex Responses surface used for the request. The chat model itself is only
# the host that calls the ``image_generation`` tool; the actual image work is
# done by ``API_MODEL``.
_CODEX_CHAT_MODEL = "gpt-5.4"
_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_CODEX_INSTRUCTIONS = (
    "You are an assistant that must fulfill image generation requests by "
    "using the image_generation tool when provided."
)


# ---------------------------------------------------------------------------
# Config + auth helpers
# ---------------------------------------------------------------------------


def _load_image_gen_config() -> Dict[str, Any]:
    """Read ``image_gen`` from config.yaml (returns {} on any failure)."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception as exc:
        logger.debug("Could not load image_gen config: %s", exc)
        return {}


def _resolve_model() -> Tuple[str, Dict[str, Any]]:
    """Decide which tier to use and return ``(model_id, meta)``."""
    import os

    env_override = os.environ.get("OPENAI_IMAGE_MODEL")
    if env_override and env_override in _MODELS:
        return env_override, _MODELS[env_override]

    cfg = _load_image_gen_config()
    sub = cfg.get("openai-codex") if isinstance(cfg.get("openai-codex"), dict) else {}
    candidate: Optional[str] = None
    if isinstance(sub, dict):
        value = sub.get("model")
        if isinstance(value, str) and value in _MODELS:
            candidate = value
    if candidate is None:
        top = cfg.get("model")
        if isinstance(top, str) and top in _MODELS:
            candidate = top

    if candidate is not None:
        return candidate, _MODELS[candidate]

    return DEFAULT_MODEL, _MODELS[DEFAULT_MODEL]


def _read_codex_access_token() -> Optional[str]:
    """Return a usable Codex OAuth token, or None.

    Delegates to the canonical reader in ``agent.auxiliary_client`` so token
    expiry, credential pool selection, and JWT decoding stay in one place.
    """
    try:
        from agent.auxiliary_client import _read_codex_access_token as _reader

        token = _reader()
        if isinstance(token, str) and token.strip():
            return token.strip()
        return None
    except Exception as exc:
        logger.debug("Could not resolve Codex access token: %s", exc)
        return None


def _build_codex_client():
    """Return an OpenAI client pointed at the ChatGPT/Codex backend, or None."""
    token = _read_codex_access_token()
    if not token:
        return None
    try:
        import openai
        from agent.auxiliary_client import _codex_cloudflare_headers

        return openai.OpenAI(
            api_key=token,
            base_url=_CODEX_BASE_URL,
            default_headers=_codex_cloudflare_headers(token),
        )
    except Exception as exc:
        logger.debug("Could not build Codex image client: %s", exc)
        return None


def _reference_image_to_input_item(reference: str) -> Dict[str, str]:
    """Convert a URL/data URL/local path into a Responses API input_image item."""
    ref = (reference or "").strip()
    if not ref:
        raise ValueError("reference image entries must be non-empty strings")

    if ref.startswith(("http://", "https://", "data:")):
        return {"type": "input_image", "image_url": ref}

    path = _validate_local_image_path(ref)
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "input_image", "image_url": f"data:{mime};base64,{b64}"}


def _validate_local_image_path(reference: str) -> Path:
    """Resolve a local reference image, rejecting non-image or unsafe paths."""
    path = Path(os.path.expanduser(reference)).resolve(strict=False)
    if not path.exists() or not path.is_file():
        raise ValueError(f"Reference image not found: {reference}")

    mime = mimetypes.guess_type(path.name)[0]
    if not mime or not mime.startswith("image/"):
        raise ValueError(f"Reference file is not an image: {reference}")

    size = path.stat().st_size
    if size > _MAX_LOCAL_REFERENCE_BYTES:
        raise ValueError(f"Reference image is too large ({size} bytes): {reference}")

    with path.open("rb") as fh:
        header = fh.read(16)
    if not _looks_like_image_bytes(header):
        raise ValueError(f"Reference file is not a valid image: {reference}")

    allowed_roots = [Path.cwd(), Path("/tmp"), Path("/var/tmp")]
    try:
        from hermes_constants import get_hermes_home

        allowed_roots.append(get_hermes_home() / "cache" / "images")
    except Exception:
        pass

    for root in allowed_roots:
        try:
            path.relative_to(root.resolve())
            return path
        except ValueError:
            continue

    raise ValueError(
        "Local reference images must be under the current workspace, /tmp, /var/tmp, or HERMES_HOME/cache/images"
    )


def _build_input_content(prompt: str, reference_images: Optional[List[str]] = None) -> List[Dict[str, str]]:
    """Build a Responses API message content array with optional references."""
    content: List[Dict[str, str]] = [{"type": "input_text", "text": prompt}]
    refs = [reference_images] if isinstance(reference_images, str) else (reference_images or [])
    for ref in refs:
        if not isinstance(ref, str):
            raise ValueError("reference image entries must be strings")
        content.append(_reference_image_to_input_item(ref))
    return content


def _normalize_size(value: Any, aspect: str) -> str:
    if isinstance(value, str) and value.strip():
        candidate = value.strip().lower()
        if candidate in _VALID_SIZES:
            return candidate
        raise ValueError(
            "Unsupported image size. Use one of: auto, 1024x1024, 1536x1024, 1024x1536"
        )
    return _SIZES.get(aspect, _SIZES["square"])


def _looks_like_image_bytes(header: bytes) -> bool:
    return (
        header.startswith(b"\x89PNG\r\n\x1a\n")
        or header.startswith(b"\xff\xd8\xff")
        or header.startswith(b"GIF87a")
        or header.startswith(b"GIF89a")
        or (len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP")
    )


def _normalize_quality(value: Any, default: str) -> str:
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in _VALID_QUALITIES:
            return candidate
    return default


def _normalize_output_format(value: Any) -> str:
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in _VALID_OUTPUT_FORMATS:
            return candidate
    return "png"


def _normalize_n(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(4, n))


def _mask_image_to_tool_config(mask_image: str) -> Dict[str, str]:
    """Build the mask config for the Responses image_generation tool."""
    item = _reference_image_to_input_item(mask_image)
    return {"image_url": item["image_url"]}


def _collect_image_b64(
    client: Any,
    *,
    prompt: str,
    size: str,
    quality: str,
    reference_images: Optional[List[str]] = None,
    n: int = 1,
    output_format: str = "png",
    mask_image: Optional[str] = None,
) -> List[str]:
    """Stream a Codex Responses image_generation call and return b64 images."""
    images_b64: List[str] = []
    partial_b64: Optional[str] = None
    tool: Dict[str, Any] = {
        "type": "image_generation",
        "model": API_MODEL,
        "size": size,
        "quality": quality,
        "output_format": output_format,
        "background": "opaque",
        "partial_images": 1,
    }
    if n != 1:
        tool["n"] = n
    if mask_image:
        tool["input_image_mask"] = _mask_image_to_tool_config(mask_image)

    with client.responses.stream(
        model=_CODEX_CHAT_MODEL,
        store=False,
        instructions=_CODEX_INSTRUCTIONS,
        input=[{
            "type": "message",
            "role": "user",
            "content": _build_input_content(prompt, reference_images),
        }],
        tools=[tool],
        tool_choice={
            "type": "allowed_tools",
            "mode": "required",
            "tools": [{"type": "image_generation"}],
        },
    ) as stream:
        for event in stream:
            event_type = getattr(event, "type", "")
            if event_type == "response.output_item.done":
                item = getattr(event, "item", None)
                if getattr(item, "type", None) == "image_generation_call":
                    result = getattr(item, "result", None)
                    if isinstance(result, str) and result:
                        images_b64.append(result)
            elif event_type == "response.image_generation_call.partial_image":
                partial = getattr(event, "partial_image_b64", None)
                if isinstance(partial, str) and partial:
                    partial_b64 = partial
        final = stream.get_final_response()

    # Final-response sweep covers both missing stream events and partial stream
    # coverage. Codex can stream one image_generation_call.done while the final
    # response contains more images for n>1.
    final_images: List[str] = []
    for item in getattr(final, "output", None) or []:
        if getattr(item, "type", None) == "image_generation_call":
            result = getattr(item, "result", None)
            if isinstance(result, str) and result:
                final_images.append(result)
    if final_images:
        if not images_b64:
            images_b64 = final_images
        elif final_images[: len(images_b64)] == images_b64:
            images_b64.extend(final_images[len(images_b64):])
        else:
            images_b64.extend(final_images)

    if not images_b64 and partial_b64:
        images_b64.append(partial_b64)
    return images_b64


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class OpenAICodexImageGenProvider(ImageGenProvider):
    """gpt-image-2 routed through ChatGPT/Codex OAuth instead of an API key."""

    @property
    def name(self) -> str:
        return "openai-codex"

    @property
    def display_name(self) -> str:
        return "OpenAI (Codex auth)"

    def is_available(self) -> bool:
        if not _read_codex_access_token():
            return False
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": model_id,
                "display": meta["display"],
                "speed": meta["speed"],
                "strengths": meta["strengths"],
                "price": "varies",
            }
            for model_id, meta in _MODELS.items()
        ]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "OpenAI (Codex auth)",
            "badge": "free",
            "tag": "gpt-image-2 via ChatGPT/Codex OAuth — no API key required",
            "env_vars": [],
            "post_setup_hint": (
                "Sign in with `hermes auth codex` (or `hermes setup` → Codex) "
                "if you haven't already. No API key needed."
            ),
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)

        if not prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider="openai-codex",
                aspect_ratio=aspect,
            )

        if not _read_codex_access_token():
            return error_response(
                error=(
                    "No Codex/ChatGPT OAuth credentials available. Run "
                    "`hermes auth codex` (or `hermes setup` → Codex) to sign in."
                ),
                error_type="auth_required",
                provider="openai-codex",
                aspect_ratio=aspect,
            )

        try:
            import openai  # noqa: F401
        except ImportError:
            return error_response(
                error="openai Python package not installed (pip install openai)",
                error_type="missing_dependency",
                provider="openai-codex",
                aspect_ratio=aspect,
            )

        tier_id, meta = _resolve_model()
        try:
            size = _normalize_size(kwargs.get("size"), aspect)
            quality = _normalize_quality(kwargs.get("quality"), meta["quality"])
            n = _normalize_n(kwargs.get("n"))
            output_format = _normalize_output_format(kwargs.get("output_format"))
            mask_image = kwargs.get("mask_image") or None
            reference_images = kwargs.get("reference_images") or None
        except ValueError as exc:
            return error_response(
                error=str(exc),
                error_type="invalid_argument",
                provider="openai-codex",
                model=tier_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        client = _build_codex_client()
        if client is None:
            return error_response(
                error="Could not initialize Codex image client",
                error_type="auth_required",
                provider="openai-codex",
                model=tier_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        try:
            b64_images = _collect_image_b64(
                client,
                prompt=prompt,
                size=size,
                quality=quality,
                reference_images=reference_images,
                n=n,
                output_format=output_format,
                mask_image=mask_image,
            )
        except Exception as exc:
            logger.debug("Codex image generation failed", exc_info=True)
            return error_response(
                error=f"OpenAI image generation via Codex auth failed: {exc}",
                error_type="api_error",
                provider="openai-codex",
                model=tier_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        if not b64_images:
            return error_response(
                error="Codex response contained no image_generation_call result",
                error_type="empty_response",
                provider="openai-codex",
                model=tier_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        try:
            saved_paths = [
                save_b64_image(b64, prefix=f"openai_codex_{tier_id}", extension=output_format)
                for b64 in b64_images
            ]
        except Exception as exc:
            return error_response(
                error=f"Could not save image to cache: {exc}",
                error_type="io_error",
                provider="openai-codex",
                model=tier_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        return success_response(
            image=str(saved_paths[0]),
            model=tier_id,
            prompt=prompt,
            aspect_ratio=aspect,
            provider="openai-codex",
            extra={
                "size": size,
                "quality": quality,
                "n": n,
                "output_format": output_format,
                "images": [str(path) for path in saved_paths],
            },
        )


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Plugin entry point — register the Codex-backed image-gen provider."""
    ctx.register_image_gen_provider(OpenAICodexImageGenProvider())
