"""Rule checks. Plain code, no model. Each finding names a defect code, a suggested decision and a reason."""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .models import CaseType, Decision, Requirement, TestCase

# shortcut: word overlap only. Paraphrased dupes (SB-019..021) score 0.46-0.68, good-vs-good tops at 0.63,
# so 0.85 catches none; leave paraphrase detection to the model checks.
SIMILARITY = 0.85

VAGUE = re.compile(
    r"\b(behaves? correctly|works?( correctly| fine| as expected)?|is fine|are fine|appropriate|as expected|"
    r"properly|successfully|everything|correct (result|behaviou?r)|results? (is|are) fine)\b", re.I)
# "X or Y" between two outcomes: the case does not say which one must happen
AMBIGUOUS = re.compile(r"\b(blocked|rejected|declined|referred|accepted|approved|sent to \w+)\b[^.]*\bor\b[^.]*"
                       r"\b(blocked|rejected|declined|referred|accepted|approved|sent to \w+|review)\b", re.I)
# "blocked or referred per configured rule" is fine: the pack config picks the outcome
CONFIGURED = re.compile(r"\b(per|according to|as configured|configured)\b", re.I)
# A figure in the expected result must come from somewhere a person can check. These phrases count
# as naming that place; a domain pack can add its own (source_markers) and a case can fill source_ref.
SOURCE_MARKERS = re.compile(
    r"\b(rate (?:table|chart|sheet|file)s?|rating (?:table|manual)s?|fee schedule|price list|"
    r"worked example|example \d+|table [\w.-]+|section [\d.]+|clause [\d.]+|per spec|per requirement|"
    r"as per [A-Z]{2,}-)\b|§\s?\d", re.I)
MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
NUMBER = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(\s?%)?")
MAX_POOL = 15  # cap on source numbers tried for derivations, so coincidences stay rare
STOP = {"a", "an", "the", "is", "are", "of", "to", "and", "in", "on", "for", "with", "that", "it"}


@dataclass(frozen=True)
class Finding:
    case_id: str | None  # None for requirement-level findings
    code: str
    decision: Decision
    reason: str
    requirement_id: str | None = None


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9$%.]+", text.lower()) if w not in STOP]


def _similar(a: TestCase, b: TestCase) -> float:
    x, y = set(_words(a.scenario)), set(_words(b.scenario))
    jaccard = len(x & y) / len(x | y) if x | y else 0.0
    seq = SequenceMatcher(None, " ".join(_words(a.expected)), " ".join(_words(b.expected))).ratio()
    return (jaccard + seq) / 2


def _is_compound(c: TestCase) -> bool:
    # three or more separate actions joined by commas / "and": one case should test one thing
    return len(re.split(r",\s*(?:and\s+)?|\s+and\s+", c.scenario.strip().rstrip("."))) >= 3 and \
        bool(VAGUE.search(c.expected) or re.search(r"\ball\b", c.expected, re.I))


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def _figures(text: str) -> list[float]:
    """Money and percent amounts a case asserts. A % is kept as written (7% -> 7.0)."""
    return [_num(m) for m in MONEY.findall(text)] + [_num(m) for m in PERCENT.findall(text)]


def _pool(*texts: str) -> list[float]:
    """Every number in the given texts. A percent also contributes its fraction (7% -> 7 and 0.07)."""
    out: list[float] = []
    for t in texts:
        for value, pct in NUMBER.findall(t):
            n = _num(value)
            for v in (n, n / 100 if pct else None):
                if v is not None and v not in out:
                    out.append(v)
    return out


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.005, abs(b) * 1e-9)


def _grounded(value: float, pool: list[float]) -> bool:
    """True if value is in the pool, or is the sum, difference, product or ratio of two pool numbers."""
    pool = pool[:MAX_POOL]
    if any(_close(value, p) for p in pool):
        return True
    for i, a in enumerate(pool):
        for b in pool[i + 1:]:
            if any(_close(value, v) for v in (a + b, abs(a - b), a * b, a / b if b else None, b / a if a else None)
                   if v is not None):
                return True
    return False


