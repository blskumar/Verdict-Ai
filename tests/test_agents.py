"""Tests for Package B: agents and orchestrator.

All tests use MockModel — zero real network calls.
MockModel returns pre-set JSON so each agent's parse + output logic is verified.
"""
from __future__ import annotations

import json

import pytest

from verdict_ai.agents.analyst import run_analyst
from verdict_ai.agents.arbiter import run_arbiter, _is_disputed
from verdict_ai.agents.checker import run_checker
from verdict_ai.agents.generator import run_generator
from verdict_ai.agents.reviewer import run_reviewer
from verdict_ai.llm import MockModel
from verdict_ai.models import CheckResult, CaseType, Requirement, TestCase
from verdict_ai.orchestrator import Orchestrator, OrchestratorConfig, RunResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _req(id: str = "REQ-01", risk: str = "high") -> Requirement:
    return Requirement(
        id=id,
        title="Test requirement",
        text="The system shall process claims under $10,000 automatically.",
        acceptance_criteria="Claim processed within 2 seconds.",
        risk=risk,  # type: ignore[arg-type]
    )


def _case(id: str = "TC-01", req_id: str = "REQ-01") -> TestCase:
    return TestCase(
        id=id,
        requirement_id=req_id,
        type=CaseType.positive,
        scenario="Submit a claim for $5,000 with all required documents.",
        expected="Claim is automatically approved within 2 seconds.",
        source="answer_key",
    )


def _mock_router(response: str, role: str) -> "object":
    return MockModel(response).as_router(role)


# ---------------------------------------------------------------------------
# Analyst tests
# ---------------------------------------------------------------------------

def test_analyst_returns_check_result_per_req():
    reqs = [_req("REQ-01"), _req("REQ-02")]
    response = json.dumps([
        {"req_id": "REQ-01", "passed": True, "score": 0.95, "reason": "Clear and testable."},
        {"req_id": "REQ-02", "passed": False, "score": 0.4, "reason": "Vague verb: 'works correctly'."},
    ])
    router = _mock_router(response, "analyst")
    results = run_analyst(router, reqs)
    assert len(results) == 2
    assert all(r.check_name == "analyst" for r in results)
    assert all(r.source == "model" for r in results)
    assert results[0].passed is True
    assert results[1].passed is False
    assert results[1].score == pytest.approx(0.4)


def test_analyst_model_name_recorded():
    reqs = [_req()]
    response = json.dumps([{"req_id": "REQ-01", "passed": True, "score": 0.9, "reason": "OK"}])
    router = _mock_router(response, "analyst")
    results = run_analyst(router, reqs)
    assert results[0].model_name == "mock-model"


def test_analyst_bad_json_falls_back_neutral():
    reqs = [_req()]
    router = _mock_router("this is not json at all", "analyst")
    results = run_analyst(router, reqs)
    assert len(results) == 1
    assert results[0].passed is True          # fallback neutral
    assert results[0].score == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Generator tests
# ---------------------------------------------------------------------------

def test_generator_returns_list_of_dicts():
    reqs = [_req()]
    response = json.dumps([{
        "req_id": "REQ-01",
        "cases": [
            {"type": "Positive", "scenario": "Submit $5k claim.", "expected": "Approved in 2s."},
            {"type": "Negative", "scenario": "Submit $15k claim.", "expected": "Rejected."},
        ],
    }])
    router = _mock_router(response, "generator")
    result = run_generator(router, reqs)
    assert len(result) == 1
    assert result[0]["req_id"] == "REQ-01"
    assert len(result[0]["cases"]) == 2


def test_generator_bad_json_returns_empty_cases():
    reqs = [_req()]
    router = _mock_router("bad json", "generator")
    result = run_generator(router, reqs)
    assert result[0]["cases"] == []


# ---------------------------------------------------------------------------
# Reviewer tests
# ---------------------------------------------------------------------------

def test_reviewer_returns_check_result_per_case():
    cases = [_case("TC-01"), _case("TC-02")]
    reqs = [_req()]
    response = json.dumps([
        {"case_id": "TC-01", "passed": True, "score": 0.92, "reason": "Fully covers req."},
        {"case_id": "TC-02", "passed": False, "score": 0.55, "reason": "Expected result is vague."},
    ])
    router = _mock_router(response, "reviewer")
    results = run_reviewer(router, cases, reqs)
    assert len(results) == 2
    assert results[0].check_name == "reviewer"
    assert results[0].passed is True
    assert results[1].passed is False


def test_reviewer_model_name_recorded():
    cases = [_case()]
    reqs = [_req()]
    response = json.dumps([{"case_id": "TC-01", "passed": True, "score": 0.9, "reason": "OK"}])
    router = _mock_router(response, "reviewer")
    results = run_reviewer(router, cases, reqs)
    assert results[0].model_name == "mock-model"


# ---------------------------------------------------------------------------
# Checker tests
# ---------------------------------------------------------------------------

