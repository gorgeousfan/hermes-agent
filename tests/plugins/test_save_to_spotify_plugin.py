from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from hermes_cli.plugins import PluginManager
from plugins.save_to_spotify import tools as save_tools


def _completed(stdout: str, *, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["save-to-spotify"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_plugin_manifest_exists_and_is_bundled_backend() -> None:
    plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "save_to_spotify"
    manifest_path = plugin_dir / "plugin.yaml"
    assert manifest_path.exists()

    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert data["name"] == "save_to_spotify"
    assert data["kind"] == "backend"
    assert data["provides_tools"] == [
        "save_to_spotify_upload",
        "save_to_spotify_shows",
        "save_to_spotify_episodes",
        "save_to_spotify_timeline",
    ]


def test_plugin_manager_discovers_bundled_save_to_spotify_backend() -> None:
    mgr = PluginManager()
    mgr.discover_and_load()

    assert "save_to_spotify" in mgr._plugins
    loaded = mgr._plugins["save_to_spotify"]
    assert loaded.manifest.source == "bundled"
    assert loaded.manifest.kind == "backend"
    assert loaded.enabled is True, loaded.error


def test_missing_binary_returns_install_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(save_tools.shutil, "which", lambda _: None)

    payload = json.loads(
        save_tools.handle_save_to_spotify_shows({"action": "list"})
    )

    assert "error" in payload
    assert "not installed" in payload["error"]
    assert "hermes auth save-to-spotify" in payload["error"]


def test_subprocess_success_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(save_tools.shutil, "which", lambda _: "/usr/local/bin/save-to-spotify")
    monkeypatch.setattr(
        save_tools.subprocess,
        "run",
        lambda *args, **kwargs: _completed('{"shows":[{"title":"Daily Briefings"}]}'),
    )

    payload = json.loads(save_tools.handle_save_to_spotify_shows({"action": "list"}))

    assert payload["shows"][0]["title"] == "Daily Briefings"


def test_subprocess_json_error_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(save_tools.shutil, "which", lambda _: "/usr/local/bin/save-to-spotify")
    monkeypatch.setattr(
        save_tools.subprocess,
        "run",
        lambda *args, **kwargs: _completed('{"error":"show not found"}', returncode=1),
    )

    payload = json.loads(
        save_tools.handle_save_to_spotify_shows({"action": "get", "show_id": "spotify:show:abc"})
    )

    assert payload == {"error": "show not found"}


def test_auth_missing_path_maps_to_login_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(save_tools.shutil, "which", lambda _: "/usr/local/bin/save-to-spotify")
    monkeypatch.setattr(
        save_tools.subprocess,
        "run",
        lambda *args, **kwargs: _completed(
            '{"error":"not authenticated - run save-to-spotify auth login"}',
            returncode=1,
        ),
    )

    payload = json.loads(save_tools.handle_save_to_spotify_shows({"action": "list"}))

    assert payload["error"] == (
        "Save to Spotify is not authenticated. Run `hermes auth save-to-spotify` "
        "(or `save-to-spotify auth login`) first."
    )


def test_file_path_validation_failures(tmp_path: Path) -> None:
    missing = tmp_path / "missing.mp3"
    payload = json.loads(
        save_tools.handle_save_to_spotify_upload(
            {"file_path": str(missing), "title": "Episode"}
        )
    )
    assert "does not exist" in payload["error"]


def test_image_path_validation_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    media = tmp_path / "episode.mp3"
    media.write_bytes(b"audio")
    payload = json.loads(
        save_tools.handle_save_to_spotify_upload(
            {
                "file_path": str(media),
                "title": "Episode",
                "image_path": str(tmp_path / "missing.png"),
            }
        )
    )
    assert "image_path does not exist" in payload["error"]


def test_upload_wait_true_without_wait_timeout_uses_bare_wait(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media = tmp_path / "episode.mp3"
    media.write_bytes(b"audio")
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["timeout"] = kwargs["timeout"]
        return _completed('{"episode_uri":"spotify:episode:abc"}')

    monkeypatch.setattr(save_tools.shutil, "which", lambda _: "/usr/local/bin/save-to-spotify")
    monkeypatch.setattr(save_tools.subprocess, "run", fake_run)

    payload = json.loads(
        save_tools.handle_save_to_spotify_upload(
            {"file_path": str(media), "title": "Episode", "wait": True}
        )
    )

    assert payload["episode_uri"] == "spotify:episode:abc"
    assert seen["cmd"] == [
        "/usr/local/bin/save-to-spotify",
        "--json",
        "upload",
        str(media),
        "--title",
        "Episode",
        "--wait",
    ]
    assert seen["timeout"] == 330


def test_upload_wait_true_with_wait_timeout_passes_duration_and_buffer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media = tmp_path / "episode.mp3"
    media.write_bytes(b"audio")
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["timeout"] = kwargs["timeout"]
        return _completed('{"episode_uri":"spotify:episode:abc"}')

    monkeypatch.setattr(save_tools.shutil, "which", lambda _: "/usr/local/bin/save-to-spotify")
    monkeypatch.setattr(save_tools.subprocess, "run", fake_run)

    json.loads(
        save_tools.handle_save_to_spotify_upload(
            {
                "file_path": str(media),
                "title": "Episode",
                "wait": True,
                "wait_timeout": "2m",
            }
        )
    )

    assert seen["cmd"] == [
        "/usr/local/bin/save-to-spotify",
        "--json",
        "upload",
        str(media),
        "--title",
        "Episode",
        "--wait",
        "2m",
    ]
    assert seen["timeout"] == 150


def test_timeline_temp_file_creation_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(save_tools.shutil, "which", lambda _: "/usr/local/bin/save-to-spotify")
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        timeline_path = Path(cmd[-1])
        seen["cmd"] = cmd
        seen["path"] = timeline_path
        seen["contents"] = json.loads(timeline_path.read_text(encoding="utf-8"))
        return _completed('{"ok":true}')

    monkeypatch.setattr(save_tools.subprocess, "run", fake_run)

    payload = json.loads(
        save_tools.handle_save_to_spotify_timeline(
            {
                "action": "set",
                "episode_id": "spotify:episode:abc",
                "timeline": {"items": [{"chapter": {"title": "Intro", "start_time_ms": 0}}]},
            }
        )
    )

    assert payload["ok"] is True
    assert seen["cmd"][:5] == [
        "/usr/local/bin/save-to-spotify",
        "--json",
        "timeline",
        "set",
        "--episode-id",
    ]
    assert seen["contents"] == {"items": [{"chapter": {"title": "Intro", "start_time_ms": 0}}]}
    assert not Path(seen["path"]).exists()


def test_timeline_spotify_entity_requires_prefix() -> None:
    payload = json.loads(
        save_tools.handle_save_to_spotify_timeline(
            {
                "action": "set",
                "episode_id": "spotify:episode:abc",
                "timeline": {
                    "items": [
                        {"spotify_entity": {"start_time_ms": 1000, "uri": "https://open.spotify.com/track/abc"}}
                    ]
                },
            }
        )
    )

    assert payload["error"] == "timeline spotify_entity.uri must use a full `spotify:...` URI"


def test_representative_command_mapping(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    media = tmp_path / "episode.mp3"
    media.write_bytes(b"audio")
    image = tmp_path / "cover.png"
    image.write_bytes(b"png")
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return _completed('{"ok":true}')

    monkeypatch.setattr(save_tools.shutil, "which", lambda _: "/usr/local/bin/save-to-spotify")
    monkeypatch.setattr(save_tools.subprocess, "run", fake_run)

    json.loads(
        save_tools.handle_save_to_spotify_upload(
            {
                "file_path": str(media),
                "title": "Episode",
                "show_id": "spotify:show:abc",
                "summary": "sum",
                "image_path": str(image),
                "language": "tr",
            }
        )
    )
    json.loads(save_tools.handle_save_to_spotify_shows({"action": "create", "title": "Show", "summary": "desc"}))
    json.loads(save_tools.handle_save_to_spotify_episodes({"action": "status", "episode_id": "spotify:episode:def"}))
    json.loads(save_tools.handle_save_to_spotify_timeline({"action": "get", "episode_id": "spotify:episode:def"}))

    assert seen == [
        [
            "/usr/local/bin/save-to-spotify",
            "--json",
            "upload",
            str(media),
            "--title",
            "Episode",
            "--show-id",
            "spotify:show:abc",
            "--summary",
            "sum",
            "--image",
            str(image),
            "--language",
            "tr",
        ],
        [
            "/usr/local/bin/save-to-spotify",
            "--json",
            "shows",
            "create",
            "--title",
            "Show",
            "--summary",
            "desc",
        ],
        [
            "/usr/local/bin/save-to-spotify",
            "--json",
            "episodes",
            "status",
            "spotify:episode:def",
        ],
        [
            "/usr/local/bin/save-to-spotify",
            "--json",
            "timeline",
            "get",
            "spotify:episode:def",
        ],
    ]


def test_subprocess_timeout_maps_to_hard_hang_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media = tmp_path / "episode.mp3"
    media.write_bytes(b"audio")
    monkeypatch.setattr(save_tools.shutil, "which", lambda _: "/usr/local/bin/save-to-spotify")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(save_tools.subprocess, "run", fake_run)

    payload = json.loads(
        save_tools.handle_save_to_spotify_upload(
            {"file_path": str(media), "title": "Episode", "wait": True}
        )
    )

    assert "system timeout" in payload["error"]
