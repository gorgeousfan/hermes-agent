from __future__ import annotations

from types import SimpleNamespace

from agent.silverbullet_context import build_silverbullet_context_prompt, _truncate


def _cfg(space, **overrides):
    context = {
        "enabled": True,
        "space_path": str(space) if space is not None else None,
        "max_chars": 2000,
        "include_activity": True,
        "include_indexes": True,
        "recent_daily_notes": 1,
    }
    context.update(overrides)
    return {"silverbullet": {"context": context}}


def test_disabled_returns_empty(tmp_path):
    cfg = _cfg(tmp_path, enabled=False)

    assert build_silverbullet_context_prompt(cfg) == ""


def test_missing_space_returns_empty(tmp_path):
    cfg = _cfg(tmp_path / "missing")

    assert build_silverbullet_context_prompt(cfg) == ""


def test_enabled_temp_space_includes_entrypoints_and_bullets(tmp_path):
    (tmp_path / "Services").mkdir()
    (tmp_path / "Projects").mkdir()
    (tmp_path / "_activity.md").write_text(
        "# Activity\n\n- [[Projects/Hermes]] tighten workflow context\nPlain paragraph ignored\n",
        encoding="utf-8",
    )
    (tmp_path / "_ops.md").write_text(
        "- Rotate backups\n[Runbook](Services/Backups.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "_projects.md").write_text(
        "- Hermes: SilverBullet prompt integration\n",
        encoding="utf-8",
    )
    (tmp_path / "_review.md").write_text(
        "- Review open PRs weekly\n",
        encoding="utf-8",
    )
    daily_dir = tmp_path / "Daily Notes"
    daily_dir.mkdir()
    (daily_dir / "2026-05-22.md").write_text(
        "# Daily\n\nLarge note body that should not be injected.\n\n"
        "## Carry Forward\n"
        "- Keep SilverBullet compact\n"
        "- [[Projects/Hermes]] follow-up\n"
        "## Later\n"
        "- Not included\n",
        encoding="utf-8",
    )

    block = build_silverbullet_context_prompt(_cfg(tmp_path))

    assert "# SilverBullet Workflow Context" in block
    assert "`_activity.md`" in block
    assert "`Services`" in block
    assert "- [[Projects/Hermes]] tighten workflow context" in block
    assert "- [Runbook](Services/Backups.md)" in block
    assert "## Carry Forward from `Daily Notes/2026-05-22.md`" in block
    assert "- Keep SilverBullet compact" in block
    assert "Large note body" not in block
    assert "Not included" not in block


def test_env_space_fallback_used_before_default(tmp_path, monkeypatch):
    monkeypatch.setenv("SILVERBULLET_SPACE", str(tmp_path))
    (tmp_path / "_activity.md").write_text("- Env fallback note\n", encoding="utf-8")

    block = build_silverbullet_context_prompt(_cfg(None))

    assert "- Env fallback note" in block


def test_activity_ignores_bullets_inside_fenced_code(tmp_path):
    (tmp_path / "_activity.md").write_text(
        "# Activity\n\n"
        "```markdown\n"
        "- [ ] Short next action\n"
        "- [[Projects/Template]] example link\n"
        "```\n\n"
        "- [[Projects/Hermes]] real next action\n",
        encoding="utf-8",
    )

    block = build_silverbullet_context_prompt(_cfg(tmp_path))

    assert "- [[Projects/Hermes]] real next action" in block
    assert "- [ ] Short next action" not in block
    assert "Projects/Template" not in block


def test_max_chars_truncates_with_marker(tmp_path):
    (tmp_path / "_activity.md").write_text(
        "\n".join(f"- Item {idx} {'x' * 40}" for idx in range(20)),
        encoding="utf-8",
    )

    block = build_silverbullet_context_prompt(_cfg(tmp_path, max_chars=260))

    assert len(block) <= 260
    assert "SilverBullet context truncated" in block


def test_truncate_prefers_line_boundary_before_marker():
    marker = "\n\n[SilverBullet context truncated to configured max_chars.]"
    text = "Intro\n\n## Complete Heading\n- complete line\n## Next Heading\n- later " + ("x" * 100)
    max_chars = len(marker) + len("Intro\n\n## Complete Heading\n- complete line\n## Next")

    block = _truncate(text, max_chars)

    assert block.endswith(marker)
    assert "- complete line" in block
    assert "## Next" not in block
    assert len(block) <= max_chars


def test_system_prompt_includes_silverbullet_block_when_enabled(monkeypatch):
    from agent import system_prompt

    fake_run_agent = SimpleNamespace(
        load_soul_md=lambda: "",
        build_nous_subscription_prompt=lambda _tools: "",
        build_environment_hints=lambda: "",
        build_context_files_prompt=lambda cwd=None, skip_soul=False: "PROJECT CONTEXT",
        build_skills_system_prompt=lambda **_kwargs: "",
        get_toolset_for_tool=lambda _tool: None,
        build_silverbullet_context_prompt=lambda: "SILVERBULLET BLOCK",
    )
    monkeypatch.setattr(system_prompt, "_ra", lambda: fake_run_agent)
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    agent = SimpleNamespace(
        load_soul_identity=False,
        skip_context_files=False,
        valid_tool_names=[],
        _kanban_worker_guidance=None,
        provider="",
        model="",
        platform="",
        _tool_use_enforcement="auto",
        _memory_store=None,
        _memory_enabled=False,
        _user_profile_enabled=False,
        _memory_manager=None,
        pass_session_id=False,
        session_id="",
    )

    parts = system_prompt.build_system_prompt_parts(agent)

    assert "PROJECT CONTEXT" in parts["context"]
    assert "SILVERBULLET BLOCK" in parts["context"]
    assert parts["context"].index("PROJECT CONTEXT") < parts["context"].index("SILVERBULLET BLOCK")
