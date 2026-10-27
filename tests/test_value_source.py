"""A figure in an expected result must say where it came from (rate table, worked example, spec)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from verdict_ai import domains as dom
from verdict_ai.gate import GateEngine
from verdict_ai.loader import load_cases
from verdict_ai.models import CaseType, CheckResult, Requirement, TestCase
from verdict_ai.rules import check_value_source, run_rules
from verdict_ai.web.app import create_app

REQ = Requirement(id="R1", title="Premium", text="Premium comes from the approved rate table.",
                  acceptance_criteria="Fee is $25.00.")


def case(scenario="Quote a 35 year old non-smoker.", expected="Premium is $42.50 per month.", **kw) -> TestCase:
    return TestCase(id="C1", requirement_id="R1", type=kw.pop("type", CaseType.calculation),
                    scenario=scenario, expected=expected, **kw)


def codes(c: TestCase, req=REQ, markers=()) -> list[str]:
    return [f.code for f in check_value_source(c, req, markers)]


def test_figure_with_no_source_is_flagged():
    found = check_value_source(case(), REQ)
    assert [f.code for f in found] == ["uncited_value"]
    assert found[0].decision == "revise"
    assert "42.5" in found[0].reason and "rate table or worked example" in found[0].reason
    assert found[0].case_id == "C1" and found[0].requirement_id == "R1"


@pytest.mark.parametrize("kw", [
    {"source_ref": "Rate table RT-2026-03, row 35/NS"},
    {"expected": "Premium is $42.50 per month per rate table RT-2026-03."},
    {"scenario": "Quote a 35 year old non-smoker using the worked example in section 4.2."},
    {"expected": "Premium is $42.50 (Table 3)."},
])
def test_citation_clears_it(kw):
    assert codes(case(**kw)) == []


def test_blank_source_ref_does_not_count():
    assert codes(case(source_ref="   ")) == ["uncited_value"]


def test_figure_given_in_the_scenario_is_an_input_not_a_claim():
    c = case(scenario="Use a premium of $42.50 and switch to annual mode.", expected="Premium shows $42.50.")
    assert codes(c) == []


def test_figure_stated_in_the_requirement_is_sourced():
    assert codes(case(expected="Fee of $25.00 is added.")) == []


def test_derived_figure_is_accepted_and_a_wrong_one_is_not():
    scenario = "Apply 7% fleet credit to eligible premium of $5,000.00."
    assert codes(case(scenario=scenario, expected="Credit equals $350.00 and appears once.")) == []
    assert codes(case(scenario=scenario, expected="Credit equals $375.00 and appears once.")) == ["uncited_value"]


def test_percentages_that_add_up_are_accepted():
    c = case(type=CaseType.positive, scenario="Enter allocations of 60% and 40%.",
             expected="Application accepts allocations totalling 100%.")
    assert codes(c) == []


def test_text_without_a_figure_is_not_checked():
    assert codes(case(type=CaseType.positive, expected="Application is accepted and a reference is shown.")) == []


def test_bare_number_in_a_calculation_case_is_checked():
    assert codes(case(expected="Premium is 42.5 per month.")) == ["uncited_value"]
    assert codes(case(type=CaseType.positive, expected="Premium is 42.5 per month.")) == []  # no $ or %


def test_pack_markers_add_to_the_defaults():
    c = case(expected="Premium is $42.50 per Schedule S-4.")
    assert codes(c) == ["uncited_value"]
    assert codes(c, markers=["schedule s-4"]) == []


def test_pack_inherits_source_markers():
    pack = dom.merge("a/b", [{"name": "a", "source_markers": ["Schedule S-4"]},
                             {"name": "b", "source_markers": ["Schedule S-4", "Rider R"]}])
    assert pack.source_markers == ["Schedule S-4", "Rider R"]


def test_run_rules_uses_requirement_and_pack():
    c = case(expected="Premium is $42.50 per Schedule S-4.")
    plain = [f.code for f in run_rules([REQ], [c])]
    pack = dom.merge("x", [{"name": "x", "source_markers": ["Schedule S-4"]}])
    assert "uncited_value" in plain
    assert "uncited_value" not in [f.code for f in run_rules([REQ], [c], pack=pack)]


def test_model_score_of_100_cannot_outvote_the_rule():
    c = case()
    findings = run_rules([REQ], [c])
    perfect = [CheckResult(check_name="reviewer", case_id="C1", passed=True, score=1.0, reason="ok", source="model",
                           model_name="m")]
    result = GateEngine().decide("C1", findings, perfect)
    assert result.decision == "revise"
    assert any("where it comes from" in r for r in result.reasons)
    cited = run_rules([REQ], [case(source_ref="Rate table RT-2026-03")])
    assert GateEngine().decide("C1", cited, perfect).decision == "approve"


def test_loader_reads_source_ref_from_json_and_csv(tmp_path):
    (tmp_path / "c.json").write_text(
        '[{"id":"C1","requirement_id":"R1","scenario":"s","expected":"e","source_ref":"RT-1"}]', encoding="utf-8")
    assert load_cases(tmp_path / "c.json")[0].source_ref == "RT-1"
    (tmp_path / "c.csv").write_text(
        "id,requirement_id,scenario,expected,source_ref,source\nC1,R1,s,e,RT-2,answer_key\n", encoding="utf-8")
    loaded = load_cases(tmp_path / "c.csv")[0]
    assert loaded.source_ref == "RT-2"
    assert loaded.source == "uploaded"  # a column called "source" is provenance, not a citation


def test_web_evaluate_applies_the_rule_and_accepts_source_ref():
    client = TestClient(create_app())

    def run(extra):
        body = {"requirements": [{"id": "R1", "title": "t", "text": "Premium comes from the approved rate table."}],
                "cases": [{"id": "C1", "requirement_id": "R1", "type": "Calculation",
                           "scenario": "Quote a 35 year old non-smoker.", "expected": "Premium is $42.50 per month.",
                           **extra}],
                "use_agents": False}
        resp = client.post("/api/evaluate", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()["gate_results"][0]

    bad = run({})
    assert bad["decision"] == "revise" and any("where it comes from" in r for r in bad["reasons"])
    assert run({"source_ref": "Rate table RT-2026-03"})["decision"] == "approve"
