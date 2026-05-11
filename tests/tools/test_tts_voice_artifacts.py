import json
import subprocess
from pathlib import Path

from tools import tts_tool


def _completed(stdout: str):
    return subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=0,
        stdout=stdout,
        stderr="",
    )


def test_mp3_payload_named_ogg_is_not_telegram_voice(monkeypatch, tmp_path):
    path = tmp_path / "reply.ogg"
    path.write_bytes(b"fake")

    monkeypatch.setattr(
        tts_tool.subprocess,
        "run",
        lambda *args, **kwargs: _completed(json.dumps({
            "format": {"format_name": "mp3"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "mp3",
                    "duration": "1.2",
                }
            ],
        })),
    )

    info = tts_tool._inspect_audio_file(str(path))

    assert info["container"] == "mp3"
    assert info["codec"] == "mp3"
    assert tts_tool._is_telegram_voice_artifact(str(path)) is False


def test_ogg_opus_is_telegram_voice(monkeypatch, tmp_path):
    path = tmp_path / "reply.ogg"
    path.write_bytes(b"fake")

    monkeypatch.setattr(
        tts_tool.subprocess,
        "run",
        lambda *args, **kwargs: _completed(json.dumps({
            "format": {"format_name": "ogg"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "opus",
                    "duration": "1.2",
                }
            ],
        })),
    )

    assert tts_tool._is_telegram_voice_artifact(str(path)) is True


def test_opus_extension_with_ogg_opus_payload_is_telegram_voice(monkeypatch, tmp_path):
    path = tmp_path / "reply.opus"
    path.write_bytes(b"fake")

    monkeypatch.setattr(
        tts_tool.subprocess,
        "run",
        lambda *args, **kwargs: _completed(json.dumps({
            "format": {"format_name": "ogg"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "opus",
                    "duration": "1.2",
                }
            ],
        })),
    )

    assert tts_tool._is_telegram_voice_artifact(str(path)) is True


def test_edge_requested_ogg_synthesizes_native_mp3_then_converts(monkeypatch, tmp_path):
    requested = tmp_path / "reply.ogg"
    generated_paths = []

    async def fake_generate_edge(text, output_path, config):
        generated_paths.append(Path(output_path).suffix)
        Path(output_path).write_bytes(b"mp3-bytes")
        return output_path

    def fake_convert(path):
        converted = tmp_path / "reply.voice.ogg"
        converted.write_bytes(b"ogg-opus")
        return str(converted)

    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {"provider": "edge"})
    monkeypatch.setattr(tts_tool, "_import_edge_tts", lambda: object())
    monkeypatch.setattr(tts_tool, "_generate_edge_tts", fake_generate_edge)
    monkeypatch.setattr(tts_tool, "_convert_to_opus", fake_convert)
    monkeypatch.setattr(
        tts_tool,
        "_is_telegram_voice_artifact",
        lambda path: path.endswith(".ogg"),
    )

    data = json.loads(tts_tool.text_to_speech_tool(text="你好", output_path=str(requested)))

    assert data["success"] is True
    assert data["voice_compatible"] is True
    assert data["file_path"].endswith(".voice.ogg")
    assert generated_paths == [".mp3"]


def test_default_edge_non_telegram_keeps_regular_mp3(monkeypatch, tmp_path):
    generated_paths = []

    async def fake_generate_edge(text, output_path, config):
        generated_paths.append(Path(output_path).suffix)
        Path(output_path).write_bytes(b"mp3-bytes")
        return output_path

    def fail_convert(path):
        raise AssertionError(f"unexpected voice conversion for {path}")

    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {"provider": "edge"})
    monkeypatch.setattr(tts_tool, "_import_edge_tts", lambda: object())
    monkeypatch.setattr(tts_tool, "_generate_edge_tts", fake_generate_edge)
    monkeypatch.setattr(tts_tool, "_convert_to_opus", fail_convert)
    monkeypatch.setattr(tts_tool, "_is_telegram_voice_artifact", lambda path: False)
    monkeypatch.setattr(
        "gateway.session_context.get_session_env",
        lambda name, default="": "",
    )

    data = json.loads(
        tts_tool.text_to_speech_tool(
            text="hello",
            output_path=str(tmp_path / "reply.mp3"),
        )
    )

    assert data["success"] is True
    assert data["voice_compatible"] is False
    assert data["file_path"].endswith(".mp3")
    assert ".voice.ogg" not in data["file_path"]
    assert generated_paths == [".mp3"]


def test_convert_to_opus_rejects_non_voice_artifact(monkeypatch, tmp_path):
    source = tmp_path / "reply.mp3"
    source.write_bytes(b"mp3")

    def fake_run(*args, **kwargs):
        (tmp_path / "reply.ogg").write_bytes(b"not-opus")
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(tts_tool, "_has_ffmpeg", lambda: True)
    monkeypatch.setattr(tts_tool.subprocess, "run", fake_run)
    monkeypatch.setattr(tts_tool, "_is_telegram_voice_artifact", lambda path: False)

    assert tts_tool._convert_to_opus(str(source)) is None


def test_convert_to_opus_uses_distinct_voice_artifact_for_ogg_input(monkeypatch, tmp_path):
    source = tmp_path / "reply.ogg"
    source.write_bytes(b"mp3-bytes")
    captured = {}

    def fake_run(args, *unused_args, **unused_kwargs):
        target = Path(args[-2])
        captured["source"] = args[2]
        captured["target"] = str(target)
        target.write_bytes(b"ogg-opus")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(tts_tool, "_has_ffmpeg", lambda: True)
    monkeypatch.setattr(tts_tool.subprocess, "run", fake_run)
    monkeypatch.setattr(
        tts_tool,
        "_is_telegram_voice_artifact",
        lambda path: Path(path).name == "reply.voice.ogg",
    )

    result = tts_tool._convert_to_opus(str(source))

    assert result == str(tmp_path / "reply.voice.ogg")
    assert captured == {
        "source": str(source),
        "target": str(tmp_path / "reply.voice.ogg"),
    }
