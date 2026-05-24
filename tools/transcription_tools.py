#!/usr/bin/env python3
"""
Transcription Tools Module

Provides speech-to-text transcription with six providers:

  - **local** (default, free) — faster-whisper running locally, no API key needed.
    Auto-downloads the model (~150 MB for ``base``) on first use.
  - **groq** (free tier) — Groq Whisper API, requires ``GROQ_API_KEY``.
  - **openai** (paid) — OpenAI Whisper API, requires ``VOICE_TOOLS_OPENAI_KEY``.
  - **mistral** — Mistral Voxtral Transcribe API, requires ``MISTRAL_API_KEY``.
  - **xai** — xAI Grok STT API, requires ``XAI_API_KEY``. High accuracy,
    Inverse Text Normalization, diarization, 21 languages.

Used by the messaging gateway to automatically transcribe voice messages
sent by users on Telegram, Discord, WhatsApp, Slack, and Signal.

Supported input formats: mp3, mp4, mpeg, mpga, m4a, wav, webm, ogg, aac

Usage::

    from tools.transcription_tools import transcribe_audio

    result = transcribe_audio("/path/to/audio.ogg")
    if result["success"]:
        print(result["transcript"])
"""

import logging
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin, urlparse

from utils import is_truthy_value
from tools.managed_tool_gateway import resolve_managed_tool_gateway
from tools.tool_backend_helpers import managed_nous_tools_enabled, resolve_openai_audio_api_key

logger = logging.getLogger(__name__)

def get_env_value(name, default=None):
    """Read env values through the live config module.

    Tests may monkeypatch and later restore ``hermes_cli.config.get_env_value``
    before this module is imported. Resolve the helper at call time so STT does
    not keep a stale imported function for the rest of the test process.
    """
    try:
        from hermes_cli.config import get_env_value as _get_env_value
    except ImportError:
        return os.getenv(name, default)
    value = _get_env_value(name)
    return default if value is None else value


def _get_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip())
    except ValueError:
        return default
    return value if value > 0 else default

# ---------------------------------------------------------------------------
# Optional imports — graceful degradation
# ---------------------------------------------------------------------------

import importlib.util as _ilu


def _safe_find_spec(module_name: str) -> bool:
    try:
        return _ilu.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return module_name in globals() or module_name in os.sys.modules


_HAS_FASTER_WHISPER = _safe_find_spec("faster_whisper")
_HAS_OPENAI = _safe_find_spec("openai")
_HAS_MISTRAL = _safe_find_spec("mistralai")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PROVIDER = "local"
DEFAULT_LOCAL_MODEL = "base"
DEFAULT_LOCAL_STT_LANGUAGE = "en"
DEFAULT_STT_MODEL = os.getenv("STT_OPENAI_MODEL", "whisper-1")
DEFAULT_GROQ_STT_MODEL = os.getenv("STT_GROQ_MODEL", "whisper-large-v3-turbo")
DEFAULT_MISTRAL_STT_MODEL = os.getenv("STT_MISTRAL_MODEL", "voxtral-mini-latest")
LOCAL_STT_COMMAND_ENV = "HERMES_LOCAL_STT_COMMAND"
LOCAL_STT_LANGUAGE_ENV = "HERMES_LOCAL_STT_LANGUAGE"
COMMON_LOCAL_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")

GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
OPENAI_BASE_URL = os.getenv("STT_OPENAI_BASE_URL", "https://api.openai.com/v1")
XAI_STT_BASE_URL = os.getenv("XAI_STT_BASE_URL", "https://api.x.ai/v1")

SUPPORTED_FORMATS = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".ogg", ".aac", ".flac"}
LOCAL_NATIVE_AUDIO_FORMATS = {".wav", ".aiff", ".aif"}
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB
REMOTE_AUDIO_MAX_DOWNLOAD_SIZE = _get_int_env(
    "HERMES_STT_REMOTE_AUDIO_MAX_BYTES",
    250 * 1024 * 1024,
)
REMOTE_AUDIO_CHUNK_SIZE = min(
    _get_int_env("HERMES_STT_REMOTE_AUDIO_CHUNK_BYTES", 20 * 1024 * 1024),
    MAX_FILE_SIZE - 1024 * 1024,
)
REMOTE_AUDIO_TIMEOUT = (5, 30)

# Known model sets for auto-correction
OPENAI_MODELS = {"whisper-1", "gpt-4o-mini-transcribe", "gpt-4o-transcribe"}
GROQ_MODELS = {"whisper-large-v3", "whisper-large-v3-turbo", "distil-whisper-large-v3-en"}

# Singleton for the local model — loaded once, reused across calls
_local_model: Optional[object] = None
_local_model_name: Optional[str] = None

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------



def _load_stt_config() -> dict:
    """Load the ``stt`` section from user config, falling back to defaults."""
    try:
        from hermes_cli.config import load_config
        return load_config().get("stt", {})
    except Exception:
        return {}


def is_stt_enabled(stt_config: Optional[dict] = None) -> bool:
    """Return whether STT is enabled in config."""
    if stt_config is None:
        stt_config = _load_stt_config()
    enabled = stt_config.get("enabled", True)
    return is_truthy_value(enabled, default=True)


def _has_openai_audio_backend() -> bool:
    """Return True when OpenAI audio can use config credentials, env credentials, or the managed gateway."""
    try:
        _resolve_openai_audio_client_config()
        return True
    except ValueError:
        return False


def _find_binary(binary_name: str) -> Optional[str]:
    """Find a local binary, checking common Homebrew/local prefixes as well as PATH."""
    for directory in COMMON_LOCAL_BIN_DIRS:
        candidate = Path(directory) / binary_name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which(binary_name)


def _find_ffmpeg_binary() -> Optional[str]:
    return _find_binary("ffmpeg")


def _find_whisper_binary() -> Optional[str]:
    return _find_binary("whisper")


def _get_local_command_template() -> Optional[str]:
    configured = os.getenv(LOCAL_STT_COMMAND_ENV, "").strip()
    if configured:
        return configured

    whisper_binary = _find_whisper_binary()
    if whisper_binary:
        quoted_binary = shlex.quote(whisper_binary)
        return (
            f"{quoted_binary} {{input_path}} --model {{model}} --output_format txt "
            "--output_dir {output_dir} --language {language}"
        )
    return None


