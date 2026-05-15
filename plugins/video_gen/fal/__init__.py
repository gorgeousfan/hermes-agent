"""FAL.ai video generation backend.

User-facing surface: pick a **model family** (e.g. "Pixverse v6",
"Veo 3.1", "Seedance 2.0", "Kling v3 4K", "LTX 2.3", "Happy Horse").
The plugin auto-routes to the family's text-to-video endpoint when
called without ``image_url``, and to its image-to-video endpoint when
``image_url`` is provided. The agent never sees the routing — it just
calls ``video_generate(prompt=..., image_url=...)``.

Model families (each with t2v + i2v endpoints):

  Cheap tier:
    ltx-2.3       fal-ai/ltx-2.3-22b/text-to-video               /  fal-ai/ltx-2.3-22b/image-to-video
    pixverse-v6   fal-ai/pixverse/v6/text-to-video               /  fal-ai/pixverse/v6/image-to-video

  Premium tier:
    veo3.1        fal-ai/veo3.1                                  /  fal-ai/veo3.1/image-to-video
    seedance-2.0  bytedance/seedance-2.0/text-to-video           /  bytedance/seedance-2.0/image-to-video
    kling-v3-4k   fal-ai/kling-video/v3/4k/text-to-video         /  fal-ai/kling-video/v3/4k/image-to-video
    happy-horse   fal-ai/happy-horse/text-to-video               /  fal-ai/happy-horse/image-to-video

Selection precedence for the active family:
    1. ``model=`` arg from the tool call
    2. ``FAL_VIDEO_MODEL`` env var
    3. ``video_gen.fal.model`` in ``config.yaml``
    4. ``video_gen.model`` in ``config.yaml`` (when it's one of our family IDs)
    5. ``DEFAULT_MODEL``

Authentication via ``FAL_KEY``. Output is an HTTPS URL from FAL's CDN; the
gateway downloads and delivers it.
"""

from __future__ import annotations

import logging
import os
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse
from urllib.request import urlopen

