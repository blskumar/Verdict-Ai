"""Scenario reviewer agent.

Job: check each test case against its requirement for coverage, correctness,
and testability. Returns one CheckResult per case.
"""
from __future__ import annotations

from verdict_ai.agents._common import build_check, index_by_id, parse_items
from verdict_ai.llm import ModelRouter
from verdict_ai.models import CheckResult, Requirement, TestCase

ROLE = "reviewer"

_SYSTEM = (
    "You are a senior QA reviewer. "
    "Evaluate whether each test case correctly and completely covers its requirement. "
    "Return ONLY valid JSON — no markdown fences, no commentary."
)

_ITEM_TMPL = """\
---
case_id: {case_id}
requirement_id: {req_id}
requirement: {req_text}
type: {case_type}
scenario: {scenario}
expected: {expected}
"""

_USER_TMPL = """\
Domain context:
{context}

Review each test case below against its requirement.
Return a JSON array — one object per case:
[
  {{
    "case_id": "<id>",
    "passed": true|false,
    "score": <0.0-1.0>,
    "reason": "<one specific sentence>"
  }}
]

Scoring guide:
- 1.0  Scenario is clear, expected result is specific and measurable, fully covers req.
- 0.85 Minor wording issue but testable.
- 0.70 Partially covers requirement or expected result is borderline vague.
- 0.50 Scenario does not match requirement intent or expected result is untestable.
- 0.0  Completely wrong or missing.

Flag: vague expected results, wrong requirement scope, untestable outcomes,
missing preconditions, ambiguous pass/fail criteria.

Cases:
{items}
"""


def run_reviewer(
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
                req_text=req_map.get(c.requirement_id, _stub(c.requirement_id)).text,
                case_type=c.type.value if c.type else "—",
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
        replies, error = parse_items(raw)
        by_id = index_by_id(replies, "case_id")
        for case in batch:
            results.append(build_check("reviewer", case.id, by_id.get(case.id), model, error))
    return results


def _stub(req_id: str) -> "object":
    class _Stub:
        text = f"Requirement {req_id} not found."
    return _Stub()