def test_checker_returns_check_result_per_case():
    cases = [_case()]
    reqs = [_req()]
    response = json.dumps([
        {"case_id": "TC-01", "passed": True, "score": 0.97, "reason": "No hallucination detected."},
    ])
    router = _mock_router(response, "checker")
    results = run_checker(router, cases, reqs)
    assert len(results) == 1
    assert results[0].check_name == "checker"
    assert results[0].passed is True


def test_checker_missing_req_surfaces_in_reason():
    """Case with non-existent requirement_id — checker prompt sets req_exists=False."""
    cases = [_case(req_id="REQ-MISSING")]
    reqs = [_req("REQ-01")]  # REQ-MISSING not in reqs
    response = json.dumps([
        {"case_id": "TC-01", "passed": False, "score": 0.0, "reason": "Requirement REQ-MISSING not found."},
    ])
    router = _mock_router(response, "checker")
    results = run_checker(router, cases, reqs)
    assert results[0].passed is False
    assert "REQ-MISSING" in results[0].reason


# ---------------------------------------------------------------------------
# Arbiter tests
# ---------------------------------------------------------------------------

def test_is_disputed_verdict_conflict():
    rv = CheckResult(check_name="reviewer", case_id="TC-01", passed=True, score=0.9, reason="ok", source="model")
    ch = CheckResult(check_name="checker", case_id="TC-01", passed=False, score=0.9, reason="bad", source="model")
    assert _is_disputed(rv, ch) is True


def test_is_disputed_score_gap():
    rv = CheckResult(check_name="reviewer", case_id="TC-01", passed=True, score=0.9, reason="ok", source="model")
    ch = CheckResult(check_name="checker", case_id="TC-01", passed=True, score=0.5, reason="borderline", source="model")
    assert _is_disputed(rv, ch) is True   # gap = 0.4 >= 0.3


def test_is_not_disputed_when_agree():
    rv = CheckResult(check_name="reviewer", case_id="TC-01", passed=True, score=0.9, reason="ok", source="model")
    ch = CheckResult(check_name="checker", case_id="TC-01", passed=True, score=0.88, reason="ok", source="model")
    assert _is_disputed(rv, ch) is False   # gap = 0.02 < 0.3


def test_arbiter_only_called_for_disputed():
    cases = [_case("TC-01"), _case("TC-02")]
    reqs = [_req()]
    # TC-01 is disputed (conflict), TC-02 agrees
    rv = [
        CheckResult(check_name="reviewer", case_id="TC-01", passed=True, score=0.9, reason="ok", source="model"),
        CheckResult(check_name="reviewer", case_id="TC-02", passed=True, score=0.9, reason="ok", source="model"),
    ]
    ch = [
        CheckResult(check_name="checker", case_id="TC-01", passed=False, score=0.2, reason="bad", source="model"),
        CheckResult(check_name="checker", case_id="TC-02", passed=True, score=0.88, reason="ok", source="model"),
    ]
    response = json.dumps([
        {"case_id": "TC-01", "passed": False, "score": 0.72, "reason": "Unresolved — flagged for human review."}
    ])
    router = _mock_router(response, "arbiter")
    results = run_arbiter(router, cases, reqs, rv, ch)
    assert len(results) == 1    # only TC-01 was disputed
    assert results[0].case_id == "TC-01"
    assert results[0].check_name == "arbiter"


def test_arbiter_skips_when_no_disputes():
    cases = [_case()]
    reqs = [_req()]
    rv = [CheckResult(check_name="reviewer", case_id="TC-01", passed=True, score=0.9, reason="ok", source="model")]
    ch = [CheckResult(check_name="checker", case_id="TC-01", passed=True, score=0.88, reason="ok", source="model")]
    router = _mock_router("[]", "arbiter")
    results = run_arbiter(router, cases, reqs, rv, ch)
    assert results == []


# ---------------------------------------------------------------------------
# Orchestrator end-to-end tests
# ---------------------------------------------------------------------------

def _make_orchestrator(role_responses: dict[str, str]) -> Orchestrator:
    """Build an orchestrator with per-role mock responses."""
    from verdict_ai.llm import ModelRouter

    class MultiMockRouter(ModelRouter):
        def __init__(self):
            self.request_cap = 1000
            self.cooldown_seconds = 60
            self.roles_config = {
                role: {"models": ["mock-model"]} for role in role_responses
            }
            from pathlib import Path
            self.cache_dir = Path(".cache/llm-test")
            self._cooldowns = {}
            self._request_count = 0
            self._bad_json_streak = {}
            self._responses = role_responses

        def _post(self, model, messages):
            # Infer role from model list lookup doesn't work here; use message content
            for role, resp in self._responses.items():
                if role in (messages[-1]["content"][:500] if messages else ""):
                    return resp
            # return first available response
            return next(iter(self._responses.values()))

        def chat(self, role, messages, *, context=""):
            return self._responses.get(role, "[]"), "mock-model"

    return Orchestrator(MultiMockRouter())