from agent.video_gen_provider import (
    VideoGenProvider,
    error_response,
    success_response,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Family catalog
# ---------------------------------------------------------------------------
#
# Each family declares both endpoints (when available) plus a per-family
# capability sheet derived from FAL's OpenAPI schemas. Capability flags
# drive which keys get added to the request payload — keys a family doesn't
# advertise are dropped before send.
#
# Capabilities:
#   aspect_ratios  : tuple of supported ratios (None = endpoint decides)
#   resolutions    : tuple of supported resolutions (None = endpoint decides)
#   durations      : tuple of supported durations OR (min, max) range
#                    (heuristic: 2-element with gap > 1 is a range)
#   audio          : True if generate_audio is supported
#   negative       : True if negative_prompt is supported

FAL_FAMILIES: Dict[str, Dict[str, Any]] = {
    # ─── Cheap / fast tier ─────────────────────────────────────────────
    "ltx-2.3": {
        "display": "LTX 2.3 (22B)",
        "speed": "~30-60s",
        "price": "cheap",
        "strengths": "22B model with native audio generation. Affordable.",
        "tier": "cheap",
        "text_endpoint": "fal-ai/ltx-2.3-22b/text-to-video",
        "image_endpoint": "fal-ai/ltx-2.3-22b/image-to-video",
        # LTX docs don't expose duration/aspect/resolution enums — leave
        # blank so we don't send unrecognized payload keys.
        "aspect_ratios": None,
        "resolutions": None,
        "durations": None,
        "duration_suffix": "s",
        "audio": True,
        "negative": True,
    },
    "pixverse-v6": {
        "display": "Pixverse v6",
        "speed": "~30-90s",
        "price": "cheap",
        "strengths": "Affordable. Negative prompts. 1-15s durations.",
        "tier": "cheap",
        "text_endpoint": "fal-ai/pixverse/v6/text-to-video",
        "image_endpoint": "fal-ai/pixverse/v6/image-to-video",
        "aspect_ratios": None,
        "resolutions": ("360p", "540p", "720p", "1080p"),
        "durations": (1, 15),
        "duration_suffix": "",
        "audio": True,
        "negative": True,
    },
    # ─── Expensive / premium tier ──────────────────────────────────────
    "veo3.1": {
        "display": "Veo 3.1",
        "speed": "~60-120s",
        "price": "premium",
        "strengths": "Google DeepMind. Cinematic, native audio, strong prompt adherence.",
        "tier": "premium",
        "text_endpoint": "fal-ai/veo3.1",
        "image_endpoint": "fal-ai/veo3.1/image-to-video",
        "aspect_ratios": ("16:9", "9:16"),
        "resolutions": ("720p", "1080p", "4k"),
        "durations": (4, 6, 8),
        "duration_suffix": "s",
        "audio": True,
        "negative": True,
    },
    "grok-imagine-video": {
        "display": "Grok Imagine Video",
        "speed": "~30-120s",
        "price": "premium",
        "strengths": "xAI image-to-video with audio, plus text/reference/video edit/extend routes.",
        "tier": "premium",
        "text_endpoint": "xai/grok-imagine-video/text-to-video",
        "image_endpoint": "xai/grok-imagine-video/image-to-video",
        "reference_endpoint": "xai/grok-imagine-video/reference-to-video",
        "edit_endpoint": "xai/grok-imagine-video/edit-video",
        "extend_endpoint": "xai/grok-imagine-video/extend-video",
        "reference_image_param_key": "reference_image_urls",
        "max_reference_images": 7,
        "aspect_ratios": ("auto", "16:9", "4:3", "3:2", "1:1", "2:3", "3:4", "9:16"),
        "resolutions": ("480p", "720p"),
        "durations": (1, 15),
        "duration_suffix": "",
        "audio": False,  # native audio, no generate_audio toggle in schema
        "native_audio": True,
        "negative": False,
        "edit_resolutions": ("auto", "480p", "720p"),
        "extend_durations": (2, 10),
    },
    "seedance-2.0": {
        "display": "Seedance 2.0",
        "speed": "~60-120s",
        "price": "premium",
        "strengths": "ByteDance. Cinematic, synchronized audio + lip-sync, 4-15s.",
        "tier": "premium",
        "text_endpoint": "bytedance/seedance-2.0/text-to-video",
        "image_endpoint": "bytedance/seedance-2.0/image-to-video",
        "reference_endpoint": "bytedance/seedance-2.0/reference-to-video",
        "reference_image_param_key": "image_urls",
        "max_reference_images": 9,
        "supports_end_image": True,
        # Seedance accepts "auto" too — we omit it from the enum so the
        # agent can't pass it; the endpoint defaults handle the rest.
        "aspect_ratios": ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16"),
        "resolutions": ("480p", "720p", "1080p"),
        "durations": (4, 15),
        "duration_suffix": "",
        "audio": True,
        "negative": False,
    },
    "kling-v3-standard": {
        "display": "Kling v3 Standard",
        "speed": "~60-180s",
        "price": "premium",
        "strengths": "Kling 3.0 standard image-to-video with native audio and start/end frames.",
        "tier": "premium",
        "text_endpoint": "fal-ai/kling-video/v3/standard/text-to-video",
        "image_endpoint": "fal-ai/kling-video/v3/standard/image-to-video",
        "image_param_key": "start_image_url",
        "supports_end_image": True,
        "aspect_ratios": ("16:9", "9:16", "1:1"),
        "resolutions": None,
        "durations": (3, 15),
        "duration_suffix": "",
        "audio": True,
        "negative": True,
    },
    "kling-v3-pro": {
        "display": "Kling v3 Pro",
        "speed": "~90-240s",
        "price": "premium",
        "strengths": "Kling 3.0 pro image-to-video with native audio, multi-shot, start/end frames.",
        "tier": "premium",
        "text_endpoint": "fal-ai/kling-video/v3/pro/text-to-video",
        "image_endpoint": "fal-ai/kling-video/v3/pro/image-to-video",
        "image_param_key": "start_image_url",
        "supports_end_image": True,
        "aspect_ratios": ("16:9", "9:16", "1:1"),
        "resolutions": None,
        "durations": (3, 15),
        "duration_suffix": "",
        "audio": True,
        "negative": True,
    },
    "kling-v3-4k": {
        "display": "Kling v3 4K",
        "speed": "~120-300s",
        "price": "premium",
        "strengths": "4K output, native audio (Chinese/English), 3-15s.",
        "tier": "premium",
        "text_endpoint": "fal-ai/kling-video/v3/4k/text-to-video",
        "image_endpoint": "fal-ai/kling-video/v3/4k/image-to-video",
        # Kling 4K image-to-video uses `start_image_url` instead of
        # `image_url`. Handled in _build_payload via image_param_key.
        "image_param_key": "start_image_url",
        "supports_end_image": True,
        "aspect_ratios": ("16:9", "9:16", "1:1"),
        "resolutions": None,  # 4K is implicit
        "durations": (3, 15),
        "duration_suffix": "",
        "audio": True,
        "negative": True,
    },
    "happy-horse": {
        "display": "Happy Horse 1.0",
        "speed": "~30-90s",
        "price": "premium",
        "strengths": "Alibaba. 1080p synchronized native audio, lip-sync, i2v/reference/video-edit.",
        "tier": "premium",
        "text_endpoint": "alibaba/happy-horse/text-to-video",
        "image_endpoint": "alibaba/happy-horse/image-to-video",
        "reference_endpoint": "alibaba/happy-horse/reference-to-video",
        "edit_endpoint": "alibaba/happy-horse/video-edit",
        "reference_image_param_key": "image_urls",
        "max_reference_images": 9,
        "max_edit_reference_images": 5,
        "aspect_ratios": ("16:9", "9:16", "1:1", "4:3", "3:4"),
        "resolutions": ("720p", "1080p"),
        "durations": (3, 15),
        "duration_suffix": "",
        "audio": False,  # native audio, no generate_audio toggle in schemas
        "native_audio": True,
        "negative": False,
        "edit_resolutions": ("720p", "1080p"),
    },
}

DEFAULT_MODEL = "pixverse-v6"  # cheap, both modalities, sane defaults

FAL_FAMILY_ALIASES = {
    "grok-imagine": "grok-imagine-video",
    "grok-imagine-video": "grok-imagine-video",
    "xai/grok-imagine-video": "grok-imagine-video",
    "kling-3": "kling-v3-pro",
    "kling-v3": "kling-v3-pro",
    "kling-v3-standard": "kling-v3-standard",
    "kling-v3-pro": "kling-v3-pro",
    "kling-3-standard": "kling-v3-standard",
    "kling-3-pro": "kling-v3-pro",
    "seedance-2": "seedance-2.0",
    "seedance-2.0": "seedance-2.0",
    "happyhorse": "happy-horse",
    "happy-horse": "happy-horse",
    "alibaba/happy-horse": "happy-horse",
}


def _is_duration_range(durations: Any) -> bool:
    """Heuristic: a 2-tuple of ints with a gap > 1 is treated as ``(min, max)``."""
    if not isinstance(durations, tuple) or len(durations) != 2:
        return False
    if not all(isinstance(d, int) for d in durations):
        return False
    return durations[1] - durations[0] > 1


def _clamp_duration(family: Dict[str, Any], duration: Optional[int]) -> Optional[int]:
    durations = family.get("durations")
    if not durations:
        return duration
    if duration is None:
        return durations[0]
    if _is_duration_range(durations):
        lo, hi = durations
        return max(lo, min(hi, duration))
    # enum
    if duration in durations:
        return duration
    return min(durations, key=lambda d: abs(d - duration))


def _payload_duration(family: Dict[str, Any], duration: Optional[int]) -> Any:
    clamped = _clamp_duration(family, duration)
    if clamped is None:
        return None
    suffix = family.get("duration_suffix", "")
    if suffix:
        return f"{clamped}{suffix}"
    return clamped


def _duration_seconds(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text.endswith("s"):
            text = text[:-1].strip()
        try:
            return int(float(text))
        except ValueError:
            return 0
    return 0


# ---------------------------------------------------------------------------
# Config / model resolution
# ---------------------------------------------------------------------------


def _load_video_gen_section() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = cfg.get("video_gen") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception as exc:
        logger.debug("Could not load video_gen config: %s", exc)
        return {}


def _resolve_family(explicit: Optional[str]) -> Tuple[str, Dict[str, Any]]:
    """Decide which FAL family to use. Returns ``(family_id, meta)``."""
    candidates: List[Optional[str]] = []
    candidates.append(explicit)
    candidates.append(os.environ.get("FAL_VIDEO_MODEL"))

    cfg = _load_video_gen_section()
    fal_cfg = cfg.get("fal") if isinstance(cfg.get("fal"), dict) else {}
    if isinstance(fal_cfg, dict):
        candidates.append(fal_cfg.get("model"))
    top = cfg.get("model")
    if isinstance(top, str):
        candidates.append(top)

    for c in candidates:
        if not isinstance(c, str) or not c.strip():
            continue
        raw = c.strip()
        fid = FAL_FAMILY_ALIASES.get(raw, FAL_FAMILY_ALIASES.get(raw.lower(), raw))
        if fid in FAL_FAMILIES:
            return fid, FAL_FAMILIES[fid]

    return DEFAULT_MODEL, FAL_FAMILIES[DEFAULT_MODEL]


# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------


def _build_payload(
    family: Dict[str, Any],
    *,
    prompt: str,
    image_url: Optional[str],
    duration: Optional[int],
    aspect_ratio: str,
    resolution: str,
    negative_prompt: Optional[str],
    audio: Optional[bool],
    seed: Optional[int],
    reference_image_urls: Optional[List[str]] = None,
    end_image_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a family-specific payload, dropping keys the family doesn't declare."""
    payload: Dict[str, Any] = {}

    if prompt:
        payload["prompt"] = prompt
    if image_url:
        # Some endpoints (e.g. Kling v3 4K image-to-video) expect
        # `start_image_url` instead of `image_url`. The family entry can
        # declare an override.
        key = family.get("image_param_key") or "image_url"
        payload[key] = image_url
    refs = [u.strip() for u in (reference_image_urls or []) if isinstance(u, str) and u.strip()]
    if refs and family.get("reference_image_param_key"):
        max_refs = int(family.get("max_reference_images") or len(refs))
        payload[family["reference_image_param_key"]] = refs[:max_refs]
    if end_image_url and family.get("supports_end_image"):
        payload["end_image_url"] = end_image_url
    if seed is not None:
        payload["seed"] = seed

    if family.get("aspect_ratios"):
        if aspect_ratio in family["aspect_ratios"]:
            payload["aspect_ratio"] = aspect_ratio
        # otherwise let the endpoint auto-crop / use its default

    if family.get("resolutions"):
        if resolution in family["resolutions"]:
            payload["resolution"] = resolution
        # else: let the endpoint default

    if family.get("durations"):
        payload_duration = _payload_duration(family, duration)
        if payload_duration is not None:
            payload["duration"] = payload_duration

    if family.get("audio") and audio is not None:
        payload["generate_audio"] = bool(audio)

    if family.get("negative") and negative_prompt:
        payload["negative_prompt"] = negative_prompt

    return payload


# ---------------------------------------------------------------------------
# fal_client lazy import (same pattern as image_generation_tool)
# ---------------------------------------------------------------------------

_fal_client: Any = None


def _load_fal_client() -> Any:
    global _fal_client
    if _fal_client is not None:
        return _fal_client
    try:
        from tools.lazy_deps import ensure as _lazy_ensure
        _lazy_ensure("video.fal", prompt=False)
    except ImportError:
        pass
    except Exception as exc:
        raise ImportError(str(exc))
    import fal_client  # type: ignore

    _fal_client = fal_client
    return fal_client


def _is_http_source(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _local_source_path(value: str) -> Path:
    parsed = urlparse(value)
    if parsed.scheme.lower() == "file":
        return Path(unquote(parsed.path)).expanduser()
    return Path(value).expanduser()


def _upload_fal_local_file(source: str, fal_client: Any) -> str:
    """Upload a local file to FAL storage and return the public URL."""
    path = _local_source_path(source)
    if not path.is_file():
        raise ValueError(f"Local file not found: {source}")
    upload_file = getattr(fal_client, "upload_file", None)
    if upload_file is None:
        raise RuntimeError(
            "fal_client.upload_file is unavailable; upgrade fal-client or "
            "pass a public URL."
        )
    return upload_file(path)


def _prepare_fal_file_url(source: Optional[str], fal_client: Any) -> Optional[str]:
    """Preserve HTTP(S) URLs; upload local files so FAL can fetch them."""
    if not source:
        return None
    clean = source.strip()
    if not clean:
        return None
    if _is_http_source(clean):
        return clean
    return _upload_fal_local_file(clean, fal_client)


def _prepare_fal_file_urls(sources: Optional[List[str]], fal_client: Any) -> Optional[List[str]]:
    if not sources:
        return None
    prepared = [_prepare_fal_file_url(source, fal_client) for source in sources]
    return [url for url in prepared if url]


def _dimensions_from_png(raw: bytes) -> Optional[Tuple[int, int]]:
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        width, height = struct.unpack(">II", raw[16:24])
        return int(width), int(height)
    return None


def _dimensions_from_jpeg(raw: bytes) -> Optional[Tuple[int, int]]:
    if not raw.startswith(b"\xff\xd8"):
        return None
    offset = 2
    size = len(raw)
    while offset + 9 < size:
        if raw[offset] != 0xFF:
            offset += 1
            continue
        marker = raw[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > size:
            return None
        segment_len = struct.unpack(">H", raw[offset:offset + 2])[0]
        if segment_len < 2:
            return None
        if marker in {
            0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
        }:
            if offset + 7 > size:
                return None
            height, width = struct.unpack(">HH", raw[offset + 3:offset + 7])
            return int(width), int(height)
        offset += segment_len
    return None


def _dimensions_from_webp(raw: bytes) -> Optional[Tuple[int, int]]:
    if len(raw) < 30 or raw[:4] != b"RIFF" or raw[8:12] != b"WEBP":
        return None
    chunk = raw[12:16]
    if chunk == b"VP8 " and len(raw) >= 30:
        width = struct.unpack("<H", raw[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", raw[28:30])[0] & 0x3FFF
        return int(width), int(height)
    if chunk == b"VP8L" and len(raw) >= 25:
        bits = int.from_bytes(raw[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return int(width), int(height)
    if chunk == b"VP8X" and len(raw) >= 30:
        width = int.from_bytes(raw[24:27], "little") + 1
        height = int.from_bytes(raw[27:30], "little") + 1
        return int(width), int(height)
    return None


def _dimensions_from_image_bytes(raw: bytes) -> Optional[Tuple[int, int]]:
    return (
        _dimensions_from_png(raw)
        or _dimensions_from_jpeg(raw)
        or _dimensions_from_webp(raw)
    )


def _read_image_dimensions(source: str) -> Optional[Tuple[int, int]]:
    try:
        if _is_http_source(source):
            with urlopen(source, timeout=10) as response:  # nosec B310 - user-selected media URL
                raw = response.read(8 * 1024 * 1024)
        else:
            raw = _local_source_path(source).read_bytes()
    except Exception as exc:
        logger.debug("Could not inspect source image dimensions for %s: %s", source, exc)
        return None
    return _dimensions_from_image_bytes(raw)


def _aspect_ratio_from_dimensions(width: int, height: int) -> Optional[str]:
    if width <= 0 or height <= 0:
        return None
    ratio = width / height
    candidates = {
        "16:9": 16 / 9,
        "9:16": 9 / 16,
        "4:3": 4 / 3,
        "3:4": 3 / 4,
        "3:2": 3 / 2,
        "2:3": 2 / 3,
        "1:1": 1.0,
    }
    closest = min(candidates, key=lambda key: abs(candidates[key] - ratio))
    closest_ratio = candidates[closest]
    if abs(ratio - closest_ratio) / closest_ratio <= 0.08:
        return closest
    if height > width:
        return "9:16"
    if width > height:
        return "16:9"
    return "1:1"


def _infer_source_aspect_ratio(source: Optional[str]) -> Optional[str]:
    if not source:
        return None
    dimensions = _read_image_dimensions(source)
    if not dimensions:
        return None
    return _aspect_ratio_from_dimensions(*dimensions)


def _should_preserve_source_aspect(aspect_ratio: str) -> bool:
    return not aspect_ratio or aspect_ratio == "auto"


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class FALVideoGenProvider(VideoGenProvider):
    """FAL.ai multi-family video generation backend.

    Routes between text-to-video and image-to-video endpoints automatically
    based on whether ``image_url`` was provided.
    """

    @property
    def name(self) -> str:
        return "fal"

    @property
    def display_name(self) -> str:
        return "FAL"

    def is_available(self) -> bool:
        if not os.environ.get("FAL_KEY", "").strip():
            return False
        try:
            _load_fal_client()
        except ImportError:
            return False
        return True

    def list_models(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for fid, meta in FAL_FAMILIES.items():
            modalities: List[str] = []
            if meta.get("text_endpoint"):
                modalities.append("text")
            if meta.get("image_endpoint"):
                modalities.append("image")
            if meta.get("reference_endpoint"):
                modalities.append("reference")
            if meta.get("edit_endpoint"):
                modalities.append("edit")
            if meta.get("extend_endpoint"):
                modalities.append("extend")
            out.append({
                "id": fid,
                "display": meta["display"],
                "speed": meta["speed"],
                "strengths": meta["strengths"],
                "price": meta["price"],
                "tier": meta.get("tier", "premium"),
                "modalities": modalities,
            })
        return out

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "FAL",
            "badge": "paid",
            "tag": "Veo 3.1, Seedance 2.0, Kling 3, Grok Imagine, Happy Horse — text/image/reference/video-edit",
            "env_vars": [
                {
                    "key": "FAL_KEY",
                    "prompt": "FAL.ai API key",
                    "url": "https://fal.ai/dashboard/keys",
                },
            ],
        }

    def capabilities(self) -> Dict[str, Any]:
        return {
            "modalities": ["text", "image", "reference", "edit", "extend"],
            "aspect_ratios": ["16:9", "9:16", "1:1"],
            "resolutions": ["360p", "540p", "720p", "1080p", "4k"],
            "max_duration": 15,
            "min_duration": 1,
            "supports_audio": True,
            "supports_negative_prompt": True,
            "max_reference_images": max(
                int(meta.get("max_reference_images") or 0)
                for meta in FAL_FAMILIES.values()
            ),
            "supports_video_edit": True,
            "supports_video_extend": True,
        }

    def _run_video_operation(
        self,
        *,
        operation: str,
        prompt: str,
        video_url: str,
        model: Optional[str] = None,
        duration: Optional[int] = None,
        resolution: str = "720p",
        reference_image_urls: Optional[List[str]] = None,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        if not os.environ.get("FAL_KEY", "").strip():
            return error_response(
                error=(
                    "FAL_KEY not set. Run `hermes tools` → Video Generation "
                    "→ FAL to configure."
                ),
                error_type="auth_required",
                provider="fal",
                prompt=prompt,
            )

        try:
            fal_client = _load_fal_client()
        except ImportError:
            return error_response(
                error="fal_client Python package not installed (pip install fal-client)",
                error_type="missing_dependency",
                provider="fal",
                prompt=prompt,
            )

        prompt = (prompt or "").strip()
        video_url_norm = (video_url or "").strip()
        family_id, family = _resolve_family(model)
        endpoint = family.get(f"{operation}_endpoint")

        if not endpoint:
            return error_response(
                error=f"FAL family {family_id} has no video {operation} endpoint.",
                error_type="operation_unsupported",
                provider="fal",
                model=family_id,
                prompt=prompt,
            )
        if not prompt:
            return error_response(
                error=f"prompt is required for video {operation}.",
                error_type="missing_prompt",
                provider="fal",
                model=family_id,
                prompt=prompt,
            )
        if not video_url_norm:
            return error_response(
                error=f"video_url is required for video {operation}.",
                error_type="missing_video_url",
                provider="fal",
                model=family_id,
                prompt=prompt,
            )

        try:
            video_url_norm = _prepare_fal_file_url(video_url_norm, fal_client) or video_url_norm
            reference_image_urls = _prepare_fal_file_urls(reference_image_urls, fal_client)
        except Exception as exc:
            return error_response(
                error=f"Could not prepare local file for FAL video {operation}: {exc}",
                error_type="local_upload_failed",
                provider="fal",
                model=family_id,
                prompt=prompt,
            )

        payload: Dict[str, Any] = {"prompt": prompt, "video_url": video_url_norm}
        if seed is not None:
            payload["seed"] = seed

        if operation == "edit":
            edit_resolutions = family.get("edit_resolutions")
            if edit_resolutions and resolution in edit_resolutions:
                payload["resolution"] = resolution
            refs = [
                u.strip()
                for u in (reference_image_urls or [])
                if isinstance(u, str) and u.strip()
            ]
            max_refs = int(family.get("max_edit_reference_images") or 0)
            if refs and max_refs:
                payload["reference_image_urls"] = refs[:max_refs]
        elif operation == "extend":
            extend_durations = family.get("extend_durations")
            if extend_durations:
                clamped = _clamp_duration({"durations": extend_durations}, duration)
                if clamped is not None:
                    payload["duration"] = clamped

        try:
            result = fal_client.subscribe(
                endpoint,
                arguments=payload,
                with_logs=False,
            )
        except Exception as exc:
            logger.warning(
                "FAL video %s failed (family=%s, endpoint=%s): %s",
                operation, family_id, endpoint, exc, exc_info=True,
            )
            return error_response(
                error=f"FAL video {operation} failed: {exc}",
                error_type="api_error",
                provider="fal",
                model=family_id,
                prompt=prompt,
            )

        video = (result or {}).get("video") if isinstance(result, dict) else None
        url: Optional[str] = None
        if isinstance(video, dict):
            url = video.get("url")
        elif isinstance(video, str):
            url = video
        if not url:
            return error_response(
                error=f"FAL returned no video URL for video {operation}",
                error_type="empty_response",
                provider="fal",
                model=family_id,
                prompt=prompt,
            )

        extra: Dict[str, Any] = {"endpoint": endpoint}
        if isinstance(video, dict):
            if video.get("file_size"):
                extra["file_size"] = video["file_size"]
            if video.get("content_type"):
                extra["content_type"] = video["content_type"]
        if isinstance(result, dict) and result.get("seed") is not None:
            extra["seed"] = result["seed"]

        duration_value = video.get("duration") if isinstance(video, dict) else payload.get("duration")
        return success_response(
            video=url,
            model=family_id,
            prompt=prompt,
            modality=operation,
            aspect_ratio="",
            duration=_duration_seconds(duration_value),
            provider="fal",
            extra=extra,
        )

    def edit(
        self,
        prompt: str,
        *,
        video_url: str,
        model: Optional[str] = None,
        resolution: str = "720p",
        reference_image_urls: Optional[List[str]] = None,
        seed: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return self._run_video_operation(
            operation="edit",
            prompt=prompt,
            video_url=video_url,
            model=model,
            resolution=resolution,
            reference_image_urls=reference_image_urls,
            seed=seed,
        )

    def extend(
        self,
        prompt: str,
        *,
        video_url: str,
        model: Optional[str] = None,
        duration: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return self._run_video_operation(
            operation="extend",
            prompt=prompt,
            video_url=video_url,
            model=model,
            duration=duration,
        )

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        duration: Optional[int] = None,
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        negative_prompt: Optional[str] = None,
        audio: Optional[bool] = None,
        seed: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if not os.environ.get("FAL_KEY", "").strip():
            return error_response(
                error=(
                    "FAL_KEY not set. Run `hermes tools` → Video Generation "
                    "→ FAL to configure."
                ),
                error_type="auth_required",
                provider="fal",
                prompt=prompt,
            )

        try:
            fal_client = _load_fal_client()
        except ImportError:
            return error_response(
                error="fal_client Python package not installed (pip install fal-client)",
                error_type="missing_dependency",
                provider="fal",
                prompt=prompt,
            )

        prompt = (prompt or "").strip()
        family_id, family = _resolve_family(model)
        end_image_url = (kwargs.get("end_image_url") or "").strip() or None
        refs = [u.strip() for u in (reference_image_urls or []) if isinstance(u, str) and u.strip()]

        # Route: reference images → reference-to-video, image_url →
        # image-to-video, else text-to-video.
        image_url_norm = (image_url or "").strip() or None
        if refs and family.get("reference_endpoint") and not image_url_norm:
            endpoint = family.get("reference_endpoint")
            modality_used = "reference"
        elif image_url_norm:
            endpoint = family.get("image_endpoint")
            modality_used = "image"
            if not endpoint:
                return error_response(
                    error=(
                        f"FAL family {family_id} has no image-to-video "
                        f"endpoint. Pick a family with image-to-video support "
                        f"via `hermes tools` → Video Generation."
                    ),
                    error_type="modality_unsupported",
                    provider="fal", model=family_id, prompt=prompt,
                )
        else:
            endpoint = family.get("text_endpoint")
            modality_used = "text"
            if not endpoint:
                return error_response(
                    error=(
                        f"FAL family {family_id} has no text-to-video "
                        f"endpoint. Pass an image_url to use its "
                        f"image-to-video endpoint, or pick a different family."
                    ),
                    error_type="modality_unsupported",
                    provider="fal", model=family_id, prompt=prompt,
                )

        if not prompt:
            return error_response(
                error="prompt is required.",
                error_type="missing_prompt",
                provider="fal", model=family_id, prompt=prompt,
            )

        aspect_ratio = (aspect_ratio or "").strip()
        if image_url_norm and _should_preserve_source_aspect(aspect_ratio):
            inferred_aspect_ratio = _infer_source_aspect_ratio(image_url_norm)
            family_aspect_ratios = family.get("aspect_ratios") or ()
            if inferred_aspect_ratio and inferred_aspect_ratio in family_aspect_ratios:
                aspect_ratio = inferred_aspect_ratio
            elif "auto" in family_aspect_ratios:
                aspect_ratio = "auto"
        if not aspect_ratio:
            aspect_ratio = "16:9"

        try:
            image_url_norm = _prepare_fal_file_url(image_url_norm, fal_client)
            end_image_url = _prepare_fal_file_url(end_image_url, fal_client)
            refs = _prepare_fal_file_urls(refs, fal_client) or []
        except Exception as exc:
            return error_response(
                error=f"Could not prepare local file for FAL video generation: {exc}",
                error_type="local_upload_failed",
                provider="fal", model=family_id, prompt=prompt,
                aspect_ratio=aspect_ratio,
            )

        payload = _build_payload(
            family,
            prompt=prompt,
            image_url=image_url_norm,
            duration=duration,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            negative_prompt=negative_prompt,
            audio=audio,
            seed=seed,
            reference_image_urls=refs,
            end_image_url=end_image_url,
        )

        try:
            result = fal_client.subscribe(
                endpoint,
                arguments=payload,
                with_logs=False,
            )
        except Exception as exc:
            logger.warning(
                "FAL video gen failed (family=%s, endpoint=%s): %s",
                family_id, endpoint, exc, exc_info=True,
            )
            return error_response(
                error=f"FAL video generation failed: {exc}",
                error_type="api_error",
                provider="fal", model=family_id, prompt=prompt,
                aspect_ratio=aspect_ratio,
            )

        video = (result or {}).get("video") if isinstance(result, dict) else None
        url: Optional[str] = None
        if isinstance(video, dict):
            url = video.get("url")
        elif isinstance(video, str):
            url = video

        if not url:
            return error_response(
                error="FAL returned no video URL in response",
                error_type="empty_response",
                provider="fal", model=family_id, prompt=prompt,
            )

        extra: Dict[str, Any] = {"endpoint": endpoint}
        if isinstance(video, dict):
            if video.get("file_size"):
                extra["file_size"] = video["file_size"]
            if video.get("content_type"):
                extra["content_type"] = video["content_type"]

        return success_response(
            video=url,
            model=family_id,
            prompt=prompt,
            modality=modality_used,
            aspect_ratio=aspect_ratio if "aspect_ratio" in payload else "",
            duration=_duration_seconds(payload.get("duration")),
            provider="fal",
            extra=extra,
        )


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Plugin entry point — wire ``FALVideoGenProvider`` into the registry."""
    ctx.register_video_gen_provider(FALVideoGenProvider())
