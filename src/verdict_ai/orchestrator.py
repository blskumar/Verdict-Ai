"""Orchestrator: runs all agents in order and returns CheckResults for every case.

Flow:
  1. analyst   → CheckResult per requirement (flags bad reqs)
  2. reviewer  → CheckResult per case (coverage + correctness)
  3. checker   → CheckResult per case (hallucination + traceability)
  4. arbiter   → CheckResult per *disputed* case only (reviewer ≠ checker)

The orchestrator owns batching, context injection, and progress tracking.
It works with any ModelRouter — including MockModel.as_router().
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from verdict_ai.agents.analyst import run_analyst
from verdict_ai.agents.arbiter import run_arbiter
from verdict_ai.agents.checker import run_checker
from verdict_ai.agents.reviewer import run_reviewer
from verdict_ai.llm import AllModelsExhausted, AuthenticationError, ModelRouter
from verdict_ai.models import CheckResult, Requirement, TestCase

# Default batch sizes (overridable per run)
_DEFAULT_BATCH = 5


@dataclass
class OrchestratorConfig:
    batch_size: int = _DEFAULT_BATCH
    run_analyst: bool = True
    run_arbiter: bool = True
    # resume file: if set, save progress after each stage so runs can continue
    resume_path: Path | None = None


@dataclass
class RunResult:
    """All CheckResults from one orchestrator run."""
    analyst_results: list[CheckResult] = field(default_factory=list)
    reviewer_results: list[CheckResult] = field(default_factory=list)
    checker_results: list[CheckResult] = field(default_factory=list)
    arbiter_results: list[CheckResult] = field(default_factory=list)
    exhausted_case_ids: list[str] = field(default_factory=list)  # models ran out
    auth_error: str | None = None  # Authentication error details if occurred

    def all_checks(self) -> list[CheckResult]:
        """Flat list: arbiter results replace reviewer/checker for disputed cases."""
        arbiter_ids = {r.case_id for r in self.arbiter_results}
        base = [
            r for r in self.reviewer_results + self.checker_results
            if r.case_id not in arbiter_ids
        ]
        return self.analyst_results + base + self.arbiter_results


class Orchestrator:
    def __init__(
        self,
        router: ModelRouter,
        config: OrchestratorConfig | None = None,
    ) -> None:
        self.router = router
        self.cfg = config or OrchestratorConfig()

    def run(
        self,
        reqs: list[Requirement],
        cases: list[TestCase],
        context: str = "",
        *,
        on_progress: Callable[[str], None] | None = None,
    ) -> RunResult:
        """Run analyst → reviewer → checker → arbiter.

        on_progress: optional callback called with a status string after each stage.
        Returns RunResult. exhausted_case_ids are cases where all models ran out —
        the caller should treat these as human_review.
        """
        result = RunResult()
        bs = self.cfg.batch_size

        # Load from resume_path if available
        if self.cfg.resume_path and Path(self.cfg.resume_path).exists():
            try:
                res_data = json.loads(Path(self.cfg.resume_path).read_text(encoding="utf-8"))
                result.analyst_results = [CheckResult(**d) for d in res_data.get("analyst", [])]
                result.reviewer_results = [CheckResult(**d) for d in res_data.get("reviewer", [])]
                result.checker_results = [CheckResult(**d) for d in res_data.get("checker", [])]
                result.arbiter_results = [CheckResult(**d) for d in res_data.get("arbiter", [])]
                result.exhausted_case_ids = list(res_data.get("exhausted", []))
            except Exception:
                pass

        def _progress(msg: str) -> None:
            if on_progress:
                on_progress(msg)

        # ── Stage 1: analyst ───────────────────────────────────────────────
        if self.cfg.run_analyst and reqs:
            if not result.analyst_results:
                _progress(f"analyst: reviewing {len(reqs)} requirements …")
                try:
                    result.analyst_results = run_analyst(
                        self.router, reqs, context=context, batch_size=bs
                    )
                except AuthenticationError as e:
                    result.auth_error = str(e)
                    _progress(f"analyst: {e}")
                except AllModelsExhausted:
                    _progress("analyst: all models exhausted — skipping requirement review.")
                self._save_progress(result)
            else:
                _progress(f"analyst: resumed {len(result.analyst_results)} requirements from saved progress.")

        # ── Stage 2: reviewer ──────────────────────────────────────────────
        _progress(f"reviewer: reviewing {len(cases)} cases …")
        reviewer_done: list[CheckResult] = list(result.reviewer_results)
        exhausted_ids: list[str] = []
        already_reviewed = {r.case_id for r in reviewer_done}
        cases_to_review = [c for c in cases if c.id not in already_reviewed]

        for i in range(0, len(cases_to_review), bs):
            batch = cases_to_review[i : i + bs]
            _progress(f"reviewer: batch {i // bs + 1} of {-(-len(cases_to_review) // bs)} ({len(batch)} cases) ...")
            if result.auth_error:
                exhausted_ids.extend(c.id for c in batch)
                continue
            try:
                reviewer_done.extend(
                    run_reviewer(self.router, batch, reqs, context=context, batch_size=bs)
                )
            except AuthenticationError as e:
                result.auth_error = str(e)
                exhausted_ids.extend(c.id for c in batch)
                _progress(f"reviewer: {e} — aborting remaining live calls.")
            except AllModelsExhausted:
                exhausted_ids.extend(c.id for c in batch)
                _progress(f"reviewer: models exhausted on batch {i // bs + 1} — {len(batch)} cases marked exhausted.")
        result.reviewer_results = reviewer_done
        for cid in exhausted_ids:
            if cid not in result.exhausted_case_ids:
                result.exhausted_case_ids.append(cid)
        self._save_progress(result)

        # ── Stage 3: checker ───────────────────────────────────────────────
        _progress(f"checker: checking {len(cases)} cases …")
        checker_done: list[CheckResult] = list(result.checker_results)
        already_checked = {c.case_id for c in checker_done}
        cases_to_check = [c for c in cases if c.id not in already_checked]

        for i in range(0, len(cases_to_check), bs):
            batch = cases_to_check[i : i + bs]
            _progress(f"checker: batch {i // bs + 1} of {-(-len(cases_to_check) // bs)} ({len(batch)} cases) ...")
            if result.auth_error:
                for c in batch:
                    if c.id not in result.exhausted_case_ids:
                        result.exhausted_case_ids.append(c.id)
                continue
            try:
                checker_done.extend(
                    run_checker(self.router, batch, reqs, context=context, batch_size=bs)
                )
            except AuthenticationError as e:
                result.auth_error = str(e)
                for c in batch:
                    if c.id not in result.exhausted_case_ids:
                        result.exhausted_case_ids.append(c.id)
                _progress(f"checker: {e} — aborting remaining live calls.")
            except AllModelsExhausted:
                for c in batch:
                    if c.id not in result.exhausted_case_ids:
                        result.exhausted_case_ids.append(c.id)
                _progress(f"checker: models exhausted on batch {i // bs + 1}.")
        result.checker_results = checker_done
        self._save_progress(result)

        # ── Stage 4: arbiter (disputed only) ───────────────────────────────
        if self.cfg.run_arbiter and result.reviewer_results and result.checker_results and not result.auth_error:
            disputed_count = _count_disputes(result.reviewer_results, result.checker_results)
            if disputed_count:
                _progress(f"arbiter: resolving {disputed_count} disputed cases …")
                try:
                    result.arbiter_results = run_arbiter(
                        self.router,
                        cases,
                        reqs,
                        result.reviewer_results,
                        result.checker_results,
                        context=context,
                        batch_size=bs,
                    )
                except AuthenticationError as e:
                    result.auth_error = str(e)
                    _progress(f"arbiter: {e}")
                except AllModelsExhausted:
                    _progress("arbiter: all models exhausted — disputed cases left unresolved.")
                self._save_progress(result)

        _progress(
            f"done — {len(result.all_checks())} checks produced, "
            f"{len(result.exhausted_case_ids)} cases exhausted."
        )
        return result

    def _save_progress(self, result: RunResult) -> None:
        if not self.cfg.resume_path:
            return
        try:
            data = {
                "analyst": [r.model_dump() for r in result.analyst_results],
                "reviewer": [r.model_dump() for r in result.reviewer_results],
                "checker": [r.model_dump() for r in result.checker_results],
                "arbiter": [r.model_dump() for r in result.arbiter_results],
                "exhausted": result.exhausted_case_ids,
            }
            Path(self.cfg.resume_path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass


def _count_disputes(
    reviewer_results: list[CheckResult],
    checker_results: list[CheckResult],
) -> int:
    from verdict_ai.agents.arbiter import _is_disputed
    rv_map = {r.case_id: r for r in reviewer_results}
    ch_map = {r.case_id: r for r in checker_results}
    return sum(
        1 for cid in rv_map
        if cid in ch_map and _is_disputed(rv_map[cid], ch_map[cid])
    )
