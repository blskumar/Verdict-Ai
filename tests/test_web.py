"""Tests for Package D: FastAPI Web Application & UI."""
from __future__ import annotations

import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from verdict_ai.web.app import create_app


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


def test_index_page(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    text = resp.text
    assert "Verdict" in text
    assert "Gate Engine" in text
    assert "dark" in text


def test_api_health(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "verdict-ai"


def test_api_domains(client: TestClient) -> None:
    resp = client.get("/api/domains")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0
    # Check tree contains insurance or banking
    paths = [d["path"] for d in data]
    assert "insurance" in paths or "insurance/life/term" in paths


def test_api_sample(client: TestClient) -> None:
    resp = client.get("/api/sample")
    assert resp.status_code == 200
    data = resp.json()
    assert "requirements" in data
    assert "cases" in data
    assert len(data["requirements"]) > 0
    assert len(data["cases"]) > 0


def test_api_evaluate_mock(client: TestClient) -> None:
    reqs = [
        {
            "id": "REQ-01",
            "title": "Claim Age",
            "text": "Applicants must be at least 18.",
            "acceptance_criteria": "Ages >= 18 accepted.",
            "risk": "high",
        }
    ]
    cases = [
        {
            "id": "TC-01",
            "requirement_id": "REQ-01",
            "type": "Positive",
            "scenario": "Age 25 applies.",
            "expected": "Accepted for processing.",
        },
        {
            "id": "TC-02",
            "requirement_id": "REQ-01",
            "type": "Negative",
            "scenario": "Age 15 applies.",
            "expected": "Works correctly and is fine.",  # triggers rule vague_expected
        },
    ]

    resp = client.post(
        "/api/evaluate",
        json={
            "requirements": reqs,
            "cases": cases,
            "domain": "insurance/life/term",
            "approve_threshold": 0.85,
            "use_rules": True,
            "use_agents": True,
            "agent_mode": "mock",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    summary = data["summary"]
    assert summary["total_cases"] == 2
    assert summary["approved"] + summary["revised"] + summary["rejected"] + summary["human_review"] == 2

    # TC-02 should be revised or rejected due to vague expected
    results = {r["case_id"]: r for r in data["gate_results"]}
    assert "TC-01" in results
    assert "TC-02" in results
    assert results["TC-02"]["decision"] in ("revise", "reject")

    # Reports should be generated
    assert "markdown" in data["reports"]
    assert "html" in data["reports"]
    assert "json" in data["reports"]
    assert "csv" in data["reports"]


def test_api_evaluate_empty_cases_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/evaluate",
        json={
            "requirements": [],
            "cases": [],
        },
    )
    assert resp.status_code == 400
    assert "No test cases provided" in resp.json()["detail"]


def test_api_export_reports(client: TestClient) -> None:
    results_payload = [
        {
            "case_id": "TC-01",
            "decision": "approve",
            "reasons": [],
            "scores": {"rule": 1.0, "reviewer": 0.95},
            "models_used": ["mock-model"],
        }
    ]

    for fmt in ("json", "csv", "md", "html"):
        resp = client.post(
            f"/api/export/{fmt}",
            json={"results": results_payload, "format": fmt},
        )
        assert resp.status_code == 200
        assert f"verdict_report.{fmt}" in resp.headers["content-disposition"]
        assert len(resp.content) > 0


def test_api_upload_json_files(client: TestClient) -> None:
    req_json = json.dumps([
        {"id": "REQ-01", "title": "Test", "text": "Requirement", "acceptance_criteria": "AC", "risk": "low"}
    ]).encode("utf-8")

    case_json = json.dumps([
        {"id": "TC-01", "requirement_id": "REQ-01", "scenario": "Test", "expected": "Pass"}
    ]).encode("utf-8")

    files = {
        "requirements_file": ("reqs.json", BytesIO(req_json), "application/json"),
        "cases_file": ("cases.json", BytesIO(case_json), "application/json"),
    }

    resp = client.post("/api/upload", files=files)
    assert resp.status_code == 200
    data = resp.json()
    assert data["requirements_count"] == 1
    assert data["cases_count"] == 1
    assert data["requirements"][0]["id"] == "REQ-01"
    assert data["cases"][0]["id"] == "TC-01"


def test_api_requirements_recognize(client: TestClient) -> None:
    raw = """
    1. User Authentication
    The system shall authenticate users using email and password.
    Acceptance criteria: Valid credentials log in; invalid produce error.

    2. Password Reset
    As an applicant, I want to reset my password via email token.
    Acceptance criteria: Token expires after 15 minutes.
    """
    resp = client.post("/api/requirements/recognize", json={"raw_text": raw})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 2
    assert len(data["requirements"]) >= 2
    ids = [r["id"] for r in data["requirements"]]
    assert "REQ-1" in ids or "REQ-001" in ids


def test_api_requirements_analyze(client: TestClient) -> None:
    reqs = [
        {
            "id": "REQ-ANALYSIS-01",
            "title": "Fast Quote Calculation",
            "text": "The system should calculate quotes fast and behave properly.",
            "acceptance_criteria": "",  # missing AC + vague words
            "risk": "high",
        },
        {
            "id": "REQ-ANALYSIS-02",
            "title": "Age Eligibility",
            "text": "The applicant must be between 18 and 65 years old at issue date.",
            "acceptance_criteria": "Ages 18-65 accepted; outside declined.",
            "risk": "medium",
        },
    ]
    resp = client.post(
        "/api/requirements/analyze",
        json={"requirements": reqs, "agent_mode": "mock"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["total"] == 2
    assert "analyses" in data
    first = next(a for a in data["analyses"] if a["req_id"] == "REQ-ANALYSIS-01")
    # First requirement has vague words + missing AC, so score is lowered
    assert first["testability_score"] < 0.8
    assert any(i["code"] == "vague_terms" for i in first["issues"])
    assert any(i["code"] == "missing_acceptance_criteria" for i in first["issues"])

    second = next(a for a in data["analyses"] if a["req_id"] == "REQ-ANALYSIS-02")
    assert second["testability_verdict"] == "testable"
    assert len(second["suggested_scenarios"]) >= 2


def test_api_requirements_generate_cases(client: TestClient) -> None:
    reqs = [
        {
            "id": "REQ-GEN-01",
            "title": "Payment Tokenization",
            "text": "Credit card data must be tokenized via payment gateway.",
            "acceptance_criteria": "No raw PAN or CVV stored.",
            "risk": "high",
        }
    ]
    resp = client.post(
        "/api/requirements/generate-cases",
        json={"requirements": reqs, "agent_mode": "mock"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["cases_count"] >= 3
    types = [c["type"] for c in data["cases"]]
    assert "Positive" in types
    assert "Negative" in types
    assert all(c["requirement_id"] == "REQ-GEN-01" for c in data["cases"])


def test_api_requirements_export(client: TestClient) -> None:
    reqs = [
        {
            "id": "REQ-EXP-01",
            "title": "Fast Processing",
            "text": "The app should process fast and be fine.",
            "acceptance_criteria": "",
            "risk": "high",
        }
    ]
    for fmt in ("csv", "json", "md", "html"):
        resp = client.post(
            f"/api/requirements/export/{fmt}",
            json={"requirements": reqs, "agent_mode": "mock"},
        )
        assert resp.status_code == 200
        assert f"requirements_review_report.{fmt}" in resp.headers["content-disposition"]
        assert len(resp.content) > 0
        if fmt == "csv":
            assert "REQ-EXP-01" in resp.text
            assert "vague_terms" in resp.text
        elif fmt == "md":
            assert "Executive Summary" in resp.text
            assert "REQ-EXP-01" in resp.text


def test_api_cases_export_grouped(client: TestClient) -> None:
    cases = [
        {"id": "TC-01", "requirement_id": "REQ-A", "type": "Positive", "scenario": "A1", "expected": "Exp1"},
        {"id": "TC-02", "requirement_id": "REQ-A", "type": "Negative", "scenario": "A2", "expected": "Exp2"},
        {"id": "TC-03", "requirement_id": "REQ-B", "type": "Positive", "scenario": "B1", "expected": "Exp3"},
    ]
    # Test CSV
    resp_csv = client.post("/api/cases/export/csv", json=cases)
    assert resp_csv.status_code == 200
    csv_text = resp_csv.text
    assert "=== REQUIREMENT: REQ-A (2 test cases) ===" in csv_text
    assert "=== REQUIREMENT: REQ-B (1 test cases) ===" in csv_text
    # Verify break between groups
    lines = csv_text.splitlines()
    assert any(line.strip() == "" for line in lines)

    # Test JSON
    resp_json = client.post("/api/cases/export/json", json=cases)
    assert resp_json.status_code == 200
    data = resp_json.json()
    assert "groups" in data
    assert "REQ-A" in data["groups"]
    assert "REQ-B" in data["groups"]
    assert len(data["groups"]["REQ-A"]) == 2
    assert len(data["groups"]["REQ-B"]) == 1


