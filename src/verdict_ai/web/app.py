"""FastAPI web application for Verdict AI.

Provides REST endpoints and serves the single-page Tailwind + Shadcn UI.
"""
from __future__ import annotations

import csv
import io
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from verdict_ai import domains as dom
from verdict_ai.agents.generator import run_generator
from verdict_ai.gate import GateEngine
from verdict_ai.llm import ModelRouter
from verdict_ai.loader import load_cases, load_requirements
from verdict_ai.models import CaseType, CheckResult, Decision, GateResult, Requirement, TestCase
from verdict_ai.orchestrator import Orchestrator, OrchestratorConfig
from verdict_ai.report import to_csv, to_html, to_json, to_markdown
from verdict_ai.requirement_analyzer import (
    RequirementAnalysis,
    RequirementIssue,
    analyze_requirements,
    derive_suggested_scenarios,
    parse_raw_requirements_text,
    requirements_review_to_csv,
    requirements_review_to_html,
    requirements_review_to_json,
    requirements_review_to_markdown,
)
from verdict_ai.rules import Finding, run_rules

STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Request / Response Schemas
# ---------------------------------------------------------------------------

class EvaluateRequest(BaseModel):
    requirements: list[dict[str, Any]] = Field(default_factory=list)
    cases: list[dict[str, Any]] = Field(default_factory=list)
    domain: str | None = None
    approve_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    use_rules: bool = True
    use_agents: bool = True
    agent_mode: Literal["mock", "live"] = "mock"


class RecognizeRequest(BaseModel):
    raw_text: str


class AnalyzeRequirementsRequest(BaseModel):
    requirements: list[dict[str, Any]] = Field(default_factory=list)
    raw_text: str | None = None
    domain: str | None = None
    agent_mode: Literal["mock", "live"] = "mock"


class ExportRequirementsReviewRequest(BaseModel):
    requirements: list[dict[str, Any]] = Field(default_factory=list)
    analyses: list[dict[str, Any]] = Field(default_factory=list)
    domain: str | None = None
    agent_mode: Literal["mock", "live"] = "mock"


class GenerateCasesRequest(BaseModel):
    requirements: list[dict[str, Any]] = Field(default_factory=list)
    domain: str | None = None
    agent_mode: Literal["mock", "live"] = "mock"


class SummaryStats(BaseModel):
    total_cases: int
    approved: int
    revised: int
    rejected: int
    human_review: int
    mean_score: float


class FindingItem(BaseModel):
    case_id: str | None
    code: str
    decision: str
    reason: str
    requirement_id: str | None


class DecisionAnalysisItem(BaseModel):
    why_approved: str = ""
    why_revised: str = ""
    why_rejected: str = ""
    human_review_actions: str = ""
    human_discretion_grounds: str = ""


class GateResultItem(BaseModel):
    case_id: str
    decision: Decision
    reasons: list[str]
    scores: dict[str, float]
    models_used: list[str]
    mean_score: float
    analysis: DecisionAnalysisItem = Field(default_factory=DecisionAnalysisItem)
    human_override: bool = False



class EvaluateResponse(BaseModel):
    summary: SummaryStats
    gate_results: list[GateResultItem]
    requirement_findings: list[FindingItem]
    reports: dict[str, str]


class ExportRequest(BaseModel):
    results: list[dict[str, Any]]
    format: Literal["json", "csv", "md", "html"]


# ---------------------------------------------------------------------------
# Mock Router Helper for Fast / Safe Web Execution
# ---------------------------------------------------------------------------

