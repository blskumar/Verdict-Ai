"""Requirement recognition and deep analysis engine.

Recognizes requirements from raw text or structured documents, evaluates
their testability, flags ambiguities, and suggests test coverage scenarios.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass, field
from io import StringIO
from typing import Any, Literal

from verdict_ai.agents.analyst import run_analyst
from verdict_ai.domain_review import hints_for
from verdict_ai.domains import Pack
from verdict_ai.llm import ModelRouter
from verdict_ai.loader import Requirement, _universal_requirements_fallback
from verdict_ai.models import CheckResult

# Vague words and subjective qualifiers that reduce testability
VAGUE_TERMS = re.compile(
    r"\b(behaves? correctly|works?( correctly| fine| as expected| properly)?|is fine|are fine|"
    r"appropriate|as expected|properly|successfully|everything|correct (result|behaviou?r)|"
    r"fast|slow|speedy|user-?friendly|intuitive|seamless(ly)?|robust|reasonable( time)?|"
    r"normal( load| conditions)?|sufficient|adequate|flexible|modern|easy|easily)\b",
    re.I,
)

# Ambiguous split outcomes without rule definition
AMBIGUOUS_SPLIT = re.compile(
    r"\b(blocked|rejected|declined|referred|accepted|approved|sent to \w+)\b[^.]*\bor\b[^.]*"
    r"\b(blocked|rejected|declined|referred|accepted|approved|sent to \w+|review)\b",
    re.I,
)
CONFIGURED_CONDITION = re.compile(r"\b(per|according to|as configured|configured|if|when|provided)\b", re.I)

# High risk topics
HIGH_RISK_TERMS = re.compile(
    r"\b(payment|credit card|auth|login|password|token|encrypt|pii|phi|compliance|gdpr|hipaa|"
    r"underwriting|premium|death benefit|beneficiary|dispute|wire|transfer|legal|pci|ssn)\b",
    re.I,
)

# Numerical / boundary indicators without concrete values
NUMERIC_INDICATOR = re.compile(
    r"\b(maximum|minimum|threshold|quota|timeout|latency)\b|"
    r"\b(?:age|size|time|amount|balance)\s+limits?\b|"
    r"\blimits?\s+(?:of|to|for)\b|"
    r"\bwithin\s+(?:a\s+)?configured\s+(?:time|period|limit)\b",
    re.I,
)
HAS_NUMBER = re.compile(
    r"\b(?:\d+(?:\.\d+)?(?:%|s|ms|sec|min|hr|mb|gb|kb|usd|\$)?|"
    r"zero|one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million)\b",
    re.I,
)


@dataclass
class RequirementIssue:
    code: str
    severity: Literal["error", "warning", "info"]
    message: str
    suggestion: str


@dataclass
class RequirementAnalysis:
    req_id: str
    title: str
    testability_score: float  # 0.0 to 1.0
    testability_verdict: Literal["testable", "needs_review", "untestable"]
    risk: Literal["high", "medium", "low"]
    risk_score: float = 0.5  # 0.0 to 1.0
    priority: str = "P1 (Medium)"  # P0 (High), P1 (Medium), P2 (Low)
    issues: list[RequirementIssue] = field(default_factory=list)
    suggested_scenarios: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_raw_requirements_text(text: str) -> list[Requirement]:
    """Parse raw plain text, user stories, or pasted specifications into Requirement objects."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    return _universal_requirements_fallback(lines)


