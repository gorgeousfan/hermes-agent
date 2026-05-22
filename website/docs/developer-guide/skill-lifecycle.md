---
title: Skill Lifecycle Contracts
---

# Skill Lifecycle Contracts

Tier 5 makes skill rot visible without letting the agent quietly delete or rewrite the library.

The skill lifecycle surface is read-only by design. It inventories the skill library, registry, references, and promotion state, then returns metadata and issue codes only.

## Runtime surfaces

- Python: `agent.skill_lifecycle.audit_skill_lifecycle()`
- Harness facade: `HermesHarness().control_plane.skill_lifecycle()`
- Dashboard/API: `GET /api/harness/skill-lifecycle`

The response declares:

- `content_policy: metadata_only`
- `mode: audit_only_no_delete`

That mode is a contract: this audit is not permission to delete skills, rewrite skills, or promote drafts without explicit verification.

## What the audit checks

| Check | Issue code | Why it matters |
| --- | --- | --- |
| Duplicate frontmatter names | `skill_duplicate_names` | Skill lookup and future consolidation become ambiguous. |
| Missing required frontmatter | `skill_frontmatter_incomplete` | Skills need at least `name` and `description` for discovery and review. |
| Missing local markdown references | `skill_missing_reference` | Broken `references/`, `templates/`, or `scripts/` pointers make procedures non-replayable. |
| Escaping local references | `skill_reference_escapes_directory` | A skill should not require path traversal to explain itself. |
| Support files outside allowed folders | `skill_support_file_policy_violation` | Keeps skill directories structured: `references/`, `templates/`, `scripts/`, `assets/`. |
| Stale skill file mtime | `skill_stale` | Prompts review; does not prove the skill is wrong. |
| Promoted registry row without passing gate | `skill_promoted_without_gate` | Promotion must be backed by verification/offline-eval evidence. |

## Privacy contract

The audit must not return raw:

- skill names
- file paths
- skill bodies
- support file contents
- markdown link labels/targets
- registry evidence text

It may return counts, hashes, issue codes, byte/line totals, and promotion state counters.

## Promotion contract

A skill can be created or patched into draft state, but promotion requires verification evidence. The harness registry records skill mutations and promotion gate state; Tier 5 audits for rows that claim promotion without a passing gate.

This is separate from human curation. A stale, duplicate, or incomplete skill should be reported and reviewed, not automatically deleted.

## Verification

Focused Tier 5 gate:

```bash
scripts/run_tests.sh tests/agent/test_skill_lifecycle.py \
  tests/agent/test_hermes_harness.py::test_control_plane_harness_exposes_skill_lifecycle \
  tests/hermes_cli/test_web_server.py::TestWebServerEndpoints::test_harness_trace_replay_endpoints_are_content_safe -q
```

For broader harness changes, also run the harness control-plane and context-hygiene tests.
