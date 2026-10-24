"""Requirement analyst agent.

Job: flag vague, ambiguous, or untestable requirements before cases are judged.
One CheckResult per requirement (case_id = req.id).
"""
from __future__ import annotations

import json

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
        parsed = _parse(raw, batch)
        parsed_by_id = {}
        for item in parsed:
            if isinstance(item, dict) and "req_id" in item and isinstance(item["req_id"], (str, int)):
                parsed_by_id[str(item["req_id"])] = item

        for req in batch:
            if req.id in parsed_by_id:
                item = parsed_by_id[req.id]
                try:
                    score_val = float(item.get("score", 1.0))
                    score_val = max(0.0, min(1.0, score_val))
                except (ValueError, TypeError):
                    score_val = 0.5
                passed_val = bool(item.get("passed", True))
                reason_val = str(item.get("reason", "No reason provided."))
            else:
                passed_val = True
                score_val = 0.5
                reason_val = "No evaluation returned for requirement — treated as neutral."

            results.append(
                CheckResult(
                    check_name="analyst",
                    case_id=req.id,
                    passed=passed_val,
                    score=score_val,
                    reason=reason_val,
                    source="model",
                    model_name=model,
                )
            )
    return results


def _parse(raw: str, batch: list[Requirement]) -> list[dict]:
    """Best-effort parse. Falls back to neutral pass on error."""
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
    # fallback: one neutral result per req
    return [{"req_id": r.id, "passed": True, "score": 0.5, "reason": "Parse error — treated as neutral."} for r in batch]