def test_orchestrator_produces_check_results_for_every_case():
    reqs = [_req("REQ-01")]
    cases = [_case("TC-01"), _case("TC-02")]

    analyst_resp = json.dumps([
        {"req_id": "REQ-01", "passed": True, "score": 0.95, "reason": "Clear."}
    ])
    reviewer_resp = json.dumps([
        {"case_id": "TC-01", "passed": True, "score": 0.92, "reason": "Good."},
        {"case_id": "TC-02", "passed": True, "score": 0.88, "reason": "OK."},
    ])
    checker_resp = json.dumps([
        {"case_id": "TC-01", "passed": True, "score": 0.95, "reason": "No hallucination."},
        {"case_id": "TC-02", "passed": True, "score": 0.90, "reason": "Traceable."},
    ])

    orch = _make_orchestrator({
        "analyst": analyst_resp,
        "reviewer": reviewer_resp,
        "checker": checker_resp,
        "arbiter": "[]",
    })

    run = orch.run(reqs, cases)
    all_checks = run.all_checks()

    case_ids_covered = {r.case_id for r in all_checks}
    # Every case gets at least one check
    assert "TC-01" in case_ids_covered
    assert "TC-02" in case_ids_covered
    assert len(all_checks) >= len(cases)


def test_orchestrator_all_checks_flat_list():
    reqs = [_req()]
    cases = [_case()]
    reviewer_resp = json.dumps([{"case_id": "TC-01", "passed": True, "score": 0.9, "reason": "ok"}])
    checker_resp = json.dumps([{"case_id": "TC-01", "passed": True, "score": 0.88, "reason": "ok"}])
    orch = _make_orchestrator({
        "analyst": json.dumps([{"req_id": "REQ-01", "passed": True, "score": 0.9, "reason": "ok"}]),
        "reviewer": reviewer_resp,
        "checker": checker_resp,
        "arbiter": "[]",
    })
    run = orch.run(reqs, cases)
    checks = run.all_checks()
    assert isinstance(checks, list)
    assert all(isinstance(c, CheckResult) for c in checks)


def test_orchestrator_progress_callback():
    reqs = [_req()]
    cases = [_case()]
    messages: list[str] = []
    reviewer_resp = json.dumps([{"case_id": "TC-01", "passed": True, "score": 0.9, "reason": "ok"}])
    checker_resp = json.dumps([{"case_id": "TC-01", "passed": True, "score": 0.9, "reason": "ok"}])
    orch = _make_orchestrator({
        "analyst": json.dumps([{"req_id": "REQ-01", "passed": True, "score": 0.9, "reason": "ok"}]),
        "reviewer": reviewer_resp,
        "checker": checker_resp,
        "arbiter": "[]",
    })
    orch.run(reqs, cases, on_progress=messages.append)
    assert len(messages) > 0


def test_orchestrator_arbiter_fires_on_dispute():
    reqs = [_req()]
    cases = [_case()]
    # reviewer passes, checker fails with big gap → arbiter should fire
    reviewer_resp = json.dumps([{"case_id": "TC-01", "passed": True, "score": 0.95, "reason": "Good."}])
    checker_resp = json.dumps([{"case_id": "TC-01", "passed": False, "score": 0.1, "reason": "Hallucination."}])
    arbiter_resp = json.dumps([{"case_id": "TC-01", "passed": False, "score": 0.72, "reason": "Unresolved."}])
    orch = _make_orchestrator({
        "analyst": json.dumps([{"req_id": "REQ-01", "passed": True, "score": 0.9, "reason": "ok"}]),
        "reviewer": reviewer_resp,
        "checker": checker_resp,
        "arbiter": arbiter_resp,
    })
    run = orch.run(reqs, cases)
    arbiter_ids = {r.case_id for r in run.arbiter_results}
    assert "TC-01" in arbiter_ids


def test_orchestrator_resume_file(tmp_path):
    reqs = [_req()]
    cases = [_case()]
    resume = tmp_path / "progress.json"
    reviewer_resp = json.dumps([{"case_id": "TC-01", "passed": True, "score": 0.9, "reason": "ok"}])
    checker_resp = json.dumps([{"case_id": "TC-01", "passed": True, "score": 0.9, "reason": "ok"}])
    cfg = OrchestratorConfig(resume_path=resume)
    orch = _make_orchestrator({
        "analyst": json.dumps([{"req_id": "REQ-01", "passed": True, "score": 0.9, "reason": "ok"}]),
        "reviewer": reviewer_resp,
        "checker": checker_resp,
        "arbiter": "[]",
    })
    orch.cfg = cfg
    orch.run(reqs, cases)
    assert resume.exists()
    saved = json.loads(resume.read_text())
    assert "reviewer" in saved
