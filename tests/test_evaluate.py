"""Tests for Package E: Evaluation and Model Bake-off."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from verdict_ai.evaluate import (
    EvaluationReport,
    format_markdown_report,
    run_evaluation,
    save_evaluation,
)


def test_run_evaluation_mock() -> None:
    report = run_evaluation(
        requirements_path="data/requirements/insurance_requirements.json",
        answer_key_path="data/answer_key/insurance_answer_key.json",
        seeded_bad_path="data/seeded_bad/insurance_seeded_bad.json",
        domain="insurance/life/term",
        approve_threshold=0.85,
        agent_mode="mock",
    )

    assert isinstance(report, EvaluationReport)
    assert report.total_cases == 108
    assert report.good_cases == 75
    assert report.bad_cases == 33

    # Quality KPI checks
    assert report.catch_rate >= 0.80, f"Expected catch rate >= 0.80, got {report.catch_rate}"
    assert report.false_reject_rate < 0.15, f"Expected false reject rate < 0.15, got {report.false_reject_rate}"
    assert report.reasons_compliance >= 0.99, f"Expected reasons compliance 100%, got {report.reasons_compliance}"
    assert report.targets_met is True

    # Defect breakdown checks
    assert len(report.defect_breakdown) > 0
    for defect, stats in report.defect_breakdown.items():
        assert stats["total"] > 0
        assert stats["caught"] + stats["missed"] == stats["total"]


def test_format_markdown_report() -> None:
    report = run_evaluation(
        requirements_path="data/requirements/insurance_requirements.json",
        answer_key_path="data/answer_key/insurance_answer_key.json",
        seeded_bad_path="data/seeded_bad/insurance_seeded_bad.json",
        domain="insurance/life/term",
        approve_threshold=0.85,
        agent_mode="mock",
    )

    md = format_markdown_report(report)
    assert "# Verdict AI — Evaluation Bake-off Report" in md
    assert "Catch Rate" in md
    assert "False Reject Rate" in md
    assert "Defect Breakdown" in md
    assert "Models Used" in md


def test_save_evaluation(tmp_path: Path) -> None:
    report = run_evaluation(
        requirements_path="data/requirements/insurance_requirements.json",
        answer_key_path="data/answer_key/insurance_answer_key.json",
        seeded_bad_path="data/seeded_bad/insurance_seeded_bad.json",
        domain="insurance/life/term",
        approve_threshold=0.85,
        agent_mode="mock",
    )

    json_p, md_p = save_evaluation(report, output_dir=tmp_path)
    assert json_p.exists()
    assert md_p.exists()

    data = json.loads(json_p.read_text(encoding="utf-8"))
    assert data["total_cases"] == 108
    assert data["catch_rate"] == report.catch_rate


def test_evaluation_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        run_evaluation(
            requirements_path="nonexistent_reqs.json",
            answer_key_path="data/answer_key/insurance_answer_key.json",
            seeded_bad_path="data/seeded_bad/insurance_seeded_bad.json",
        )
