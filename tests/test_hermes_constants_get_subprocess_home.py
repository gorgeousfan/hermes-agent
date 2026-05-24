"""Tests for hermes_constants.get_subprocess_home() — per-profile HOME for child procs."""
import os

from hermes_constants import get_subprocess_home


class TestGetSubprocessHome:
    def test_returns_none_when_hermes_home_unset(self, monkeypatch):
        monkeypatch.delenv("HERMES_HOME", raising=False)
        assert get_subprocess_home() is None

    def test_returns_none_when_home_subdir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        assert get_subprocess_home() is None

    def test_returns_path_when_home_subdir_exists(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        sub = tmp_path / "home"
        sub.mkdir()
        assert get_subprocess_home() == str(sub)

    def test_returns_none_when_home_is_a_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "home").write_text("not-a-dir")
        assert get_subprocess_home() is None

    def test_returns_string_not_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "home").mkdir()
        assert isinstance(get_subprocess_home(), str)

    def test_does_not_mutate_os_environ_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HOME", "/some/user/home")
        (tmp_path / "home").mkdir()
        get_subprocess_home()
        assert os.environ["HOME"] == "/some/user/home"

    def test_returns_none_for_empty_hermes_home(self, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", "")
        assert get_subprocess_home() is None
