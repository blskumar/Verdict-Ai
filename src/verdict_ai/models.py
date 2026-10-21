"""Data contracts. Every other module reads and writes these shapes."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class CaseType(str, Enum):
    positive = "Positive"
    negative = "Negative"
    boundary = "Boundary"
    calculation = "Calculation"


class Requirement(BaseModel):
    id: str
    title: str
    text: str
    acceptance_criteria: str = ""
    domain: str | None = None  # path such as "insurance/life/term"
    risk: Literal["low", "medium", "high"] | None = None  # beats pack risk tags


class TestCase(BaseModel):
    __test__ = False  # not a pytest class

    id: str
    requirement_id: str
    type: CaseType | None = None
    scenario: str
    expected: str
    source: Literal["answer_key", "generated", "seeded_bad", "uploaded"] = "uploaded"


Decision = Literal["approve", "revise", "reject", "human_review"]

CheckSource = Literal["rule", "model"]


class CheckResult(BaseModel):
    """One check outcome for one test case, from either a rule or a model."""

    check_name: str  # e.g. "vague_expected" or "scenario_reviewer"
    case_id: str
    passed: bool  # True = no problem found
    score: float = Field(ge=0.0, le=1.0)  # 0 = worst, 1 = best
    reason: str
    source: CheckSource
    model_name: str | None = None  # None for rule-based checks


class GateResult(BaseModel):
    """Final aggregated verdict for one test case."""

    case_id: str
    decision: Decision
    reasons: list[str]  # one entry per contributing check
    scores: dict[str, float]  # check_name -> score
    models_used: list[str]  # model names that contributed (deduplicated)


class SeededBadCase(TestCase):
    """A deliberately flawed case. The labels say what the gate should do."""

    source: Literal["answer_key", "generated", "seeded_bad", "uploaded"] = "seeded_bad"
    defect: str  # short code, for example "wrong_arithmetic"
    defect_note: str  # one line saying what is wrong
    base_id: str | None = None  # the good case it was bent from, if any
    expected_decision: Decision
