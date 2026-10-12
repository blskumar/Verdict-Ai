"""Read requirements and test cases from PDF, CSV or JSON files.

No ALM or tracker connections: files only.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pdfplumber

from .models import CaseType, Requirement, TestCase

SUPPORTED = {".pdf", ".csv", ".json"}

_REQ_HEAD = re.compile(r"^([A-Z][A-Z0-9]*-REQ-\d+)\s+[—–-]\s+(.+)$")
_ACCEPT = re.compile(r"^Acceptance criteria:\s*(.*)$", re.I)
_TC_ID = re.compile(r"^([A-Z][A-Z0-9]*)-TC-(\d+)-(\d+)$")
_FOOTER = re.compile(r"^(Sample .* Page \d+|Page \d+)$")


class LoadError(ValueError):
    pass


def _suffix(path: Path) -> str:
    s = path.suffix.lower()
    if s not in SUPPORTED:
        raise LoadError(f"Unsupported file type '{s}'. Use PDF, CSV or JSON.")
    if not path.exists():
        raise LoadError(f"File not found: {path}")
    return s


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


# ---------- requirements ----------

def load_requirements(path: str | Path) -> list[Requirement]:
    path = Path(path)
    kind = _suffix(path)
    if kind == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("requirements", [])
        reqs = [Requirement(**row) for row in data]
    elif kind == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reqs = [Requirement(**{k: (v or None) if k in ("domain", "risk") else (v or "")
                                   for k, v in row.items() if k}) for row in csv.DictReader(fh)]
    else:
        reqs = _requirements_from_pdf(path)
    _check_unique([r.id for r in reqs], "requirement")
    return reqs


def _pdf_lines(path: Path) -> list[str]:
    lines: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for raw in (page.extract_text() or "").splitlines():
                line = raw.strip()
                if line and not _FOOTER.match(line):
                    lines.append(line)
    return lines


def _requirements_from_pdf(path: Path) -> list[Requirement]:
    reqs: list[Requirement] = []
    cur: dict | None = None
    mode = "text"
    lines = _pdf_lines(path)
    if any(re.search(r"\b[A-Z][A-Z0-9]*-TC-\d+-\d+\b", ln) for ln in lines):
        raise LoadError(f"{path.name} looks like a test case file, not a requirements file. "
                        "Use --cases for it.")
    for line in lines:
        head = _REQ_HEAD.match(line)
        if head:
            if cur:
                reqs.append(_finish(cur))
            cur = {"id": head.group(1), "title": head.group(2), "text": [], "ac": []}
            mode = "text"
            continue
        if cur is None:
            continue
        acc = _ACCEPT.match(line)
        if acc:
            mode = "ac"
            cur["ac"].append(acc.group(1))
        elif _is_section_heading(line):
            reqs.append(_finish(cur))
            cur = None
        else:
            cur[mode].append(line)
    if cur:
        reqs.append(_finish(cur))
    if not reqs:
        raise LoadError(f"No requirements found in {path.name}. Expected headings like 'ABC-REQ-01 — Title'.")
    return reqs


def _is_section_heading(line: str) -> bool:
    # Section titles such as "Life Insurance — Term Life" or "P&C — Commercial Auto"
    return bool(re.match(r"^(P&C|Life Insurance)\s+[—–-]\s+", line))


def _finish(cur: dict) -> Requirement:
    return Requirement(id=cur["id"], title=cur["title"], text=_norm(" ".join(cur["text"])),
                       acceptance_criteria=_norm(" ".join(cur["ac"])))


# ---------- test cases ----------

def load_cases(path: str | Path, source: str = "uploaded") -> list[TestCase]:
    path = Path(path)
    kind = _suffix(path)
    if kind == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("cases", data.get("test_cases", []))
        cases = [TestCase(**{"source": source, **row}) for row in data]
    elif kind == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as fh:
            rows = [{k: v for k, v in row.items() if k} for row in csv.DictReader(fh)]
        cases = [TestCase(**{"source": source, **{**r, "type": r.get("type") or None}}) for r in rows]
    else:
        cases = _cases_from_pdf(path, source)
    _check_unique([c.id for c in cases], "test case")
    return cases


def _cases_from_pdf(path: Path, source: str) -> list[TestCase]:
    cases: list[TestCase] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    if not row or not row[0]:
                        continue
                    first = row[0].strip().splitlines()
                    m = _TC_ID.match(first[0].strip())
                    if not m:
                        continue  # header row or summary table
                    prefix, req_no, _ = m.groups()
                    ctype = first[1].strip() if len(first) > 1 else None
                    cases.append(TestCase(
                        id=first[0].strip(),
                        requirement_id=f"{prefix}-REQ-{req_no}",
                        type=CaseType(ctype) if ctype else None,
                        scenario=_norm(row[1]),
                        expected=_norm(row[2]),
                        source=source,
                    ))
    if not cases:
        raise LoadError(f"No test cases found in {path.name}. Expected tables with 'ABC-TC-01-01' IDs.")
    return cases


def _check_unique(ids: list[str], what: str) -> None:
    seen: set[str] = set()
    for i in ids:
        if i in seen:
            raise LoadError(f"Duplicate {what} ID: {i}")
        seen.add(i)
