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


class SeededBadCase(TestCase):
    """A deliberately flawed case. The labels say what the gate should do."""

    source: Literal["answer_key", "generated", "seeded_bad", "uploaded"] = "seeded_bad"
    defect: str  # short code, for example "wrong_arithmetic"
    defect_note: str  # one line saying what is wrong
    base_id: str | None = None  # the good case it was bent from, if any
    expected_decision: Decision
