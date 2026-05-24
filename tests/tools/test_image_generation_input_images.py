from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import image_gen_registry
from agent.image_gen_provider import ImageGenProvider


@pytest.fixture(autouse=True)
def _reset_registry():
    image_gen_registry._reset_for_tests()
    yield
    image_gen_registry._reset_for_tests()


class _RecordingProvider(ImageGenProvider):
    @property
    def name(self) -> str:
        return "recording"

    def generate(self, prompt, aspect_ratio="landscape", **kwargs):
        return {
            "success": True,
            "image": "/tmp/reference-aware.png",
            "model": "test-model",
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "provider": self.name,
            "received_kwargs": kwargs,
        }


def _load_openai_codex_plugin():
    root = Path(__file__).resolve().parents[2]
    plugin_path = root / "plugins" / "image_gen" / "openai-codex" / "__init__.py"
    spec = importlib.util.spec_from_file_location("openai_codex_image_gen_for_tests", plugin_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _DummyResponses:
    def __init__(self):
        self.kwargs = None

    def stream(self, **kwargs):
        self.kwargs = kwargs

        class _Stream:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                return False

            def __iter__(self_inner):
                return iter(())

            def get_final_response(self_inner):
                item = SimpleNamespace(type="image_generation_call", result="ZmFrZS1wbmc=")
                return SimpleNamespace(output=[item])

        return _Stream()


class _DummyClient:
    def __init__(self):
        self.responses = _DummyResponses()


class TestImageGenerateSchemaAndDispatch:
    def test_schema_exposes_reference_image_inputs_to_agent(self):
        from tools import image_generation_tool

        props = image_generation_tool.IMAGE_GENERATE_SCHEMA["parameters"]["properties"]

        assert set(props) == {"prompt", "aspect_ratio", "input_images", "background"}
        assert props["input_images"]["type"] == "array"
        assert props["input_images"]["items"]["type"] == "string"
        assert props["background"]["enum"] == ["transparent", "opaque", "auto"]

    def test_plugin_dispatch_forwards_input_images_and_background(self, monkeypatch, tmp_path):
        from tools import image_generation_tool
        from agent import image_gen_registry as registry_module
        from hermes_cli import plugins as plugins_module

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        provider = _RecordingProvider()
        image_gen_registry.register_provider(provider)

        monkeypatch.setattr(image_generation_tool, "_read_configured_image_provider", lambda: "recording")
        monkeypatch.setattr(plugins_module, "_ensure_plugins_discovered", lambda force=False: None)
        monkeypatch.setattr(registry_module, "get_provider", lambda name: provider if name == "recording" else None)

        dispatched = image_generation_tool._dispatch_to_plugin_provider(
            "extract the blue icon as a transparent asset",
            "square",
            input_images=["/tmp/reference.png", "/tmp/crop.png"],
            background="transparent",
        )
        payload = json.loads(dispatched)

        assert payload["success"] is True
        assert payload["received_kwargs"]["input_images"] == ["/tmp/reference.png", "/tmp/crop.png"]
        assert payload["received_kwargs"]["background"] == "transparent"


class TestOpenAICodexImageInputs:
    def test_builds_input_image_content_from_local_files(self, tmp_path):
        module = _load_openai_codex_plugin()
        crop = tmp_path / "crop.png"
        raw = b"\x89PNG\r\n\x1a\nminimal-test-bytes"
        crop.write_bytes(raw)

        content = module._build_response_content("extract this icon", [str(crop)])

        assert content[0] == {"type": "input_text", "text": "extract this icon"}
        assert content[1]["type"] == "input_image"
        assert content[1]["image_url"].startswith("data:image/png;base64,")
        encoded = content[1]["image_url"].split(",", 1)[1]
        assert base64.b64decode(encoded) == raw

    def test_passes_remote_and_data_urls_through_as_input_images(self):
        module = _load_openai_codex_plugin()

        content = module._build_response_content(
            "use these references",
            ["https://example.com/ref.png", "data:image/png;base64,AAAA"],
        )

        assert content[1] == {"type": "input_image", "image_url": "https://example.com/ref.png"}
        assert content[2] == {"type": "input_image", "image_url": "data:image/png;base64,AAAA"}

    def test_collect_image_b64_sends_input_images_and_background_to_codex_responses(self, tmp_path):
        module = _load_openai_codex_plugin()
        crop = tmp_path / "crop.png"
        crop.write_bytes(b"\x89PNG\r\n\x1a\nminimal-test-bytes")
        client = _DummyClient()

        image_b64 = module._collect_image_b64(
            client,
            prompt="extract transparent icon",
            size="1024x1024",
            quality="medium",
            input_images=[str(crop)],
            background="transparent",
        )

        assert image_b64 == "ZmFrZS1wbmc="
        kwargs = client.responses.kwargs
        assert kwargs["input"][0]["content"][0]["type"] == "input_text"
        assert kwargs["input"][0]["content"][1]["type"] == "input_image"
        assert kwargs["tools"][0]["type"] == "image_generation"
        assert kwargs["tools"][0]["background"] == "transparent"