def _has_local_command() -> bool:
    return _get_local_command_template() is not None


def _normalize_local_model(model_name: Optional[str]) -> str:
    """Return a valid faster-whisper model size, mapping cloud-only names to the default.

    Cloud providers like OpenAI use names such as ``whisper-1`` which are not
    valid for faster-whisper (which expects ``tiny``, ``base``, ``small``,
    ``medium``, or ``large-v*``).  When such a name is detected we fall back to
    the default local model and emit a warning so the user knows what happened.
    """
    if not model_name or model_name in OPENAI_MODELS or model_name in GROQ_MODELS:
        if model_name and (model_name in OPENAI_MODELS or model_name in GROQ_MODELS):
            logger.warning(
                "STT model '%s' is a cloud-only name and cannot be used with the local "
                "provider. Falling back to '%s'. Set stt.local.model to a valid "
                "faster-whisper size (tiny, base, small, medium, large-v3).",
                model_name,
                DEFAULT_LOCAL_MODEL,
            )
        return DEFAULT_LOCAL_MODEL
    return model_name


def _normalize_local_command_model(model_name: Optional[str]) -> str:
    return _normalize_local_model(model_name)


def _try_lazy_install_stt() -> bool:
    """Attempt to lazy-install faster-whisper and return True on success.

    The module-level ``_HAS_FASTER_WHISPER`` flag is set at import time and
    cached. If the package wasn't installed at startup, calling ``ensure()``
    installs it. This function re-checks dynamically after installation so
    the provider can use it immediately without a process restart.
    """
    try:
        from tools.lazy_deps import ensure
        ensure("stt.faster_whisper")
        # Re-check dynamically after install
        import importlib.util as _iu
        if _iu.find_spec("faster_whisper"):
            return True
    except Exception as exc:
        logger.debug("Lazy install of faster-whisper failed: %s", exc)
    return False


def _get_provider(stt_config: dict) -> str:
    """Determine which STT provider to use.

    When ``stt.provider`` is explicitly set in config, that choice is
    honoured — no silent cloud fallback.  When no provider is configured,
    auto-detect tries: local > groq (free) > openai (paid).
    """
    if not is_stt_enabled(stt_config):
        return "none"

    explicit = "provider" in stt_config
    provider = stt_config.get("provider", DEFAULT_PROVIDER)

    # --- Explicit provider: respect the user's choice ----------------------

    if explicit:
        if provider == "local":
            if _HAS_FASTER_WHISPER:
                return "local"
            if _has_local_command():
                return "local_command"
            # Try lazy-install before giving up
            if _try_lazy_install_stt():
                return "local"
            logger.warning(
                "STT provider 'local' configured but unavailable "
                "(install faster-whisper or set HERMES_LOCAL_STT_COMMAND)"
            )
            return "none"

        if provider == "local_command":
            if _has_local_command():
                return "local_command"
            if _HAS_FASTER_WHISPER:
                logger.info("Local STT command unavailable, using local faster-whisper")
                return "local"
            logger.warning(
                "STT provider 'local_command' configured but unavailable"
            )
            return "none"

        if provider == "groq":
            if _HAS_OPENAI and get_env_value("GROQ_API_KEY"):
                return "groq"
            logger.warning(
                "STT provider 'groq' configured but GROQ_API_KEY not set"
            )
            return "none"

        if provider == "openai":
            if _HAS_OPENAI and _has_openai_audio_backend():
                return "openai"
            logger.warning(
                "STT provider 'openai' configured but no API key available"
            )
            return "none"

        if provider == "mistral":
            # `mistralai` PyPI package was quarantined on 2026-05-12 after a
            # malicious 2.4.6 release. Refuse to use this provider until it's
            # available again so we surface a clear message instead of an
            # opaque ImportError mid-call.
            logger.warning(
                "STT provider 'mistral' (Voxtral Transcribe) is temporarily "
                "disabled — `mistralai` PyPI package is quarantined "
                "(malicious 2.4.6 release on 2026-05-12). Falling back to "
                "another provider. Set stt.provider in config.yaml to 'local' "
                "or 'openai' to silence this warning."
            )
            return "none"

        if provider == "xai":
            from tools.xai_http import resolve_xai_http_credentials

            if resolve_xai_http_credentials().get("api_key"):
                return "xai"
            logger.warning(
                "STT provider 'xai' configured but no xAI credentials are available"
            )
            return "none"

        return provider  # Unknown — let it fail downstream

    # --- Auto-detect (no explicit provider): local > groq > openai > xai ---
    # mistral is intentionally skipped while `mistralai` is quarantined on
    # PyPI (malicious 2.4.6 release on 2026-05-12).

    if _HAS_FASTER_WHISPER:
        return "local"
    if _has_local_command():
        return "local_command"
    # Try lazy-install before falling through to cloud providers
    if _try_lazy_install_stt():
        return "local"
    if _HAS_OPENAI and get_env_value("GROQ_API_KEY"):
        logger.info("No local STT available, using Groq Whisper API")
        return "groq"
    if _HAS_OPENAI and _has_openai_audio_backend():
        logger.info("No local STT available, using OpenAI Whisper API")
        return "openai"
    try:
        from tools.xai_http import resolve_xai_http_credentials

        if resolve_xai_http_credentials().get("api_key"):
            logger.info("No local STT available, using xAI Grok STT API")
            return "xai"
    except Exception:
        pass
    return "none"

# ---------------------------------------------------------------------------
# Shared validation
# ---------------------------------------------------------------------------


def _validate_audio_file(file_path: str) -> Optional[Dict[str, Any]]:
    """Validate the audio file.  Returns an error dict or None if OK."""
    audio_path = Path(file_path)

    if not audio_path.exists():
        return {"success": False, "transcript": "", "error": f"Audio file not found: {file_path}"}
    if not audio_path.is_file():
        return {"success": False, "transcript": "", "error": f"Path is not a file: {file_path}"}
    if audio_path.suffix.lower() not in SUPPORTED_FORMATS:
        return {
            "success": False,
            "transcript": "",
            "error": f"Unsupported format: {audio_path.suffix}. Supported: {', '.join(sorted(SUPPORTED_FORMATS))}",
        }
    try:
        file_size = audio_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            return {
                "success": False,
                "transcript": "",
                "error": f"File too large: {file_size / (1024*1024):.1f}MB (max {MAX_FILE_SIZE / (1024*1024):.0f}MB)",
            }
    except OSError as e:
        return {"success": False, "transcript": "", "error": f"Failed to access file: {e}"}

    return None


