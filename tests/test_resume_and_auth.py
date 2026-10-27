"""Orchestrator: --resume skips finished work (A7); a 401 stops live calls and is reported (N8)."""
import json
import re

from verdict_ai.llm import AuthenticationError
from verdict_ai.models import CheckResult, Requirement, TestCase
from verdict_ai.orchestrator import Orchestrator, OrchestratorConfig

REQ = Requirement(id="REQ-01", title="t", text="Spec text.")


def _case(i: str) -> TestCase:
    return TestCase(id=i, requirement_id="REQ-01", scenario="Do it.", expected="It is done.")


def _ok(role: str, check: str, ident: str) -> CheckResult:
    return CheckResult(check_name=check, case_id=ident, passed=True, score=0.9, reason="ok", source="model", model_name="m")


class StubRouter:
    """Answers every case or requirement id it finds in the prompt; records what it was asked."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.asked: list[tuple[str, str]] = []

    def chat(self, role, messages, *, context="", validate=None):
        if self.error:
            raise self.error
        prompt = messages[-1]["content"]
        key, rx = ("req_id", r"req_id:\s*(\S+)") if role == "analyst" else ("case_id", r"case_id:\s*(\S+)")
        ids = re.findall(rx, prompt)
        self.asked += [(role, i) for i in ids]
        return json.dumps([{key: i, "passed": True, "score": 0.9, "reason": "ok"} for i in ids]), "stub"


def test_resume_skips_cases_already_judged(tmp_path):
    resume = tmp_path / "progress.json"
    resume.write_text(json.dumps({
        "analyst": [_ok("analyst", "analyst", "REQ-01").model_dump()],
        "reviewer": [_ok("reviewer", "reviewer", "TC-01").model_dump()],
        "checker": [_ok("checker", "checker", "TC-01").model_dump()],
        "arbiter": [],
        "exhausted": [],
    }), encoding="utf-8")
    router = StubRouter()
    run = Orchestrator(router, OrchestratorConfig(resume_path=resume)).run([REQ], [_case("TC-01"), _case("TC-02")])
    asked = set(router.asked)
    assert ("reviewer", "TC-01") not in asked and ("checker", "TC-01") not in asked
    assert ("reviewer", "TC-02") in asked and ("checker", "TC-02") in asked
    assert not any(role == "analyst" for role, _ in asked)  # analyst stage was resumed too
    assert {r.case_id for r in run.reviewer_results} == {"TC-01", "TC-02"}


def test_auth_failure_is_reported_and_stops_live_calls():
    router = StubRouter(error=AuthenticationError("OpenRouter authentication failed (HTTP 401). Check OPENROUTER_API_KEY."))
    run = Orchestrator(router, OrchestratorConfig(run_analyst=False)).run([REQ], [_case("TC-01"), _case("TC-02")])
    assert run.auth_error and "401" in run.auth_error
    assert set(run.exhausted_case_ids) == {"TC-01", "TC-02"}
    assert run.arbiter_results == []