def check_value_source(c: TestCase, req: Requirement | None = None, markers: list[str] = ()) -> list[Finding]:
    """The expected result states a figure that appears nowhere a tester can check, and the case cites no source.

    A figure is fine if it is in the scenario, in the requirement text or acceptance criteria, or is a sum,
    difference, product or ratio of two numbers found there. Otherwise the case must name where it came
    from: fill source_ref, or write it in the scenario or expected result ("per rate table RT-2026-03").
    This is a presence check only. It cannot tell whether the cited table says what the case claims.
    """
    figures = _figures(c.expected)
    if not figures and c.type is CaseType.calculation:
        figures = [_num(m) for m, _ in NUMBER.findall(c.expected)]
    if not figures:
        return []
    if c.source_ref.strip():
        return []
    custom = re.compile("|".join(re.escape(m) for m in markers), re.I) if markers else None
    text = f"{c.scenario} {c.expected}"
    if SOURCE_MARKERS.search(text) or (custom and custom.search(text)):
        return []
    pool = _pool(c.scenario, req.text if req else "", req.acceptance_criteria if req else "")
    loose = [v for v in figures if not _grounded(v, pool)]
    if not loose:
        return []
    shown = ", ".join(f"{v:g}" for v in loose[:3])
    return [Finding(c.id, "uncited_value", "revise",
                    f"Expected result states {shown}, but the case does not say where it comes from. "
                    "Cite the rate table or worked example (source_ref, or write it in the case).",
                    c.requirement_id)]


def check_case(c: TestCase, req_ids: set[str], req: Requirement | None = None,
               markers: list[str] = ()) -> list[Finding]:
    out: list[Finding] = []
    rid = c.requirement_id

    def add(code: str, decision: Decision, reason: str) -> None:
        out.append(Finding(c.id, code, decision, reason, rid))

    if rid not in req_ids:
        add("untraceable", "reject", f"Requirement {rid} does not exist, so the case traces to nothing.")
    if not c.expected.strip():
        add("missing_expected", "revise", "No expected result. A tester cannot pass or fail this case.")
    elif VAGUE.search(c.expected):
        add("vague_expected", "revise", f"Expected result is vague: '{c.expected.strip()}'. State the exact outcome.")
    elif AMBIGUOUS.search(c.expected) and not CONFIGURED.search(c.expected):
        add("ambiguous_outcome", "human_review", "Expected result allows two outcomes. A person must decide which one is right.")
    if _is_compound(c):
        add("compound_case", "revise", "Case bundles several actions. Split it into one case per action.")
    out += check_value_source(c, req, markers)
    return out


def check_duplicates(cases: list[TestCase], reference: list[TestCase] = ()) -> list[Finding]:
    """Flag a case that is near-identical to an earlier case (or a reference case) on the same requirement."""
    out: list[Finding] = []
    seen: list[TestCase] = list(reference)
    for c in cases:
        for o in seen:
            if o.id != c.id and o.requirement_id == c.requirement_id and _similar(c, o) >= SIMILARITY:
                out.append(Finding(c.id, "near_duplicate", "reject",
                                   f"Near-duplicate of {o.id} (similarity >= {SIMILARITY}).", c.requirement_id))
                break
        seen.append(c)
    return out


def check_coverage(reqs: list[Requirement], cases: list[TestCase]) -> list[Finding]:
    """Requirement-level gaps: no cases at all, or high risk with no Negative/Boundary case."""
    out: list[Finding] = []
    for r in reqs:
        mine = [c for c in cases if c.requirement_id == r.id]
        if not mine:
            out.append(Finding(None, "no_cases", "human_review", f"Requirement {r.id} has no test cases.", r.id))
        elif r.risk == "high" and not any(c.type and c.type.value in ("Negative", "Boundary") for c in mine):
            out.append(Finding(None, "coverage_gap", "human_review",
                               f"High-risk requirement {r.id} has no Negative or Boundary case.", r.id))
    return out


def run_rules(reqs: list[Requirement], cases: list[TestCase], reference: list[TestCase] = (),
              pack=None) -> list[Finding]:
    ids = {r.id for r in reqs}
    by_id = {r.id: r for r in reqs}
    markers = list(getattr(pack, "source_markers", None) or [])
    found = [f for c in cases for f in check_case(c, ids, by_id.get(c.requirement_id), markers)]
    return found + check_duplicates(cases, reference) + check_coverage(reqs, cases)
