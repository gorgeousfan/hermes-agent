import json

from hermes_cli.memory_setup import _write_env_vars


def test_write_env_vars_removes_none_values(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "MEM0_HOST=https://mem0.example.com\nMEM0_API_KEY=keep\n",
        encoding="utf-8",
    )

    _write_env_vars(env_path, {"MEM0_HOST": None})

    assert env_path.read_text(encoding="utf-8") == "MEM0_API_KEY=keep\n"


def test_write_env_vars_seeds_env_example_when_missing(tmp_path):
    env_path = tmp_path / ".env"

    _write_env_vars(env_path, {"MEM0_API_KEY": "test-key"})

    text = env_path.read_text(encoding="utf-8")
    assert "# Hermes Agent Environment Configuration" in text
    assert "MEM0_API_KEY=test-key" in text
    assert "# MEM0_HOST=" in text


def test_write_env_vars_uncomments_matching_placeholders(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# MEM0_API_KEY=\n# MEM0_HOST=\n# OTHER_KEY=\n",
        encoding="utf-8",
    )

    _write_env_vars(env_path, {
        "MEM0_HOST": "https://mem0.example.com",
        "MEM0_API_KEY": "test-key",
    })

    assert env_path.read_text(encoding="utf-8") == (
        "MEM0_API_KEY=test-key\n"
        "MEM0_HOST=https://mem0.example.com\n"
        "# OTHER_KEY=\n"
    )


def test_mem0_host_setup_is_env_only(tmp_path, monkeypatch):
    from hermes_cli import memory_setup
    from plugins.memory.mem0 import Mem0MemoryProvider

    hermes_home = tmp_path / "hermes-home"
    prompts = iter([
        "https://mem0.example.com",
        "mem0-key",
        "tommy",
        "hermes",
    ])
    monkeypatch.setattr(memory_setup, "get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr(memory_setup, "_get_available_providers", lambda: [
        ("mem0", "API key / local", Mem0MemoryProvider())
    ])
    monkeypatch.setattr(memory_setup, "_curses_select", lambda *args, **kwargs: 0)
    monkeypatch.setattr(memory_setup, "_install_dependencies", lambda name: None)
    monkeypatch.setattr(memory_setup, "_prompt", lambda *args, **kwargs: next(prompts))
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"memory": {}})
    monkeypatch.setattr("hermes_cli.config.save_config", lambda cfg: None)

    memory_setup.cmd_setup(None)

    env_text = (hermes_home / ".env").read_text(encoding="utf-8")
    assert "MEM0_HOST=https://mem0.example.com\n" in env_text
    assert "MEM0_API_KEY=mem0-key\n" in env_text

    provider_cfg = json.loads((hermes_home / "mem0.json").read_text(encoding="utf-8"))
    assert "host" not in provider_cfg
    assert provider_cfg["user_id"] == "tommy"
    assert provider_cfg["agent_id"] == "hermes"