def analyze_requirement(
    req: Requirement,
    *,
    model_check: CheckResult | None = None,
    pack: Pack | None = None,
) -> RequirementAnalysis:
    """Perform deterministic rule and model-assisted quality analysis on a single requirement.

    pack: the selected domain pack. Its vague_terms and ambiguous_phrases are checked on top of the built-in
    lists, and its boundary hints are offered as extra scenarios.
    """
    issues: list[RequirementIssue] = []
    base_score = 1.0
    combined_text = f"{req.title} {req.text} {req.acceptance_criteria}".strip()

    # 1. Check for vague/subjective language
    vague_matches = list(VAGUE_TERMS.finditer(combined_text))
    if vague_matches:
        vague_words = list({m.group(0) for m in vague_matches})
        issues.append(
            RequirementIssue(
                code="vague_terms",
                severity="warning",
                message=f"Requirement contains subjective or unquantifiable phrasing: {', '.join(vague_words)}.",
                suggestion="Replace qualitative terms with verifiable criteria (e.g. 'under 2.0s', 'displays error ERR-01').",
            )
        )
        base_score -= 0.15 * min(len(vague_words), 3)

    if pack:
        low = combined_text.lower()
        pack_vague = [t for t in pack.vague_terms if re.search(rf"\b{re.escape(t.lower())}\b", low)]
        if pack_vague:
            issues.append(RequirementIssue(
                code="domain_vague_terms", severity="warning",
                message=f"Vague for {pack.name}: {', '.join(pack_vague)}.",
                suggestion="Replace with the configured value, limit or named rule the domain uses."))
            base_score -= 0.10 * min(len(pack_vague), 3)
        pack_ambiguous = [p for p in pack.ambiguous_phrases if p.lower() in low]
        if pack_ambiguous:
            issues.append(RequirementIssue(
                code="domain_ambiguous_phrases", severity="warning",
                message=f"Ambiguous for {pack.name}: '{'; '.join(pack_ambiguous)}'.",
                suggestion="Say who decides, on what condition, and what happens in each case."))
            base_score -= 0.10 * min(len(pack_ambiguous), 2)

    # 2. Check for missing acceptance criteria
    if not req.acceptance_criteria or len(req.acceptance_criteria.strip()) < 10:
        issues.append(
            RequirementIssue(
                code="missing_acceptance_criteria",
                severity="error",
                message="Acceptance criteria are missing or too sparse for unambiguous verification.",
                suggestion="Add explicit 'Given-When-Then' or concrete acceptance rules specifying expected system behavior.",
            )
        )
        base_score -= 0.25

    # 3. Check for ambiguous outcomes without configured condition
    if AMBIGUOUS_SPLIT.search(combined_text) and not CONFIGURED_CONDITION.search(combined_text):
        issues.append(
            RequirementIssue(
                code="ambiguous_outcomes",
                severity="error",
                message="Requirement allows multiple conflicting outcomes without a clear deciding condition.",
                suggestion="Define the exact condition that triggers each outcome (e.g., 'approve if score >= 700, else refer').",
            )
        )
        base_score -= 0.20

    # 4. Check for boundary indicators without numeric limits
    if NUMERIC_INDICATOR.search(combined_text) and not HAS_NUMBER.search(combined_text):
        issues.append(
            RequirementIssue(
                code="unspecified_threshold",
                severity="warning",
                message="Mentions threshold, limit, or timing without specifying exact numeric boundaries.",
                suggestion="State specific numeric boundaries (e.g., age limits 18-65, timeout 3000ms).",
            )
        )
        base_score -= 0.10

    # 5. Evaluate Risk & Derive Numeric Risk Score and Priority
    if req.risk in ("high", "medium", "low"):
        risk = req.risk
    elif HIGH_RISK_TERMS.search(combined_text):
        risk = "high"
    elif re.search(r"\b(shall|must|mandatory|critical)\b", combined_text, re.I):
        risk = "high"
    else:
        risk = "medium"

    # Base risk score: High=0.85, Medium=0.50, Low=0.20
    base_risk_score = 0.85 if risk == "high" else (0.50 if risk == "medium" else 0.20)
    # Critical defects or ambiguities increase operational risk
    if any(i.severity == "error" for i in issues):
        base_risk_score = min(1.0, base_risk_score + 0.10)
    risk_score = round(base_risk_score, 2)

    # Priority mapped strictly from risk score
    if risk == "high" or risk_score >= 0.75:
        priority = "P0 (High)"
    elif risk == "medium" or risk_score >= 0.45:
        priority = "P1 (Medium)"
    else:
        priority = "P2 (Low)"

    # 6. Incorporate model agent findings if available
    if model_check:
        model_score = model_check.score
        base_score = (base_score * 0.5) + (model_score * 0.5)
        if not model_check.passed:
            issues.append(
                RequirementIssue(
                    code="model_analyst_flag",
                    severity="error" if model_score < 0.6 else "warning",
                    message=f"AI QA Analyst: {model_check.reason}",
                    suggestion="Refine requirement specification to satisfy automated QA testability checks.",
                )
            )

    final_score = max(0.05, min(1.0, round(base_score, 2)))

    # Determine verdict
    if final_score >= 0.80 and not any(i.severity == "error" for i in issues):
        verdict: Literal["testable", "needs_review", "untestable"] = "testable"
        summary = "Requirement is clear, bounded, and ready for test case authoring."
    elif final_score >= 0.55:
        verdict = "needs_review"
        summary = "Requirement is partially testable but contains ambiguities or missing criteria that need clarification."
    else:
        verdict = "untestable"
        summary = "Requirement is untestable in current form; requires explicit criteria and boundary definitions."

    # 7. Generate suggested test scenarios
    scenarios = _derive_suggested_scenarios(req, risk)
    if pack:
        scenarios += [{
            "type": "Boundary", "scenario": f"{hint} ({pack.name}).",
            "expected": "The outcome is stated on each side of the limit, and the limit itself is stated as inclusive or not.",
        } for hint in hints_for(combined_text, pack)]

    return RequirementAnalysis(
        req_id=req.id,
        title=req.title,
        testability_score=final_score,
        testability_verdict=verdict,
        risk=risk,
        risk_score=risk_score,
        priority=priority,
        issues=issues,
        suggested_scenarios=scenarios,
        summary=summary,
    )


