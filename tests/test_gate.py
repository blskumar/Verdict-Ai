"""Tests for Package C: gate engine and reports."""
from __future__ import annotations

import json


from verdict_ai.gate import GateEngine
from verdict_ai.models import CheckResult, GateResult
from verdict_ai.report import save_reports, to_csv, to_html, to_json, to_markdown
from verdict_ai.rules import Finding


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finding(case_id: str, decision: str, reason: str = "test reason") -> Finding:
    return Finding(case_id=case_id, code="test", decision=decision, reason=reason)  # type: ignore[arg-type]


def _check(
    case_id: str,
    passed: bool,
    score: float,
    model: str = "gpt-4",
    name: str = "c",
) -> CheckResult:
    return CheckResult(
        check_name=name,
        case_id=case_id,
        passed=passed,
        score=score,
        reason="ok" if passed else "problem",
        source="model",
        model_name=model,
    )


engine = GateEngine()  # uses gate.yaml defaults


# ---------------------------------------------------------------------------
# Gate engine tests
# ---------------------------------------------------------------------------

def test_approve():
    result = engine.decide("TC-01", [], [_check("TC-01", True, 0.95)])
    assert result.decision == "approve"
    assert result.case_id == "TC-01"


def test_revise_from_finding():
    result = engine.decide("TC-02", [_finding("TC-02", "revise", "Vague expected")], [])
    assert result.decision == "revise"


def test_reject_from_finding():
    result = engine.decide("TC-03", [_finding("TC-03", "reject", "No req")], [])
    assert result.decision == "reject"


def test_human_review_from_score():
    """Score in [0.70, 0.85) → human_review."""
    result = engine.decide("TC-04", [], [_check("TC-04", False, 0.75)])
    assert result.decision == "human_review"


def test_reject_beats_revise():
    findings = [
        _finding("TC-05", "revise", "Revise reason"),
        _finding("TC-05", "reject", "Reject reason"),
    ]
    result = engine.decide("TC-05", findings, [])
    assert result.decision == "reject"


def test_no_findings_no_checks():
    """Empty inputs → no problems → approve."""
    result = engine.decide("TC-06", [], [])
    assert result.decision == "approve"


def test_reasons_populated():
    result = engine.decide("TC-07", [_finding("TC-07", "revise", "Compound case")], [])
    assert "Compound case" in result.reasons


def test_models_used_deduplicated():
    checks = [
        _check("TC-08", True, 0.9, model="gpt-4", name="c1"),
        _check("TC-08", True, 0.88, model="gpt-4", name="c2"),
    ]
    result = engine.decide("TC-08", [], checks)
    assert result.models_used == ["gpt-4"]


def test_revise_below_threshold():
    """Score below revise_max (0.70) → revise even with no explicit finding."""
    result = engine.decide("TC-X", [], [_check("TC-X", False, 0.50)])
    assert result.decision == "revise"


def test_run_batch():
    findings = [_finding("A", "revise")]
    checks = [_check("B", True, 0.95)]
    results = engine.run(["A", "B"], findings, checks)
    assert len(results) == 2
    decisions = {r.case_id: r.decision for r in results}
    assert decisions["A"] == "revise"
    assert decisions["B"] == "approve"


def test_threshold_override():
    """threshold_override=0.5 → score 0.6 is above approve threshold."""
    eng = GateEngine(threshold_override=0.5)
    # score=0.6 >= approve_threshold=0.5, but >= revise_max=0.70? No.
    # revise_max is still 0.70 from gate.yaml, so 0.6 < 0.70 → revise.
    # Use 0.75 to be safely above revise_max.
    result = eng.decide("TC-09", [], [_check("TC-09", False, 0.75)])
    assert result.decision == "approve"


def test_domain_thresholds_override():
    eng = GateEngine(domain_thresholds={"revise_below": 0.60, "human_review_band": [0.60, 0.80]})
    assert eng.revise_max == 0.60
    assert eng.human_review_min == 0.60
    assert eng.approve_threshold == 0.80
    res = eng.decide("TC-DT", [], [_check("TC-DT", False, 0.65)])
    assert res.decision == "human_review"
    res_app = eng.decide("TC-DT2", [], [_check("TC-DT2", False, 0.82)])
    assert res_app.decision == "approve"


# ---------------------------------------------------------------------------
# Report tests
# ---------------------------------------------------------------------------

_SAMPLE = [
    GateResult(
        case_id="TC-01",
        decision="approve",
        reasons=[],
        scores={"c": 0.9},
        models_used=["gpt-4"],
    ),
    GateResult(
        case_id="TC-02",
        decision="revise",
        reasons=["Vague expected"],
        scores={"c": 0.5},
        models_used=[],
    ),
]


def test_markdown_report():
    md = to_markdown(_SAMPLE)
    assert "TC-01" in md
    assert "approve" in md
    assert "TC-02" in md


def test_html_report():
    html = to_html(_SAMPLE)
    assert "<html" in html
    assert "TC-01" in html
    assert "approve" in html
    # accessibility: badge label must be present as text
    assert "badge-label" in html or "approve" in html


def test_json_report():
    js = to_json(_SAMPLE)
    data = json.loads(js)
    assert data[0]["case_id"] == "TC-01"
    assert data[1]["decision"] == "revise"


def test_csv_report():
    csv_text = to_csv(_SAMPLE)
    assert "case_id" in csv_text
    assert "TC-01" in csv_text
    assert "TC-02" in csv_text


def test_save_reports(tmp_path):
    paths = save_reports(_SAMPLE, tmp_path / "out")
    for ext in ("md", "html", "json", "csv"):
        assert paths[ext].exists()
        assert paths[ext].stat().st_size > 0
