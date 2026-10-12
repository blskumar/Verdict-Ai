import csv
import json
from pathlib import Path

import pytest

from verdict_ai.loader import LoadError, load_cases, load_requirements

ROOT = Path(__file__).resolve().parents[1]
REQ_PDF = ROOT / "data/requirements/insurance_requirements.pdf"
TC_PDF = ROOT / "data/answer_key/insurance_test_cases.pdf"


def test_requirements_pdf_has_25():
    reqs = load_requirements(REQ_PDF)
    assert len(reqs) == 25
    assert {r.id.split("-")[0] for r in reqs} == {"HOME", "CA", "TERM", "WL", "LIFE"}
    assert all(r.text and r.acceptance_criteria for r in reqs)


def test_cases_pdf_has_75_and_each_traces_to_a_requirement():
    reqs = {r.id for r in load_requirements(REQ_PDF)}
    cases = load_cases(TC_PDF)
    assert len(cases) == 75
    assert all(c.requirement_id in reqs for c in cases)
    assert all(c.type is not None for c in cases)


def test_every_requirement_has_three_cases():
    cases = load_cases(TC_PDF)
    counts = {}
    for c in cases:
        counts[c.requirement_id] = counts.get(c.requirement_id, 0) + 1
    assert set(counts.values()) == {3}


def test_json_and_csv_round_trip(tmp_path):
    reqs = load_requirements(REQ_PDF)
    j = tmp_path / "r.json"
    j.write_text(json.dumps([r.model_dump(mode="json") for r in reqs]))
    assert load_requirements(j) == reqs

    c = tmp_path / "r.csv"
    with c.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "title", "text", "acceptance_criteria"])
        w.writeheader()
        for r in reqs:
            w.writerow({k: getattr(r, k) for k in w.fieldnames})
    back = load_requirements(c)
    assert [(r.id, r.title, r.text) for r in back] == [(r.id, r.title, r.text) for r in reqs]


def test_cases_csv_and_json(tmp_path):
    cases = load_cases(TC_PDF)
    j = tmp_path / "c.json"
    j.write_text(json.dumps({"cases": [c.model_dump(mode="json") for c in cases]}))
    assert len(load_cases(j)) == 75
    c = tmp_path / "c.csv"
    with c.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "requirement_id", "type", "scenario", "expected"])
        w.writeheader()
        for x in cases:
            w.writerow({"id": x.id, "requirement_id": x.requirement_id, "type": x.type.value,
                        "scenario": x.scenario, "expected": x.expected})
    assert len(load_cases(c)) == 75


def test_unsupported_and_missing_files(tmp_path):
    bad = tmp_path / "x.xlsx"
    bad.write_text("x")
    with pytest.raises(LoadError, match="PDF, CSV or JSON"):
        load_requirements(bad)
    with pytest.raises(LoadError, match="not found"):
        load_requirements(tmp_path / "nope.json")


def test_duplicate_ids_rejected(tmp_path):
    p = tmp_path / "d.json"
    row = {"id": "A-REQ-01", "title": "t", "text": "x"}
    p.write_text(json.dumps([row, row]))
    with pytest.raises(LoadError, match="Duplicate"):
        load_requirements(p)


def test_pdf_without_requirements_gives_clear_error():
    with pytest.raises(LoadError, match="looks like a test case file"):
        load_requirements(TC_PDF)