def derive_suggested_scenarios(req: Requirement, risk: str) -> list[dict[str, str]]:
    """Derive standard recommended test scenario blueprints for this requirement."""
    title_short = req.title if len(req.title) < 50 else req.title[:47] + "..."
    scenarios: list[dict[str, str]] = [
        {
            "type": "Positive",
            "scenario": f"Verify valid workflow for {title_short} under standard inputs.",
            "expected": "System successfully executes action and reflects updated state per requirement specification.",
        },
        {
            "type": "Negative",
            "scenario": f"Submit invalid, missing, or malformed inputs for {title_short}.",
            "expected": "System blocks transaction, displays descriptive validation message, and prevents data corruption.",
        },
    ]

    has_numbers = bool(HAS_NUMBER.search(f"{req.text} {req.acceptance_criteria}"))
    if has_numbers or risk == "high":
        scenarios.append({
            "type": "Boundary",
            "scenario": f"Test edge thresholds and boundary conditions for {title_short}.",
            "expected": "Valid boundaries are accepted; off-by-one or out-of-range boundaries are strictly rejected.",
        })

    if risk == "high":
        scenarios.append({
            "type": "Security",
            "scenario": f"Attempt unauthorized access, state tampering, or concurrent modification on {title_short}.",
            "expected": "Request is rejected with authorization error; audit log records security event.",
        })

    return scenarios


_derive_suggested_scenarios = derive_suggested_scenarios


def analyze_requirements(
    reqs: list[Requirement],
    *,
    router: ModelRouter | None = None,
    context: str = "",
    pack: Pack | None = None,
) -> list[RequirementAnalysis]:
    """Run comprehensive analysis over a list of requirements."""
    model_checks: dict[str, CheckResult] = {}
    if router and reqs:
        try:
            results = run_analyst(router, reqs, context=context)
            for res in results:
                model_checks[res.case_id] = res
        except Exception:
            pass

    return [analyze_requirement(r, model_check=model_checks.get(r.id), pack=pack) for r in reqs]


