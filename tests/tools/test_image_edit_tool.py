from __future__ import annotations

import json
from typing import Any, cast

from agent import image_gen_registry
from agent.image_gen_provider import ImageGenProvider


class _FakeEditProvider(ImageGenProvider):
    def __init__(self):
        self.last_edit: dict[str, object] | None = None

    @property
    def name(self) -> str:
        return "codex"

    def generate(self, prompt, aspect_ratio="landscape", **kwargs):
        raise AssertionError("generate should not be called by image_edit")

    def supports_edit(self) -> bool:
        return True

    def is_available(self) -> bool:
        return True

    def edit(self, prompt, image, aspect_ratio="landscape", **kwargs):
        self.last_edit = {
            "prompt": prompt,
            "image": image,
            "aspect_ratio": aspect_ratio,
            **kwargs,
        }
        return {
            "success": True,
            "image": "/tmp/edit.png",
            "model": kwargs.get("model", "gpt-image-2-medium"),
            "quality_tier": kwargs.get("quality_tier"),
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "size": kwargs.get("size"),
            "provider": "codex",
            "source_image": image,
        }


class _NoEditProvider(ImageGenProvider):
    @property
    def name(self) -> str:
        return "noedit"

    def generate(self, prompt, aspect_ratio="landscape", **kwargs):
        return {}


def setup_function():
    image_gen_registry._reset_for_tests()


def teardown_function():
    image_gen_registry._reset_for_tests()


def test_image_edit_schema_requires_prompt_and_image():
    from tools.image_edit_tool import IMAGE_EDIT_SCHEMA

    parameters = cast(dict[str, Any], IMAGE_EDIT_SCHEMA["parameters"])
    properties = cast(dict[str, Any], parameters["properties"])

    assert IMAGE_EDIT_SCHEMA["name"] == "image_edit"
    assert parameters["required"] == ["prompt", "image"]
    assert "aspect_ratio" in properties
    assert "size" in properties
    assert "quality_tier" in properties
    assert properties["quality_tier"]["enum"] == ["auto", "low", "medium", "high"]
    assert "model" in properties
    assert properties["model"]["enum"] == ["gpt-image-2-low", "gpt-image-2-medium", "gpt-image-2-high"]
    assert "9:16" in properties["aspect_ratio"]["enum"]


def test_dispatch_routes_to_edit_provider(monkeypatch):
    from tools import image_edit_tool
    from agent import image_gen_registry as registry_module
    from hermes_cli import plugins as plugins_module

    provider = _FakeEditProvider()
    monkeypatch.setattr(image_edit_tool, "_read_configured_image_provider", lambda: "codex")
    monkeypatch.setattr(image_edit_tool, "_read_configured_image_model", lambda: "gpt-image-2-low")
    monkeypatch.setattr(plugins_module, "_ensure_plugins_discovered", lambda force=False: None)
    monkeypatch.setattr(registry_module, "get_provider", lambda name: provider if name == "codex" else None)

    payload = json.loads(
        image_edit_tool._dispatch_to_plugin_provider(
            "make it blue",
            "/tmp/source.png",
            "9:16",
            "1024x1824",
            quality_tier="high",
            model="gpt-image-2-high",
        )
    )

    assert payload["success"] is True
    assert payload["provider"] == "codex"
    assert payload["image"] == "/tmp/edit.png"
    assert payload["source_image"] == "/tmp/source.png"
    assert payload["aspect_ratio"] == "9:16"
    assert payload["size"] == "1024x1824"
    assert payload["model"] == "gpt-image-2-high"
    assert payload["quality_tier"] == "high"
    assert provider.last_edit == {
        "prompt": "make it blue",
        "image": "/tmp/source.png",
        "aspect_ratio": "9:16",
        "size": "1024x1824",
        "quality_tier": "high",
        "model": "gpt-image-2-high",
    }