def _is_remote_audio_url(value: str) -> bool:
    parsed = urlparse(str(value).strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _parse_content_length(headers: Any) -> Optional[int]:
    raw_length = headers.get("Content-Length") if headers else None
    if raw_length:
        try:
            return int(raw_length)
        except (TypeError, ValueError):
            pass

    content_range = headers.get("Content-Range") if headers else None
    if content_range and "/" in content_range:
        _, _, total = content_range.rpartition("/")
        if total and total != "*":
            try:
                return int(total)
            except ValueError:
                return None
    return None


def _looks_like_audio_content_type(content_type: str) -> bool:
    normalized = content_type.split(";", 1)[0].strip().lower()
    return (
        normalized.startswith("audio/")
        or normalized in {"video/mp4", "video/mpeg", "video/webm", "application/ogg", "application/octet-stream"}
    )


def _guess_remote_audio_suffix(url: str, content_type: str = "") -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in SUPPORTED_FORMATS:
        return suffix

    normalized_type = content_type.split(";", 1)[0].strip().lower()
    content_type_suffixes = {
        "audio/aac": ".aac",
        "audio/flac": ".flac",
        "audio/m4a": ".m4a",
        "audio/mp4": ".m4a",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/ogg": ".ogg",
        "audio/wav": ".wav",
        "audio/wave": ".wav",
        "audio/webm": ".webm",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "application/ogg": ".ogg",
    }
    return content_type_suffixes.get(normalized_type, ".mp3")


def _validate_remote_audio_metadata(info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not info.get("success"):
        return {
            "success": False,
            "transcript": "",
            "error": info.get("error") or "Failed to resolve remote audio URL",
        }

    final_url = str(info.get("url") or "")
    if not _is_remote_audio_url(final_url):
        return {
            "success": False,
            "transcript": "",
            "error": "Remote audio redirects must resolve to an http(s) URL",
        }

    suffix = Path(urlparse(final_url).path).suffix.lower()
    content_type = str(info.get("content_type") or "")
    if suffix and suffix not in SUPPORTED_FORMATS and not _looks_like_audio_content_type(content_type):
        return {
            "success": False,
            "transcript": "",
            "error": f"Unsupported remote audio format: {suffix}. Supported: {', '.join(sorted(SUPPORTED_FORMATS))}",
        }
    if content_type and not suffix and not _looks_like_audio_content_type(content_type):
        return {
            "success": False,
            "transcript": "",
            "error": f"Remote URL does not look like audio (Content-Type: {content_type})",
        }
    return None


def _probe_remote_audio_url(audio_url: str) -> Dict[str, Any]:
    """Resolve redirects and gather cheap metadata for an http(s) audio URL."""
    if not _is_remote_audio_url(audio_url):
        return {
            "success": False,
            "url": audio_url,
            "error": "Remote audio URL must use http or https",
        }

    try:
        import requests
    except ImportError:
        return {
            "success": False,
            "url": audio_url,
            "error": "requests package not installed",
        }

    session = requests.Session()
    last_error = ""
    try:
        try:
            response = session.head(
                audio_url,
                allow_redirects=True,
                timeout=REMOTE_AUDIO_TIMEOUT,
            )
            if 200 <= response.status_code < 400:
                return {
                    "success": True,
                    "url": response.url,
                    "content_type": response.headers.get("Content-Type", ""),
                    "content_length": _parse_content_length(response.headers),
                }
            last_error = f"HEAD returned HTTP {response.status_code}"
        except requests.RequestException as exc:
            last_error = str(exc)

        response = session.get(
            audio_url,
            allow_redirects=True,
            headers={"Range": "bytes=0-0"},
            stream=True,
            timeout=REMOTE_AUDIO_TIMEOUT,
        )
        try:
            if response.status_code not in {200, 206}:
                detail = response.text[:200] if response.text else last_error
                return {
                    "success": False,
                    "url": audio_url,
                    "error": f"Remote audio probe failed (HTTP {response.status_code}): {detail}",
                }

            return {
                "success": True,
                "url": response.url,
                "content_type": response.headers.get("Content-Type", ""),
                "content_length": _parse_content_length(response.headers),
            }
        finally:
            response.close()
    except requests.RequestException as exc:
        detail = f"{last_error}; {exc}" if last_error else str(exc)
        return {
            "success": False,
            "url": audio_url,
            "error": f"Remote audio probe failed: {detail}",
        }
    finally:
        session.close()


def _download_remote_audio(
    audio_url: str,
    work_dir: str,
    *,
    size_hint: Optional[int] = None,
    content_type: str = "",
) -> tuple[Optional[str], Optional[str]]:
    if size_hint is not None and size_hint > REMOTE_AUDIO_MAX_DOWNLOAD_SIZE:
        return (
            None,
            (
                f"Remote audio is too large to download safely: "
                f"{size_hint / (1024 * 1024):.1f}MB "
                f"(max {REMOTE_AUDIO_MAX_DOWNLOAD_SIZE / (1024 * 1024):.0f}MB)"
            ),
        )

    try:
        import requests
    except ImportError:
        return None, "requests package not installed"

    session = requests.Session()
    response = None
    output_path: Optional[Path] = None
    try:
        response = session.get(
            audio_url,
            allow_redirects=True,
            stream=True,
            timeout=REMOTE_AUDIO_TIMEOUT,
        )
        if response.status_code < 200 or response.status_code >= 300:
            detail = response.text[:200] if response.text else ""
            return None, f"Remote audio download failed (HTTP {response.status_code}): {detail}"

        final_url = response.url
        if not _is_remote_audio_url(final_url):
            return None, "Remote audio redirects must resolve to an http(s) URL"

        response_length = _parse_content_length(response.headers)
        if response_length is not None and response_length > REMOTE_AUDIO_MAX_DOWNLOAD_SIZE:
            return (
                None,
                (
                    f"Remote audio is too large to download safely: "
                    f"{response_length / (1024 * 1024):.1f}MB "
                    f"(max {REMOTE_AUDIO_MAX_DOWNLOAD_SIZE / (1024 * 1024):.0f}MB)"
                ),
            )

        response_type = response.headers.get("Content-Type", "") or content_type
        suffix = _guess_remote_audio_suffix(final_url, response_type)
        output_path = Path(work_dir) / f"remote-audio{suffix}"
        total = 0
        with open(output_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > REMOTE_AUDIO_MAX_DOWNLOAD_SIZE:
                    return (
                        None,
                        (
                            f"Remote audio exceeded safe download limit "
                            f"({REMOTE_AUDIO_MAX_DOWNLOAD_SIZE / (1024 * 1024):.0f}MB)"
                        ),
                    )
                handle.write(chunk)

        return str(output_path), None
    except requests.RequestException as exc:
        return None, f"Remote audio download failed: {exc}"
    except OSError as exc:
        return None, f"Failed to write remote audio download: {exc}"
    finally:
        if response is not None:
            response.close()
        session.close()
        if output_path is not None and output_path.exists() and output_path.stat().st_size == 0:
            output_path.unlink(missing_ok=True)


def _find_ffprobe_binary() -> Optional[str]:
    return _find_binary("ffprobe")


def _probe_audio_duration(file_path: str) -> tuple[Optional[float], Optional[str]]:
    ffprobe = _find_ffprobe_binary()
    if not ffprobe:
        return None, "Chunk fallback requires ffprobe, but ffprobe was not found"

    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        file_path,
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        duration = float(result.stdout.strip())
        if duration <= 0:
            return None, "Could not determine a positive audio duration for chunk fallback"
        return duration, None
    except (subprocess.CalledProcessError, ValueError) as exc:
        return None, f"Failed to inspect audio duration for chunk fallback: {exc}"


def _split_audio_file_for_stt(
    file_path: str,
    work_dir: str,
    *,
    max_chunk_bytes: int = REMOTE_AUDIO_CHUNK_SIZE,
) -> tuple[List[str], Optional[str]]:
    audio_path = Path(file_path)
    try:
        file_size = audio_path.stat().st_size
    except OSError as exc:
        return [], f"Failed to inspect audio file for chunk fallback: {exc}"

    if file_size <= MAX_FILE_SIZE:
        return [file_path], None

    ffmpeg = _find_ffmpeg_binary()
    if not ffmpeg:
        return [], "Chunk fallback requires ffmpeg, but ffmpeg was not found"

    duration, duration_error = _probe_audio_duration(file_path)
    if duration_error:
        return [], duration_error

    target_bytes = min(max_chunk_bytes, MAX_FILE_SIZE - 1024 * 1024)
    segment_seconds = max(10, int(duration * target_bytes / file_size * 0.85))
    suffix = audio_path.suffix.lower() if audio_path.suffix.lower() in SUPPORTED_FORMATS else ".mp3"

    for attempt in range(4):
        chunks_dir = Path(work_dir) / f"chunks-{attempt}"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        output_pattern = str(chunks_dir / f"chunk-%03d{suffix}")
        command = [
            ffmpeg,
            "-y",
            "-i",
            file_path,
            "-map",
            "0:a:0",
            "-vn",
            "-f",
            "segment",
            "-segment_time",
            str(segment_seconds),
            "-reset_timestamps",
            "1",
            "-c",
            "copy",
            output_pattern,
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            details = exc.stderr.strip() or exc.stdout.strip() or str(exc)
            return [], f"Failed to split audio for chunk fallback: {details}"

        chunks = sorted(str(path) for path in chunks_dir.glob(f"*{suffix}"))
        if not chunks:
            return [], "Chunk fallback did not produce any audio chunks"

        largest_chunk = max(Path(path).stat().st_size for path in chunks)
        if largest_chunk <= MAX_FILE_SIZE:
            return chunks, None

        segment_seconds = max(5, int(segment_seconds * target_bytes / largest_chunk * 0.8))

    return [], "Chunk fallback could not produce chunks below the provider size limit"


def _looks_like_provider_size_limit_error(message: str) -> bool:
    lowered = str(message).lower()
    return any(
        marker in lowered
        for marker in (
            "too large",
            "size limit",
            "maximum file",
            "maximum content",
            "payload too large",
            "413",
        )
    )


def _transcribe_audio_file_chunks(
    file_path: str,
    provider: str,
    stt_config: dict,
    model: Optional[str],
) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="hermes-stt-chunks-") as chunk_dir:
        chunks, chunk_error = _split_audio_file_for_stt(file_path, chunk_dir)
        if chunk_error:
            return {"success": False, "transcript": "", "error": chunk_error}

        transcripts: List[str] = []
        for index, chunk_path in enumerate(chunks, start=1):
            result = _transcribe_audio_file(chunk_path, provider, stt_config, model)
            if not result.get("success"):
                return {
                    "success": False,
                    "transcript": "\n".join(transcripts).strip(),
                    "provider": provider,
                    "error": (
                        f"Chunk {index}/{len(chunks)} transcription failed: "
                        f"{result.get('error', 'unknown error')}"
                    ),
                }
            transcript = str(result.get("transcript") or "").strip()
            if transcript:
                transcripts.append(transcript)

        return {
            "success": True,
            "transcript": "\n".join(transcripts).strip(),
            "provider": provider,
            "chunks": len(chunks),
        }


def _no_stt_provider_error() -> Dict[str, Any]:
    return {
        "success": False,
        "transcript": "",
        "error": (
            "No STT provider available. Install faster-whisper for free local "
            f"transcription, configure {LOCAL_STT_COMMAND_ENV} or install a local whisper CLI, "
            "set GROQ_API_KEY for free Groq Whisper, set MISTRAL_API_KEY for Mistral "
            "Voxtral Transcribe, configure xAI OAuth or set XAI_API_KEY for xAI Grok STT, or set VOICE_TOOLS_OPENAI_KEY "
            "or OPENAI_API_KEY for the OpenAI Whisper API."
        ),
    }


def _transcribe_remote_audio_url(audio_url: str, model: Optional[str] = None) -> Dict[str, Any]:
    """Resolve and transcribe a remote http(s) audio URL."""
    stt_config = _load_stt_config()
    if not is_stt_enabled(stt_config):
        return {
            "success": False,
            "transcript": "",
            "error": "STT is disabled in config.yaml (stt.enabled: false).",
        }

    provider = _get_provider(stt_config)
    if provider == "none":
        return _no_stt_provider_error()

    probe = _probe_remote_audio_url(audio_url)
    metadata_error = _validate_remote_audio_metadata(probe)
    if metadata_error:
        return metadata_error

    final_url = str(probe["url"])
    content_type = str(probe.get("content_type") or "")
    content_length = probe.get("content_length")

    if provider == "groq":
        model_name = model or DEFAULT_GROQ_STT_MODEL
        if content_length is None or content_length <= MAX_FILE_SIZE:
            direct_result = _transcribe_groq_remote_url(final_url, model_name)
            if direct_result.get("success"):
                direct_result["source_url"] = final_url
                return direct_result
            if not _looks_like_provider_size_limit_error(str(direct_result.get("error") or "")):
                return direct_result

    with tempfile.TemporaryDirectory(prefix="hermes-stt-remote-") as work_dir:
        downloaded_path, download_error = _download_remote_audio(
            final_url,
            work_dir,
            size_hint=content_length if isinstance(content_length, int) else None,
            content_type=content_type,
        )
        if download_error:
            return {"success": False, "transcript": "", "error": download_error}
        if downloaded_path is None:
            return {"success": False, "transcript": "", "error": "Remote audio download failed"}

        try:
            downloaded_size = Path(downloaded_path).stat().st_size
        except OSError as exc:
            return {"success": False, "transcript": "", "error": f"Failed to inspect downloaded audio: {exc}"}

        if downloaded_size > MAX_FILE_SIZE:
            return _transcribe_audio_file_chunks(downloaded_path, provider, stt_config, model)

        validation_error = _validate_audio_file(downloaded_path)
        if validation_error:
            return validation_error
        result = _transcribe_audio_file(downloaded_path, provider, stt_config, model)
        if result.get("success"):
            result["source_url"] = final_url
        return result

# ---------------------------------------------------------------------------
# Provider: local (faster-whisper)
# ---------------------------------------------------------------------------


# Substrings that identify a missing/unloadable CUDA runtime library.  When
# ctranslate2 (the backend for faster-whisper) cannot dlopen one of these, the
# "auto" device picker has already committed to CUDA and the model can no
# longer be used — we fall back to CPU and reload.
#
# Deliberately narrow: we match on library-name tokens and dlopen phrasing so
# we DO NOT accidentally catch legitimate runtime failures like "CUDA out of
# memory" — those should surface to the user, not silently fall back to CPU
# (a 32GB audio clip on CPU at int8 isn't useful either).
_CUDA_LIB_ERROR_MARKERS = (
    "libcublas",
    "libcudnn",
    "libcudart",
    "cannot be loaded",
    "cannot open shared object",
    "no kernel image is available",
    "no CUDA-capable device",
    "CUDA driver version is insufficient",
)


def _looks_like_cuda_lib_error(exc: BaseException) -> bool:
    """Heuristic: is this exception a missing/broken CUDA runtime library?

    ctranslate2 raises plain RuntimeError with messages like
    ``Library libcublas.so.12 is not found or cannot be loaded``.  We want to
    catch missing/unloadable shared libs and driver-mismatch errors, NOT
    legitimate runtime failures ("CUDA out of memory", model bugs, etc.).
    """
    msg = str(exc)
    return any(marker in msg for marker in _CUDA_LIB_ERROR_MARKERS)


def _load_local_whisper_model(model_name: str):
    """Load faster-whisper with graceful CUDA → CPU fallback.

    faster-whisper's ``device="auto"`` picks CUDA when the ctranslate2 wheel
    ships CUDA shared libs, even on hosts where the NVIDIA runtime
    (``libcublas.so.12`` / ``libcudnn*``) isn't installed — common on WSL2
    without CUDA-on-WSL, headless servers, and CPU-only developer machines.
    On those hosts the load itself sometimes succeeds and the dlopen failure
    only surfaces at first ``transcribe()`` call.

    We try ``auto`` first (fast CUDA path when it works), and on any CUDA
    library load failure fall back to CPU + int8.
    """
    from faster_whisper import WhisperModel
    try:
        return WhisperModel(model_name, device="auto", compute_type="auto")
    except Exception as exc:
        if not _looks_like_cuda_lib_error(exc):
            raise
        logger.warning(
            "faster-whisper CUDA load failed (%s) — falling back to CPU (int8). "
            "Install the NVIDIA CUDA runtime (libcublas/libcudnn) to use GPU.",
            exc,
        )
        return WhisperModel(model_name, device="cpu", compute_type="int8")


def _transcribe_local(file_path: str, model_name: str) -> Dict[str, Any]:
    """Transcribe using faster-whisper (local, free)."""
    global _local_model, _local_model_name

    if not _HAS_FASTER_WHISPER:
        if not _try_lazy_install_stt():
            return {"success": False, "transcript": "", "error": "faster-whisper not installed"}

    try:
        # Lazy-load the model (downloads on first use, ~150 MB for 'base')
        if _local_model is None or _local_model_name != model_name:
            logger.info("Loading faster-whisper model '%s' (first load downloads the model)...", model_name)
            _local_model = _load_local_whisper_model(model_name)
            _local_model_name = model_name

        # Language: config.yaml (stt.local.language) > env var > auto-detect.
        _forced_lang = (
            _load_stt_config().get("local", {}).get("language")
            or os.getenv(LOCAL_STT_LANGUAGE_ENV)
            or None
        )
        transcribe_kwargs = {"beam_size": 5}
        if _forced_lang:
            transcribe_kwargs["language"] = _forced_lang

        try:
            segments, info = _local_model.transcribe(file_path, **transcribe_kwargs)
            transcript = " ".join(segment.text.strip() for segment in segments)
        except Exception as exc:
            # CUDA runtime libs sometimes only fail at dlopen-on-first-use,
            # AFTER the model loaded successfully.  Evict the broken cached
            # model, reload on CPU, retry once.  Without this the module-
            # global `_local_model` is poisoned and every subsequent voice
            # message on this process fails identically until restart.
            if not _looks_like_cuda_lib_error(exc):
                raise
            logger.warning(
                "faster-whisper CUDA runtime failed mid-transcribe (%s) — "
                "evicting cached model and retrying on CPU (int8).",
                exc,
            )
            _local_model = None
            _local_model_name = None
            from faster_whisper import WhisperModel
            _local_model = WhisperModel(model_name, device="cpu", compute_type="int8")
            _local_model_name = model_name
            segments, info = _local_model.transcribe(file_path, **transcribe_kwargs)
            transcript = " ".join(segment.text.strip() for segment in segments)

        logger.info(
            "Transcribed %s via local whisper (%s, lang=%s, %.1fs audio)",
            Path(file_path).name, model_name, info.language, info.duration,
        )

        return {"success": True, "transcript": transcript, "provider": "local"}

    except Exception as e:
        logger.error("Local transcription failed: %s", e, exc_info=True)
        return {"success": False, "transcript": "", "error": f"Local transcription failed: {e}"}


def _prepare_local_audio(file_path: str, work_dir: str) -> tuple[Optional[str], Optional[str]]:
    """Normalize audio for local CLI STT when needed."""
    audio_path = Path(file_path)
    if audio_path.suffix.lower() in LOCAL_NATIVE_AUDIO_FORMATS:
        return file_path, None

    ffmpeg = _find_ffmpeg_binary()
    if not ffmpeg:
        return None, "Local STT fallback requires ffmpeg for non-WAV inputs, but ffmpeg was not found"

    converted_path = os.path.join(work_dir, f"{audio_path.stem}.wav")
    command = [ffmpeg, "-y", "-i", file_path, converted_path]

    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        return converted_path, None
    except subprocess.CalledProcessError as e:
        details = e.stderr.strip() or e.stdout.strip() or str(e)
        logger.error("ffmpeg conversion failed for %s: %s", file_path, details)
        return None, f"Failed to convert audio for local STT: {details}"


def _transcribe_local_command(file_path: str, model_name: str) -> Dict[str, Any]:
    """Run the configured local STT command template and read back a .txt transcript."""
    command_template = _get_local_command_template()
    if not command_template:
        return {
            "success": False,
            "transcript": "",
            "error": (
                f"{LOCAL_STT_COMMAND_ENV} not configured and no local whisper binary was found"
            ),
        }

    # Language: config.yaml (stt.local.language) > env var > "en" default.
    language = (
        _load_stt_config().get("local", {}).get("language")
        or os.getenv(LOCAL_STT_LANGUAGE_ENV)
        or DEFAULT_LOCAL_STT_LANGUAGE
    )
    normalized_model = _normalize_local_command_model(model_name)

    try:
        with tempfile.TemporaryDirectory(prefix="hermes-local-stt-") as output_dir:
            prepared_input, prep_error = _prepare_local_audio(file_path, output_dir)
            if prep_error:
                return {"success": False, "transcript": "", "error": prep_error}

            command = command_template.format(
                input_path=shlex.quote(prepared_input),
                output_dir=shlex.quote(output_dir),
                language=shlex.quote(language),
                model=shlex.quote(normalized_model),
            )
            # User-provided templates (env var) may contain shell syntax; auto-detected commands are safe for list mode.
            use_shell = bool(os.getenv(LOCAL_STT_COMMAND_ENV, "").strip())
            if use_shell:
                subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
            else:
                subprocess.run(shlex.split(command), check=True, capture_output=True, text=True)
            

            txt_files = sorted(Path(output_dir).glob("*.txt"))
            if not txt_files:
                return {
                    "success": False,
                    "transcript": "",
                    "error": "Local STT command completed but did not produce a .txt transcript",
                }

            transcript_text = txt_files[0].read_text(encoding="utf-8").strip()
            logger.info(
                "Transcribed %s via local STT command (%s, %d chars)",
                Path(file_path).name,
                normalized_model,
                len(transcript_text),
            )
            return {"success": True, "transcript": transcript_text, "provider": "local_command"}

    except KeyError as e:
        return {
            "success": False,
            "transcript": "",
            "error": f"Invalid {LOCAL_STT_COMMAND_ENV} template, missing placeholder: {e}",
        }
    except subprocess.CalledProcessError as e:
        details = e.stderr.strip() or e.stdout.strip() or str(e)
        logger.error("Local STT command failed for %s: %s", file_path, details)
        return {"success": False, "transcript": "", "error": f"Local STT failed: {details}"}
    except Exception as e:
        logger.error("Unexpected error during local command transcription: %s", e, exc_info=True)
        return {"success": False, "transcript": "", "error": f"Local transcription failed: {e}"}

# ---------------------------------------------------------------------------
# Provider: groq (Whisper API — free tier)
# ---------------------------------------------------------------------------


def _transcribe_groq(file_path: str, model_name: str) -> Dict[str, Any]:
    """Transcribe using Groq Whisper API (free tier available)."""
    api_key = get_env_value("GROQ_API_KEY")
    if not api_key:
        return {"success": False, "transcript": "", "error": "GROQ_API_KEY not set"}

    if not _HAS_OPENAI:
        return {"success": False, "transcript": "", "error": "openai package not installed"}

    # Auto-correct model if caller passed an OpenAI-only model
    if model_name in OPENAI_MODELS:
        logger.info("Model %s not available on Groq, using %s", model_name, DEFAULT_GROQ_STT_MODEL)
        model_name = DEFAULT_GROQ_STT_MODEL

    try:
        from openai import OpenAI, APIError, APIConnectionError, APITimeoutError
        client = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL, timeout=30, max_retries=0)
        try:
            with open(file_path, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    model=model_name,
                    file=audio_file,
                    response_format="text",
                )

            transcript_text = str(transcription).strip()
            logger.info("Transcribed %s via Groq API (%s, %d chars)",
                         Path(file_path).name, model_name, len(transcript_text))

            return {"success": True, "transcript": transcript_text, "provider": "groq"}
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    except PermissionError:
        return {"success": False, "transcript": "", "error": f"Permission denied: {file_path}"}
    except APIConnectionError as e:
        return {"success": False, "transcript": "", "error": f"Connection error: {e}"}
    except APITimeoutError as e:
        return {"success": False, "transcript": "", "error": f"Request timeout: {e}"}
    except APIError as e:
        return {"success": False, "transcript": "", "error": f"API error: {e}"}
    except Exception as e:
        logger.error("Groq transcription failed: %s", e, exc_info=True)
        return {"success": False, "transcript": "", "error": f"Transcription failed: {e}"}


def _transcribe_groq_remote_url(audio_url: str, model_name: str) -> Dict[str, Any]:
    """Transcribe a resolved remote audio URL using Groq's URL input."""
    api_key = get_env_value("GROQ_API_KEY")
    if not api_key:
        return {"success": False, "transcript": "", "error": "GROQ_API_KEY not set"}

    if model_name in OPENAI_MODELS:
        logger.info("Model %s not available on Groq, using %s", model_name, DEFAULT_GROQ_STT_MODEL)
        model_name = DEFAULT_GROQ_STT_MODEL

    try:
        import requests
    except ImportError:
        return {"success": False, "transcript": "", "error": "requests package not installed"}

    try:
        response = requests.post(
            f"{GROQ_BASE_URL.rstrip('/')}/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={
                "model": (None, model_name),
                "url": (None, audio_url),
                "response_format": (None, "text"),
            },
            timeout=120,
        )
        if response.status_code != 200:
            detail = ""
            try:
                error_body = response.json()
                detail = error_body.get("error", {}).get("message", "") or response.text[:300]
            except Exception:
                detail = response.text[:300]
            return {
                "success": False,
                "transcript": "",
                "error": f"Groq STT API error (HTTP {response.status_code}): {detail}",
            }

        transcript_text = response.text.strip()
        logger.info(
            "Transcribed remote URL via Groq API (%s, %d chars)",
            model_name,
            len(transcript_text),
        )

        return {"success": True, "transcript": transcript_text, "provider": "groq"}

    except requests.ConnectionError as e:
        return {"success": False, "transcript": "", "error": f"Connection error: {e}"}
    except requests.Timeout as e:
        return {"success": False, "transcript": "", "error": f"Request timeout: {e}"}
    except requests.RequestException as e:
        return {"success": False, "transcript": "", "error": f"API error: {e}"}
    except Exception as e:
        logger.error("Groq remote URL transcription failed: %s", e, exc_info=True)
        return {"success": False, "transcript": "", "error": f"Transcription failed: {e}"}

# ---------------------------------------------------------------------------
# Provider: openai (Whisper API)
# ---------------------------------------------------------------------------


def _transcribe_openai(file_path: str, model_name: str) -> Dict[str, Any]:
    """Transcribe using OpenAI Whisper API (paid)."""
    try:
        api_key, base_url = _resolve_openai_audio_client_config()
    except ValueError as exc:
        return {
            "success": False,
            "transcript": "",
            "error": str(exc),
        }

    if not _HAS_OPENAI:
        return {"success": False, "transcript": "", "error": "openai package not installed"}

    # Auto-correct model if caller passed a Groq-only model
    if model_name in GROQ_MODELS:
        logger.info("Model %s not available on OpenAI, using %s", model_name, DEFAULT_STT_MODEL)
        model_name = DEFAULT_STT_MODEL

    try:
        from openai import OpenAI, APIError, APIConnectionError, APITimeoutError
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=30, max_retries=0)
        try:
            with open(file_path, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    model=model_name,
                    file=audio_file,
                    response_format="text" if model_name == "whisper-1" else "json",
                )

            transcript_text = _extract_transcript_text(transcription)
            logger.info("Transcribed %s via OpenAI API (%s, %d chars)",
                         Path(file_path).name, model_name, len(transcript_text))

            return {"success": True, "transcript": transcript_text, "provider": "openai"}
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    except PermissionError:
        return {"success": False, "transcript": "", "error": f"Permission denied: {file_path}"}
    except APIConnectionError as e:
        return {"success": False, "transcript": "", "error": f"Connection error: {e}"}
    except APITimeoutError as e:
        return {"success": False, "transcript": "", "error": f"Request timeout: {e}"}
    except APIError as e:
        return {"success": False, "transcript": "", "error": f"API error: {e}"}
    except Exception as e:
        logger.error("OpenAI transcription failed: %s", e, exc_info=True)
        return {"success": False, "transcript": "", "error": f"Transcription failed: {e}"}