def requirements_review_to_csv(analyses: list[RequirementAnalysis], reqs: list[Requirement] | None = None) -> str:
    req_map = {r.id: r for r in (reqs or [])}
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow([
        "requirement_id",
        "title",
        "risk",
        "risk_score",
        "priority",
        "testability_score",
        "testability_verdict",
        "quality_issues_count",
        "quality_issues",
        "remediation_suggestions",
        "suggested_scenarios",
        "original_text",
        "acceptance_criteria",
    ])
    for a in analyses:
        r = req_map.get(a.req_id)
        issues_str = " | ".join(f"[{i.code}] {i.message}" for i in a.issues) if a.issues else "None"
        suggestions_str = " | ".join(f"[{i.code}] {i.suggestion}" for i in a.issues) if a.issues else "Ready for test authoring."
        scenarios_str = " | ".join(f"{s['type']}: {s['scenario']}" for s in a.suggested_scenarios) if a.suggested_scenarios else "Standard happy path"
        orig_text = r.text if r else ""
        ac_text = r.acceptance_criteria if r else ""
        w.writerow([
            a.req_id,
            a.title,
            a.risk,
            f"{a.risk_score:.2f}",
            a.priority,
            f"{a.testability_score:.2f}",
            a.testability_verdict,
            len(a.issues),
            issues_str,
            suggestions_str,
            scenarios_str,
            orig_text,
            ac_text,
        ])
    return buf.getvalue()


def requirements_review_to_markdown(analyses: list[RequirementAnalysis], reqs: list[Requirement] | None = None, title: str = "Requirement Specification Review") -> str:
    req_map = {r.id: r for r in (reqs or [])}
    total = len(analyses)
    testable = sum(1 for a in analyses if a.testability_verdict == "testable")
    needs_review = sum(1 for a in analyses if a.testability_verdict == "needs_review")
    untestable = sum(1 for a in analyses if a.testability_verdict == "untestable")
    avg_score = (sum(a.testability_score for a in analyses) / total) if total else 1.0

    lines = [
        f"# {title}",
        "",
        "## Executive Summary",
        "",
        f"- **Total Requirements Analyzed:** {total}",
        f"- **Testable (Ready for QA):** {testable} ({round((testable/total)*100 if total else 0)}%)",
        f"- **Needs Review / Ambiguous:** {needs_review} ({round((needs_review/total)*100 if total else 0)}%)",
        f"- **Untestable:** {untestable} ({round((untestable/total)*100 if total else 0)}%)",
        f"- **Mean Quality Score:** {avg_score:.2f} / 1.00",
        "",
        "## Detailed Requirement Findings & Remediation Guide",
        "",
    ]

    for a in analyses:
        r = req_map.get(a.req_id)
        verdict_icon = "✅" if a.testability_verdict == "testable" else ("⚠️" if a.testability_verdict == "needs_review" else "❌")
        lines.append(f"### {verdict_icon} {a.req_id}: {a.title}")
        lines.append(f"- **Verdict:** `{a.testability_verdict.upper()}` ({round(a.testability_score * 100)}% quality score) | **Risk:** `{a.risk.upper()}` (Score: {a.risk_score:.2f}) | **Priority:** `{a.priority}`")
        if r and r.text:
            lines.append(f"- **Specification:** {r.text}")
        if r and r.acceptance_criteria:
            lines.append(f"- **Acceptance Criteria:** {r.acceptance_criteria}")

        if a.issues:
            lines.append("- **Quality Issues & Ambiguities:**")
            for iss in a.issues:
                lines.append(f"  - ⚠️ **[{iss.code}]** {iss.message}")
                lines.append(f"    - *How to fix:* {iss.suggestion}")
        else:
            lines.append("- **Quality Issues:** None detected. Specification is clear and verifiable.")

        if a.suggested_scenarios:
            lines.append("- **Recommended Test Coverage:**")
            for sc in a.suggested_scenarios:
                lines.append(f"  - `{sc['type']}`: {sc['scenario']}")

        lines.append("")

    return "\n".join(lines) + "\n"


