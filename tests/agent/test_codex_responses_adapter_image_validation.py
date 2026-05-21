"""Tests for inline data:image/* magic-byte validation in the Codex Responses adapter.

Regression coverage for issue #29711: a Discord attachment misclassified as an
``input_image`` could embed non-image bytes (e.g. a .docx ZIP starting with
``PK\\x03\\x04``) under a ``data:image/jpeg;base64,...`` URL.  The Responses
provider then 400'd the entire request, poisoning all sibling valid images.
The adapter must now drop such mismatched parts (with a warning) while
preserving valid inline images, remote URLs, and unknown image subtypes.
"""

from __future__ import annotations

import base64
import logging

import pytest

from agent.codex_responses_adapter import (
    _chat_content_to_responses_parts,
    _is_valid_inline_image_data_url,
)


def _data_url(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
GIF_BYTES = b"GIF89a\x01\x00\x01\x00"
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBPVP8 "
ZIP_BYTES = b"PK\x03\x04\x14\x00\x00\x00word/numbering.xml"


# ---------------------------------------------------------------------------
# Unit tests for the guard helper
# ---------------------------------------------------------------------------


def test_valid_jpeg_data_url_passes():
    assert _is_valid_inline_image_data_url(_data_url("image/jpeg", JPEG_BYTES)) is True


def test_valid_png_data_url_passes():
    assert _is_valid_inline_image_data_url(_data_url("image/png", PNG_BYTES)) is True


def test_valid_gif_data_url_passes():
    assert _is_valid_inline_image_data_url(_data_url("image/gif", GIF_BYTES)) is True


def test_valid_webp_data_url_passes():
    assert _is_valid_inline_image_data_url(_data_url("image/webp", WEBP_BYTES)) is True


def test_jpeg_declared_with_zip_payload_is_dropped(caplog):
    url = _data_url("image/jpeg", ZIP_BYTES)
    with caplog.at_level(logging.WARNING, logger="agent.codex_responses_adapter"):
        assert _is_valid_inline_image_data_url(url) is False
    assert any("mismatched magic bytes" in r.message for r in caplog.records)


def test_png_declared_with_random_bytes_is_dropped(caplog):
    url = _data_url("image/png", b"not a real png at all")
    with caplog.at_level(logging.WARNING, logger="agent.codex_responses_adapter"):
        assert _is_valid_inline_image_data_url(url) is False
    assert any("mismatched magic bytes" in r.message for r in caplog.records)


def test_webp_declared_with_zip_payload_is_dropped(caplog):
    url = _data_url("image/webp", ZIP_BYTES)
    with caplog.at_level(logging.WARNING, logger="agent.codex_responses_adapter"):
        assert _is_valid_inline_image_data_url(url) is False


def test_remote_http_url_passes_through():
    assert _is_valid_inline_image_data_url("https://example.com/cat.png") is True


def test_unknown_image_subtype_passes_through():
    # We don't track heic magic — must not over-aggressively drop it.
    url = _data_url("image/heic", b"\x00\x00\x00\x20ftypheic")
    assert _is_valid_inline_image_data_url(url) is True


def test_malformed_data_url_is_dropped(caplog):
    with caplog.at_level(logging.WARNING, logger="agent.codex_responses_adapter"):
        assert _is_valid_inline_image_data_url("data:image/jpeg;notbase64") is False


def test_empty_url_is_dropped():
    assert _is_valid_inline_image_data_url("") is False


# ---------------------------------------------------------------------------
# Integration: full conversion pipeline
# ---------------------------------------------------------------------------


def test_mixed_attachments_drops_only_invalid(caplog):
    good_jpeg = _data_url("image/jpeg", JPEG_BYTES)
    good_png = _data_url("image/png", PNG_BYTES)
    bad_docx_as_jpeg = _data_url("image/jpeg", ZIP_BYTES)

    content = [
        {"type": "text", "text": "look at these"},
        {"type": "image_url", "image_url": {"url": good_jpeg}},
        {"type": "image_url", "image_url": {"url": bad_docx_as_jpeg}},
        {"type": "image_url", "image_url": {"url": good_png}},
    ]

    with caplog.at_level(logging.WARNING, logger="agent.codex_responses_adapter"):
        parts = _chat_content_to_responses_parts(content, role="user")

    image_parts = [p for p in parts if p.get("type") == "input_image"]
    urls = [p["image_url"] for p in image_parts]
    assert urls == [good_jpeg, good_png], (
        "Bad image must be dropped while preserving order of the valid ones."
    )
    assert any("mismatched magic bytes" in r.message for r in caplog.records)


def test_remote_image_url_preserved_in_pipeline():
    content = [
        {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
    ]
    parts = _chat_content_to_responses_parts(content, role="user")
    image_parts = [p for p in parts if p.get("type") == "input_image"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"] == "https://example.com/x.png"