# ---------------------------------------------------------------------------
# Provider: mistral (Voxtral Transcribe API)
# ---------------------------------------------------------------------------


def _transcribe_mistral(file_path: str, model_name: str) -> Dict[str, Any]:
    """Transcribe using Mistral Voxtral Transcribe API.

    Uses the ``mistralai`` Python SDK to call ``/v1/audio/transcriptions``.
    Requires ``MISTRAL_API_KEY`` environment variable.
    """
    api_key = get_env_value("MISTRAL_API_KEY")
    if not api_key:
        return {"success": False, "transcript": "", "error": "MISTRAL_API_KEY not set"}

    try:
        from mistralai.client import Mistral

        with Mistral(api_key=api_key) as client:
            with open(file_path, "rb") as audio_file:
                result = client.audio.transcriptions.complete(
                    model=model_name,
                    file={"content": audio_file, "file_name": Path(file_path).name},
                )

            transcript_text = _extract_transcript_text(result)
            logger.info(
                "Transcribed %s via Mistral API (%s, %d chars)",
                Path(file_path).name, model_name, len(transcript_text),
            )
            return {"success": True, "transcript": transcript_text, "provider": "mistral"}

    except PermissionError:
        return {"success": False, "transcript": "", "error": f"Permission denied: {file_path}"}
    except Exception as e:
        logger.error("Mistral transcription failed: %s", e, exc_info=True)
        return {"success": False, "transcript": "", "error": f"Mistral transcription failed: {type(e).__name__}"}


