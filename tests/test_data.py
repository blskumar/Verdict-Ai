"""Integrity checks on the answer key and the seeded bad cases."""
import json
from pathlib import Path

from verdict_ai.loader import load_cases, load_requirements
from verdict_ai.models import SeededBadCase

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _seeded():
    return [SeededBadCase(**row) for row in json.loads((DATA / "seeded_bad/insurance_seeded_bad.json").read_text())]


def test_json_exports_match_the_pdfs():
    from_pdf = [r.model_dump(exclude={"domain"}) for r in load_requirements(DATA / "requirements/insurance_requirements.pdf")]
    from_json = [r.model_dump(exclude={"domain"}) for r in load_requirements(DATA / "requirements/insurance_requirements.json")]
    assert from_pdf == from_json
    pdf = [c.model_dump() for c in load_cases(DATA / "answer_key/insurance_test_cases.pdf", "answer_key")]
    js = [c.model_dump() for c in load_cases(DATA / "answer_key/insurance_answer_key.json", "answer_key")]
    assert pdf == js


def test_every_requirement_has_a_domain():
    reqs = load_requirements(DATA / "requirements/insurance_requirements.json")
    assert all(r.domain and r.domain.startswith("insurance/") for r in reqs)


def test_seeded_cases_are_labelled_and_unique():
    s = _seeded()
    assert len(s) >= 30
    assert len({c.id for c in s}) == len(s)
    assert all(c.defect and c.defect_note and c.expected_decision for c in s)
    assert {c.expected_decision for c in s} == {"reject", "revise", "human_review"}


def test_seeded_base_ids_exist_in_answer_key():
    key = {c.id for c in load_cases(DATA / "answer_key/insurance_answer_key.json")}
    for c in _seeded():
        assert c.base_id is None or c.base_id in key, c.id


def test_untraceable_cases_really_are_untraceable():
    reqs = {r.id for r in load_requirements(DATA / "requirements/insurance_requirements.json")}
    for c in _seeded():
        if c.defect == "untraceable":
            assert c.requirement_id not in reqs
        elif c.defect != "out_of_scope":
            assert c.requirement_id in reqs, c.id


def test_seeded_ids_do_not_clash_with_answer_key():
    key = {c.id for c in load_cases(DATA / "answer_key/insurance_answer_key.json")}
    assert not key & {c.id for c in _seeded()}


def test_seeded_arithmetic_claims_are_really_wrong():
    s = {c.id: c for c in _seeded()}
    assert round(0.07 * 5000, 2) == 350 and "375" in s["SB-001"].expected
    assert 12 * 42.50 == 510 and "500" in s["SB-002"].expected
    assert round(12 * 125 * 0.96, 2) == 1440 and "1,500" in s["SB-003"].expected


def test_every_defect_kind_is_covered_by_at_least_two_cases():
    from collections import Counter
    counts = Counter(c.defect for c in _seeded())
    assert min(counts.values()) >= 2 and len(counts) >= 12