def _build_mock_router_for_evaluation(cases: list[TestCase], reqs: list[Requirement]) -> ModelRouter:
    """Generate a realistic mock response router for instant evaluation and generation."""
    analyst_payload = [
        {"req_id": r.id, "passed": True, "score": 0.94, "reason": "Requirement is specific and testable."}
        for r in reqs
    ]
    reviewer_payload = []
    checker_payload = []

    for c in cases:
        # Give diverse realistic scores based on case characteristics
        if not c.expected or "works correctly" in c.expected.lower():
            reviewer_payload.append({
                "case_id": c.id, "passed": False, "score": 0.45,
                "reason": "Vague or missing outcome in expected result.",
            })
        elif "or" in c.expected.lower() and "review" in c.expected.lower():
            reviewer_payload.append({
                "case_id": c.id, "passed": False, "score": 0.72,
                "reason": "Ambiguous outcome requires human judgment.",
            })
        else:
            reviewer_payload.append({
                "case_id": c.id, "passed": True, "score": 0.92,
                "reason": "Scenario covers requirement criteria and steps are reproducible.",
            })

        checker_payload.append({
            "case_id": c.id, "passed": True, "score": 0.95,
            "reason": "All fields trace directly to requirement specification.",
        })

    class WebMockRouter(ModelRouter):
        def __init__(self) -> None:
            self.request_cap = 1000
            self.cooldown_seconds = 60
            self.roles_config = {
                "analyst": {"models": ["mock-analyst:free"]},
                "reviewer": {"models": ["mock-reviewer:free"]},
                "checker": {"models": ["mock-checker:free"]},
                "arbiter": {"models": ["mock-arbiter:free"]},
                "generator": {"models": ["mock-generator:free"]},
            }
            self.cache_dir = Path(".cache/llm-web-mock")
            self._cooldowns = {}
            self._request_count = 0
            self._bad_json_streak = {}

        def chat(self, role: str, messages: list[dict], *, context: str = ""):  # noqa: ARG002
            user_msg = messages[1]["content"] if len(messages) > 1 else ""
            batch_req_ids = set(re.findall(r"req_id:\s*([^\s\n]+)", user_msg))
            batch_case_ids = set(re.findall(r"case_id:\s*([^\s\n]+)", user_msg))

            if role == "analyst":
                target_reqs = [r for r in reqs if r.id in batch_req_ids] if batch_req_ids else reqs
                analyst_data = [
                    {"req_id": r.id, "passed": True, "score": 0.94, "reason": "Requirement is specific and testable."}
                    for r in target_reqs
                ]
                return json.dumps(analyst_data), "mock-analyst"
            if role == "reviewer":
                target_cases = [c for c in reviewer_payload if c["case_id"] in batch_case_ids] if batch_case_ids else reviewer_payload
                return json.dumps(target_cases), "mock-reviewer"
            if role == "checker":
                target_cases = [c for c in checker_payload if c["case_id"] in batch_case_ids] if batch_case_ids else checker_payload
                return json.dumps(target_cases), "mock-checker"
            if role == "arbiter":
                return json.dumps([]), "mock-arbiter"
            if role == "generator":
                target_reqs = [r for r in reqs if r.id in batch_req_ids] if batch_req_ids else reqs
                gen_data = []
                for r in target_reqs:
                    derived = derive_suggested_scenarios(r, r.risk or "medium")
                    cases_for_r = []
                    for s in derived:
                        cases_for_r.append({
                            "type": s["type"],
                            "scenario": s["scenario"],
                            "expected": s.get("expected") or "System executes strictly per requirement criteria.",
                        })
                    gen_data.append({
                        "req_id": r.id,
                        "cases": cases_for_r,
                    })
                return json.dumps(gen_data), "mock-generator"
            return "[]", "mock-model"

    return WebMockRouter()


def _dict_to_requirement(r: dict[str, Any], default_idx: int = 1) -> Requirement:
    risk_val = r.get("risk", "medium")
    risk = risk_val if risk_val in ("low", "medium", "high") else "medium"
    req_id = str(r.get("id") or f"REQ-{default_idx:03d}")
    return Requirement(
        id=req_id,
        title=str(r.get("title") or req_id),
        text=str(r.get("text", "")),
        acceptance_criteria=str(r.get("acceptance_criteria", "")),
        domain=r.get("domain"),
        risk=risk,
    )