# ---------------------------------------------------------------------------
# Provider: xAI (Grok STT API)
# ---------------------------------------------------------------------------


def _transcribe_xai(file_path: str, model_name: str) -> Dict[str, Any]:
    """Transcribe using xAI Grok STT API.

    Uses the ``POST /v1/stt`` REST endpoint with multipart/form-data.
    Supports Inverse Text Normalization, diarization, and word-level timestamps.
    Requires ``XAI_API_KEY`` environment variable.
    """
    from tools.xai_http import resolve_xai_http_credentials

    creds = resolve_xai_http_credentials()
    api_key = str(creds.get("api_key") or "").strip()
    if not api_key:
        return {
            "success": False,
            "transcript": "",
            "error": "No xAI credentials found. Configure xAI OAuth in `hermes model` or set XAI_API_KEY",
        }

    stt_config = _load_stt_config()
    xai_config = stt_config.get("xai", {})
    base_url = str(
        xai_config.get("base_url")
        or get_env_value("XAI_STT_BASE_URL")
        or creds.get("base_url")
        or XAI_STT_BASE_URL
    ).strip().rstrip("/")
    language = str(
        xai_config.get("language")
        or os.getenv("HERMES_LOCAL_STT_LANGUAGE")
        or DEFAULT_LOCAL_STT_LANGUAGE
    ).strip()
    # .get("format", True) already defaults to True when the key is absent;
    # is_truthy_value only normalizes truthy/falsy strings from config.
    use_format = is_truthy_value(xai_config.get("format", True))
    use_diarize = is_truthy_value(xai_config.get("diarize", False))

    try:
        import requests
        from tools.xai_http import hermes_xai_user_agent

        data: Dict[str, str] = {}
        if language:
            data["language"] = language
        if use_format:
            data["format"] = "true"
        if use_diarize:
            data["diarize"] = "true"

        with open(file_path, "rb") as audio_file:
            response = requests.post(
                f"{base_url}/stt",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "User-Agent": hermes_xai_user_agent(),
                },
                files={
                    "file": (Path(file_path).name, audio_file),
                },
                data=data,
                timeout=120,
            )

        if response.status_code != 200:
            detail = ""
            try:
                err_body = response.json()
                detail = err_body.get("error", {}).get("message", "") or response.text[:300]
            except Exception:
                detail = response.text[:300]
            return {
                "success": False,
                "transcript": "",
                "error": f"xAI STT API error (HTTP {response.status_code}): {detail}",
            }

        result = response.json()
        transcript_text = result.get("text", "").strip()

        if not transcript_text:
            return {
                "success": False,
                "transcript": "",
                "error": "xAI STT returned empty transcript",
            }

        logger.info(
            "Transcribed %s via xAI Grok STT (lang=%s, %.1fs audio, %d chars)",
            Path(file_path).name,
            result.get("language", language),
            result.get("duration", 0),
            len(transcript_text),
        )

        return {"success": True, "transcript": transcript_text, "provider": "xai"}

    except PermissionError:
        return {"success": False, "transcript": "", "error": f"Permission denied: {file_path}"}
    except Exception as e:
        logger.error("xAI STT transcription failed: %s", e, exc_info=True)
        return {"success": False, "transcript": "", "error": f"xAI STT transcription failed: {e}"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _transcribe_audio_file(
    file_path: str,
    provider: str,
    stt_config: dict,
    model: Optional[str],
) -> Dict[str, Any]:
    if provider == "local":
        local_cfg = stt_config.get("local", {})
        model_name = _normalize_local_model(
            model or local_cfg.get("model", DEFAULT_LOCAL_MODEL)
        )
        return _transcribe_local(file_path, model_name)

    if provider == "local_command":
        local_cfg = stt_config.get("local", {})
        model_name = _normalize_local_command_model(
            model or local_cfg.get("model", DEFAULT_LOCAL_MODEL)
        )
        return _transcribe_local_command(file_path, model_name)

    if provider == "groq":
        model_name = model or DEFAULT_GROQ_STT_MODEL
        return _transcribe_groq(file_path, model_name)

    if provider == "openai":
        openai_cfg = stt_config.get("openai", {})
        model_name = model or openai_cfg.get("model", DEFAULT_STT_MODEL)
        return _transcribe_openai(file_path, model_name)

    if provider == "mistral":
        mistral_cfg = stt_config.get("mistral", {})
        model_name = model or mistral_cfg.get("model", DEFAULT_MISTRAL_STT_MODEL)
        return _transcribe_mistral(file_path, model_name)

    if provider == "xai":
        model_name = model or "grok-stt"
        return _transcribe_xai(file_path, model_name)

    return _no_stt_provider_error()


def transcribe_audio(file_path: str, model: Optional[str] = None) -> Dict[str, Any]:
    """
    Transcribe an audio file or http(s) audio URL using the configured STT provider.

    Provider priority:
      1. User config (``stt.provider`` in config.yaml)
      2. Auto-detect: local faster-whisper (free) > Groq (free tier) > OpenAI (paid)

    Args:
        file_path: Absolute path or remote http(s) URL for the audio to transcribe.
        model:     Override the model. If None, uses config or provider default.

    Returns:
        dict with keys:
          - "success" (bool): Whether transcription succeeded
          - "transcript" (str): The transcribed text (empty on failure)
          - "error" (str, optional): Error message if success is False
          - "provider" (str, optional): Which provider was used
    """
    if _is_remote_audio_url(file_path):
        return _transcribe_remote_audio_url(file_path, model)

    # Validate input
    error = _validate_audio_file(file_path)
    if error:
        return error

    # Load config and determine provider
    stt_config = _load_stt_config()
    if not is_stt_enabled(stt_config):
        return {
            "success": False,
            "transcript": "",
            "error": "STT is disabled in config.yaml (stt.enabled: false).",
        }

    provider = _get_provider(stt_config)
    return _transcribe_audio_file(file_path, provider, stt_config, model)


def _resolve_openai_audio_client_config() -> tuple[str, str]:
    """Return direct OpenAI audio config or a managed gateway fallback."""
    stt_config = _load_stt_config()
    openai_cfg = stt_config.get("openai", {})
    cfg_api_key = openai_cfg.get("api_key", "")
    cfg_base_url = openai_cfg.get("base_url", "")
    if cfg_api_key:
        return cfg_api_key, (cfg_base_url or OPENAI_BASE_URL)

    direct_api_key = resolve_openai_audio_api_key()
    if direct_api_key:
        return direct_api_key, OPENAI_BASE_URL

    managed_gateway = resolve_managed_tool_gateway("openai-audio")
    if managed_gateway is None:
        message = "Neither stt.openai.api_key in config nor VOICE_TOOLS_OPENAI_KEY/OPENAI_API_KEY is set"
        if managed_nous_tools_enabled():
            message += ", and the managed OpenAI audio gateway is unavailable"
        raise ValueError(message)

    return managed_gateway.nous_user_token, urljoin(
        f"{managed_gateway.gateway_origin.rstrip('/')}/", "v1"
    )


def _extract_transcript_text(transcription: Any) -> str:
    """Normalize text and JSON transcription responses to a plain string."""
    if isinstance(transcription, str):
        return transcription.strip()

    if hasattr(transcription, "text"):
        value = getattr(transcription, "text")
        if isinstance(value, str):
            return value.strip()

    if isinstance(transcription, dict):
        value = transcription.get("text")
        if isinstance(value, str):
            return value.strip()

    return str(transcription).strip()