def requirements_review_to_html(analyses: list[RequirementAnalysis], reqs: list[Requirement] | None = None, title: str = "Requirement Specification Review") -> str:
    import html
    rows_html = ""
    req_map = {r.id: r for r in (reqs or [])}
    for a in analyses:
        r = req_map.get(a.req_id)
        badge_cls = "badge-approve" if a.testability_verdict == "testable" else ("badge-revise" if a.testability_verdict == "needs_review" else "badge-reject")
        priority_cls = "badge-p0" if "P0" in a.priority else ("badge-p1" if "P1" in a.priority else "badge-p2")
        issues_list = "".join(f"<li><strong>[{html.escape(i.code)}]</strong> {html.escape(i.message)}<br><small style='color:#555'>Fix: {html.escape(i.suggestion)}</small></li>" for i in a.issues) if a.issues else "<em>No issues. Clear specification.</em>"
        scenarios_list = "".join(f"<li><code>{html.escape(s['type'])}</code>: {html.escape(s['scenario'])}</li>" for s in a.suggested_scenarios) if a.suggested_scenarios else "—"
        rows_html += f"""
        <tr>
          <td><strong>{html.escape(a.req_id)}</strong><br><small>{html.escape(a.risk.upper())} ({a.risk_score:.2f})</small></td>
          <td><span class="badge {priority_cls}">{html.escape(a.priority)}</span></td>
          <td>{html.escape(a.title)}<br><small style='color:#666'>{html.escape((r.text if r else ''))}</small></td>
          <td><span class="badge {badge_cls}">{html.escape(a.testability_verdict)}</span><br><small>{round(a.testability_score * 100)}%</small></td>
          <td><ul style='padding-left:16px;margin:0'>{issues_list}</ul></td>
          <td><ul style='padding-left:16px;margin:0'>{scenarios_list}</ul></td>
        </tr>
        """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 2rem; color: #1a1a1a; background: #f8f9fa; }}
    h1 {{ margin-bottom: 0.5rem; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,.1); border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid #eee; font-size: 0.85rem; vertical-align: top; }}
    th {{ background: #f1f3f5; font-weight: 600; color: #555; }}
    .badge {{ display: inline-flex; align-items: center; padding: 3px 9px; border-radius: 12px; font-size: 0.75rem; font-weight: 700; text-transform: uppercase; }}
    .badge-approve {{ background: #d3f9d8; color: #2b8a3e; }}
    .badge-revise {{ background: #fff3bf; color: #7c5800; }}
    .badge-reject {{ background: #ffe3e3; color: #c92a2a; }}
    .badge-p0 {{ background: #ffe3e3; color: #c92a2a; }}
    .badge-p1 {{ background: #fff3bf; color: #7c5800; }}
    .badge-p2 {{ background: #e7f5ff; color: #1c7ed6; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p style="color:#666;font-size:0.9rem">Review report for loaded requirements with quality scores, ambiguities flagged, and remediation suggestions.</p>
  <table>
    <thead><tr>
      <th>ID / Risk (Score)</th><th>Priority</th><th>Requirement Statement</th><th>Testability</th><th>Flagged Ambiguities & Remediation</th><th>Recommended Test Scenarios</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</body>
</html>"""


def requirements_review_to_json(analyses: list[RequirementAnalysis], reqs: list[Requirement] | None = None) -> str:
    req_map = {r.id: r.model_dump() for r in (reqs or [])}
    combined = []
    for a in analyses:
        item = a.to_dict()
        item["original_requirement"] = req_map.get(a.req_id, {})
        combined.append(item)
    return json.dumps(combined, indent=2)
