"""Tests for the unified ``video_generate`` tool dispatch surface."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from agent import video_gen_registry
from agent.video_gen_provider import VideoGenProvider


@pytest.fixture(autouse=True)
def _reset_registry():
    video_gen_registry._reset_for_tests()
    yield
    video_gen_registry._reset_for_tests()


class _RecordingProvider(VideoGenProvider):
    """Captures the kwargs the tool layer hands it."""

    def __init__(self, name: str = "fake"):
        self._name = name
        self.last_kwargs: Dict[str, Any] = {}

    @property
    def name(self) -> str:
        return self._name

    def list_models(self) -> List[Dict[str, Any]]:
        return [{"id": "model-a"}]

    def default_model(self) -> Optional[str]:
        return "model-a"

    def generate(self, prompt, **kwargs):
        self.last_kwargs = {"prompt": prompt, **kwargs}
        modality = "image" if kwargs.get("image_url") else "text"
        return {
            "success": True,
            "video": "https://example.com/v.mp4",
            "model": kwargs.get("model") or "model-a",
            "prompt": prompt,
            "modality": modality,
            "aspect_ratio": kwargs.get("aspect_ratio", ""),
            "duration": kwargs.get("duration") or 0,
            "provider": self._name,
        }

    def edit(self, prompt, **kwargs):
        self.last_kwargs = {"prompt": prompt, **kwargs}
        return {
            "success": True,
            "video": "https://example.com/edit.mp4",
            "model": kwargs.get("model") or "model-a",
            "prompt": prompt,
            "modality": "edit",
            "aspect_ratio": "",
            "duration": 0,
            "provider": self._name,
        }

    def extend(self, prompt, **kwargs):
        self.last_kwargs = {"prompt": prompt, **kwargs}
        return {
            "success": True,
            "video": "https://example.com/extend.mp4",
            "model": kwargs.get("model") or "model-a",
            "prompt": prompt,
            "modality": "extend",
            "aspect_ratio": "",
            "duration": kwargs.get("duration") or 0,
            "provider": self._name,
        }


class _RaisingProvider(VideoGenProvider):
    @property
    def name(self) -> str:
        return "raises"

    def generate(self, prompt, **kwargs):
        raise RuntimeError("boom")


class TestUnifiedDispatch:
    def _run(self, args: Dict[str, Any], *, configured: Optional[str] = None) -> Dict[str, Any]:
        from tools import video_generation_tool
        import hermes_cli.plugins as plugins_module

        saved = video_generation_tool._read_configured_video_provider
        video_generation_tool._read_configured_video_provider = lambda: configured  # type: ignore
        saved_discover = plugins_module._ensure_plugins_discovered
        plugins_module._ensure_plugins_discovered = lambda *_a, **_k: None  # type: ignore
        try:
            raw = video_generation_tool._handle_video_generate(args)
        finally:
            video_generation_tool._read_configured_video_provider = saved  # type: ignore
            plugins_module._ensure_plugins_discovered = saved_discover  # type: ignore
        return json.loads(raw)

    def test_no_provider_returns_clear_error(self):
        result = self._run({"prompt": "a dog"})
        assert result["success"] is False
        assert result["error_type"] == "no_provider_configured"

    def test_unknown_provider_returns_clear_error(self):
        result = self._run({"prompt": "a dog"}, configured="ghost")
        assert result["success"] is False
        assert result["error_type"] == "provider_not_registered"

    def test_text_to_video_routes_without_image_url(self):
        provider = _RecordingProvider("rec")
        video_gen_registry.register_provider(provider)
        result = self._run({"prompt": "a happy dog"})
        assert result["success"] is True
        assert result["modality"] == "text"
        assert "image_url" not in provider.last_kwargs
        assert provider.last_kwargs["aspect_ratio"] == "16:9"
        assert provider.last_kwargs["resolution"] == "720p"

    def test_image_to_video_routes_with_image_url(self):
        provider = _RecordingProvider("rec")
        video_gen_registry.register_provider(provider)
        result = self._run({
            "prompt": "animate this",
            "image_url": "https://example.com/img.png",
            "duration": 5,
            "aspect_ratio": "9:16",
            "resolution": "720p",
            "audio": True,
            "model": "grok-imagine-video",
        })
        assert result["success"] is True
        assert result["modality"] == "image"
        assert provider.last_kwargs["model"] == "grok-imagine-video"
        assert provider.last_kwargs["image_url"] == "https://example.com/img.png"
        assert provider.last_kwargs["duration"] == 5
        assert provider.last_kwargs["aspect_ratio"] == "9:16"
        assert provider.last_kwargs["resolution"] == "720p"
        assert provider.last_kwargs["audio"] is True

    def test_image_to_video_omitted_aspect_uses_auto_not_landscape_default(self):
        provider = _RecordingProvider("rec")
        video_gen_registry.register_provider(provider)
        result = self._run({
            "prompt": "animate this",
            "image_url": "https://example.com/img.png",
        })
        assert result["success"] is True
        assert provider.last_kwargs["aspect_ratio"] == "auto"

    def test_edit_operation_routes_to_provider_edit(self):
        provider = _RecordingProvider("rec")
        video_gen_registry.register_provider(provider)
        result = self._run({
            "operation": "edit",
            "prompt": "colorize this clip",
            "video_url": "https://example.com/source.mp4",
            "reference_image_urls": ["https://example.com/ref.png"],
        })
        assert result["success"] is True
        assert result["modality"] == "edit"
        assert provider.last_kwargs["video_url"] == "https://example.com/source.mp4"
        assert provider.last_kwargs["reference_image_urls"] == ["https://example.com/ref.png"]

    def test_extend_operation_routes_to_provider_extend(self):
        provider = _RecordingProvider("rec")
        video_gen_registry.register_provider(provider)
        result = self._run({
            "operation": "extend",
            "prompt": "camera pulls back",
            "video_url": "https://example.com/source.mp4",
            "duration": 6,
        })
        assert result["success"] is True
        assert result["modality"] == "extend"
        assert provider.last_kwargs["video_url"] == "https://example.com/source.mp4"
        assert provider.last_kwargs["duration"] == 6

    def test_prompt_required(self):
        provider = _RecordingProvider("rec")
        video_gen_registry.register_provider(provider)
        result = self._run({"prompt": "", "image_url": "https://example.com/i.png"})
        assert "error" in result
        assert "prompt" in result["error"].lower()

    def test_provider_exception_caught(self):
        video_gen_registry.register_provider(_RaisingProvider())
        result = self._run({"prompt": "x"})
        assert result["success"] is False
        assert result["error_type"] == "provider_exception"

    def test_operation_fields_in_schema(self):
        """The unified surface now includes generate/edit/extend."""
        from tools.video_generation_tool import VIDEO_GENERATE_SCHEMA
        props = VIDEO_GENERATE_SCHEMA["parameters"]["properties"]
        assert props["operation"]["enum"] == ["generate", "edit", "extend"]
        assert "video_url" in props
        assert "end_image_url" in props
