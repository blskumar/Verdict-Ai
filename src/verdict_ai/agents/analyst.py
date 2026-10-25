"""Requirement analyst agent.

Job: flag vague, ambiguous, or untestable requirements before cases are judged.
One CheckResult per requirement (case_id = req.id).
"""
from __future__ import annotations

from verdict_ai.agents._common import build_check, index_by_id, parse_items
from verdict_ai.llm import ModelRouter
from verdict_ai.models import CheckResult, Requirement

ROLE = "analyst"

_SYSTEM = (
    "You are a senior QA analyst. "
    "Assess whether requirements are clear, specific, and testable. "
    "Return ONLY valid JSON — no markdown fences, no commentary."
)

_ITEM_TMPL = """\
---
req_id: {req_id}
title: {title}
text: {text}
acceptance_criteria: {ac}
"""

_USER_TMPL = """\
Domain context:
{context}

Evaluate each requirement below and return a JSON array — one object per requirement:
[
  {{
    "req_id": "<id>",
    "passed": true|false,
    "score": <0.0-1.0>,
    "reason": "<one specific sentence>"
  }}
]

Rules:
- passed=true means the requirement is clear, specific, and testable.
- score=1.0 is perfect; 0.0 is completely untestable.
- Flag: vague verbs (works correctly, behaves properly), missing acceptance values,
  contradictory statements, untestable absolutes (always, never without condition).

Requirements:
{items}
"""


def run_analyst(
    router: ModelRouter,
    reqs: list[Requirement],
    *,
    context: str = "",
    batch_size: int = 5,
) -> list[CheckResult]:
    """Return one CheckResult per requirement (case_id = req.id)."""
    results: list[CheckResult] = []
    for i in range(0, len(reqs), batch_size):
        batch = reqs[i : i + batch_size]
        items = "".join(
            _ITEM_TMPL.format(
                req_id=r.id,
                title=r.title,
                text=r.text,
                ac=r.acceptance_criteria or "—",
            )
            for r in batch
        )
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _USER_TMPL.format(context=context or "None", items=items)},
        ]
        raw, model = router.chat(ROLE, messages)
        replies, error = parse_items(raw)
        by_id = index_by_id(replies, "req_id")
        for req in batch:
            results.append(build_check("analyst", req.id, by_id.get(req.id), model, error))
    return results

