"""Test case generator agent.

Job: draft new test cases for a requirement when none exist.
Returns raw dicts (not TestCase objects) so the caller decides whether to persist.
Used by the orchestrator when coverage_gap or no_cases findings exist.
"""
from __future__ import annotations

import json

from verdict_ai.agents._common import reply_is_valid
from verdict_ai.llm import ModelRouter
from verdict_ai.models import Requirement

ROLE = "generator"

_SYSTEM = (
    "You are an expert QA engineer. "
    "Draft concise, precise test cases for the given requirements. "
    "Return ONLY valid JSON — no markdown fences, no commentary."
)

_USER_TMPL = """\
Domain context:
{context}

For each requirement below, draft test cases covering all recommended scenarios:
- One Positive case (standard valid inputs and happy path)
- One Negative case (invalid input, missing field, or error handling)
- One Boundary case (threshold limits, boundary edges, zero, or max values)
- One Security case (unauthorized access, role bypass, tampering, or compliance rule)

If an expected result contains a calculated figure (a premium, fee, rate or percentage), put the rate table
or worked example it comes from in "source_ref". Never copy a figure from running the system. If the
requirement gives no table or example, leave source_ref empty rather than inventing one.

Return a JSON array — one object per requirement:
[
  {{
    "req_id": "<id>",
    "cases": [
      {{
        "type": "Positive|Negative|Boundary|Security|Calculation",
        "scenario": "<one sentence: given X when Y>",
        "expected": "<specific, measurable outcome>",
        "source_ref": "<rate table or worked example behind any figure, else empty>"
      }}
    ]
  }}
]

Requirements:
{items}
"""

_ITEM_TMPL = """\
---
req_id: {req_id}
title: {title}
text: {text}
acceptance_criteria: {ac}
"""


def run_generator(
    router: ModelRouter,
    reqs: list[Requirement],
    *,
    context: str = "",
    batch_size: int = 3,
) -> list[dict]:
    """Return list of dicts: [{req_id, cases: [{type, scenario, expected}]}].

    Caller is responsible for persisting or converting to TestCase objects.
    """
    output: list[dict] = []
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
        # AllModelsExhausted propagates: the caller must know generation failed.
        raw, _model = router.chat(ROLE, messages, validate=reply_is_valid)
        parsed = _parse(raw, batch)
        output.extend(parsed)
    return output


def _parse(raw: str | None, batch: list[Requirement]) -> list[dict]:
    if not raw or not isinstance(raw, str):
        return [{"req_id": r.id, "cases": []} for r in batch]
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
    return [{"req_id": r.id, "cases": []} for r in batch]
