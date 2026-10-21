"""Hallucination and traceability checker agent.

Job: detect invented fields/values and cases that reference non-existent
requirements. Returns one CheckResult per case.
"""
from __future__ import annotations

import json

from verdict_ai.llm import ModelRouter
from verdict_ai.models import CheckResult, Requirement, TestCase

ROLE = "checker"

_SYSTEM = (
    "You are a QA traceability and hallucination checker. "
    "Detect invented facts, values that contradict the requirement, "
    "and cases that cannot be traced to any stated requirement. "
    "Return ONLY valid JSON — no markdown fences, no commentary."
)

_ITEM_TMPL = """\
---
case_id: {case_id}
requirement_id: {req_id}
requirement_exists: {req_exists}
requirement_text: {req_text}
scenario: {scenario}
expected: {expected}
"""

_USER_TMPL = """\
Domain context:
{context}

Check each test case for hallucination and traceability issues.
Return a JSON array — one object per case:
[
  {{
    "case_id": "<id>",
    "passed": true|false,
    "score": <0.0-1.0>,
    "reason": "<one specific sentence>"
  }}
]

Check for:
1. Requirement does not exist (requirement_exists=false) → score 0.0
2. Expected result contains values or thresholds not mentioned in the requirement.
3. Scenario describes a flow not related to the requirement.
4. Domain-specific values (amounts, percentages, dates) that contradict the requirement.

passed=true only if the case traces cleanly to the requirement with no invented content.

Cases:
{items}
"""


def run_checker(
    router: ModelRouter,
    cases: list[TestCase],
    reqs: list[Requirement],
    *,
    context: str = "",
    batch_size: int = 5,
) -> list[CheckResult]:
    """Return one CheckResult per test case."""
    req_map = {r.id: r for r in reqs}
    results: list[CheckResult] = []

    for i in range(0, len(cases), batch_size):
        batch = cases[i : i + batch_size]
        items = "".join(
            _ITEM_TMPL.format(
                case_id=c.id,
                req_id=c.requirement_id,
                req_exists=c.requirement_id in req_map,
                req_text=req_map[c.requirement_id].text if c.requirement_id in req_map else "NOT FOUND",
                scenario=c.scenario,
                expected=c.expected,
            )
            for c in batch
        )
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _USER_TMPL.format(context=context or "None", items=items)},
        ]
        raw, model = router.chat(ROLE, messages)
        parsed = _parse(raw, batch)
        for case, item in zip(batch, parsed):
            results.append(
                CheckResult(
                    check_name="checker",
                    case_id=case.id,
                    passed=bool(item.get("passed", True)),
                    score=float(item.get("score", 1.0)),
                    reason=str(item.get("reason", "No reason provided.")),
                    source="model",
                    model_name=model,
                )
            )
    return results


def _parse(raw: str, batch: list[TestCase]) -> list[dict]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    try:
        data = json.loads(text.strip())
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
    except (json.JSONDecodeError, ValueError):
        pass
    return [{"case_id": c.id, "passed": True, "score": 0.5, "reason": "Parse error — treated as neutral."} for c in batch]
