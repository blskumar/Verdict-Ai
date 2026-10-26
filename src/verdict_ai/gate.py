"""Gate engine: combines rule Findings and model CheckResults into GateResults."""
from __future__ import annotations

from pathlib import Path

import yaml

from verdict_ai.models import CheckResult, Decision, GateResult
from verdict_ai.rules import Finding

_PRIORITY: list[Decision] = ["reject", "revise", "human_review", "approve"]


def _priority(d: Decision) -> int:
    try:
        return _PRIORITY.index(d)
    except ValueError:
        return len(_PRIORITY)


class GateEngine:
    def __init__(
        self,
        config_path: str | Path = "gate.yaml",
        threshold_override: float | None = None,
        domain_thresholds: dict | None = None,
    ) -> None:
        config_file = Path(config_path)
        pkg_fallback = Path(__file__).resolve().parent / "gate.yaml"
        if config_path == "gate.yaml" and pkg_fallback.exists():
            config_file = pkg_fallback
        elif not config_file.exists() and pkg_fallback.exists():
            config_file = pkg_fallback

        if config_file.exists():
            with open(config_file, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        else:
            cfg = {}

        thresh = cfg.get("thresholds", {})
        if domain_thresholds:
            if "revise_below" in domain_thresholds:
                thresh["revise_max"] = domain_thresholds["revise_below"]
                thresh["human_review_min"] = domain_thresholds["revise_below"]
            if "human_review_band" in domain_thresholds:
                band = domain_thresholds["human_review_band"]
                if isinstance(band, (list, tuple)) and len(band) >= 2:
                    thresh["human_review_min"] = band[0]
                    thresh["approve"] = band[1]
            for k in ("approve", "human_review_min", "revise_max"):
                if k in domain_thresholds:
                    thresh[k] = domain_thresholds[k]

        self.approve_threshold: float = (
            threshold_override if threshold_override is not None
            else thresh.get("approve", 0.85)
        )
        self.human_review_min: float = thresh.get("human_review_min", 0.70)
        self.revise_max: float = thresh.get("revise_max", self.human_review_min)

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def decide(
        self,
        case_id: str,
        findings: list[Finding],
        checks: list[CheckResult],
    ) -> GateResult:
        """Combine rule findings and model checks for one case → GateResult."""
        case_findings = [f for f in findings if f.case_id == case_id]
        case_checks = [c for c in checks if c.case_id == case_id]

        scores: dict[str, float] = {}
        reasons: list[str] = []
        models_seen: set[str] = set()
        best_decision: Decision = "approve"

        # Rule findings → synthetic scores
        for i, f in enumerate(case_findings):
            key = f"rule_{f.code}_{i}"
            score_map: dict[str, float] = {
                "reject": 0.0,
                "revise": 0.5,
                "human_review": 0.72,
                "approve": 1.0,
            }
            score = score_map.get(f.decision, 1.0)
            scores[key] = score
            reasons.append(f.reason)
            if _priority(f.decision) < _priority(best_decision):
                best_decision = f.decision

        # Model check results
        for c in case_checks:
            scores[c.check_name] = c.score
            if not c.passed:
                reasons.append(c.reason)
                # Infer decision from score
                if c.score < self.human_review_min:
                    inferred: Decision = "revise"
                elif c.score < self.approve_threshold:
                    inferred = "human_review"
                else:
                    inferred = "approve"
                if _priority(inferred) < _priority(best_decision):
                    best_decision = inferred
            if c.model_name:
                models_seen.add(c.model_name)

        # Overall score → score-based decision floor
        all_scores = list(scores.values())
        overall = sum(all_scores) / len(all_scores) if all_scores else 1.0

        if best_decision == "approve":
            # No explicit trigger → fall through to score thresholds
            if overall < self.human_review_min:
                best_decision = "revise"
            elif overall < self.approve_threshold:
                best_decision = "human_review"

        if not reasons:
            # Every decision carries a reason, including approvals and score-floor decisions.
            ran = f"{len(case_checks)} model check(s)" if case_checks else "no model check (rules only)"
            if best_decision == "approve":
                reasons.append(f"Approved: no rule finding and no failed check; mean score {overall:.2f} is at or above "
                               f"the approval threshold {self.approve_threshold:.2f} ({ran}).")
            elif best_decision == "human_review":
                reasons.append(f"Mean score {overall:.2f} is below the approval threshold {self.approve_threshold:.2f}; a person should decide.")
            else:
                reasons.append(f"Mean score {overall:.2f} is below the review band ({self.human_review_min:.2f}); rework the case.")

        return GateResult(
            case_id=case_id,
            decision=best_decision,
            reasons=reasons,
            scores=scores,
            models_used=sorted(models_seen),
        )

    def run(
        self,
        case_ids: list[str],
        findings: list[Finding],
        checks: list[CheckResult],
    ) -> list[GateResult]:
        """Run decide() for each case_id."""
        return [self.decide(cid, findings, checks) for cid in case_ids]
