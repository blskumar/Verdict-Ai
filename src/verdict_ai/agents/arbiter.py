"""Arbiter agent.

Job: resolve disagreements between the reviewer and checker.
Called only when their scores diverge significantly or their pass/fail
judgements conflict. Returns one CheckResult per disputed case.
"""
from __future__ import annotations

from verdict_ai.agents._common import build_check, index_by_id, parse_items, reply_is_valid
from verdict_ai.llm import ModelRouter
from verdict_ai.models import CheckResult, Requirement, TestCase

ROLE = "arbiter"
UNRESOLVED = "Unresolved — flagged for human review."

# Disagreement thresholds
SCORE_GAP = 0.3          # abs(reviewer_score - checker_score) >= this triggers arbiter
VERDICT_CONFLICT = True  # reviewer.passed != checker.passed always triggers arbiter

_SYSTEM = (
    "You are an impartial QA arbiter. "
    "Two reviewers disagree on a test case. Study both opinions and give a final verdict. "
    "Judge each case on its own. A case may test one rule, one boundary or one combination of its requirement; "
    "do not mark it down for leaving the rest of the requirement untested. "
    "Routine detail that follows from the requirement (the next workflow step, a reference number, a status) is acceptable. "
    "Mark a case down only when it contradicts the requirement, states a figure or rule that cannot be inferred from it or from "
    "the domain context, tests a different requirement, or repeats another case. "
    "Return ONLY valid JSON — no markdown fences, no commentary."
)

_ITEM_TMPL = """\
---
case_id: {case_id}
requirement: {req_text}
scenario: {scenario}
expected: {expected}
reviewer_verdict: passed={rv_passed}, score={rv_score:.2f}, reason="{rv_reason}"
checker_verdict:  passed={ch_passed}, score={ch_score:.2f}, reason="{ch_reason}"
"""

_USER_TMPL = """\
Domain context:
{context}

The two reviewers below disagree. Give a final binding verdict for each case.
Return a JSON array — one object per case:
[
  {{
    "case_id": "<id>",
    "passed": true|false,
    "score": <0.0-1.0>,
    "reason": "<one sentence explaining the final decision>"
  }}
]

If disagreement cannot be resolved, set passed=false, score=0.72, and say
"Unresolved — flagged for human review."

Cases:
{items}
"""


def _is_disputed(rv: CheckResult, ch: CheckResult) -> bool:
    return (rv.passed != ch.passed) or (abs(rv.score - ch.score) >= SCORE_GAP)


def run_arbiter(
    router: ModelRouter,
    cases: list[TestCase],
    reqs: list[Requirement],
    reviewer_results: list[CheckResult],
    checker_results: list[CheckResult],
    *,
    context: str = "",
    batch_size: int = 5,
) -> list[CheckResult]:
    """Return one CheckResult per disputed case. Non-disputed cases are skipped.

    The orchestrator merges arbiter results on top of reviewer/checker results.
    """
    req_map = {r.id: r for r in reqs}
    rv_map = {c.case_id: c for c in reviewer_results}
    ch_map = {c.case_id: c for c in checker_results}

    disputed = [
        c for c in cases
        if c.id in rv_map and c.id in ch_map and _is_disputed(rv_map[c.id], ch_map[c.id])
    ]

    results: list[CheckResult] = []
    for i in range(0, len(disputed), batch_size):
        batch = disputed[i : i + batch_size]
        items = "".join(
            _ITEM_TMPL.format(
                case_id=c.id,
                req_text=req_map.get(c.requirement_id, _stub(c.requirement_id)).text,
                scenario=c.scenario,
                expected=c.expected,
                rv_passed=rv_map[c.id].passed,
                rv_score=rv_map[c.id].score,
                rv_reason=rv_map[c.id].reason,
                ch_passed=ch_map[c.id].passed,
                ch_score=ch_map[c.id].score,
                ch_reason=ch_map[c.id].reason,
            )
            for c in batch
        )
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _USER_TMPL.format(context=context or "None", items=items)},
        ]
        raw, model = router.chat(ROLE, messages, validate=reply_is_valid)
        replies, error = parse_items(raw)
        by_id = index_by_id(replies, "case_id")
        for case in batch:
            results.append(build_check("arbiter", case.id, by_id.get(case.id), model, error, unjudged_reason=UNRESOLVED))
    return results


def _stub(req_id: str) -> "object":
    class _Stub:
        text = f"Requirement {req_id} not found."
    return _Stub()

