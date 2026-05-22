import json
import os
from datetime import datetime, timedelta, timezone

from agent.skill_lifecycle import audit_skill_lifecycle


def test_skill_lifecycle_audit_detects_rot_without_raw_content(tmp_path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    skills = hermes_home / "skills"

    good = skills / "local" / "good-skill"
    good.mkdir(parents=True)
    (good / "references").mkdir()
    (good / "references" / "api.md").write_text("private reference body")
    (good / "SKILL.md").write_text(
        "---\nname: shared-skill\ndescription: Good skill\nversion: 1.0.0\n---\n"
        "# Good\nSee [api](references/api.md).\n"
    )

    bad = skills / "local" / "bad-skill"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text(
        "---\nname: shared-skill\n---\n# Bad\nSee [private](references/private-plan.md).\n"
    )
    (bad / "scratch.txt").write_text("raw scratch note")
    old = datetime.now(timezone.utc) - timedelta(days=400)
    os.utime(bad / "SKILL.md", (old.timestamp(), old.timestamp()))

    harness = hermes_home / "harness"
    harness.mkdir()
    (harness / "skill-registry.json").write_text(json.dumps({
        "schema_version": 1,
        "skills": {
            "shared-skill": {
                "status": "promoted",
                "promotion_status": "verified",
                "promotion_gate_status": "blocked",
            }
        },
    }))

    summary = audit_skill_lifecycle(
        hermes_home=hermes_home,
        now=datetime.now(timezone.utc),
        stale_days=180,
    )

    assert summary["content_policy"] == "metadata_only"
    assert summary["skill_count"] == 2
    assert summary["duplicate_name_count"] == 1
    assert summary["invalid_frontmatter_count"] == 1
    assert summary["missing_reference_count"] == 1
    assert summary["support_file_violation_count"] == 1
    assert summary["stale_skill_count"] == 1
    assert summary["promotion"]["promoted_without_gate_count"] == 1

    codes = {issue["code"] for issue in summary["issues"]}
    assert {
        "skill_duplicate_names",
        "skill_frontmatter_incomplete",
        "skill_missing_reference",
        "skill_support_file_policy_violation",
        "skill_stale",
        "skill_promoted_without_gate",
    } <= codes

    raw = json.dumps(summary, sort_keys=True)
    assert "private-plan" not in raw
    assert "raw scratch note" not in raw
    assert "shared-skill" not in raw
    assert str(skills) not in raw


def test_skill_lifecycle_empty_library_is_structural(tmp_path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()

    summary = audit_skill_lifecycle(hermes_home=hermes_home)

    assert summary["skill_count"] == 0
    assert summary["issues"] == []
    assert summary["promotion"]["registered_count"] == 0