def test_dispatch_reports_provider_without_edit(monkeypatch):
    from tools import image_edit_tool
    from agent import image_gen_registry as registry_module
    from hermes_cli import plugins as plugins_module

    monkeypatch.setattr(image_edit_tool, "_read_configured_image_provider", lambda: "noedit")
    monkeypatch.setattr(plugins_module, "_ensure_plugins_discovered", lambda force=False: None)
    monkeypatch.setattr(registry_module, "get_provider", lambda name: _NoEditProvider() if name == "noedit" else None)

    payload = json.loads(image_edit_tool._dispatch_to_plugin_provider("make it blue", "/tmp/source.png", "square"))

    assert payload["success"] is False
    assert payload["error_type"] == "unsupported"


def test_dispatch_uses_configured_model_when_no_call_override(monkeypatch):
    from tools import image_edit_tool
    from agent import image_gen_registry as registry_module
    from hermes_cli import plugins as plugins_module

    provider = _FakeEditProvider()
    monkeypatch.setattr(image_edit_tool, "_read_configured_image_provider", lambda: "codex")
    monkeypatch.setattr(image_edit_tool, "_read_configured_image_model", lambda: "gpt-image-2-high")
    monkeypatch.setattr(plugins_module, "_ensure_plugins_discovered", lambda force=False: None)
    monkeypatch.setattr(registry_module, "get_provider", lambda name: provider if name == "codex" else None)

    payload = json.loads(image_edit_tool._dispatch_to_plugin_provider("make it blue", "/tmp/source.png", "square"))

    assert payload["success"] is True
    assert payload["model"] == "gpt-image-2-high"
    assert provider.last_edit is not None
    assert provider.last_edit["model"] == "gpt-image-2-high"


def test_dispatch_quality_tier_overrides_configured_model(monkeypatch):
    from tools import image_edit_tool
    from agent import image_gen_registry as registry_module
    from hermes_cli import plugins as plugins_module

    provider = _FakeEditProvider()
    monkeypatch.setattr(image_edit_tool, "_read_configured_image_provider", lambda: "codex")
    monkeypatch.setattr(image_edit_tool, "_read_configured_image_model", lambda: "gpt-image-2-high")
    monkeypatch.setattr(plugins_module, "_ensure_plugins_discovered", lambda force=False: None)
    monkeypatch.setattr(registry_module, "get_provider", lambda name: provider if name == "codex" else None)

    payload = json.loads(
        image_edit_tool._dispatch_to_plugin_provider(
            "make it blue",
            "/tmp/source.png",
            "square",
            quality_tier="low",
        )
    )

    assert payload["success"] is True
    assert provider.last_edit is not None
    assert provider.last_edit["quality_tier"] == "low"
    assert "model" not in provider.last_edit


def test_handle_rejects_blank_image():
    from tools import image_edit_tool

    payload = json.loads(image_edit_tool._handle_image_edit({"prompt": "make it blue", "image": "   "}))

    assert "image is required" in payload["error"]


def test_handle_strips_image_path(monkeypatch):
    from tools import image_edit_tool

    captured = {}

    def _fake_dispatch(prompt, image, aspect_ratio, size=None, *, quality_tier=None, model=None):
        captured.update({"prompt": prompt, "image": image, "aspect_ratio": aspect_ratio, "size": size})
        return json.dumps({"success": True, "image": "/tmp/out.png"})

    monkeypatch.setattr(image_edit_tool, "_dispatch_to_plugin_provider", _fake_dispatch)

    payload = json.loads(image_edit_tool._handle_image_edit({"prompt": " make it blue ", "image": "  /tmp/source.png  "}))

    assert payload["success"] is True
    assert captured["prompt"] == "make it blue"
    assert captured["image"] == "/tmp/source.png"


def test_image_gen_toolset_includes_image_edit():
    from toolsets import resolve_toolset

    tools = resolve_toolset("image_gen")
    assert "image_generate" in tools
    assert "image_edit" in tools


def test_hermes_acp_toolset_does_not_enable_image_tools_by_default():
    from toolsets import resolve_toolset

    tools = resolve_toolset("hermes-acp")
    assert "image_generate" not in tools
    assert "image_edit" not in tools
