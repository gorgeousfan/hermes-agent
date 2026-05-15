"""Tests for the FAL video gen plugin — family routing, payload shape."""

from __future__ import annotations

import struct
import zlib

import pytest

from agent import video_gen_registry


def _png_bytes(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = b"".join(b"\x00" + (b"\x00\x00\x00" * width) for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


@pytest.fixture(autouse=True)
def _reset_registry():
    video_gen_registry._reset_for_tests()
    yield
    video_gen_registry._reset_for_tests()


def test_fal_provider_registers():
    from plugins.video_gen.fal import FALVideoGenProvider, DEFAULT_MODEL

    provider = FALVideoGenProvider()
    video_gen_registry.register_provider(provider)

    assert video_gen_registry.get_provider("fal") is provider
    assert provider.display_name == "FAL"
    # DEFAULT_MODEL is the cheap-tier default
    assert provider.default_model() == DEFAULT_MODEL
    assert DEFAULT_MODEL in {"pixverse-v6", "ltx-2.3"}


def test_fal_family_catalog():
    """Each family declares both endpoints. The catalog covers the
    cheap + premium tiers Teknium listed."""
    from plugins.video_gen.fal import FAL_FAMILIES

    expected = {
        # cheap
        "ltx-2.3", "pixverse-v6",
        # premium
        "veo3.1", "grok-imagine-video", "seedance-2.0",
        "kling-v3-standard", "kling-v3-pro", "kling-v3-4k",
        "happy-horse",
    }
    assert expected.issubset(set(FAL_FAMILIES.keys())), (
        f"missing families: {expected - set(FAL_FAMILIES.keys())}"
    )
    for fid, meta in FAL_FAMILIES.items():
        assert meta.get("text_endpoint"), f"{fid} missing text_endpoint"
        assert meta.get("image_endpoint"), f"{fid} missing image_endpoint"
        assert meta["text_endpoint"] != meta["image_endpoint"]
        assert meta.get("tier") in {"cheap", "premium"}, (
            f"{fid} has invalid tier"
        )


def test_kling_4k_uses_start_image_url():
    """Kling v3 4K's image-to-video endpoint expects start_image_url,
    not image_url. The family must declare image_param_key='start_image_url'."""
    from plugins.video_gen.fal import FAL_FAMILIES, _build_payload

    meta = FAL_FAMILIES["kling-v3-4k"]
    assert meta.get("image_param_key") == "start_image_url"
    payload = _build_payload(
        meta,
        prompt="x",
        image_url="https://example.com/i.png",
        duration=5,
        aspect_ratio="16:9",
        resolution="720p",
        negative_prompt=None,
        audio=None,
        seed=None,
    )
    assert payload.get("start_image_url") == "https://example.com/i.png"
    assert "image_url" not in payload


def test_fal_list_models_advertises_both_modalities():
    from plugins.video_gen.fal import FALVideoGenProvider

    models = FALVideoGenProvider().list_models()
    for m in models:
        assert {"text", "image"}.issubset(set(m["modalities"])), (
            f"{m['id']} doesn't advertise both modalities — every family "
            f"should have t2v + i2v"
        )


def test_fal_unavailable_without_key(monkeypatch):
    from plugins.video_gen.fal import FALVideoGenProvider

    monkeypatch.delenv("FAL_KEY", raising=False)
    assert FALVideoGenProvider().is_available() is False


def test_fal_generate_requires_fal_key(monkeypatch):
    from plugins.video_gen.fal import FALVideoGenProvider

    monkeypatch.delenv("FAL_KEY", raising=False)
    result = FALVideoGenProvider().generate("a happy dog")
    assert result["success"] is False
    assert result["error_type"] == "auth_required"


class TestFamilyRouting:
    """The headline behavior: image_url presence picks the endpoint."""

    @pytest.fixture
    def with_fake_fal(self, monkeypatch):
        """Stub fal_client.subscribe to capture which endpoint we hit."""
        import sys
        import types

        captured = {"endpoint": None, "arguments": None}

        fake = types.ModuleType("fal_client")
        def _subscribe(endpoint, arguments=None, with_logs=False):
            captured["endpoint"] = endpoint
            captured["arguments"] = arguments
            return {"video": {"url": "https://fake/out.mp4"}}
        fake.subscribe = _subscribe  # type: ignore
        fake.uploads = []  # type: ignore
        def _upload_file(path):
            fake.uploads.append(path)
            return f"https://fake/uploads/{getattr(path, 'name', 'file')}"
        fake.upload_file = _upload_file  # type: ignore
        monkeypatch.setitem(sys.modules, "fal_client", fake)

        # Reset the lazy global so it picks up our stub
        from plugins.video_gen import fal as fal_plugin
        fal_plugin._fal_client = None

        monkeypatch.setenv("FAL_KEY", "test")
        return captured

    def test_text_to_video_routes_to_text_endpoint(self, with_fake_fal):
        from plugins.video_gen.fal import FALVideoGenProvider

        result = FALVideoGenProvider().generate(
            "a dog running",
            model="pixverse-v6",
        )
        assert result["success"] is True
        assert with_fake_fal["endpoint"] == "fal-ai/pixverse/v6/text-to-video"
        assert result["modality"] == "text"
        assert with_fake_fal["arguments"]["prompt"] == "a dog running"
        assert "image_url" not in with_fake_fal["arguments"]

    def test_image_to_video_routes_to_image_endpoint(self, with_fake_fal):
        from plugins.video_gen.fal import FALVideoGenProvider

        result = FALVideoGenProvider().generate(
            "animate this dog",
            model="pixverse-v6",
            image_url="https://example.com/dog.png",
        )
        assert result["success"] is True
        assert with_fake_fal["endpoint"] == "fal-ai/pixverse/v6/image-to-video"
        assert result["modality"] == "image"
        assert with_fake_fal["arguments"]["image_url"] == "https://example.com/dog.png"

    def test_default_family_text_routing(self, with_fake_fal):
        """No model arg → DEFAULT_MODEL → text-to-video endpoint."""
        from plugins.video_gen.fal import FALVideoGenProvider, FAL_FAMILIES, DEFAULT_MODEL

        result = FALVideoGenProvider().generate("a dog")
        assert result["success"] is True
        expected_endpoint = FAL_FAMILIES[DEFAULT_MODEL]["text_endpoint"]
        assert with_fake_fal["endpoint"] == expected_endpoint

    def test_default_family_image_routing(self, with_fake_fal):
        from plugins.video_gen.fal import FALVideoGenProvider, FAL_FAMILIES, DEFAULT_MODEL

        result = FALVideoGenProvider().generate(
            "animate this",
            image_url="https://example.com/i.png",
        )
        assert result["success"] is True
        expected_endpoint = FAL_FAMILIES[DEFAULT_MODEL]["image_endpoint"]
        assert with_fake_fal["endpoint"] == expected_endpoint

    def test_unknown_family_falls_back_to_default(self, with_fake_fal):
        from plugins.video_gen.fal import FALVideoGenProvider, FAL_FAMILIES, DEFAULT_MODEL

        result = FALVideoGenProvider().generate(
            "x",
            model="not-a-real-family",
        )
        assert result["success"] is True
        expected_endpoint = FAL_FAMILIES[DEFAULT_MODEL]["text_endpoint"]
        assert with_fake_fal["endpoint"] == expected_endpoint

    def test_premium_seedance_routing(self, with_fake_fal):
        """Sanity check the premium-tier seedance routes correctly."""
        from plugins.video_gen.fal import FALVideoGenProvider

        result = FALVideoGenProvider().generate(
            "a dog",
            model="seedance-2.0",
            image_url="https://example.com/dog.png",
        )
        assert result["success"] is True
        assert with_fake_fal["endpoint"] == "bytedance/seedance-2.0/image-to-video"
        # Seedance uses regular image_url (not start_image_url)
        assert with_fake_fal["arguments"]["image_url"] == "https://example.com/dog.png"

    def test_kling_4k_remaps_image_param(self, with_fake_fal):
        """Kling v3 4K image-to-video receives start_image_url, not image_url."""
        from plugins.video_gen.fal import FALVideoGenProvider

        result = FALVideoGenProvider().generate(
            "x",
            model="kling-v3-4k",
            image_url="https://example.com/frame.png",
        )
        assert result["success"] is True
        assert with_fake_fal["endpoint"] == "fal-ai/kling-video/v3/4k/image-to-video"
        assert with_fake_fal["arguments"].get("start_image_url") == "https://example.com/frame.png"
        assert "image_url" not in with_fake_fal["arguments"]

    def test_kling_3_alias_routes_to_pro_image_endpoint(self, with_fake_fal):
        from plugins.video_gen.fal import FALVideoGenProvider

        result = FALVideoGenProvider().generate(
            "x",
            model="kling-3",
            image_url="https://example.com/frame.png",
            audio=True,
            duration=4,
        )
        assert result["success"] is True
        assert result["model"] == "kling-v3-pro"
        assert with_fake_fal["endpoint"] == "fal-ai/kling-video/v3/pro/image-to-video"
        assert with_fake_fal["arguments"]["start_image_url"] == "https://example.com/frame.png"
        assert with_fake_fal["arguments"]["generate_audio"] is True

    def test_grok_imagine_video_routes_to_xai_i2v_endpoint(self, with_fake_fal):
        from plugins.video_gen.fal import FALVideoGenProvider

        result = FALVideoGenProvider().generate(
            "make it move",
            model="grok-imagine-video",
            image_url="https://example.com/frame.png",
            duration=5,
            aspect_ratio="9:16",
            resolution="720p",
        )
        assert result["success"] is True
        assert with_fake_fal["endpoint"] == "xai/grok-imagine-video/image-to-video"
        assert with_fake_fal["arguments"]["image_url"] == "https://example.com/frame.png"
        assert with_fake_fal["arguments"]["duration"] == 5
        assert isinstance(with_fake_fal["arguments"]["duration"], int)
        assert with_fake_fal["arguments"]["aspect_ratio"] == "9:16"
        assert with_fake_fal["arguments"]["resolution"] == "720p"

    def test_local_image_url_is_uploaded_before_i2v(self, tmp_path, with_fake_fal):
        from plugins.video_gen.fal import FALVideoGenProvider

        source = tmp_path / "frame.png"
        source.write_bytes(_png_bytes(16, 9))

        result = FALVideoGenProvider().generate(
            "make it move",
            model="grok-imagine-video",
            image_url=str(source),
            duration=5,
            aspect_ratio="9:16",
            resolution="720p",
        )
        assert result["success"] is True
        assert with_fake_fal["arguments"]["image_url"] == "https://fake/uploads/frame.png"
        assert with_fake_fal["arguments"]["duration"] == 5
        assert with_fake_fal["arguments"]["aspect_ratio"] == "9:16"
        assert with_fake_fal["arguments"]["resolution"] == "720p"

    def test_portrait_source_with_auto_aspect_infers_portrait_ar(self, tmp_path, with_fake_fal):
        from plugins.video_gen.fal import FALVideoGenProvider

        source = tmp_path / "portrait.png"
        source.write_bytes(_png_bytes(9, 16))

        result = FALVideoGenProvider().generate(
            "animate this vertical still",
            model="grok-imagine-video",
            image_url=str(source),
            aspect_ratio="auto",
            duration=5,
        )
        assert result["success"] is True
        assert with_fake_fal["arguments"]["aspect_ratio"] == "9:16"
        assert result["aspect_ratio"] == "9:16"

    def test_explicit_landscape_aspect_is_not_rewritten(self, tmp_path, with_fake_fal):
        from plugins.video_gen.fal import FALVideoGenProvider

        source = tmp_path / "portrait.png"
        source.write_bytes(_png_bytes(9, 16))

        result = FALVideoGenProvider().generate(
            "animate this vertical still as landscape",
            model="grok-imagine-video",
            image_url=str(source),
            aspect_ratio="16:9",
            duration=5,
        )
        assert result["success"] is True
        assert with_fake_fal["arguments"]["aspect_ratio"] == "16:9"

    def test_happy_horse_uses_alibaba_image_endpoint(self, with_fake_fal):
        from plugins.video_gen.fal import FALVideoGenProvider

        result = FALVideoGenProvider().generate(
            "bring the scene to life",
            model="happy-horse",
            image_url="https://example.com/frame.png",
            resolution="1080p",
        )
        assert result["success"] is True
        assert with_fake_fal["endpoint"] == "alibaba/happy-horse/image-to-video"
        assert with_fake_fal["arguments"]["image_url"] == "https://example.com/frame.png"
        assert with_fake_fal["arguments"]["resolution"] == "1080p"

    def test_seedance_reference_to_video_uses_image_urls(self, with_fake_fal):
        from plugins.video_gen.fal import FALVideoGenProvider

        result = FALVideoGenProvider().generate(
            "animate @Image1",
            model="seedance-2.0",
            reference_image_urls=["https://example.com/ref.png"],
        )
        assert result["success"] is True
        assert result["modality"] == "reference"
        assert with_fake_fal["endpoint"] == "bytedance/seedance-2.0/reference-to-video"
        assert with_fake_fal["arguments"]["image_urls"] == ["https://example.com/ref.png"]

    def test_local_reference_images_are_uploaded_before_reference_to_video(self, tmp_path, with_fake_fal):
        from plugins.video_gen.fal import FALVideoGenProvider

        ref = tmp_path / "ref.png"
        ref.write_bytes(_png_bytes(16, 9))

        result = FALVideoGenProvider().generate(
            "animate @Image1",
            model="seedance-2.0",
            reference_image_urls=[str(ref)],
        )
        assert result["success"] is True
        assert result["modality"] == "reference"
        assert with_fake_fal["arguments"]["image_urls"] == ["https://fake/uploads/ref.png"]

    def test_local_start_and_end_frames_are_uploaded_before_i2v(self, tmp_path, with_fake_fal):
        from plugins.video_gen.fal import FALVideoGenProvider

        start = tmp_path / "start.png"
        end = tmp_path / "end.png"
        start.write_bytes(_png_bytes(16, 9))
        end.write_bytes(_png_bytes(16, 9))

        result = FALVideoGenProvider().generate(
            "move from start to end frame",
            model="seedance-2.0",
            image_url=str(start),
            end_image_url=str(end),
            duration=5,
            aspect_ratio="16:9",
            resolution="720p",
        )
        assert result["success"] is True
        assert with_fake_fal["arguments"]["image_url"] == "https://fake/uploads/start.png"
        assert with_fake_fal["arguments"]["end_image_url"] == "https://fake/uploads/end.png"
        assert with_fake_fal["arguments"]["duration"] == 5

    def test_happy_horse_video_edit_endpoint(self, with_fake_fal):
        from plugins.video_gen.fal import FALVideoGenProvider

        result = FALVideoGenProvider().edit(
            "recolor the sky",
            model="happy-horse",
            video_url="https://example.com/source.mp4",
            resolution="1080p",
            reference_image_urls=["https://example.com/ref.png"],
        )
        assert result["success"] is True
        assert result["modality"] == "edit"
        assert with_fake_fal["endpoint"] == "alibaba/happy-horse/video-edit"
        assert with_fake_fal["arguments"]["video_url"] == "https://example.com/source.mp4"
        assert with_fake_fal["arguments"]["reference_image_urls"] == ["https://example.com/ref.png"]

    def test_local_video_edit_inputs_are_uploaded_before_edit(self, tmp_path, with_fake_fal):
        from plugins.video_gen.fal import FALVideoGenProvider

        source = tmp_path / "source.mp4"
        ref = tmp_path / "ref.png"
        source.write_bytes(b"fake mp4 bytes")
        ref.write_bytes(_png_bytes(16, 9))

        result = FALVideoGenProvider().edit(
            "recolor the sky",
            model="happy-horse",
            video_url=str(source),
            resolution="720p",
            reference_image_urls=[str(ref)],
        )
        assert result["success"] is True
        assert result["modality"] == "edit"
        assert with_fake_fal["arguments"]["video_url"] == "https://fake/uploads/source.mp4"
        assert with_fake_fal["arguments"]["reference_image_urls"] == ["https://fake/uploads/ref.png"]

    def test_grok_video_extend_endpoint(self, with_fake_fal):
        from plugins.video_gen.fal import FALVideoGenProvider

        result = FALVideoGenProvider().extend(
            "camera pulls back",
            model="grok-imagine-video",
            video_url="https://example.com/source.mp4",
            duration=30,
        )
        assert result["success"] is True
        assert result["modality"] == "extend"
        assert with_fake_fal["endpoint"] == "xai/grok-imagine-video/extend-video"
        assert with_fake_fal["arguments"]["duration"] == 10

    def test_veo_duration_payload_keeps_response_duration_numeric(self, with_fake_fal):
        from plugins.video_gen.fal import FALVideoGenProvider

        result = FALVideoGenProvider().generate(
            "a dog",
            model="veo3.1",
        )
        assert result["success"] is True
        assert with_fake_fal["arguments"]["duration"] == "4s"
        assert result["duration"] == 4


class TestPayloadBuilder:
    def test_drops_unsupported_keys(self):
        """Veo enum-clamps duration, supports aspect+resolution+audio+neg."""
        from plugins.video_gen.fal import FAL_FAMILIES, _build_payload

        meta = FAL_FAMILIES["veo3.1"]
        p = _build_payload(
            meta,
            prompt="x",
            image_url=None,
            duration=12,           # not in enum (4,6,8) — snap to 8
            aspect_ratio="16:9",
            resolution="720p",
            negative_prompt="ugly",
            audio=True,
            seed=42,
        )
        assert p["prompt"] == "x"
        assert p["duration"] == "8s"
        assert p["aspect_ratio"] == "16:9"
        assert p["resolution"] == "720p"
        assert p["generate_audio"] is True
        assert p["negative_prompt"] == "ugly"
        assert p["seed"] == 42

    def test_pixverse_range_clamps_correctly(self):
        from plugins.video_gen.fal import FAL_FAMILIES, _build_payload

        meta = FAL_FAMILIES["pixverse-v6"]
        p = _build_payload(
            meta,
            prompt="x",
            image_url="https://i.png",
            duration=99,        # over max → 15
            aspect_ratio="16:9",
            resolution="540p",
            negative_prompt=None,
            audio=None,
            seed=None,
        )
        assert p["duration"] == 15

    def test_kling_4k_clamps_below_min(self):
        from plugins.video_gen.fal import FAL_FAMILIES, _build_payload

        meta = FAL_FAMILIES["kling-v3-4k"]
        p = _build_payload(
            meta,
            prompt="x",
            image_url="https://i.png",
            duration=1,         # below min (3) → 3
            aspect_ratio="16:9",
            resolution="720p",
            negative_prompt=None,
            audio=None,
            seed=None,
        )
        assert p["duration"] == 3

    def test_ltx_omits_duration_aspect_resolution(self):
        """LTX 2.3 doesn't declare duration/aspect/resolution enums —
        the payload should NOT include those keys (let FAL default)."""
        from plugins.video_gen.fal import FAL_FAMILIES, _build_payload

        meta = FAL_FAMILIES["ltx-2.3"]
        p = _build_payload(
            meta,
            prompt="x",
            image_url=None,
            duration=8,
            aspect_ratio="16:9",
            resolution="720p",
            negative_prompt="ugly",
            audio=True,
            seed=None,
        )
        assert "duration" not in p
        assert "aspect_ratio" not in p
        assert "resolution" not in p
        # But audio + negative are advertised
        assert p["generate_audio"] is True
        assert p["negative_prompt"] == "ugly"

    def test_happy_horse_payload_uses_documented_controls(self):
        """Happy Horse docs now expose duration/aspect/resolution."""
        from plugins.video_gen.fal import FAL_FAMILIES, _build_payload

        meta = FAL_FAMILIES["happy-horse"]
        p = _build_payload(
            meta,
            prompt="a horse galloping",
            image_url=None,
            duration=8,
            aspect_ratio="16:9",
            resolution="720p",
            negative_prompt="watermark",
            audio=True,
            seed=None,
        )
        assert p == {
            "prompt": "a horse galloping",
            "duration": 8,
            "aspect_ratio": "16:9",
            "resolution": "720p",
        }
