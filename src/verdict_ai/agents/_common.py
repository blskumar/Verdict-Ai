"""Shared parsing and validation for model replies. Plain code, no model calls.

Rules every agent follows:
- A model reply is matched to a case by its id, never by position.
- A missing, malformed or out-of-range item never raises and never borrows
  another case's verdict: that case comes back "unjudged".
- "Unjudged" is a failed check scored in the human-review band, with a reason,
  so the gate sends it to a person ("never guess").
"""
from __future__ import annotations

import json
import math
from typing import Any

from verdict_ai.models import CheckResult

# Same value the arbiter has always used for "unresolved". It sits inside the default
# human-review band (0.70 to 0.85), so an unjudged case lands in human_review.
UNJUDGED_SCORE = 0.72


def parse_items(raw: str | None) -> tuple[list[Any], str | None]:
    """Parse a model reply into a list of items. Returns (items, error).

    error is a short reason when the reply is unusable; items is then empty.
    """
    if not raw or not isinstance(raw, str):
        return [], "Model returned an empty reply."
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    try:
        data = json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        return [], "Model reply was not valid JSON."
    if isinstance(data, dict):
        return [data], None
    if isinstance(data, list):
        return data, None
    return [], "Model reply was not a JSON list."


def reply_is_valid(raw: str | None) -> bool:
    """True when the reply parses to JSON items. Used to decide whether a reply may be cached."""
    return parse_items(raw)[1] is None


def index_by_id(items: list[Any], key: str) -> dict[str, dict]:
    """Map id -> item. Skips non-dict items and ids that are not a plain string or int."""
    out: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        ident = item.get(key)
        if isinstance(ident, bool) or not isinstance(ident, (str, int)):
            continue
        out.setdefault(str(ident), item)  # first one wins
    return out


def valid_score(value: Any) -> float | None:
    """Return the score as a float in [0, 1], or None if it is not usable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if not isinstance(value, (int, float)) or math.isnan(value):
        return None
    return float(value) if 0.0 <= value <= 1.0 else None


def unjudged(check_name: str, case_id: str, model: str | None, reason: str) -> CheckResult:
    return CheckResult(
        check_name=check_name,
        case_id=case_id,
        passed=False,
        score=UNJUDGED_SCORE,
        reason=reason,
        source="model",
        model_name=model,
    )


def build_check(
    check_name: str,
    case_id: str,
    item: dict | None,
    model: str | None,
    error: str | None = None,
    *,
    unjudged_reason: str | None = None,
) -> CheckResult:
    """Turn one model item into a CheckResult, or an unjudged result if it is unusable."""

    def fail(why: str) -> CheckResult:
        return unjudged(check_name, case_id, model, unjudged_reason or why)

    if item is None:
        return fail(error or "Model returned no evaluation for this case.")
    score = valid_score(item.get("score"))
    if score is None:
        return fail("Model returned a missing or invalid score for this case.")
    passed = item.get("passed")
    if not isinstance(passed, bool):
        return fail("Model returned a missing or invalid pass/fail flag for this case.")
    return CheckResult(
        check_name=check_name,
        case_id=case_id,
        passed=passed,
        score=score,
        reason=str(item.get("reason") or "No reason provided."),
        source="model",
        model_name=model,
    )