def _build_decision_analysis(
    decision: Decision,
    case_id: str,
    reasons: list[str],
    mean_score: float,
    requirement_id: str | None = None,
) -> DecisionAnalysisItem:
    """Build structured 5-point decision analysis for automated or human reviewed test cases."""
    reasons_text = "; ".join(reasons) if reasons else "No defects flagged across static rules or consensus models."
    req_label = f"requirement {requirement_id}" if requirement_id else "specification"

    if decision == "approve":
        return DecisionAnalysisItem(
            why_approved=(
                f"Meets all gate criteria with quality score {mean_score:.2f} >= approval threshold. "
                f"Defines explicit inputs, verifiable numeric boundaries, and deterministic expected outcomes tracing to {req_label}."
            ),
            why_revised="Not applicable. Test case contains complete inputs and unambiguous criteria without requiring rework.",
            why_rejected="Not applicable. No specification contradictions, calculation errors, or schema defects detected.",
            human_review_actions="Confirm integration test environment prerequisites and verify downstream test data availability.",
            human_discretion_grounds="Verify environment-specific configurations and third-party mock behaviors against production policies.",
        )
    elif decision == "revise":
        return DecisionAnalysisItem(
            why_approved="Cannot approve in current form because test quality score or completeness falls below the required threshold.",
            why_revised=(
                f"Quality score {mean_score:.2f} falls below approval threshold. Specific issue: {reasons_text}. "
                "Author must provide explicit boundary conditions, clarify vague expected outcomes, or align steps with acceptance criteria."
            ),
            why_rejected="Defect is non-fatal and remediable through prompt revision without discarding the core scenario.",
            human_review_actions="Clarify unstated numeric boundaries or ambiguous terms with product owner, then update expected output.",
            human_discretion_grounds="Determination of acceptable tolerance limits, timeout thresholds, and boundary inclusivity/exclusivity.",
        )
    elif decision == "reject":
        return DecisionAnalysisItem(
            why_approved="Cannot approve. Test case exhibits a fatal defect or direct specification contradiction.",
            why_revised="Revision is insufficient because the underlying assertion is fundamentally flawed or untraceable.",
            why_rejected=(
                f"Critical defect identified: {reasons_text}. "
                f"Underlying test assertion or requirement reference cannot be verified for {req_label}."
            ),
            human_review_actions="Audit requirement version history to check if requirement was deprecated, replaced, or misinterpreted.",
            human_discretion_grounds="Final business determination to deprecate this test case or issue a formal requirement change request.",
        )
    else:  # human_review
        return DecisionAnalysisItem(
            why_approved="Automated approval withheld because quality score is in the dispute/borderline zone requiring human validation.",
            why_revised="Revision may be warranted if human reviewer determines that edge conditions or non-functional criteria are missing.",
            why_rejected="No outright defect detected, but conflicting check scores or borderline confidence require human adjudication.",
            human_review_actions=(
                f"Borderline score ({mean_score:.2f}) or dispute flagged: {reasons_text}. "
                "Human auditor must review business risk and manually override verdict to Approve, Revise, or Reject."
            ),
            human_discretion_grounds="Underwriting risk appetite, regulatory compliance judgment, and business exception policy discretion.",
        )


