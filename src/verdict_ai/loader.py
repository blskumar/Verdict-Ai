"""Read requirements and test cases from PDF, DOCX, XLSX, CSV, or JSON files.

No ALM or tracker connections: local files only.
Supports Insurance, Banking, Healthcare, and general enterprise domain documents.
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

import pdfplumber

try:
    import docx
    _HAS_DOCX = True
except ImportError:
    _HAS_DOCX = False

try:
    import openpyxl
    _HAS_OPENPYXL = True
except ImportError:
    _HAS_OPENPYXL = False

from .models import CaseType, Requirement, TestCase

SUPPORTED = {".pdf", ".csv", ".json", ".docx", ".xlsx"}

_FOOTER = re.compile(r"^(Sample .* Page \d+|Page \d+|Confidential.*)$", re.I)


class LoadError(ValueError):
    pass


def _suffix(path: Path) -> str:
    s = path.suffix.lower()
    if s not in SUPPORTED:
        raise LoadError(f"Unsupported file type '{s}'. Use PDF, DOCX, XLSX, CSV or JSON.")
    if not path.exists():
        raise LoadError(f"File not found: {path}")
    return s


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _is_valid_req_id(s: str) -> bool:
    """Validate whether candidate token is a legitimate requirement identifier."""
    s = s.strip()
    if not (2 <= len(s) <= 30):
        return False
    if not re.match(r"^[A-Za-z0-9]+([-_][A-Za-z0-9]+)*$", s):
        return False
    if re.match(r"^(page|version|sample|table|section|figure|doc|document|total|all)[-_0-9]*$", s, re.I):
        return False
    # Must contain at least one digit or the keyword 'REQ'
    return bool(re.search(r"\d", s) or re.search(r"REQ", s, re.I))


# ---------------------------------------------------------------------------
# Requirements Loaders
# ---------------------------------------------------------------------------

def load_requirements(path: str | Path) -> list[Requirement]:
    path = Path(path)
    kind = _suffix(path)

    if kind == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("requirements", data.get("reqs", []))
        reqs = [Requirement(**row) for row in data]
    elif kind == ".csv":
        try:
            content = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            content = path.read_text(encoding="cp1252", errors="replace")
        reader = csv.DictReader(content.splitlines())
        rows = []
        for row in reader:
            mapped = {}
            for k, v in row.items():
                if not k:
                    continue
                lk = k.lower().strip().replace(" ", "_")
                if lk in ("id", "req_id", "requirement_id"):
                    mapped["id"] = v.strip()
                elif lk in ("title", "name", "story_name"):
                    mapped["title"] = v.strip()
                elif lk in ("text", "description", "story_description", "details"):
                    mapped["text"] = v.strip()
                elif lk in ("acceptance_criteria", "ac", "criteria"):
                    mapped["acceptance_criteria"] = v.strip()
                elif lk in ("risk", "priority"):
                    mapped["risk"] = v.strip().lower() if v else None
                elif lk == "domain":
                    mapped["domain"] = v.strip()
            if "id" in mapped and mapped["id"]:
                if "title" not in mapped:
                    mapped["title"] = mapped["id"]
                if "text" not in mapped:
                    mapped["text"] = mapped["title"]
                rows.append(Requirement(**mapped))
        reqs = rows
    elif kind == ".docx":
        reqs = _requirements_from_docx(path)
    elif kind == ".xlsx":
        reqs = _requirements_from_xlsx(path)
    else:
        reqs = _requirements_from_pdf(path)

    if not reqs:
        raise LoadError(f"No requirements found in {path.name}.")

    _check_unique([r.id for r in reqs], "requirement")
    return reqs


def _parse_lines_for_requirements(lines: list[str]) -> list[Requirement]:
    """Universal parser that extracts requirements from structured lines of text."""
    p_story_id = re.compile(r"^Story ID\s*:?\s*([A-Za-z0-9_\-]+)$", re.I)
    p_story_name = re.compile(r"^Story Name\s*:?\s*(.+)$", re.I)
    p_story_desc = re.compile(r"^Story Description\s*:?\s*(.*)$", re.I)

    p_req_delim = re.compile(r"^([A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*)\s+[—–\─\-\:\|]\s+(.+)$")
    p_req_space = re.compile(r"^([A-Za-z]{1,6}(?:[-_][A-Za-z0-9]+)*-\d{1,5})\s+([A-Za-z][A-Za-z0-9 ,/&'()_-]+)$")
    p_req_word = re.compile(r"^(?:Requirement|Req)\s*(\d+|[A-Za-z0-9_\-]+)[:\s—–\-.]+(.+)$", re.I)
    p_req_label = re.compile(r"^(?:Requirement|Req|Statement)[:./\s—–\-]+(.*)$", re.I)

    p_accept = re.compile(
        r"^(?:Acceptance criteria|Acceptance criteria / business rules|Business rules|Acceptance Criteria)[:./\s—–\-]+(.*)$",
        re.I,
    )
    p_priority = re.compile(r"\bPriority:?\s*(High|Medium|Low|Must|Should|Could|Won'?t)\b", re.I)
    p_trace = re.compile(r"^Traceability[:./\s—–\-]+(.*)$", re.I)

    prio_map = {
        "high": "high", "medium": "medium", "low": "low",
        "must": "high", "should": "medium", "could": "low", "won't": "low",
    }

    raw_reqs: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    mode = "text"

    for raw_line in lines:
        line = re.sub(r"</?[a-z0-9]+>", "", raw_line).strip()
        line = re.sub(r"\(cid:\d+\)", "-", line).strip()
        if not line or _FOOTER.match(line):
            continue

        # 1. Check Story ID
        m_sid = p_story_id.match(line)
        if m_sid and _is_valid_req_id(m_sid.group(1)):
            if cur:
                raw_reqs.append(cur)
            cur = {"id": m_sid.group(1), "title": "", "text": [], "ac": [], "risk": None}
            mode = "text"
            continue

        if cur and cur.get("title") == "" and p_story_name.match(line):
            cur["title"] = p_story_name.match(line).group(1).strip()
            continue

        if cur and p_story_desc.match(line):
            desc = p_story_desc.match(line).group(1).strip()
            if desc:
                cur["text"].append(desc)
            mode = "text"
            continue

        # 2. Check standard headers
        m_delim = p_req_delim.match(line)
        m_space = p_req_space.match(line)
        m_word = p_req_word.match(line)

        matched_head = None
        if m_delim and _is_valid_req_id(m_delim.group(1)):
            matched_head = (m_delim.group(1), m_delim.group(2))
        elif m_word and _is_valid_req_id(m_word.group(1)):
            rid = f"REQ-{m_word.group(1)}" if m_word.group(1).isdigit() else m_word.group(1)
            matched_head = (rid, m_word.group(2))
        elif m_space and _is_valid_req_id(m_space.group(1)):
            matched_head = (m_space.group(1), m_space.group(2))

        if matched_head:
            if cur:
                raw_reqs.append(cur)
            cur = {
                "id": matched_head[0].strip(),
                "title": matched_head[1].strip(),
                "text": [],
                "ac": [],
                "risk": None,
            }
            mode = "text"
            continue

        if cur is None:
            continue

        # Priority / Risk
        m_prio = p_priority.search(line)
        if m_prio:
            raw_prio = m_prio.group(1).lower()
            cur["risk"] = prio_map.get(raw_prio, "medium")

        # Acceptance Criteria
        m_acc = p_accept.match(line)
        if m_acc:
            mode = "ac"
            ac_text = m_acc.group(1).strip()
            if ac_text:
                cur["ac"].append(ac_text)
            continue

        # Requirement statement prefix (e.g. 'Requirement: The application shall...')
        m_req = p_req_label.match(line)
        if m_req:
            mode = "text"
            req_text = m_req.group(1).strip()
            if req_text:
                cur["text"].append(req_text)
            continue

        # Skip traceability line markers
        if p_trace.match(line):
            continue

        # Section title divider (e.g. 'P&C — Commercial Auto' or 'Life Insurance — Term Life')
        if re.match(r"^(?:P&C|Life Insurance|Banking Domain|Health Insurance)\s+[—–-]\s+", line):
            if cur:
                raw_reqs.append(cur)
                cur = None
            continue

        if line and not p_priority.match(line):
            cur[mode].append(line)

    if cur:
        raw_reqs.append(cur)

    # Convert to Requirement objects
    results: list[Requirement] = []
    for r in raw_reqs:
        title = r["title"] or r["id"]
        text = _norm(" ".join(r["text"])) or title
        ac = _norm(" ".join(r["ac"]))
        risk_val = r["risk"] if r["risk"] in ("low", "medium", "high") else None
        results.append(Requirement(
            id=r["id"],
            title=title,
            text=text,
            acceptance_criteria=ac,
            risk=risk_val,
        ))

    return results


def _universal_requirements_fallback(lines: list[str]) -> list[Requirement]:
    """Universal fallback parser that extracts requirements from freeform or unstructured project documents."""
    results: list[Requirement] = []
    p_num = re.compile(r"^(?:(?:Section|Req|Rule|Item|Feature)\s+)?(\d+(?:\.\d+)*)[:.\s—–\-]+(.+)$", re.I)
    p_bullet = re.compile(r"^[\*\-•\u2022]\s+(.+)$")
    p_story = re.compile(r"^(?:As an?|Given)\s+.+", re.I)
    p_normative = re.compile(r"\b(shall|must|should|will|required|needs to|expected to|allows|enables|supports|provides|validates|accepts|rejects)\b", re.I)

    blocks: list[list[str]] = []
    current_block: list[str] = []

    for raw in lines:
        ln = raw.strip()
        if not ln or _FOOTER.match(ln):
            if current_block:
                blocks.append(current_block)
                current_block = []
            continue

        if p_num.match(ln) or p_bullet.match(ln) or p_story.match(ln):
            if current_block:
                blocks.append(current_block)
                current_block = []
            current_block.append(ln)
        else:
            if not current_block:
                current_block.append(ln)
            else:
                if len(current_block) >= 4:
                    blocks.append(current_block)
                    current_block = [ln]
                else:
                    current_block.append(ln)

    if current_block:
        blocks.append(current_block)

    req_idx = 1
    seen_ids: set[str] = set()
    for block in blocks:
        full_text = " ".join(block)
        if len(full_text) < 15:
            continue

        first_line = block[0]
        m_num = p_num.match(first_line)
        m_bullet = p_bullet.match(first_line)

        if m_num:
            num_clean = m_num.group(1).replace(".", "-")
            req_id = f"REQ-{num_clean}" if not num_clean.startswith("REQ") else num_clean
            title_cand = m_num.group(2).strip()
        elif m_bullet:
            req_id = f"REQ-{req_idx:03d}"
            title_cand = m_bullet.group(1).strip()
            req_idx += 1
        else:
            req_id = f"REQ-{req_idx:03d}"
            title_cand = first_line
            req_idx += 1

        if req_id in seen_ids:
            req_id = f"{req_id}-{req_idx}"
        seen_ids.add(req_id)

        title_match = re.match(r"^([^.!?\n]+[.!?]?)", title_cand)
        title = title_match.group(1).strip() if title_match else title_cand[:60].strip()
        if len(title) > 80:
            words = title.split()
            title = " ".join(words[:8])

        ac = ""
        ac_match = re.search(r"(?:acceptance criteria|business rules?|verification)[:\s—–\-]+(.*)$", full_text, re.I)
        if ac_match:
            ac = ac_match.group(1).strip()
            full_text = full_text[:ac_match.start()].strip()

        results.append(Requirement(
            id=req_id,
            title=_norm(title),
            text=_norm(full_text),
            acceptance_criteria=_norm(ac),
            risk="medium" if p_normative.search(full_text) else None,
        ))

    return results


def _extract_requirements_from_docx_tables(doc: Any) -> list[Requirement]:
    reqs: list[Requirement] = []
    req_idx = 1
    seen_ids: set[str] = set()

    for table in getattr(doc, "tables", []):
        if len(table.rows) < 2:
            continue
        headers = [c.text.strip().lower().replace(" ", "_") for c in table.rows[0].cells]
        id_col = next((i for i, h in enumerate(headers) if any(k in h for k in ("id", "code", "no", "item", "key", "req"))), -1)
        title_col = next((i for i, h in enumerate(headers) if any(k in h for k in ("name", "title", "feature", "summary", "module"))), -1)
        text_col = next((i for i, h in enumerate(headers) if any(k in h for k in ("desc", "text", "requirement", "statement", "detail", "spec", "story"))), -1)
        ac_col = next((i for i, h in enumerate(headers) if any(k in h for k in ("criteria", "ac", "rule", "condition", "expected"))), -1)

        if text_col != -1 or title_col != -1:
            for row in table.rows[1:]:
                cells = [c.text.strip() for c in row.cells]
                if not any(cells):
                    continue
                cid = cells[id_col] if id_col != -1 and cells[id_col] and _is_valid_req_id(cells[id_col]) else f"REQ-{req_idx:03d}"
                req_idx += 1
                if cid in seen_ids:
                    cid = f"{cid}-{req_idx}"
                seen_ids.add(cid)

                title = cells[title_col] if title_col != -1 and cells[title_col] else cid
                text = cells[text_col] if text_col != -1 and cells[text_col] else title
                ac = cells[ac_col] if ac_col != -1 and len(cells) > ac_col else ""
                if len(text) >= 5 or len(title) >= 5:
                    reqs.append(Requirement(
                        id=cid,
                        title=_norm(title),
                        text=_norm(text),
                        acceptance_criteria=_norm(ac),
                        risk=None,
                    ))
    return reqs


def _requirements_from_pdf(path: Path) -> list[Requirement]:
    lines: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for raw in (page.extract_text() or "").splitlines():
                ln = raw.strip()
                if ln and not _FOOTER.match(ln):
                    lines.append(ln)

    # Check if this PDF is actually a test case document
    if any(re.search(r"\b[A-Z0-9]+-TC-\d+-\d+\b", ln) for ln in lines):
        raise LoadError(
            f"{path.name} looks like a test case file, not a requirements file. Use cases upload for it."
        )

    # 1. Try structured headers
    reqs = _parse_lines_for_requirements(lines)

    # 2. Try PDF tables
    if not reqs:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    if not table or len(table) < 2:
                        continue
                    headers = [str(c or "").lower().strip() for c in table[0]]
                    id_col = next((i for i, h in enumerate(headers) if any(k in h for k in ("id", "req", "code", "no", "item"))), -1)
                    title_col = next((i for i, h in enumerate(headers) if any(k in h for k in ("title", "name", "feature", "summary"))), -1)
                    text_col = next((i for i, h in enumerate(headers) if any(k in h for k in ("desc", "text", "statement", "detail", "spec"))), -1)
                    ac_col = next((i for i, h in enumerate(headers) if any(k in h for k in ("criteria", "ac", "rule", "condition"))), -1)

                    if id_col != -1 or title_col != -1 or text_col != -1:
                        for row_idx, row in enumerate(table[1:], start=1):
                            if not row or not any(row):
                                continue
                            rid = str(row[id_col]).strip() if id_col != -1 and row[id_col] else f"REQ-{row_idx:03d}"
                            title = str(row[title_col]).strip() if title_col != -1 and row[title_col] else rid
                            text = str(row[text_col]).strip() if text_col != -1 and row[text_col] else title
                            ac = str(row[ac_col]).strip() if ac_col != -1 and row[ac_col] else ""
                            if len(text) >= 5 or len(title) >= 5:
                                reqs.append(Requirement(
                                    id=rid,
                                    title=_norm(title),
                                    text=_norm(text),
                                    acceptance_criteria=_norm(ac),
                                    risk=None,
                                ))

    # 3. Universal text fallback
    if not reqs and lines:
        reqs = _universal_requirements_fallback(lines)

    if not reqs:
        raise LoadError(
            f"No requirements found in {path.name}. "
            "Expected requirement headings, user stories, bullet points, or requirement tables."
        )

    # Deduplicate PDF occurrences
    dedup: dict[str, Requirement] = {}
    for r in reqs:
        score = len(r.text) + len(r.acceptance_criteria)
        if r.id not in dedup:
            dedup[r.id] = r
        else:
            existing_score = len(dedup[r.id].text) + len(dedup[r.id].acceptance_criteria)
            if score > existing_score:
                dedup[r.id] = r
    return list(dedup.values())


def _requirements_from_docx(path: Path) -> list[Requirement]:
    if not _HAS_DOCX:
        raise LoadError("python-docx is not installed to process .docx files.")

    try:
        doc = docx.Document(path)
    except Exception as e:
        if _HAS_OPENPYXL:
            try:
                return _requirements_from_xlsx(io.BytesIO(path.read_bytes()))
            except Exception:
                pass
        raise LoadError(f"Could not read {path.name} as a Word document: {e}")

    lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    # 1. Try structured line headers
    reqs = _parse_lines_for_requirements(lines)

    # 2. Try docx tables
    if not reqs:
        reqs = _extract_requirements_from_docx_tables(doc)

    # 3. Universal text fallback
    if not reqs and lines:
        reqs = _universal_requirements_fallback(lines)

    if not reqs:
        raise LoadError(f"No requirements found in {path.name}.")

    return reqs


def _requirements_from_xlsx(path: Path | io.BytesIO) -> list[Requirement]:
    if not _HAS_OPENPYXL:
        raise LoadError("openpyxl is not installed to process .xlsx files.")

    try:
        if isinstance(path, Path):
            try:
                wb = openpyxl.load_workbook(path, data_only=True)
            except Exception:
                wb = openpyxl.load_workbook(io.BytesIO(path.read_bytes()), data_only=True)
        else:
            wb = openpyxl.load_workbook(path, data_only=True)
    except Exception as e:
        name = path.name if isinstance(path, Path) else "spreadsheet"
        raise LoadError(f"Could not read {name} as an Excel spreadsheet: {e}")
    reqs: list[Requirement] = []

    for sname in wb.sheetnames:
        sheet = wb[sname]
        rows = list(sheet.iter_rows(values_only=True))
        if not rows or len(rows) < 2:
            continue

        header = [str(c or "").lower().strip().replace(" ", "_") for c in rows[0]]
        id_col = next((i for i, h in enumerate(header) if "req" in h or h in ("id", "story_id")), -1)
        title_col = next((i for i, h in enumerate(header) if "name" in h or "title" in h or "subdomain" in h), -1)
        text_col = next((i for i, h in enumerate(header) if "desc" in h or "requirement" in h or "details" in h or "text" in h), -1)
        ac_col = next((i for i, h in enumerate(header) if "acceptance" in h or "criteria" in h or "ac" in h), -1)
        risk_col = next((i for i, h in enumerate(header) if "prio" in h or "risk" in h), -1)

        if id_col != -1 and (title_col != -1 or text_col != -1 or ac_col != -1):
            for r in rows[1:]:
                if not r or not r[id_col]:
                    continue
                rid = str(r[id_col]).strip()
                if not _is_valid_req_id(rid):
                    continue
                title = str(r[title_col]).strip() if title_col != -1 and r[title_col] else rid
                text = str(r[text_col]).strip() if text_col != -1 and r[text_col] else title
                ac = str(r[ac_col]).strip() if ac_col != -1 and r[ac_col] else ""
                risk_val = None
                if risk_col != -1 and r[risk_col]:
                    val = str(r[risk_col]).strip().lower()
                    if val in ("low", "medium", "high"):
                        risk_val = val
                reqs.append(Requirement(
                    id=rid,
                    title=_norm(title),
                    text=_norm(text),
                    acceptance_criteria=_norm(ac),
                    risk=risk_val,
                ))

    if not reqs:
        # Fallback: extract requirements from any sheet containing descriptive text
        for sname in wb.sheetnames:
            if any(skip in sname.lower() for skip in ("test", "case", "setup", "data", "read_me")):
                continue
            sheet = wb[sname]
            rows = list(sheet.iter_rows(values_only=True))
            if not rows or len(rows) < 2:
                continue

            text_col = -1
            for i in range(len(rows[0])):
                # Look for a column where rows contain substantive sentences
                substantive = sum(1 for r in rows[1:6] if len(r) > i and len(str(r[i] or "").strip()) > 15)
                if substantive >= 2:
                    text_col = i
                    break

            if text_col != -1:
                req_idx = 1
                for r in rows[1:]:
                    if not r or len(r) <= text_col or not r[text_col]:
                        continue
                    text_val = str(r[text_col]).strip()
                    if len(text_val) < 10:
                        continue
                    title_match = re.match(r"^([^.!?\n]+[.!?]?)", text_val)
                    title = title_match.group(1).strip() if title_match else text_val[:60].strip()
                    reqs.append(Requirement(
                        id=f"REQ-{req_idx:03d}",
                        title=_norm(title),
                        text=_norm(text_val),
                        acceptance_criteria="",
                        risk=None,
                    ))
                    req_idx += 1
            if reqs:
                break

    if not reqs:
        raise LoadError(f"No requirements found in {path.name}.")
    return reqs


# ---------------------------------------------------------------------------
# Test Cases Loaders
# ---------------------------------------------------------------------------

def load_cases(path: str | Path, source: str = "uploaded") -> list[TestCase]:
    path = Path(path)
    kind = _suffix(path)

    if kind == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("cases", data.get("test_cases", []))
        cases = [TestCase(**{"source": source, **row}) for row in data]
    elif kind == ".csv":
        cases = _cases_from_csv(path, source)
    elif kind == ".xlsx":
        cases = _cases_from_xlsx(path, source)
    elif kind == ".docx":
        cases = _cases_from_docx(path, source)
    else:
        cases = _cases_from_pdf(path, source)

    _check_unique([c.id for c in cases], "test case")
    return cases


def _cases_from_csv(path: Path, source: str) -> list[TestCase]:
    cases: list[TestCase] = []
    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        content = path.read_text(encoding="cp1252", errors="replace")
    reader = csv.DictReader(content.splitlines())
    for row in reader:
        mapped: dict[str, Any] = {"source": source}
        for k, v in row.items():
            if not k or not v:
                continue
            lk = k.lower().strip().replace(" ", "_")
            val = str(v).strip()
            if lk in ("id", "case_id", "test_id", "test_case_id"):
                mapped["id"] = val
            elif lk in ("requirement_id", "req_id", "traces_to"):
                mapped["requirement_id"] = val
            elif lk in ("scenario", "action", "description", "test_scenario"):
                mapped["scenario"] = val
            elif lk in ("expected", "expected_result", "expected_results", "outcome"):
                mapped["expected"] = val
            elif lk in ("source_ref", "source_reference", "reference", "cites", "rate_table", "expected_source"):
                mapped["source_ref"] = val
            elif lk in ("type", "test_type", "case_type"):
                try:
                    mapped["type"] = CaseType(val)
                except ValueError:
                    mapped["type"] = None

        if "id" in mapped and "scenario" in mapped:
            if "requirement_id" not in mapped:
                mapped["requirement_id"] = ""
            if "expected" not in mapped:
                mapped["expected"] = ""
            cases.append(TestCase(**mapped))
    if not cases:
        raise LoadError(f"No test cases found in {path.name}.")
    return cases


def _cases_from_xlsx(path: Path | io.BytesIO, source: str) -> list[TestCase]:
    if not _HAS_OPENPYXL:
        raise LoadError("openpyxl is not installed to process .xlsx files.")

    try:
        if isinstance(path, Path):
            try:
                wb = openpyxl.load_workbook(path, data_only=True)
            except Exception:
                wb = openpyxl.load_workbook(io.BytesIO(path.read_bytes()), data_only=True)
        else:
            wb = openpyxl.load_workbook(path, data_only=True)
    except Exception as e:
        name = path.name if isinstance(path, Path) else "spreadsheet"
        raise LoadError(f"Could not read {name} as an Excel spreadsheet: {e}")
    cases: list[TestCase] = []

    for sheet_name in wb.sheetnames:
        sheet = wb[sheet_name]
        rows = list(sheet.iter_rows(values_only=True))
        if not rows or len(rows) < 2:
            continue

        header = [str(c or "").lower().strip().replace(" ", "_") for c in rows[0]]
        id_col = next((i for i, h in enumerate(header) if "case_id" in h or "test_id" in h or "tc_id" in h or h == "id" or "test_case" in h), -1)
        req_col = next((i for i, h in enumerate(header) if "req" in h or "story" in h or "trace" in h), -1)
        type_col = next((i for i, h in enumerate(header) if "type" in h or "category" in h), -1)
        scen_col = next((i for i, h in enumerate(header) if "scenario" in h or "description" in h or "action" in h or "step" in h or "summary" in h), -1)
        exp_col = next((i for i, h in enumerate(header) if "expect" in h or "outcome" in h or "result" in h), -1)

        if id_col == -1 or scen_col == -1:
            continue

        for r in rows[1:]:
            if not r or not r[id_col]:
                continue
            cid = str(r[id_col]).strip()
            if not cid or cid.lower() in ("test case id", "id", "case id"):
                continue
            rid = str(r[req_col]).strip() if req_col != -1 and r[req_col] else ""
            scenario = str(r[scen_col]).strip() if r[scen_col] else ""
            expected = str(r[exp_col]).strip() if exp_col != -1 and r[exp_col] else ""
            ctype = None
            if type_col != -1 and r[type_col]:
                try:
                    ctype = CaseType(str(r[type_col]).strip())
                except ValueError:
                    ctype = None

            cases.append(TestCase(
                id=cid,
                requirement_id=rid,
                type=ctype,
                scenario=_norm(scenario),
                expected=_norm(expected),
                source=source,
            ))

    name = path.name if isinstance(path, Path) else "spreadsheet"
    if not cases:
        raise LoadError(f"No test cases found in {name}.")
    return cases


def _cases_from_docx(path: Path, source: str) -> list[TestCase]:
    if not _HAS_DOCX:
        raise LoadError("python-docx is not installed to process .docx files.")

    try:
        doc = docx.Document(path)
    except Exception as e:
        # Check if file is actually an Excel spreadsheet with .docx extension
        if _HAS_OPENPYXL:
            try:
                return _cases_from_xlsx(io.BytesIO(path.read_bytes()), source)
            except Exception:
                pass
        raise LoadError(f"Could not read {path.name} as a Word document: {e}")

    cases: list[TestCase] = []
    for table in doc.tables:
        if len(table.rows) < 2:
            continue
        headers = [c.text.strip().lower().replace(" ", "_") for c in table.rows[0].cells]
        id_col = next((i for i, h in enumerate(headers) if "case_id" in h or "test_id" in h or "tc_id" in h or h == "id" or "test_case" in h), -1)
        scen_col = next((i for i, h in enumerate(headers) if "scenario" in h or "description" in h or "action" in h or "step" in h), -1)
        exp_col = next((i for i, h in enumerate(headers) if "expect" in h or "outcome" in h or "result" in h), -1)
        req_col = next((i for i, h in enumerate(headers) if "req" in h or "story" in h), -1)
        type_col = next((i for i, h in enumerate(headers) if "type" in h), -1)

        if id_col != -1 and scen_col != -1:
            for row in table.rows[1:]:
                cells = [c.text.strip() for c in row.cells]
                if not cells or not cells[id_col]:
                    continue
                cid = cells[id_col]
                rid = cells[req_col] if req_col != -1 and len(cells) > req_col else ""
                scen = cells[scen_col] if scen_col != -1 and len(cells) > scen_col else ""
                exp = cells[exp_col] if exp_col != -1 and len(cells) > exp_col else ""
                ctype = None
                if type_col != -1 and len(cells) > type_col and cells[type_col]:
                    try:
                        ctype = CaseType(cells[type_col])
                    except ValueError:
                        ctype = None
                cases.append(TestCase(
                    id=cid,
                    requirement_id=rid,
                    type=ctype,
                    scenario=_norm(scen),
                    expected=_norm(exp),
                    source=source,
                ))

    if not cases:
        raise LoadError(f"No test cases found in {path.name}.")
    return cases


def _cases_from_pdf(path: Path, source: str) -> list[TestCase]:
    cases: list[TestCase] = []
    tc_id_pattern = re.compile(r"^([A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*)\s*(?:\n\s*([A-Za-z]+))?$")

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                if not table or len(table) < 2:
                    continue
                header = [str(c or "").lower().strip() for c in table[0]]
                # Find column indices
                id_col = next((i for i, h in enumerate(header) if "id" in h or "test" in h), 0)
                scen_col = next((i for i, h in enumerate(header) if "scen" in h or "action" in h), 1)
                exp_col = next((i for i, h in enumerate(header) if "expect" in h or "result" in h), 2 if len(header) > 2 else 1)

                for row in table[1:]:
                    if not row or not row[id_col]:
                        continue
                    first_cell = str(row[id_col]).strip()
                    lines = [ln.strip() for ln in first_cell.splitlines() if ln.strip()]
                    if not lines:
                        continue

                    cid = lines[0]
                    # Skip non-test headers or summaries (must have both letters and digits, cannot be purely numeric)
                    if cid.isdigit() or len(cid) < 4 or not re.search(r"[A-Za-z]", cid) or not re.search(r"\d", cid) or len(cid) > 30:
                        continue

                    ctype = None
                    if len(lines) > 1:
                        try:
                            ctype = CaseType(lines[1])
                        except ValueError:
                            pass

                    # Inferred requirement ID from case ID if not explicit
                    # e.g. HOME-TC-01-01 -> HOME-REQ-01; TC-001-01 -> REQ-001
                    m_tc = re.match(r"^([A-Z0-9]+)-TC-(\d+)-(\d+)$", cid)
                    if m_tc:
                        prefix, req_no, _ = m_tc.groups()
                        inferred_req = f"{prefix}-REQ-{req_no}"
                    else:
                        m_tc2 = re.match(r"^TC-(\d+)-(\d+)$", cid)
                        if m_tc2:
                            inferred_req = f"BR-{m_tc2.group(1)}"
                        else:
                            inferred_req = ""

                    scenario = str(row[scen_col]).strip() if len(row) > scen_col and row[scen_col] else ""
                    expected = str(row[exp_col]).strip() if len(row) > exp_col and row[exp_col] else ""

                    cases.append(TestCase(
                        id=cid,
                        requirement_id=inferred_req,
                        type=ctype,
                        scenario=_norm(scenario),
                        expected=_norm(expected),
                        source=source,
                    ))

    if not cases:
        raise LoadError(f"No test cases found in {path.name}. Expected test case tables.")
    return cases


def _check_unique(ids: list[str], what: str) -> None:
    seen: set[str] = set()
    for i in ids:
        if i in seen:
            raise LoadError(f"Duplicate {what} ID: {i}")
        seen.add(i)
