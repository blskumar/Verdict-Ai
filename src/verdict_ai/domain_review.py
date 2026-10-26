"""Domain-driven review of a requirement set: outdated items, missing requirements, scenarios still needed.

Plain code. It compares requirements and cases with the selected domain pack (risk areas, boundary hints,
outdated terms). It proposes; a person decides.
"""
from __future__ import annotations

import math
import re
from typing import Any

from verdict_ai.domains import Pack

# Words that mark a requirement or case as outdated in any domain; a pack adds its own in obsolete_terms.
OBSOLETE_MARKERS = re.compile(
    r"\b(obsolete|deprecated|superseded|retired|legacy|end[- ]of[- ]life|sunset|no longer (?:supported|applicable|used))\b",
    re.I)
_FILLER = {"the", "and", "for", "with", "that", "than", "from", "this", "into", "when", "are", "was", "its", "vs",
           "exactly", "just", "above", "below", "check", "verify", "equal", "one", "cent", "per", "has", "have"}
MAX_PROPOSALS = 8


def _stems(text: str) -> set[str]:
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w
            for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2 and w not in _FILLER}


def _req_text(r: dict[str, Any]) -> str:
    return f"{r.get('title', '')} {r.get('text', '')} {r.get('acceptance_criteria', '')}"


def _covers(text_stems: set[str], phrase: str, frac: float = 1.0) -> bool:
    """The text mentions the phrase: at least `frac` of its content words appear (at least 2 for a longer phrase)."""
    words = _stems(phrase)
    if not words:
        return False
    need = max(2 if len(words) > 1 else 1, math.ceil(frac * len(words)))
    return len(words & text_stems) >= need


def hints_for(text: str, pack: Pack) -> list[str]:
    """The pack's boundary hints that apply to this requirement text."""
    stems = _stems(text)
    return [h.rstrip(".") for h in pack.boundary_hints if _covers(stems, h, frac=0.5)]


def review(requirements: list[dict[str, Any]], cases: list[dict[str, Any]], pack: Pack) -> dict[str, Any]:
    """Return {obsolete, proposed_requirements, extra_scenarios}, each a list of small dicts."""
    req_stems = {str(r.get("id")): _stems(_req_text(r)) for r in requirements}
    all_stems = set().union(*req_stems.values()) if req_stems else set()
    case_stems: dict[str, set[str]] = {}
    for c in cases:
        case_stems.setdefault(str(c.get("requirement_id")), set()).update(
            _stems(f"{c.get('scenario', '')} {c.get('expected', '')}"))

    # 1. Outdated: pack terms, generic markers, and cases whose requirement is gone.
    obsolete: list[dict[str, str]] = []
    custom = {k.lower(): v for k, v in pack.obsolete_terms.items()}

    def scan(kind: str, ident: str, text: str) -> None:
        low = text.lower()
        for term, why in custom.items():
            if term in low:
                obsolete.append({"kind": kind, "id": ident, "term": term, "reason": why})
        m = OBSOLETE_MARKERS.search(text)
        if m:
            obsolete.append({"kind": kind, "id": ident, "term": m.group(0),
                             "reason": "Text says this is outdated. Confirm whether to retire or rewrite it."})

    for r in requirements:
        scan("requirement", str(r.get("id")), _req_text(r))
    for c in cases:
        scan("case", str(c.get("id")), f"{c.get('scenario', '')} {c.get('expected', '')}")
        if req_stems and str(c.get("requirement_id")) not in req_stems:
            obsolete.append({"kind": "case", "id": str(c.get("id")), "term": str(c.get("requirement_id")),
                             "reason": "Its requirement is not in the set. The case may be obsolete."})

    # 2. Proposed requirements: domain-specific risk areas that no requirement mentions.
    prefix = (pack.id_prefixes[0] if pack.id_prefixes else "NEW")
    proposed: list[dict[str, str]] = []

    def propose(title: str, reason: str, risk: str) -> None:
        if len(proposed) < MAX_PROPOSALS:
            n = len(proposed) + 1
            proposed.append({"id": f"{prefix}-REQ-NEW-{n:02d}", "title": title, "risk": risk, "reason": reason,
                             "text": f"{title} for {pack.name}: state the exact limits, the outcome on each side "
                                     "of every limit, and what is recorded."})

    if requirements:
        for tag in pack.risk_tags:
            if "_" not in tag:
                continue  # a one-word tag (money, audit) is a cross-cutting concern, not a topic to write a requirement for
            label = tag.replace("_", " ")
            if not _covers(all_stems, label, frac=0.5):
                propose(f"Specify {label}", f"{pack.name} lists '{label}' as a risk area, but no requirement mentions it.", "high")

    # 3. Scenarios still needed: a boundary hint that matches a requirement but no case of that requirement.
    extra: list[dict[str, str]] = []
    for r in requirements:
        rid = str(r.get("id"))
        for hint in pack.boundary_hints:
            if _covers(req_stems[rid], hint, frac=0.5) and not _covers(case_stems.get(rid, set()), hint, frac=0.5):
                extra.append({"requirement_id": rid, "scenario": hint.rstrip("."),
                              "reason": f"Domain boundary for {pack.name} that no case of {rid} exercises."})
    return {"obsolete": obsolete, "proposed_requirements": proposed, "extra_scenarios": extra}
