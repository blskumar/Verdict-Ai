"""An unusable model reply goes to a person with a reason; the domain context reaches the models."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from verdict_ai import domains as dom
from verdict_ai.agents._common import UNJUDGED_SCORE, valid_score
from verdict_ai.agents.reviewer import run_reviewer
from verdict_ai.evaluate import run_evaluation
from verdict_ai.gate import GateEngine
from verdict_ai.models import Requirement, TestCase
from verdict_ai.orchestrator import Orchestrator
from verdict_ai.web.app import create_app

REQS = [Requirement(id="R1", title="t", text="x")]
CASES = [TestCase(id=i, requirement_id="R1", scenario="s", expected="e") for i in ("A", "B", "C")]


class Fake:
    def __init__(self, raw):
        self.raw = raw

    def chat(self, role, messages, **kw):
        return self.raw, "fake-model"


@pytest.mark.parametrize("raw", ["not json at all", "", "{}", json.dumps([1, 2, 3]), json.dumps("text")])
def test_unusable_reply_reaches_a_person_with_a_reason(raw):
    results = run_reviewer(Fake(raw), CASES, REQS)
    for r in results:
        decision = GateEngine().decide(r.case_id, [], [r])
        assert decision.decision == "human_review"
        assert decision.reasons and all(decision.reasons)


@pytest.mark.parametrize("value", [1.5, -0.1, "high", None, True, [0.9]])
def test_bad_score_is_unjudged_not_clamped(value):
    reply = json.dumps([{"case_id": "A", "passed": True, "score": value, "reason": "r"}])
    first = run_reviewer(Fake(reply), CASES, REQS)[0]
    assert first.passed is False and first.score == UNJUDGED_SCORE


def test_numeric_string_score_is_accepted():
    assert valid_score("0.8") == pytest.approx(0.8)


def test_valid_reply_still_judged_normally():
    reply = json.dumps([{"case_id": c.id, "passed": True, "score": 0.95, "reason": "good"} for c in CASES])
    assert [r.score for r in run_reviewer(Fake(reply), CASES, REQS)] == [0.95] * 3


# --- domain context ----------------------------------------------------------------------------

def test_context_text_carries_the_pack_content():
    text = dom.context_text(dom.load_pack("insurance/life/term"))
    assert text.startswith("Domain: Term Life")
    assert "approved rate table" in text
    assert "smoker class" in text
    assert "rate_table_version" in text
    assert "day 30 versus day 31" in text


@pytest.fixture
def seen_contexts(monkeypatch):
    seen: list[str] = []
    real = Orchestrator.run

    def spy(self, reqs, cases, context="", *a, **kw):
        seen.append(context)
        return real(self, reqs, cases, context, *a, **kw)

    monkeypatch.setattr(Orchestrator, "run", spy)
    return seen


def test_web_evaluate_sends_domain_context_to_the_models(seen_contexts):
    body = {"requirements": [{"id": "R1", "title": "t", "text": "Premium comes from the rate table."}],
            "cases": [{"id": "C1", "requirement_id": "R1", "type": "Positive", "scenario": "s", "expected": "Quote shown."}],
            "domain": "insurance/life/term", "agent_mode": "mock"}
    assert TestClient(create_app()).post("/api/evaluate", json=body).status_code == 200
    assert seen_contexts and "Term Life" in seen_contexts[0] and "rate table" in seen_contexts[0]


def test_run_evaluation_sends_domain_context_to_the_models(seen_contexts):
    run_evaluation(domain="insurance/life/term", agent_mode="mock")
    assert seen_contexts and "Term Life" in seen_contexts[0]