# ---------------------------------------------------------------------------
# App Factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title="Verdict AI API & WebUI",
        description="Gatekeeper for AI-generated test cases",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        index_file = STATIC_DIR / "index.html"
        if not index_file.exists():
            return HTMLResponse(
                "<h1>Verdict AI Web UI</h1><p>Static index.html not found. Please build static assets.</p>",
                status_code=404,
            )
        return HTMLResponse(
            index_file.read_text(encoding="utf-8"),
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "verdict-ai",
            "version": "0.1.0",
        }

    @app.get("/api/domains")
    def list_domains() -> list[dict[str, Any]]:
        """Return hierarchical domain pack tree."""
        try:
            return dom.tree()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/sample")
    def get_sample_data() -> dict[str, Any]:
        """Return sample requirements and test cases for quick evaluation in the UI."""
        req_path = Path("data/requirements/insurance_requirements.json")
        case_path = Path("data/answer_key/insurance_answer_key.json")

        sample_reqs: list[dict[str, Any]] = []
        sample_cases: list[dict[str, Any]] = []

        if req_path.exists():
            try:
                data = json.loads(req_path.read_text(encoding="utf-8"))
                sample_reqs = data[:4]  # First 4 requirements
            except Exception:
                pass

        if case_path.exists():
            try:
                data = json.loads(case_path.read_text(encoding="utf-8"))
                # Filter cases matching sample reqs
                sample_req_ids = {r.get("id") for r in sample_reqs}
                sample_cases = [c for c in data if c.get("requirement_id") in sample_req_ids][:8]
            except Exception:
                pass

        # Fallback if files missing
        if not sample_reqs:
            sample_reqs = [
                {
                    "id": "REQ-TERM-01",
                    "title": "Term Life Issue Age Limits",
                    "text": "The applicant must be between 18 and 65 years old at the policy issue date. Applications outside this range must be declined.",
                    "acceptance_criteria": "Ages 18 to 65 allowed; <18 or >65 rejected.",
                    "risk": "high",
                },
                {
                    "id": "REQ-TERM-02",
                    "title": "Coverage Amount Multiplier",
                    "text": "Maximum coverage amount is 10 times annual earned income for applicants aged 18-45.",
                    "acceptance_criteria": "Multiplier capped at 10x income.",
                    "risk": "medium",
                },
            ]
        if not sample_cases:
            sample_cases = [
                {
                    "id": "TC-001",
                    "requirement_id": "REQ-TERM-01",
                    "type": "Positive",
                    "scenario": "Applicant aged 35 applies for standard term policy.",
                    "expected": "Application accepted for underwriting.",
                },
                {
                    "id": "TC-002",
                    "requirement_id": "REQ-TERM-01",
                    "type": "Boundary",
                    "scenario": "Applicant aged 66 applies for standard term policy.",
                    "expected": "Application is declined due to exceeding maximum age of 65.",
                },
                {
                    "id": "TC-003",
                    "requirement_id": "REQ-TERM-01",
                    "type": "Negative",
                    "scenario": "Applicant aged 17 applies for term policy.",
                    "expected": "System works correctly and is fine.",  # Vague test case on purpose
                },
            ]

        return {
            "domain": "insurance/life/term",
            "requirements": sample_reqs,
            "cases": sample_cases,
        }

    @app.post("/api/upload")
    async def upload_files(
        requirements_file: UploadFile | None = File(None),
        cases_file: UploadFile | None = File(None),
        file: UploadFile | None = File(None),
        files: list[UploadFile] | None = File(None),
    ) -> dict[str, Any]:
        """Parse uploaded requirements and/or cases from PDF, DOCX, XLSX, JSON, or CSV."""
        parsed_reqs: list[dict[str, Any]] = []
        parsed_cases: list[dict[str, Any]] = []

        with tempfile.TemporaryDirectory() as tmp_dir:
            async def _process_upload(f: UploadFile, preferred_type: str | None = None):
                if not f or not f.filename:
                    return
                safe_name = Path(f.filename).name
                content = await f.read()
                if not content:
                    return
                if len(content) > 25 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail=f"File '{safe_name}' exceeds maximum allowed upload size (25MB).")
                tmp_path = Path(tmp_dir) / safe_name
                tmp_path.write_bytes(content)

                if preferred_type == "requirements":
                    try:
                        loaded = load_requirements(tmp_path)
                        parsed_reqs.extend([r.model_dump() for r in loaded])
                        return
                    except Exception:
                        try:
                            loaded_cases = load_cases(tmp_path)
                            parsed_cases.extend([c.model_dump() for c in loaded_cases])
                            return
                        except Exception:
                            pass
                        raise HTTPException(status_code=400, detail=f"Could not parse '{safe_name}' as requirements specification.")
                elif preferred_type == "cases":
                    try:
                        loaded = load_cases(tmp_path)
                        parsed_cases.extend([c.model_dump() for c in loaded])
                        return
                    except Exception:
                        try:
                            loaded_reqs = load_requirements(tmp_path)
                            parsed_reqs.extend([r.model_dump() for r in loaded_reqs])
                            return
                        except Exception:
                            pass
                        raise HTTPException(status_code=400, detail=f"Could not parse '{safe_name}' as test cases.")
                else:
                    # Auto-detect: try requirements then test cases
                    try:
                        loaded = load_requirements(tmp_path)
                        parsed_reqs.extend([r.model_dump() for r in loaded])
                        return
                    except Exception:
                        pass
                    try:
                        loaded = load_cases(tmp_path)
                        parsed_cases.extend([c.model_dump() for c in loaded])
                        return
                    except Exception:
                        pass
                    raise HTTPException(status_code=400, detail=f"Could not parse '{safe_name}' as requirements or test cases. Please check format.")

            if requirements_file:
                await _process_upload(requirements_file, "requirements")
            if cases_file:
                await _process_upload(cases_file, "cases")
            if file:
                await _process_upload(file, None)
            if files:
                for f_item in files:
                    await _process_upload(f_item, None)

        return {
            "requirements": parsed_reqs,
            "cases": parsed_cases,
            "requirements_count": len(parsed_reqs),
            "cases_count": len(parsed_cases),
        }

    @app.post("/api/requirements/recognize")
    def recognize_requirements(req: RecognizeRequest) -> dict[str, Any]:
        """Recognize requirements from raw unstructured or structured text."""
        if not req.raw_text.strip():
            raise HTTPException(status_code=400, detail="raw_text cannot be empty.")
        parsed = parse_raw_requirements_text(req.raw_text)
        return {
            "requirements": [r.model_dump() for r in parsed],
            "count": len(parsed),
        }

    @app.post("/api/requirements/analyze")
    def analyze_reqs_endpoint(req: AnalyzeRequirementsRequest) -> dict[str, Any]:
        """Recognize and deeply analyze requirements for testability, ambiguity, and risk."""
        req_models: list[Requirement] = []
        if req.raw_text and req.raw_text.strip():
            req_models = parse_raw_requirements_text(req.raw_text)
        elif req.requirements:
            req_models = [_dict_to_requirement(r, idx) for idx, r in enumerate(req.requirements, start=1)]

        if not req_models:
            raise HTTPException(status_code=400, detail="No requirements provided for analysis.")

        domain_context = ""
        if req.domain:
            try:
                pack = dom.load_pack(req.domain)
                domain_context = f"Domain: {pack.name}\nRules: {', '.join(pack.rules)}"
            except Exception:
                pass

        if req.agent_mode == "mock":
            router = _build_mock_router_for_evaluation([], req_models)
        else:
            router = ModelRouter()

        analyses = analyze_requirements(req_models, router=router, context=domain_context)

        total = len(analyses)
        testable_count = sum(1 for a in analyses if a.testability_verdict == "testable")
        needs_review_count = sum(1 for a in analyses if a.testability_verdict == "needs_review")
        untestable_count = sum(1 for a in analyses if a.testability_verdict == "untestable")
        mean_score = round(sum(a.testability_score for a in analyses) / total, 2) if total else 1.0
        high_risk_count = sum(1 for a in analyses if a.risk == "high")

        return {
            "analyses": [a.to_dict() for a in analyses],
            "summary": {
                "total": total,
                "testable": testable_count,
                "needs_review": needs_review_count,
                "untestable": untestable_count,
                "mean_score": mean_score,
                "high_risk": high_risk_count,
            },
            "requirements": [r.model_dump() for r in req_models],
        }

    @app.post("/api/requirements/generate-cases")
    def generate_cases_endpoint(req: GenerateCasesRequest) -> dict[str, Any]:
        """Automatically synthesize candidate test cases from requirements using AI generator."""
        req_models = [_dict_to_requirement(r, idx) for idx, r in enumerate(req.requirements, start=1)]

        if not req_models:
            raise HTTPException(status_code=400, detail="No requirements provided for test case generation.")

        domain_context = ""
        if req.domain:
            try:
                pack = dom.load_pack(req.domain)
                domain_context = f"Domain: {pack.name}\nRules: {', '.join(pack.rules)}"
            except Exception:
                pass

        if req.agent_mode == "mock":
            router = _build_mock_router_for_evaluation([], req_models)
        else:
            router = ModelRouter()

        try:
            raw_generated = run_generator(router, req_models, context=domain_context)
        except Exception:
            fallback_router = _build_mock_router_for_evaluation([], req_models)
            raw_generated = run_generator(fallback_router, req_models, context=domain_context)

        # Build map of generated cases by requirement id
        raw_by_req: dict[str, list[dict[str, Any]]] = {}
        for item in raw_generated:
            r_id = str(item.get("req_id", ""))
            raw_by_req[r_id] = item.get("cases", [])

        generated_cases: list[dict[str, Any]] = []
        for r in req_models:
            recommended = derive_suggested_scenarios(r, r.risk or "medium")
            cases_for_r = raw_by_req.get(r.id, [])

            # Ensure all recommended scenarios are covered; if generator omitted any,
            # supplement from the recommended scenarios so the numbers add up exactly
            existing_types = {c.get("type") for c in cases_for_r}
            final_cases_list = list(cases_for_r)
            for rec in recommended:
                if rec["type"] not in existing_types:
                    final_cases_list.append({
                        "type": rec["type"],
                        "scenario": rec["scenario"],
                        "expected": rec.get("expected", "System behaves strictly according to specification."),
                    })
                    existing_types.add(rec["type"])

            # Number test cases starting from 01 for this specific requirement
            for idx, c in enumerate(final_cases_list, start=1):
                case_id = f"TC-{r.id}-{idx:02d}"
                case_type = c.get("type", "Positive")
                if case_type not in ("Positive", "Negative", "Boundary", "Calculation", "Security"):
                    case_type = "Positive"
                generated_cases.append({
                    "id": case_id,
                    "requirement_id": r.id,
                    "type": case_type,
                    "scenario": c.get("scenario", f"Verify requirement {r.id} behavior."),
                    "expected": c.get("expected", "System behaves strictly according to specification."),
                    "source": "generated",
                })

        return {
            "cases": generated_cases,
            "cases_count": len(generated_cases),
        }


    @app.post("/api/evaluate", response_model=EvaluateResponse)
    def evaluate(req: EvaluateRequest) -> EvaluateResponse:
        """Run gate evaluation on submitted requirements and test cases."""
        if not req.cases:
            raise HTTPException(status_code=400, detail="No test cases provided for evaluation.")

        # Convert dicts to Pydantic models
        try:
            req_models: list[Requirement] = [
                _dict_to_requirement(r, idx) for idx, r in enumerate(req.requirements, start=1)
            ]
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid requirement: {exc}")

        case_models: list[TestCase] = []
        for c in req.cases:
            try:
                type_val = c.get("type", "Positive")
                try:
                    case_type_enum = CaseType(type_val)
                except ValueError:
                    case_type_enum = CaseType.positive
                src_val = c.get("source", "uploaded")
                if src_val not in ("answer_key", "generated", "seeded_bad", "uploaded"):
                    src_val = "uploaded"
                case_models.append(TestCase(
                    id=c["id"],
                    requirement_id=c.get("requirement_id", ""),
                    type=case_type_enum,
                    scenario=c.get("scenario", ""),
                    expected=c.get("expected", ""),
                    source=src_val,
                ))
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid test case {c.get('id')}: {exc}")

        # Domain context
        domain_context = ""
        pack = None
        if req.domain:
            try:
                pack = dom.load_pack(req.domain)
                domain_context = f"Domain: {pack.name}\nRules: {', '.join(pack.rules)}"
            except Exception:
                pass

        # 1. Rule Checks
        findings: list[Finding] = []
        if req.use_rules:
            findings = run_rules(req_models, case_models)

        # 2. Agent / LLM Checks with safe failover
        checks: list[CheckResult] = []
        if req.use_agents:
            run_res = None
            if req.agent_mode == "mock":
                router = _build_mock_router_for_evaluation(case_models, req_models)
                orchestrator = Orchestrator(router, OrchestratorConfig(run_analyst=True, run_arbiter=True))
                run_res = orchestrator.run(req_models, case_models, context=domain_context)
                checks = run_res.all_checks()
            else:
                try:
                    router = ModelRouter()
                    orchestrator = Orchestrator(router, OrchestratorConfig(run_analyst=True, run_arbiter=True))
                    run_res = orchestrator.run(req_models, case_models, context=domain_context)
                    checks = run_res.all_checks()
                except Exception as exc:
                    run_res = None
                    for c in case_models:
                        findings.append(Finding(
                            case_id=c.id,
                            code="LIVE_LLM_FAILURE",
                            decision="human_review",
                            reason=f"Live model evaluation failed: {exc}",
                            requirement_id=c.requirement_id or None,
                        ))

            if run_res and run_res.exhausted_case_ids:
                for cid in run_res.exhausted_case_ids:
                    findings.append(Finding(
                        case_id=cid,
                        code="ALL_MODELS_EXHAUSTED",
                        decision="human_review",
                        reason="No model could judge this case.",
                        requirement_id=None,
                    ))

            # Cases with zero model checks when agents are requested get human review
            cases_with_model_checks = {c.case_id for c in checks if c.source == "model"}
            for c in case_models:
                if c.id not in cases_with_model_checks:
                    if not any(f.case_id == c.id and f.decision in ("reject", "revise", "human_review") for f in findings):
                        findings.append(Finding(
                            case_id=c.id,
                            code="UNJUDGED",
                            decision="human_review",
                            reason="No model could judge this case.",
                            requirement_id=c.requirement_id or None,
                        ))

        # 3. Gate Engine
        domain_thresh = pack.thresholds if pack else None
        engine = GateEngine(threshold_override=req.approve_threshold, domain_thresholds=domain_thresh)
        case_ids = [c.id for c in case_models]
        gate_results = engine.run(case_ids, findings, checks)

        # 4. Summaries and Reports
        approved = sum(1 for r in gate_results if r.decision == "approve")
        revised = sum(1 for r in gate_results if r.decision == "revise")
        rejected = sum(1 for r in gate_results if r.decision == "reject")
        human_review = sum(1 for r in gate_results if r.decision == "human_review")
        total = len(gate_results)

        all_means = [
            (sum(r.scores.values()) / len(r.scores)) if r.scores else 1.0
            for r in gate_results
        ]
        mean_score = sum(all_means) / total if total > 0 else 1.0

        summary = SummaryStats(
            total_cases=total,
            approved=approved,
            revised=revised,
            rejected=rejected,
            human_review=human_review,
            mean_score=round(mean_score, 4),
        )

        # Requirement-level findings (case_id is None)
        req_findings = [
            FindingItem(
                case_id=f.case_id,
                code=f.code,
                decision=f.decision,
                reason=f.reason,
                requirement_id=f.requirement_id,
            )
            for f in findings
            if f.case_id is None
        ]

        case_req_map = {c.id: c.requirement_id for c in case_models}
        result_items = [
            GateResultItem(
                case_id=r.case_id,
                decision=r.decision,
                reasons=r.reasons,
                scores=r.scores,
                models_used=r.models_used,
                mean_score=round((sum(r.scores.values()) / len(r.scores)) if r.scores else 1.0, 4),
                analysis=_build_decision_analysis(
                    r.decision,
                    r.case_id,
                    r.reasons,
                    (sum(r.scores.values()) / len(r.scores)) if r.scores else 1.0,
                    case_req_map.get(r.case_id),
                ),
                human_override=False,
            )
            for r in gate_results
        ]

        reports = {
            "markdown": to_markdown(gate_results),
            "html": to_html(gate_results),
            "json": to_json(gate_results),
            "csv": to_csv(gate_results),
        }

        return EvaluateResponse(
            summary=summary,
            gate_results=result_items,
            requirement_findings=req_findings,
            reports=reports,
        )


    @app.post("/api/export/{fmt}")
    def export_report(fmt: Literal["json", "csv", "md", "html"], req: ExportRequest) -> Response:
        """Export evaluation results to downloadable file."""
        gate_models: list[GateResult] = []
        for r in req.results:
            gate_models.append(GateResult(
                case_id=r["case_id"],
                decision=r["decision"],
                reasons=r.get("reasons", []),
                scores=r.get("scores", {}),
                models_used=r.get("models_used", []),
            ))

        content_types = {
            "json": "application/json",
            "csv": "text/csv",
            "md": "text/markdown",
            "html": "text/html",
        }
        formatters = {
            "json": to_json,
            "csv": to_csv,
            "md": to_markdown,
            "html": to_html,
        }

        body = formatters[fmt](gate_models)
        filename = f"verdict_report.{fmt}"

        return Response(
            content=body,
            media_type=content_types[fmt],
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/cases/export/{fmt}")
    def export_test_cases(fmt: Literal["json", "csv"], cases: list[dict[str, Any]]) -> Response:
        """Export candidate test cases as JSON or CSV file, grouped by requirement ID with group breaks."""
        from collections import OrderedDict
        groups: dict[str, list[dict[str, Any]]] = OrderedDict()
        for c in cases:
            rid = str(c.get("requirement_id") or "UNASSIGNED")
            groups.setdefault(rid, []).append(c)

        if fmt == "json":
            body = json.dumps({"groups": groups, "cases": cases}, indent=2)
            content_type = "application/json"
        else:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["id", "requirement_id", "type", "scenario", "expected", "source"])
            first_group = True
            for rid, grp in groups.items():
                if not first_group:
                    # Provide empty break row between requirement groups
                    w.writerow([])
                w.writerow([f"=== REQUIREMENT: {rid} ({len(grp)} test cases) ===", "", "", "", "", ""])
                first_group = False
                for c in grp:
                    w.writerow([
                        c.get("id", ""),
                        c.get("requirement_id", ""),
                        c.get("type", ""),
                        c.get("scenario", ""),
                        c.get("expected", ""),
                        c.get("source", "uploaded"),
                    ])
            body = buf.getvalue()
            content_type = "text/csv"

        filename = f"test_cases.{fmt}"
        return Response(
            content=body,
            media_type=content_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/requirements/export/{fmt}")
    def export_requirements_review(fmt: Literal["json", "csv", "md", "html"], req: ExportRequirementsReviewRequest) -> Response:
        """Export analyzed requirements with quality review, ambiguities, and remediation guides."""
        req_models = [_dict_to_requirement(r, idx) for idx, r in enumerate(req.requirements, start=1)]

        if not req_models:
            raise HTTPException(status_code=400, detail="No requirements provided for review export.")

        analyses: list[RequirementAnalysis] = []
        if req.analyses:
            for a in req.analyses:
                issues = [
                    RequirementIssue(
                        code=i.get("code", "quality_issue"),
                        severity=i.get("severity", "warning"),
                        message=i.get("message", ""),
                        suggestion=i.get("suggestion", ""),
                    )
                    for i in a.get("issues", [])
                ]
                analyses.append(RequirementAnalysis(
                    req_id=a["req_id"],
                    title=a.get("title", a["req_id"]),
                    testability_score=float(a.get("testability_score", 1.0)),
                    testability_verdict=a.get("testability_verdict", "testable"),
                    risk=a.get("risk", "medium"),
                    risk_score=float(a.get("risk_score", 0.85 if a.get("risk") == "high" else (0.20 if a.get("risk") == "low" else 0.50))),
                    priority=str(a.get("priority", "P0 (High)" if a.get("risk") == "high" else ("P2 (Low)" if a.get("risk") == "low" else "P1 (Medium)"))),
                    issues=issues,
                    suggested_scenarios=a.get("suggested_scenarios", []),
                    summary=a.get("summary", ""),
                ))
        else:
            domain_context = ""
            if req.domain:
                try:
                    pack = dom.load_pack(req.domain)
                    domain_context = f"Domain: {pack.name}\nRules: {', '.join(pack.rules)}"
                except Exception:
                    pass
            if req.agent_mode == "mock":
                router = _build_mock_router_for_evaluation([], req_models)
            else:
                router = ModelRouter()
            analyses = analyze_requirements(req_models, router=router, context=domain_context)

        content_types = {
            "json": "application/json",
            "csv": "text/csv; charset=utf-8",
            "md": "text/markdown; charset=utf-8",
            "html": "text/html; charset=utf-8",
        }
        formatters = {
            "json": requirements_review_to_json,
            "csv": requirements_review_to_csv,
            "md": requirements_review_to_markdown,
            "html": requirements_review_to_html,
        }

        body = formatters[fmt](analyses, req_models)
        filename = f"requirements_review_report.{fmt}"

        return Response(
            content=body,
            media_type=content_types[fmt],
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return app


app = create_app()

