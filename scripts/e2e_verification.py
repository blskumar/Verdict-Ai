"""End-to-end verification script for Verdict AI.

Takes a sample set of requirements, constructs candidate test cases
(including valid cases and intentional defects), runs the complete
Verdict AI gatekeeper pipeline, and reports all detected defects.
"""
from __future__ import annotations

import json
from pathlib import Path

from verdict_ai import domains as dom
from verdict_ai.evaluate import _build_evaluation_mock_router
from verdict_ai.gate import GateEngine
from verdict_ai.models import CaseType, CheckResult, GateResult, Requirement, TestCase
from verdict_ai.orchestrator import Orchestrator, OrchestratorConfig
from verdict_ai.report import to_markdown, to_html, to_json, to_csv
from verdict_ai.rules import run_rules


def run_e2e_check() -> None:
    print("=" * 70)
    print(" Verdict AI — End-to-End Pipeline Verification")
    print("=" * 70)

    # 1. Define Sample Requirements
    reqs = [
        Requirement(
            id="REQ-TERM-01",
            title="Term Life Issue Age Limit",
            text="The applicant must be between 18 and 65 years old at policy issue date. Applications outside this range must be declined.",
            acceptance_criteria="Ages 18 to 65 allowed; <18 or >65 rejected.",
            risk="high",
        ),
        Requirement(
            id="REQ-TERM-02",
            title="Fleet Discount Premium Calculation",
            text="A fleet credit of 7% applies to eligible commercial auto premium. The credit displays once on the schedule.",
            acceptance_criteria="Credit = 7% * eligible premium.",
            risk="medium",
        ),
    ]

    print(f" Loaded {len(reqs)} sample requirements.")

    # 2. Define Candidate Test Cases (Mix of Good and Defective)
    cases = [
        # Good cases
        TestCase(
            id="TC-GOOD-01",
            requirement_id="REQ-TERM-01",
            type=CaseType.positive,
            scenario="Applicant aged 35 applies for standard term life policy.",
            expected="Application accepted for underwriting.",
            source="uploaded",
        ),
        TestCase(
            id="TC-GOOD-02",
            requirement_id="REQ-TERM-01",
            type=CaseType.boundary,
            scenario="Applicant aged 65 applies on exact birthday.",
            expected="Application is accepted as meeting age threshold of 65.",
            source="uploaded",
        ),
        TestCase(
            id="TC-GOOD-03",
            requirement_id="REQ-TERM-02",
            type=CaseType.calculation,
            scenario="Apply 7% fleet credit to eligible premium of $5,000.00.",
            expected="Credit equals $350.00 and appears once on schedule.",
            source="uploaded",
        ),
        # Defective case 1: Vague expected outcome
        TestCase(
            id="TC-DEFECT-VAGUE",
            requirement_id="REQ-TERM-01",
            type=CaseType.positive,
            scenario="Applicant submits application online.",
            expected="System behaves correctly and everything works as expected.",
            source="uploaded",
        ),
        # Defective case 2: Missing expected outcome
        TestCase(
            id="TC-DEFECT-EMPTY",
            requirement_id="REQ-TERM-01",
            type=CaseType.negative,
            scenario="Applicant aged 16 applies.",
            expected="",
            source="uploaded",
        ),
        # Defective case 3: Ambiguous outcome
        TestCase(
            id="TC-DEFECT-AMBIGUOUS",
            requirement_id="REQ-TERM-01",
            type=CaseType.boundary,
            scenario="Applicant aged 66 applies.",
            expected="Application is blocked or referred to senior underwriter review.",
            source="uploaded",
        ),
        # Defective case 4: Untraceable requirement
        TestCase(
            id="TC-DEFECT-UNTRACEABLE",
            requirement_id="NON-EXISTENT-REQ-999",
            type=CaseType.positive,
            scenario="Applicant enters medical history.",
            expected="Medical questionnaire saved.",
            source="uploaded",
        ),
        # Defective case 5: Compound bundled actions
        TestCase(
            id="TC-DEFECT-COMPOUND",
            requirement_id="REQ-TERM-01",
            type=CaseType.positive,
            scenario="Enter personal info, submit payment details, and request immediate delivery confirmation.",
            expected="All actions complete properly.",
            source="uploaded",
        ),
        # Defective case 6: Wrong arithmetic calculation
        TestCase(
            id="TC-DEFECT-CALC",
            requirement_id="REQ-TERM-02",
            type=CaseType.calculation,
            scenario="Apply 7% fleet credit to eligible premium of $5,000.00.",
            expected="Credit equals $375.00 and appears once on schedule.",
            source="uploaded",
        ),
        # Defective case 7: Hallucinated feature
        TestCase(
            id="TC-DEFECT-HALLUCINATION",
            requirement_id="REQ-TERM-01",
            type=CaseType.positive,
            scenario="Applicant authenticates using retinal biometric scan and blockchain wallet signature.",
            expected="Biometric token validated against decentralized registry.",
            source="uploaded",
        ),
        # Defective case 8: Near duplicate of TC-GOOD-01
        TestCase(
            id="TC-DEFECT-DUPLICATE",
            requirement_id="REQ-TERM-01",
            type=CaseType.positive,
            scenario="Applicant aged 35 applies for standard term life policy.",
            expected="Application accepted for underwriting.",
            source="uploaded",
        ),
    ]

    print(f" Loaded {len(cases)} candidate test cases (3 good, 8 defective).")

    # 3. Step 1: Run Deterministic Rules Check
    print("\n[Step 1] Running Deterministic Rule Engine...")
    findings = run_rules(reqs, cases)
    print(f"  Found {len(findings)} rule findings.")
    for f in findings:
        target = f.case_id or f"Requirement {f.requirement_id}"
        print(f"    - {target}: [{f.decision.upper()}] {f.code} -> {f.reason}")

    # 4. Step 2: Run Multi-Agent Consensus
    print("\n[Step 2] Running Multi-Agent Consensus Pool...")
    defect_meta = {
        "TC-DEFECT-CALC": {
            "defect": "wrong_arithmetic",
            "defect_note": "7% of $5,000 is $350, not $375.",
        },
        "TC-DEFECT-HALLUCINATION": {
            "defect": "hallucinated_feature",
            "defect_note": "Requirement mentions no retinal biometric scan or blockchain registry.",
        },
    }
    mock_router = _build_evaluation_mock_router(defect_meta)
    orchestrator = Orchestrator(mock_router, OrchestratorConfig(run_analyst=True, run_arbiter=True))
    run_res = orchestrator.run(reqs, cases, context="Domain: Insurance / Life / Term")
    agent_checks = run_res.all_checks()
    print(f"  Completed {len(agent_checks)} agent checks.")

    # 5. Step 3: Run Gate Engine Aggregation
    print("\n[Step 3] Running Gate Engine Aggregation...")
    engine = GateEngine(threshold_override=0.85)
    gate_results = engine.run([c.id for c in cases], findings, agent_checks)

    # 6. Step 4: Summary & Defect Table
    print("\n" + "=" * 70)
    print(" VERDICT AI DECISION MATRIX")
    print("=" * 70)
    print(f"{'Case ID':<24} | {'Decision':<14} | {'Score':<6} | {'Reason(s)'}")
    print("-" * 70)

    defect_count = 0
    approved_count = 0

    for r in gate_results:
        reasons_str = "; ".join(r.reasons) if r.reasons else "All checks passed cleanly."
        mean_s = sum(r.scores.values()) / len(r.scores) if r.scores else 1.0
        print(f"{r.case_id:<24} | {r.decision.upper():<14} | {mean_s:<6.2f} | {reasons_str[:60]}")
        if r.decision == "approve":
            approved_count += 1
        else:
            defect_count += 1

    print("-" * 70)
    print(f"Results Summary: {approved_count} Approved, {defect_count} Caught with Defects.")
    print(f"Target accuracy: 3/3 good cases approved, 8/8 defective cases caught (100% precision).")

    # Save reports
    out_dir = Path("runs/e2e")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "e2e_report.md").write_text(to_markdown(gate_results, title="Verdict AI E2E Run"), encoding="utf-8")
    (out_dir / "e2e_report.json").write_text(to_json(gate_results), encoding="utf-8")
    print(f"\n Reports written to {out_dir}/e2e_report.md and e2e_report.json")
    print("=" * 70)


if __name__ == "__main__":
    run_e2e_check()
