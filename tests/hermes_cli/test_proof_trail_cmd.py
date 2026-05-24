import json
from pathlib import Path
from types import SimpleNamespace

from hermes_cli.proof_trail_cmd import (
    build_proof_markdown,
    create_proof_record,
    default_proof_dir,
    proof_trail_command,
    slugify_title,
)


def test_default_proof_dir_uses_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert default_proof_dir() == tmp_path / "proofs"


def test_slugify_title_is_filesystem_safe_and_stable():
    assert slugify_title("Agent Run: Validation Proof!") == "agent-run-validation-proof"
    assert slugify_title("  ") == "proof"


def test_build_proof_markdown_contains_required_sections():
    markdown = build_proof_markdown(
        title="Agent Run Proof Trail",
        status="validated",
        rationale="Need durable proof for autonomous actions.",
        inputs=["handoff.md", "plan.md"],
        files=["hermes_cli/proof_trail_cmd.py"],
        commands=["pytest tests/hermes_cli/test_proof_trail_cmd.py -q"],
        validations=["5 passed"],
        related=["ticket-123"],
        final_state="Ready for future tasks.",
        references=["session:abc"],
        timestamp="2026-04-24T20:00:00+00:00",
    )

    for heading in [
        "## Rationale",
        "## Inputs",
        "## Files Changed",
        "## Commands / Evidence",
        "## Validation",
        "## Related Artifacts",
        "## References",
        "## Final State",
    ]:
        assert heading in markdown
    assert "status: \"validated\"" in markdown
    assert "- `hermes_cli/proof_trail_cmd.py`" in markdown
    assert "```bash\npytest tests/hermes_cli/test_proof_trail_cmd.py -q\n```" in markdown


def test_create_proof_record_writes_markdown_and_json_index(tmp_path):
    result = create_proof_record(
        title="Autonomy Monitor Implementation",
        status="validated",
        rationale="Record proof trail.",
        inputs=["handoff"],
        files=["script.py"],
        commands=["pytest -q"],
        validations=["passed"],
        related=["health-check"],
        final_state="done",
        references=["summary-node-3"],
        output_dir=tmp_path,
        timestamp="2026-04-24T20:00:00+00:00",
    )

    proof_path = Path(result["path"])
    index_path = tmp_path / "proof-index.json"
    assert proof_path.exists()
    assert proof_path.name == "2026-04-24-autonomy-monitor-implementation.md"
    assert index_path.exists()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["proofs"][0]["title"] == "Autonomy Monitor Implementation"
    assert index["proofs"][0]["status"] == "validated"


def test_proof_trail_command_outputs_json(tmp_path, capsys):
    args = SimpleNamespace(
        title="Proof CLI Smoke",
        status="validated",
        rationale="Smoke proof command.",
        inputs=["input-a"],
        files=["file-a"],
        commands=["cmd-a"],
        validations=["ok"],
        related=["artifact-a"],
        final_state="complete",
        references=["ref-a"],
        output_dir=str(tmp_path),
        json=True,
    )

    proof_trail_command(args, timestamp="2026-04-24T20:00:00+00:00")

    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is True
    assert Path(payload["path"]).exists()
