"""Measure rule checks against the seeded bad cases and the answer key."""
import json
from pathlib import Path

from verdict_ai.loader import load_cases, load_requirements
from verdict_ai.models import Requirement, SeededBadCase, TestCase
from verdict_ai.rules import check_coverage, run_rules

DATA = Path(__file__).resolve().parents[1] / "data"
REQS = load_requirements(DATA / "requirements/insurance_requirements.json")
KEY = load_cases(DATA / "answer_key/insurance_answer_key.json", "answer_key")
BAD = [SeededBadCase(**r) for r in json.loads((DATA / "seeded_bad/insurance_seeded_bad.json").read_text(encoding="utf-8"))]


def _flagged(cases, reference=()):
    return {f.case_id for f in run_rules(REQS, cases, reference) if f.case_id}


def test_rules_catch_seeded_bad_cases():
    caught = _flagged(BAD, KEY)
    missed = sorted(c.id + ":" + c.defect for c in BAD if c.id not in caught)
    print("rules caught", len(BAD) - len(missed), "of", len(BAD), "| missed:", missed)
    # rules alone are not the whole gate; model checks cover wrong_arithmetic, contradicts, hallucinated etc.
    assert len(caught) >= 12


def test_rules_rarely_flag_good_cases():
    wrong = sorted(_flagged(KEY))
    print("good cases flagged:", wrong)
    assert len(wrong) / len(KEY) < 0.15


def test_rules_decision_matches_label_when_flagged():
    by_id = {c.id: c for c in BAD}
    for f in run_rules(REQS, BAD, KEY):
        if f.case_id:
            assert f.decision == by_id[f.case_id].expected_decision, (f.case_id, f.code)


def test_requirement_with_no_cases_is_flagged():
    reqs = [Requirement(id="X-REQ-1", title="t", text="t")]
    assert [f.code for f in check_coverage(reqs, [])] == ["no_cases"]
    c = TestCase(id="X-TC-1-1", requirement_id="X-REQ-1", scenario="s", expected="e")
    assert check_coverage(reqs, [c]) == []
