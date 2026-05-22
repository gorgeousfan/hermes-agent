"""Hermes orchestration contract helpers.

This module centralises the live v1 workflow docs so runtime code can
load, validate, and render them instead of relying on static markdown
only.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import httpx

from hermes_constants import get_hermes_home

ORCHESTRATION_PROFILE_NAMES = frozenset(
    {"orchestrator_os", "architect_os", "dev_os", "audit_os"}
)
ORCHESTRATION_DOC_FILENAMES = (
    "claim-flow-v1.md",
    "handoff-contract-v1.md",
    "result-format-v1.md",
)
BRIEFING_DOC_FILENAMES = (
    "briefing-protocol-v1.md",
    "briefing-template-v1.md",
)
RESULT_STATUS_VALUES = frozenset({"done", "blocked", "needs_review", "failed"})

HANDOFF_REQUIRED_FIELDS = (
    "assigned_from",
    "assigned_to",
    "role_purpose",
    "objective",
    "context",
    "scope",
    "non_goals",
    "constraints",
    "expected_output",
    "stop_conditions",
    "blockers",
)
RESULT_REQUIRED_FIELDS = (
    "status",
    "summary",
    "decisions",
    "files_or_artifacts",
    "tests_or_checks",
    "risks",
    "next_step",
    "blockers",
)

ROLE_ROUTE_KEYWORDS = {
    "orchestrator_os": (
        "orchestrate", "routing", "route", "handoff", "claim", "sequence",
        "delegate", "intake", "coordination", "workflow", "pipeline",
        "assign", "dispatch", "triage",
    ),
    "architect_os": (
        "architect", "architecture", "design", "spec", "decompose",
        "decomposition", "contract", "plan", "blueprint", "tradeoff",
        "schema", "interface", "boundary", "proposal",
    ),
    "dev_os": (
        "implement", "implementation", "build", "code", "bug", "fix",
        "debug", "refactor", "wire", "integration", "test", "patch",
        "ship", "deliver",
    ),
    "audit_os": (
        "audit", "review", "verify", "verification", "qa", "quality",
        "regression", "test", "validate", "approval", "approve",
        "check", "smoke", "critique",
    ),
}
ROLE_PURPOSE_BY_ASSIGNEE = {
    "orchestrator_os": "routing and sequencing",
    "architect_os": "design and decomposition",
    "dev_os": "implementation",
    "audit_os": "verification and review",
}
WORKFLOW_TEMPLATE_ID = "architect-dev-audit-v1"
WORKFLOW_TEMPLATE_STEPS = (
    ("architect", "architect_os"),
    ("dev", "dev_os"),
    ("audit", "audit_os"),
)
WORKFLOW_TEMPLATE_STEP_INDEX = {
    step_key: index for index, (step_key, _assignee) in enumerate(WORKFLOW_TEMPLATE_STEPS)
}
WORKFLOW_TEMPLATE_STEP_ASSIGNEE = {
    step_key: assignee for step_key, assignee in WORKFLOW_TEMPLATE_STEPS
}
WORKFLOW_TEMPLATE_ASSIGNEE_STEP = {
    assignee: step_key for step_key, assignee in WORKFLOW_TEMPLATE_STEPS
}
TOGETHER_CLASSIFIER_MODEL = "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"
TOGETHER_CLASSIFIER_URL = "https://api.together.xyz/v1/chat/completions"


def is_orchestration_profile(profile_name: Optional[str]) -> bool:
    if not profile_name:
        return False
    return str(profile_name).strip().casefold() in ORCHESTRATION_PROFILE_NAMES


def load_orchestration_docs(*, include_missing: bool = False) -> str:
    """Load the canonical orchestration docs from ``~/.hermes/orchestration``.

    The caller can inject this into prompts or worker context so the live
    runtime follows the same contracts that live on disk.
    """
    root = get_hermes_home() / "orchestration"
    sections: list[str] = []
    for filename in ORCHESTRATION_DOC_FILENAMES:
        path = root / filename
        if not path.exists():
            if include_missing:
                sections.append(f"## {filename}\n\n[missing: {path}]")
            continue
        try:
            content = path.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if content:
            sections.append(f"## {filename}\n\n{content}")
    if not sections:
        return ""
    return "# Hermes orchestration contracts\n\n" + "\n\n".join(sections)


def load_briefing_docs(*, include_missing: bool = False) -> str:
    """Load the canonical briefing protocol docs from ``~/.hermes/orchestration``."""
    root = get_hermes_home() / "orchestration"
    sections: list[str] = []
    for filename in BRIEFING_DOC_FILENAMES:
        path = root / filename
        if not path.exists():
            if include_missing:
                sections.append(f"## {filename}\n\n[missing: {path}]")
            continue
        try:
            content = path.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if content:
            sections.append(f"## {filename}\n\n{content}")
    if not sections:
        return ""
    return "# Hermes briefing contracts\n\n" + "\n\n".join(sections)


def validate_handoff_packet(packet: Any, *, assignee: Optional[str] = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(packet, dict):
        return [f"handoff must be an object/dict, got {type(packet).__name__}"]

    for field in HANDOFF_REQUIRED_FIELDS:
        value = packet.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"handoff is missing required field: {field}")

    for field in ("context", "scope", "non_goals", "constraints", "stop_conditions", "blockers"):
        value = packet.get(field)
        if value is None:
            continue
        if not isinstance(value, (list, tuple)):
            errors.append(f"handoff field {field!r} must be a list")

    for field in ("freshness", "expiry"):
        if packet.get(field):
            break
    else:
        errors.append("handoff must include expiry/freshness")

    confidence = packet.get("confidence")
    if confidence is None or (isinstance(confidence, str) and not confidence.strip()):
        errors.append("handoff is missing required field: confidence")

    if assignee:
        assigned_to = str(packet.get("assigned_to") or "").strip()
        if assigned_to and assigned_to != str(assignee).strip():
            errors.append(
                f"handoff.assigned_to {assigned_to!r} does not match assignee {assignee!r}"
            )
    return errors


def _keyword_route_scores(text: str) -> dict[str, int]:
    haystack = (text or "").casefold()
    scored: dict[str, int] = {role: 0 for role in ROLE_ROUTE_KEYWORDS}
    for role, keywords in ROLE_ROUTE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in haystack:
                scored[role] += 1
    return scored


def _best_keyword_role(text: str, *, fallback: str = "orchestrator_os") -> tuple[str, int, int]:
    scored = _keyword_route_scores(text)
    best_role = fallback
    best_score = 0
    second_best = 0
    for role in ("orchestrator_os", "architect_os", "dev_os", "audit_os"):
        score = scored.get(role, 0)
        if score > best_score:
            second_best = best_score
            best_score = score
            best_role = role
        elif score > second_best:
            second_best = score
    return best_role, best_score, second_best


def _parse_together_classifier_response(content: str) -> tuple[Optional[str], float]:
    raw = (content or "").strip()
    if not raw:
        return None, 0.0
    try:
        payload = json.loads(raw)
    except Exception:
        payload = None
    if isinstance(payload, dict):
        role = payload.get("role") or payload.get("label") or payload.get("class")
        confidence = payload.get("confidence") or payload.get("score") or 0.0
        try:
            confidence_f = float(confidence)
        except Exception:
            confidence_f = 0.0
        role_text = str(role or "").strip()
        if role_text in ORCHESTRATION_PROFILE_NAMES:
            return role_text, confidence_f
    for role in ("orchestrator_os", "architect_os", "dev_os", "audit_os"):
        if re.search(rf"\b{re.escape(role)}\b", raw):
            return role, 0.5
    return None, 0.0


@lru_cache(maxsize=256)
def _classify_with_together(text: str, model: str, api_key: str) -> tuple[Optional[str], float]:
    prompt = (
        "You route Hermes tasks into exactly one role. "
        "Choose the best single label for the task and return only JSON. "
        "Labels: orchestrator_os, architect_os, dev_os, audit_os. "
        "Use orchestrator_os for routing/coordination, architect_os for design/specs, "
        "dev_os for implementation/code/debugging, and audit_os for verification/review/test.\n\n"
        f"Task text:\n{text.strip()}\n"
    )
    try:
        response = httpx.post(
            TOGETHER_CLASSIFIER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "Return JSON with fields role and confidence."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": 64,
            },
            timeout=15.0,
        )
        response.raise_for_status()
        data = response.json()
        choice0 = (data or {}).get("choices", [{}])[0]
        message = choice0.get("message") or {}
        content = message.get("content") or ""
        role, confidence = _parse_together_classifier_response(content)
        return role, confidence
    except Exception:
        return None, 0.0


def is_workflow_template_id(template_id: Optional[str]) -> bool:
    return bool(template_id) and str(template_id).strip() == WORKFLOW_TEMPLATE_ID


def workflow_template_first_step(template_id: Optional[str]) -> Optional[tuple[str, str]]:
    if not is_workflow_template_id(template_id):
        return None
    return WORKFLOW_TEMPLATE_STEPS[0]


def workflow_template_next_step(
    template_id: Optional[str],
    current_step_key: Optional[str],
) -> Optional[tuple[str, str]]:
    if not is_workflow_template_id(template_id):
        return None
    step_key = str(current_step_key or "").strip()
    if not step_key:
        return None
    index = WORKFLOW_TEMPLATE_STEP_INDEX.get(step_key)
    if index is None:
        return None
    if index + 1 >= len(WORKFLOW_TEMPLATE_STEPS):
        return None
    return WORKFLOW_TEMPLATE_STEPS[index + 1]


def workflow_template_step_assignee(step_key: Optional[str]) -> Optional[str]:
    if not step_key:
        return None
    return WORKFLOW_TEMPLATE_STEP_ASSIGNEE.get(str(step_key).strip())


def workflow_template_step_for_assignee(assignee: Optional[str]) -> Optional[str]:
    if not assignee:
        return None
    return WORKFLOW_TEMPLATE_ASSIGNEE_STEP.get(str(assignee).strip())


def infer_workflow_template(text: str) -> Optional[str]:
    """Return a workflow template id when the task clearly spans stages."""
    normalized = " ".join((text or "").split()).strip()
    if not normalized:
        return None
    scores = {
        step_key: sum(1 for keyword in keywords if keyword in normalized.casefold())
        for step_key, keywords in {
            "architect": ROLE_ROUTE_KEYWORDS["architect_os"],
            "dev": ROLE_ROUTE_KEYWORDS["dev_os"],
            "audit": ROLE_ROUTE_KEYWORDS["audit_os"],
        }.items()
    }
    active = [step for step, score in scores.items() if score > 0]
    if any(phrase in normalized.casefold() for phrase in ("end to end", "end-to-end", "design and build", "build and review", "implement and verify", "ship and verify", "design, build", "design build review")):
        return WORKFLOW_TEMPLATE_ID
    if len(active) >= 3:
        return WORKFLOW_TEMPLATE_ID
    if len(active) == 2:
        ordered = sorted(scores.values(), reverse=True)
        if ordered[0] == ordered[1]:
            return WORKFLOW_TEMPLATE_ID
    return None


def infer_orchestration_route(text: str, *, fallback: str = "orchestrator_os") -> dict[str, Any]:
    """Classify a task into a role and optional workflow template."""
    normalized = " ".join((text or "").split()).strip()
    if not normalized:
        return {
            "assignee": fallback,
            "workflow_template_id": None,
            "current_step_key": None,
            "confidence": 0.0,
        }

    workflow_template_id = infer_workflow_template(normalized)
    if workflow_template_id:
        stage = None
        cf = normalized.casefold()
        for candidate in ("architect", "dev", "audit"):
            if any(keyword in cf for keyword in ROLE_ROUTE_KEYWORDS[f"{candidate}_os"]):
                stage = candidate
                break
        if stage is None:
            stage = "architect"
        return {
            "assignee": WORKFLOW_TEMPLATE_STEP_ASSIGNEE[stage],
            "workflow_template_id": workflow_template_id,
            "current_step_key": stage,
            "confidence": 0.6,
        }

    best_role, best_score, second_best = _best_keyword_role(normalized, fallback=fallback)

    # Clear keyword hits are cheap and usually right; reserve the API call for
    # ambiguous phrasing where the keyword signal is weak or tied.
    if best_score >= 2 and (best_score - second_best) >= 1:
        return {
            "assignee": best_role,
            "workflow_template_id": None,
            "current_step_key": None,
            "confidence": 0.9,
        }

    api_key = os.environ.get("TOGETHER_API_KEY", "").strip()
    if api_key:
        model = os.environ.get("TOGETHER_CLASSIFIER_MODEL", TOGETHER_CLASSIFIER_MODEL).strip()
        if model:
            together_role, confidence = _classify_with_together(normalized, model, api_key)
            if together_role in ORCHESTRATION_PROFILE_NAMES and (
                confidence >= 0.45 or best_score == 0 or together_role != fallback
            ):
                return {
                    "assignee": together_role,
                    "workflow_template_id": None,
                    "current_step_key": None,
                    "confidence": confidence,
                }

    return {
        "assignee": best_role if best_score > 0 else fallback,
        "workflow_template_id": None,
        "current_step_key": None,
        "confidence": 0.5 if best_score > 0 else 0.0,
    }


def infer_orchestration_role(text: str, *, fallback: str = "orchestrator_os") -> str:
    return str(infer_orchestration_route(text, fallback=fallback)["assignee"])


def build_auto_handoff(*, title: str, body: str | None, assignee: str) -> dict[str, Any]:
    """Generate a minimal valid handoff packet when the caller omits one."""
    role_purpose = ROLE_PURPOSE_BY_ASSIGNEE.get(assignee, "coordination")
    body_text = (body or "").strip()
    objective = title.strip()
    if body_text and body_text != objective:
        objective = body_text.splitlines()[0].strip() or objective
    context: list[str] = []
    if body_text:
        context.append(body_text)
    if title.strip() and title.strip() not in context:
        context.append(title.strip())
    expected_output_map = {
        "orchestrator_os": "A routing decision and a bounded handoff.",
        "architect_os": "A bounded architecture and implementation plan.",
        "dev_os": "Working code changes with proof and tests.",
        "audit_os": "A clear pass/fail review with issues, if any.",
    }
    stop_map = {
        "orchestrator_os": ["task classified and routed"],
        "architect_os": ["design is complete"],
        "dev_os": ["implementation is complete"],
        "audit_os": ["verification is complete"],
    }
    return {
        "assigned_from": "Hermes",
        "assigned_to": assignee,
        "role_purpose": role_purpose,
        "objective": objective,
        "context": context,
        "scope": [objective],
        "non_goals": [],
        "constraints": ["keep v1 narrow"],
        "expected_output": expected_output_map.get(assignee, "A structured result."),
        "stop_conditions": stop_map.get(assignee, ["task complete"]),
        "blockers": [],
        "expiry": "2h",
        "confidence": "medium",
    }


def render_handoff_packet(packet: Mapping[str, Any], *, title: str, assignee: str) -> str:
    """Render a validated handoff packet into a canonical markdown body."""
    def _fmt_list(value: Any) -> str:
        if not value:
            return "- (none)"
        if isinstance(value, str):
            return f"- {value}"
        if isinstance(value, (list, tuple)):
            return "\n".join(f"- {item}" for item in value)
        return f"- {value}"

    parts = [
        "# Handoff Packet",
        "",
        f"## Task",
        f"- title: {title}",
        f"- assigned_to: {assignee}",
        f"- assigned_from: {packet.get('assigned_from', '(unknown)')}",
        f"- role_purpose: {packet.get('role_purpose', '(unknown)')}",
        "",
        "## Objective",
        str(packet.get("objective", "")).strip(),
        "",
        "## Context",
        _fmt_list(packet.get("context")),
        "",
        "## Scope",
        _fmt_list(packet.get("scope")),
        "",
        "## Non-goals",
        _fmt_list(packet.get("non_goals")),
        "",
        "## Constraints",
        _fmt_list(packet.get("constraints")),
        "",
        "## Expected output",
        str(packet.get("expected_output", "")).strip(),
        "",
        "## Stop conditions",
        _fmt_list(packet.get("stop_conditions")),
        "",
        f"## Expiry",
        str(packet.get("expiry") or packet.get("freshness") or "").strip(),
        "",
        f"## Confidence",
        str(packet.get("confidence", "")).strip(),
        "",
        "## Blockers",
        _fmt_list(packet.get("blockers")),
    ]
    extra = packet.get("extra")
    if extra:
        parts.extend(["", "## Extra", str(extra).strip()])
    return "\n".join(parts).rstrip() + "\n"


def validate_result_packet(result: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(result, dict):
        return [f"structured_result must be an object/dict, got {type(result).__name__}"]

    for field in RESULT_REQUIRED_FIELDS:
        value = result.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"structured_result is missing required field: {field}")

    status = str(result.get("status") or "").strip()
    if status not in RESULT_STATUS_VALUES:
        errors.append(
            f"structured_result.status must be one of {sorted(RESULT_STATUS_VALUES)}"
        )

    for field in ("decisions", "files_or_artifacts", "tests_or_checks", "risks", "blockers"):
        value = result.get(field)
        if value is None:
            continue
        if not isinstance(value, (list, tuple)):
            errors.append(f"structured_result field {field!r} must be a list")

    next_step = str(result.get("next_step") or "").strip()
    if not next_step:
        errors.append("structured_result.next_step must be non-empty")

    if status == "done" and result.get("blockers"):
        errors.append("structured_result.blockers must be empty when status is done")
    if status == "blocked" and not result.get("blockers"):
        errors.append("structured_result.blockers must be non-empty when status is blocked")
    return errors


def render_result_packet(result: Mapping[str, Any]) -> str:
    """Serialize a validated result packet in a canonical form."""
    return json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
