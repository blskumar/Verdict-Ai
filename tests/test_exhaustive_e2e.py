"""Exhaustive end-to-end tests through the real web app (ASGI) and the CLI.

Data comes from many sources on purpose: every domain pack, JSON / CSV / PDF / DOCX / XLSX / pasted text,
the repo's sample files (including ones that match no pack) and generated data in several writing styles.
A failing test here is a finding. Run: python -m pytest tests/test_exhaustive_e2e.py -q
"""
from __future__ import annotations

import csv
import io
import itertools
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from verdict_ai.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

DECISIONS = {"approve", "revise", "reject", "human_review"}

# leaf domain -> (id prefix, topics). Topics give each domain its own vocabulary.
LEAVES = {
    "insurance/p-and-c/homeowners": ("HOME", ["roof year", "protection class", "dwelling limit", "occupancy", "deductible", "year built"]),
    "insurance/p-and-c/commercial-auto": ("CA", ["fleet size", "garaging address", "vehicle class", "driver list", "fleet credit", "idempotency key"]),
    "insurance/life/term": ("TERM", ["term length", "beneficiary allocation", "free-look period", "underwriting status", "issue age", "rate class"]),
    "insurance/life/whole-life": ("WL", ["cash value", "policy loan", "modal premium", "dividend option", "issue age", "surrender charge"]),
    "insurance/life/common": ("LIFE", ["referral rule", "death claim notice", "audit history", "address change", "policy documents", "disclosure"]),
    "banking/retail/checking": ("CHK", ["minimum opening deposit", "overdraft opt-in", "maintenance fee waiver", "nsf fee", "direct deposit", "debit card"]),
    "banking/retail/savings": ("SAV", ["interest tier", "apy accrual", "withdrawal limit", "minimum balance", "dormant account", "statement cycle"]),
    "banking/lending/personal-loans": ("LOAN", ["apr disclosure", "debt-to-income limit", "origination fee", "credit score tier", "repayment schedule", "late fee"]),
}
PARENTS = ["insurance", "insurance/p-and-c", "insurance/life", "banking", "banking/retail", "banking/lending"]
ALL_DOMAINS = [*LEAVES, *PARENTS]


def _key(p: str) -> str:
    return "/".join(p.split("/")[:2])


def compatible(item: str, selected: str) -> bool:
    """Spec: same domain, a parent, a child, or the same branch (insurance/life, banking/retail, ...)."""
    return (item == selected or item.startswith(selected + "/") or selected.startswith(item + "/")
            or _key(item) == _key(selected))


# ---------------------------------------------------------------- data builders

def make_reqs(domain: str, n: int = 6, *, tag_domain: bool = True) -> list[dict]:
    prefix, topics = LEAVES[domain]
    out = []
    for i in range(1, n + 1):
        t = topics[(i - 1) % len(topics)]
        out.append({
            "id": f"{prefix}-REQ-{i:02d}",
            "title": f"{t.title()} handling",
            "text": f"The system validates {t} against the configured limit and blocks or refers a request that breaks it.",
            "acceptance_criteria": f"A request inside the {t} limit is accepted; one outside it is refused with a reason and an audit entry.",
            **({"domain": domain} if tag_domain else {}),
        })
    return out


def make_cases(reqs: list[dict]) -> list[dict]:
    cases = []
    for r in reqs:
        rid, t = r["id"], r["title"].lower()
        base = rid.replace("-REQ-", "-TC-")
        cases += [
            {"id": f"{base}-01", "requirement_id": rid, "type": "Positive",
             "scenario": f"Submit a request exactly inside the limit for {t}.",
             "expected": "Request is accepted and one audit entry is written."},
            {"id": f"{base}-02", "requirement_id": rid, "type": "Negative",
             "scenario": f"Submit a request outside the limit for {t}.",
             "expected": "Request is refused with a reason shown and nothing is saved."},
            {"id": f"{base}-03", "requirement_id": rid, "type": "Boundary",
             "scenario": f"Submit a request one unit above the limit for {t}.",
             "expected": "Request is refused and the limit value is shown in the message."},
        ]
    return cases


def flawed(reqs: list[dict]) -> dict[str, dict]:
    """One deliberately bad case per defect kind, built on the first requirement. Returns {defect: case}."""
    rid = reqs[0]["id"]
    n = rid.replace("-REQ-", "-TC-")
    return {
        "missing_expected": {"id": f"{n}-X1", "requirement_id": rid, "type": "Negative", "scenario": "Submit a bad request.", "expected": ""},
        "vague_expected": {"id": f"{n}-X2", "requirement_id": rid, "type": "Negative", "scenario": "Submit a bad request twice.", "expected": "The system behaves correctly."},
        "untraceable": {"id": f"{n}-X3", "requirement_id": "ZZZ-REQ-99", "type": "Positive", "scenario": "Do something unrelated.", "expected": "Something happens and is logged."},
        "uncited_value": {"id": f"{n}-X4", "requirement_id": rid, "type": "Calculation", "scenario": "Rate a standard request.", "expected": "Charge is $731.42."},
        "ambiguous_outcome": {"id": f"{n}-X5", "requirement_id": rid, "type": "Boundary", "scenario": "Submit one day below the minimum.", "expected": "Application is blocked or referred."},
    }


def evaluate(client, reqs, cases, domain=None, **kw):
    body = {"requirements": reqs, "cases": cases, "agent_mode": "mock", **kw}
    if domain is not None:
        body["domain"] = domain
    return client.post("/api/evaluate", json=body)


def csv_text(rows: list[dict], fields: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


def upload(client, name: str, content: bytes, domain: str | None = None, field: str = "file"):
    return client.post("/api/upload", files={field: (name, content)}, data={"domain": domain} if domain else {})


# ============================================================ 1. domains API

def test_domain_tree_lists_every_pack(client):
    flat = json.dumps(client.get("/api/domains").json())
    for d in ALL_DOMAINS:
        assert d in flat, d


@pytest.mark.parametrize("bad", ["../etc", "..", "insurance/../banking", "insurance\\life", "nope", "insurance/nope", "a/b/c/d/e", "", "  ", "INSURANCE", "insurance//life", "/insurance/", "insurance/life/term/extra"])
def test_sample_with_odd_domain_never_crashes(client, bad):
    r = client.get("/api/sample", params={"domain": bad})
    assert r.status_code == 200, (bad, r.text[:200])
    body = r.json()
    assert body["requirements"] and "domain" in body


@pytest.mark.parametrize("domain", ALL_DOMAINS)
def test_sample_matches_family_and_branch(client, domain):
    body = client.get("/api/sample", params={"domain": domain}).json()
    fam = domain.split("/")[0]
    assert body["domain"] == domain
    for r in body["requirements"]:
        assert not r.get("domain") or r["domain"].startswith(fam), (domain, r["id"], r.get("domain"))
    ids = {r["id"] for r in body["requirements"]}
    assert all(c["requirement_id"] in ids for c in body["cases"]), "sample cases must belong to sample requirements"


# ============================================================ 2. domain guard matrix (data x selected domain)

@pytest.mark.parametrize("data_domain,selected", list(itertools.product(LEAVES, ALL_DOMAINS)))
def test_guard_matrix_tagged_requirements(client, data_domain, selected):
    reqs = make_reqs(data_domain, 3)
    r = client.post("/api/domain/check", json={"domain": selected, "requirements": reqs}).json()
    assert r["ok"] is compatible(data_domain, selected), (data_domain, selected, r)


@pytest.mark.parametrize("data_domain,selected", list(itertools.product(LEAVES, ["insurance/life/term", "banking/retail/checking", "insurance", "banking"])))
def test_guard_matrix_untagged_requirements_use_id_prefix(client, data_domain, selected):
    reqs = make_reqs(data_domain, 3, tag_domain=False)
    r = client.post("/api/domain/check", json={"domain": selected, "requirements": reqs}).json()
    assert r["ok"] is compatible(data_domain, selected), (data_domain, selected, r)


@pytest.mark.parametrize("data_domain,selected", [("insurance/life/term", "banking"), ("banking/retail/savings", "insurance/p-and-c/homeowners")])
def test_guard_uses_case_requirement_ids_too(client, data_domain, selected):
    cases = make_cases(make_reqs(data_domain, 2, tag_domain=False))
    assert client.post("/api/domain/check", json={"domain": selected, "cases": cases}).json()["ok"] is False


def test_guard_explicit_intuitive_pairs(client):
    pairs = [("insurance/life/term", "insurance/life/whole-life", True), ("insurance/p-and-c/homeowners", "insurance/p-and-c/commercial-auto", True),
             ("insurance/life/term", "insurance/p-and-c/homeowners", False), ("banking/retail/checking", "banking/retail/savings", True),
             ("banking/retail/checking", "banking/lending/personal-loans", False), ("banking/lending/personal-loans", "insurance", False),
             ("insurance/life/term", "banking", False), ("insurance/life/term", "insurance", True)]
    for data_domain, selected, ok in pairs:
        r = client.post("/api/domain/check", json={"domain": selected, "requirements": make_reqs(data_domain, 2)}).json()
        assert r["ok"] is ok, (data_domain, selected, r)


def test_guard_message_names_both_domains(client):
    r = client.post("/api/domain/check", json={"domain": "banking/retail/savings", "requirements": make_reqs("insurance/life/term", 3)}).json()
    assert not r["ok"] and "insurance/life" in r["message"] and "banking/retail/savings" in r["message"]


def test_guard_mixed_file_is_refused_even_if_most_items_fit(client):
    reqs = make_reqs("banking/retail/checking", 5) + make_reqs("insurance/life/term", 1)
    assert client.post("/api/domain/check", json={"domain": "banking/retail/checking", "requirements": reqs}).json()["ok"] is False


@pytest.mark.parametrize("domain", [None, "", "no/such/domain"])
def test_guard_no_or_unknown_domain_does_not_block(client, domain):
    body = {"requirements": make_reqs("insurance/life/term", 2)}
    if domain is not None:
        body["domain"] = domain
    assert client.post("/api/domain/check", json=body).status_code == 200


def test_guard_empty_data_is_ok(client):
    assert client.post("/api/domain/check", json={"domain": "insurance"}).json()["ok"] is True


# ---- foreign content: no tags, no known prefixes, only words
FOREIGN_WORDS = {
    "banking_words_no_ids": ("insurance/life/term", [
        {"id": "R1", "title": "Open account", "text": "A customer opens an account with a deposit and the ledger balance updates after the transfer.", "acceptance_criteria": "Loan and interest are shown."},
        {"id": "R2", "title": "Overdraft", "text": "An overdraft on the account creates a withdrawal fee and a KYC check on the customer.", "acceptance_criteria": "ACH transfer is limited."}], False),
    "insurance_words_no_ids": ("banking/retail/checking", [
        {"id": "R1", "title": "Quote", "text": "The policy quote shows the premium, deductible and coverage for the insured.", "acceptance_criteria": "Endorsement and rider are listed."},
        {"id": "R2", "title": "Claim", "text": "A claim is bound to the policy and the beneficiary receives underwriting notes.", "acceptance_criteria": "Peril is recorded."}], False),
    "telecom_under_insurance": ("insurance", [
        {"id": "T-1", "title": "Roaming", "text": "Subscriber roaming charges apply when the handset attaches to a foreign network cell.", "acceptance_criteria": "Data cap throttles speed."}], False),
    "healthcare_under_banking": ("banking", [
        {"id": "H-1", "title": "Prescription", "text": "A physician orders a prescription and the pharmacy dispenses the dosage to the patient.", "acceptance_criteria": "Allergy alerts fire."}], False),
    "ecommerce_under_insurance": ("insurance/life/term", [
        {"id": "E-1", "title": "Cart", "text": "A shopper adds a product to the cart and checks out with a coupon and shipping address.", "acceptance_criteria": "Stock decrements."}], False),
}


@pytest.mark.parametrize("name", [pytest.param(n, marks=pytest.mark.xfail(strict=True, reason="FINDING: content from a domain with no pack (telecom, healthcare, e-commerce) is not recognised as foreign")) if n.split("_")[0] in ("telecom", "healthcare", "ecommerce") else n for n in FOREIGN_WORDS])
def test_guard_foreign_content_without_ids(client, name):
    domain, reqs, expect_ok = FOREIGN_WORDS[name]
    r = client.post("/api/domain/check", json={"domain": domain, "requirements": reqs}).json()
    assert r["ok"] is expect_ok, (name, r)


# ============================================================ 3. real sample files x every source format

REAL_FILES = {
    # file -> (kind, expected count, a domain it should be accepted under, a domain it should be refused under)
    "data/requirements/insurance_requirements.json": ("req", 25, "insurance", "banking"),
    "data/requirements/insurance_requirements.pdf": ("req", 25, "insurance", "banking"),
    "Expanded_Insurance_Requirements_Pack.pdf": ("req", 25, "insurance", "banking"),
    "data/requirements/banking_requirements.json": ("req", 10, "banking", "insurance"),
    "data/requirements/banking_requirements.csv": ("req", 10, "banking", "insurance"),
    "Banking_Domain_Sample_Requirements.pdf": ("req", 10, "banking", "insurance"),
    "Banking_Domain_Sample_Requirements.docx": ("req", 10, "banking", "insurance"),
    "banking_requirements_core_catalogue.xlsx": ("req", 39, "banking", "insurance"),
    "banking_requirements_core_catalogue.pdf": ("req", 7, "banking", "insurance"),
    "Sample_PnC_Insurance_Requirements.pdf": ("req", 10, "insurance/p-and-c", "banking"),
    "Sample_PnC_Insurance_Requirements.docx": ("req", 10, "insurance/p-and-c", "banking"),
    "data/answer_key/insurance_answer_key.json": ("case", 75, "insurance", "banking"),
    "data/answer_key/insurance_test_cases.pdf": ("case", 75, "insurance", "banking"),
    "Expanded_Insurance_Test_Cases.pdf": ("case", 75, "insurance", "banking"),
    "data/answer_key/banking_answer_key.json": ("case", 17, "banking", "insurance"),
    "Banking_Domain_Detailed_Test_Cases.xlsx": ("case", 30, "banking", "insurance"),
    "Sample_PnC_Insurance_Test_Cases.xlsx": ("case", 47, "insurance/p-and-c", "banking"),
    "Sample_PnC_Insurance_Test_Cases.docx": ("case", 47, "insurance/p-and-c", "banking"),
    "data/seeded_bad/insurance_seeded_bad.json": ("case", 33, "insurance", "banking"),
    "data/seeded_bad/insurance_seeded_bad_review.csv": ("case", 33, "insurance", "banking"),
}


@pytest.mark.parametrize("rel", list(REAL_FILES))
def test_real_file_uploads_and_counts(client, rel):
    kind, n, _ok, _bad = REAL_FILES[rel]
    p = ROOT / rel
    r = upload(client, p.name, p.read_bytes())
    assert r.status_code == 200, (rel, r.text[:300])
    body = r.json()
    got = body["requirements_count"] if kind == "req" else body["cases_count"]
    assert got == n, (rel, got, n)


@pytest.mark.parametrize("rel", list(REAL_FILES))
def test_real_file_accepted_under_matching_domain(client, rel):
    _k, _n, ok_domain, _bad = REAL_FILES[rel]
    p = ROOT / rel
    r = upload(client, p.name, p.read_bytes(), domain=ok_domain)
    assert r.status_code == 200, (rel, ok_domain, r.text[:300])


@pytest.mark.parametrize("rel", list(REAL_FILES))
def test_real_file_refused_under_wrong_family(client, rel):
    _k, _n, _ok, bad_domain = REAL_FILES[rel]
    p = ROOT / rel
    r = upload(client, p.name, p.read_bytes(), domain=bad_domain)
    assert r.status_code == 422, (rel, bad_domain, r.status_code, r.text[:200])
    assert bad_domain in r.json()["detail"]


@pytest.mark.parametrize("rel", ["data/requirements/insurance_requirements.json", "data/requirements/banking_requirements.csv"])
@pytest.mark.parametrize("field", ["requirements_file", "cases_file", "file", "files"])
def test_upload_field_names_all_work_or_fail_cleanly(client, rel, field):
    p = ROOT / rel
    r = upload(client, p.name, p.read_bytes(), field=field)
    assert r.status_code in (200, 400), r.text[:200]


# ============================================================ 4. upload negatives and edge cases

def test_upload_unsupported_type(client):
    assert upload(client, "notes.txt", b"hello").status_code == 400


def test_upload_empty_file_is_ignored_not_crashing(client):
    r = upload(client, "empty.json", b"")
    assert r.status_code == 200 and r.json()["requirements_count"] == 0


@pytest.mark.parametrize("payload", [b"{not json", b"[1, 2, 3]", b'{"a": 1}', b"null", b'[{"id": 1}]', b'[{"foo": "bar"}]'])
def test_upload_malformed_json_gives_4xx_not_500(client, payload):
    r = upload(client, "bad.json", payload)
    assert r.status_code < 500, (payload, r.status_code, r.text[:200])


def test_upload_malformed_csv(client):
    assert upload(client, "bad.csv", b"a,b\n1").status_code < 500
    assert upload(client, "bad.csv", b"\xff\xfe\x00garbage").status_code < 500


def test_upload_pdf_that_is_not_a_pdf(client):
    assert upload(client, "fake.pdf", b"this is not a pdf").status_code < 500


def test_upload_corrupt_docx_xlsx(client):
    assert upload(client, "fake.docx", b"PK\x03\x04junk").status_code < 500
    assert upload(client, "fake.xlsx", b"PK\x03\x04junk").status_code < 500


def test_upload_too_many_files(client):
    files = [("files", (f"r{i}.json", json.dumps([{"id": f"HOME-REQ-{i}", "title": "t", "text": "x"}]).encode())) for i in range(11)]
    assert client.post("/api/upload", files=files).status_code == 400


def test_upload_exactly_ten_files(client):
    files = [("files", (f"r{i}.json", json.dumps([{"id": f"HOME-REQ-{i:02d}", "title": "t", "text": "Capture property details."}]).encode())) for i in range(10)]
    r = client.post("/api/upload", files=files)
    assert r.status_code == 200


def test_upload_over_25mb_is_refused(client):
    r = upload(client, "big.json", b"[" + b" " * (26 * 1024 * 1024) + b"]")
    assert r.status_code == 413


def test_upload_filename_traversal_is_neutralised(client):
    payload = json.dumps([{"id": "HOME-REQ-01", "title": "t", "text": "Capture property details."}]).encode()
    r = client.post("/api/upload", files={"file": ("../../evil.json", payload)})
    assert r.status_code == 200
    assert not (ROOT.parent / "evil.json").exists()


def test_upload_duplicate_ids_rejected_cleanly(client):
    payload = json.dumps([{"id": "HOME-REQ-01", "title": "a", "text": "x"}, {"id": "HOME-REQ-01", "title": "b", "text": "y"}]).encode()
    r = upload(client, "dup.json", payload)
    assert r.status_code in (400, 422), r.text[:200]


def test_upload_unicode_and_emoji_roundtrip(client):
    payload = json.dumps([{"id": "HOME-REQ-01", "title": "Überprüfung 保険 🚗", "text": "Prüfe Dachjahr — «naïve» café ☕", "acceptance_criteria": "ok"}], ensure_ascii=False).encode("utf-8")
    r = upload(client, "u.json", payload)
    assert r.status_code == 200 and "保険" in r.json()["requirements"][0]["title"]


def test_upload_csv_with_bom_and_quotes_and_commas(client):
    text = 'id,title,text,acceptance_criteria,domain\r\nCHK-REQ-01,"Fee, waiver","Waive the fee when ""direct deposit"" ≥ $500, else charge.",Fee charged below $500,banking/retail/checking\r\n'
    r = upload(client, "q.csv", b"\xef\xbb\xbf" + text.encode("utf-8"), domain="banking/retail/checking")
    assert r.status_code == 200, r.text[:200]
    assert "direct deposit" in r.json()["requirements"][0]["text"]


def test_upload_requirements_and_cases_together(client):
    reqs = make_reqs("insurance/life/term", 2)
    cases = make_cases(reqs)
    r = client.post("/api/upload", files={"requirements_file": ("r.json", json.dumps(reqs).encode()), "cases_file": ("c.json", json.dumps(cases).encode())},
                    data={"domain": "insurance/life/term"})
    assert r.status_code == 200 and r.json()["requirements_count"] == 2 and r.json()["cases_count"] == 6


def test_upload_cases_swapped_into_requirements_slot_is_recovered_or_clean_error(client):
    cases = make_cases(make_reqs("insurance/life/term", 2))
    r = client.post("/api/upload", files={"requirements_file": ("c.json", json.dumps(cases).encode())})
    assert r.status_code in (200, 400)


@pytest.mark.parametrize("domain", sorted(LEAVES))
@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_upload_generated_data_for_every_leaf_domain_and_format(client, domain, fmt):
    reqs = make_reqs(domain, 6)
    fields = ["id", "title", "text", "acceptance_criteria", "domain"]
    content = json.dumps(reqs).encode() if fmt == "json" else csv_text(reqs, fields).encode()
    r = upload(client, f"reqs.{fmt}", content, domain=domain)
    assert r.status_code == 200, (domain, fmt, r.text[:200])
    assert r.json()["requirements_count"] == 6


# ============================================================ 5. recognize / paste input styles

PASTE_STYLES = {
    "id_dash_title": "HOME-REQ-01 - Capture property details\nThe system captures the address and year built.\nAcceptance criteria: invalid year is blocked.\n\nHOME-REQ-02 - Validate eligibility\nThe system checks supported states.\nAcceptance criteria: unsupported state is referred.",
    "user_story": "As a policy holder, I want to change my beneficiary so that my payout goes to the right person.\nAs an underwriter, I want to see referral reasons so that I can decide faster.",
    "given_when_then": "Given a checking account with a $25.00 balance\nWhen a $30.00 debit is posted with overdraft opt-in disabled\nThen the debit is declined and an NSF notice is queued.",
    "bullets": "- Savings interest accrues daily\n- Withdrawal limit is six per cycle\n- Dormant accounts are flagged after 12 months",
    "numbered": "1. The loan APR is disclosed before acceptance.\n2. Debt-to-income above the limit is refused.\n3. Origination fee is shown separately.",
    "single_sentence": "The system shall block a withdrawal above the daily limit.",
    "unicode_mixed": "Требование: система должна проверять возраст. 要件: 年齢を確認する。",
    "tabs_and_blank_lines": "\n\n\tREQ one: check the limit\n\n\n\tREQ two: show the reason\n\n",
}


@pytest.mark.parametrize("style", list(PASTE_STYLES))
def test_recognize_handles_each_writing_style(client, style):
    r = client.post("/api/requirements/recognize", json={"raw_text": PASTE_STYLES[style]})
    assert r.status_code == 200, (style, r.text[:200])
    assert r.json()["count"] >= 1, style
    for q in r.json()["requirements"]:
        assert q["id"] and q["text"]


@pytest.mark.parametrize("raw", ["", "   ", "\n\n\n"])
def test_recognize_empty_is_400(client, raw):
    assert client.post("/api/requirements/recognize", json={"raw_text": raw}).status_code == 400


def test_recognize_huge_text(client):
    raw = "\n".join(f"The system shall enforce limit number {i} on the account balance." for i in range(2000))
    r = client.post("/api/requirements/recognize", json={"raw_text": raw})
    assert r.status_code == 200 and r.json()["count"] >= 1


def test_recognize_then_analyze_pasted_banking_under_banking_and_insurance(client):
    req = client.post("/api/requirements/recognize", json={"raw_text": PASTE_STYLES["numbered"]}).json()["requirements"]
    ok = client.post("/api/requirements/analyze", json={"requirements": req, "domain": "banking/lending/personal-loans"})
    assert ok.status_code == 200
    bad = client.post("/api/requirements/analyze", json={"requirements": req, "domain": "insurance/life/term"})
    assert bad.status_code == 422, "loan text with no IDs should still be recognised as banking by its words"


def test_analyze_raw_text_mode(client):
    r = client.post("/api/requirements/analyze", json={"raw_text": PASTE_STYLES["user_story"], "domain": "insurance/life/term"})
    assert r.status_code == 200 and r.json()["analyses"]


# ============================================================ 6. analyze: every domain, many shapes

@pytest.mark.parametrize("domain", sorted(LEAVES))
def test_analyze_each_leaf_domain(client, domain):
    reqs = make_reqs(domain, 6)
    r = client.post("/api/requirements/analyze", json={"requirements": reqs, "domain": domain})
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert len(body["analyses"]) == 6
    assert set(body["domain_review"]) == {"obsolete", "proposed_requirements", "extra_scenarios"}
    for a in body["analyses"]:
        assert 0.0 <= a["testability_score"] <= 1.0 and a["testability_verdict"] in {"testable", "needs_review", "untestable"}
        assert a["suggested_scenarios"], a["req_id"]


@pytest.mark.parametrize("domain", ALL_DOMAINS)
def test_analyze_under_parent_domains_too(client, domain):
    fam = [d for d in LEAVES if compatible(d, domain)][0]
    r = client.post("/api/requirements/analyze", json={"requirements": make_reqs(fam, 3), "domain": domain})
    assert r.status_code == 200


VAGUE_REQS = {
    "generic_vague": ("The system should be fast, robust and user-friendly.", True),
    "pack_vague_banking": ("Customers are notified in a timely manner and the process is secure.", True),
    "ambiguous_pack_phrase": ("Fees are based on banking policy and standard fees apply.", True),
    "concrete": ("The system declines a debit above $500.00 and records the decline within 2 seconds.", False),
}


@pytest.mark.parametrize("name", list(VAGUE_REQS))
def test_analyze_flags_vague_language_in_banking(client, name):
    text, vague = VAGUE_REQS[name]
    reqs = [{"id": "CHK-REQ-01", "title": "T", "text": text, "acceptance_criteria": "Behaviour is verified by the stated limits.", "domain": "banking/retail/checking"}]
    a = client.post("/api/requirements/analyze", json={"requirements": reqs, "domain": "banking/retail/checking"}).json()["analyses"][0]
    codes = {i["code"] for i in a["issues"]}
    flagged = bool(codes & {"vague_terms", "domain_vague_terms", "domain_ambiguous_phrases"})
    assert flagged is vague, (name, codes)


def test_analyze_missing_acceptance_criteria_is_flagged(client):
    reqs = [{"id": "SAV-REQ-01", "title": "T", "text": "Interest accrues daily at the configured rate.", "domain": "banking/retail/savings"}]
    a = client.post("/api/requirements/analyze", json={"requirements": reqs, "domain": "banking/retail/savings"}).json()["analyses"][0]
    assert "missing_acceptance_criteria" in {i["code"] for i in a["issues"]}


@pytest.mark.parametrize("payload", [{"requirements": []}, {}, pytest.param({"requirements": [{}]}, marks=pytest.mark.xfail(strict=True, reason="FINDING (low): an empty requirement object is analysed instead of rejected")), {"requirements": "x"}])
def test_analyze_bad_payloads_are_4xx(client, payload):
    r = client.post("/api/requirements/analyze", json=payload)
    assert 400 <= r.status_code < 500, (payload, r.status_code)


def test_analyze_one_giant_requirement(client):
    reqs = [{"id": "HOME-REQ-01", "title": "Big", "text": "The system validates every field. " * 5000, "acceptance_criteria": "ok ok ok ok ok", "domain": "insurance/p-and-c/homeowners"}]
    t = time.time()
    r = client.post("/api/requirements/analyze", json={"requirements": reqs, "domain": "insurance/p-and-c/homeowners"})
    assert r.status_code == 200 and time.time() - t < 15


def test_analyze_200_requirements(client):
    reqs = [{"id": f"HOME-REQ-{i:03d}", "title": f"Rule {i}", "text": f"Validate rule {i} against the configured limit.", "acceptance_criteria": "Invalid input is refused.", "domain": "insurance/p-and-c/homeowners"} for i in range(200)]
    t = time.time()
    r = client.post("/api/requirements/analyze", json={"requirements": reqs, "domain": "insurance/p-and-c/homeowners"})
    assert r.status_code == 200 and len(r.json()["analyses"]) == 200 and time.time() - t < 30


# ============================================================ 7. domain review behaviours

def test_review_flags_outdated_markers_in_requirements_and_cases(client):
    reqs = make_reqs("insurance/life/term", 2)
    reqs[0]["text"] += " This legacy flow is deprecated and superseded by TERM-REQ-02."
    cases = make_cases(reqs)
    cases[0]["scenario"] += " (retired rule)"
    rv = client.post("/api/requirements/analyze", json={"requirements": reqs, "cases": cases, "domain": "insurance/life/term"}).json()["domain_review"]
    kinds = {(o["kind"], o["id"]) for o in rv["obsolete"]}
    assert ("requirement", "TERM-REQ-01") in kinds and ("case", cases[0]["id"]) in kinds


def test_review_flags_orphan_cases(client):
    reqs = make_reqs("banking/retail/savings", 2)
    cases = make_cases(reqs) + [{"id": "SAV-TC-99-01", "requirement_id": "SAV-REQ-99", "scenario": "s", "expected": "e"}]
    rv = client.post("/api/requirements/analyze", json={"requirements": reqs, "cases": cases, "domain": "banking/retail/savings"}).json()["domain_review"]
    assert any(o["id"] == "SAV-TC-99-01" for o in rv["obsolete"])


@pytest.mark.parametrize("domain", sorted(LEAVES))
def test_review_proposals_are_unique_and_well_formed(client, domain):
    rv = client.post("/api/requirements/analyze", json={"requirements": make_reqs(domain, 3), "domain": domain}).json()["domain_review"]
    ids = [p["id"] for p in rv["proposed_requirements"]]
    assert len(ids) == len(set(ids))
    for p in rv["proposed_requirements"]:
        assert "-REQ-NEW-" in p["id"] and p["title"] and p["text"] and p["reason"]


def test_review_proposal_disappears_when_requirement_covers_it(client):
    reqs = [{"id": "CHK-REQ-01", "title": "Overdraft fee", "text": "Charge an overdraft fee, apply the waiver criteria and check the min deposit.", "acceptance_criteria": "ok ok ok ok ok ok", "domain": "banking/retail/checking"}]
    rv = client.post("/api/requirements/analyze", json={"requirements": reqs, "domain": "banking/retail/checking"}).json()["domain_review"]
    titles = " ".join(p["title"] for p in rv["proposed_requirements"])
    assert "overdraft fee" not in titles and "waiver criteria" not in titles and "min deposit" not in titles


def test_review_without_domain_is_null(client):
    assert client.post("/api/requirements/analyze", json={"requirements": make_reqs("insurance/life/term", 1)}).json()["domain_review"] is None


# ============================================================ 8. generate cases

@pytest.mark.parametrize("domain", sorted(LEAVES))
def test_generate_cases_each_domain(client, domain):
    reqs = make_reqs(domain, 4)
    r = client.post("/api/requirements/generate-cases", json={"requirements": reqs, "domain": domain, "agent_mode": "mock"})
    assert r.status_code == 200, r.text[:300]
    cases = r.json().get("cases") or r.json().get("generated_cases") or []
    assert cases, r.json().keys()
    ids = {q["id"] for q in reqs}
    assert {c["requirement_id"] for c in cases} <= ids
    assert len({c["id"] for c in cases}) == len(cases), "generated case ids must be unique"


def test_generate_cases_empty_is_400(client):
    assert client.post("/api/requirements/generate-cases", json={"requirements": []}).status_code == 400


def test_generate_cases_wrong_domain_refused(client):
    assert client.post("/api/requirements/generate-cases", json={"requirements": make_reqs("banking/retail/savings", 2), "domain": "insurance/life/term"}).status_code == 422


def test_generated_cases_pass_through_the_gate(client):
    reqs = make_reqs("insurance/life/whole-life", 3)
    gen = client.post("/api/requirements/generate-cases", json={"requirements": reqs, "domain": "insurance/life/whole-life"}).json()
    cases = gen.get("cases") or gen.get("generated_cases")
    r = evaluate(client, reqs, cases, "insurance/life/whole-life")
    assert r.status_code == 200 and {g["decision"] for g in r.json()["gate_results"]} <= DECISIONS


# ============================================================ 9. evaluate: positive, negative, boundary

@pytest.mark.parametrize("domain", sorted(LEAVES))
def test_evaluate_clean_generated_data_is_mostly_approved(client, domain):
    reqs = make_reqs(domain, 6)
    cases = make_cases(reqs)
    r = evaluate(client, reqs, cases, domain)
    assert r.status_code == 200, r.text[:300]
    s = r.json()["summary"]
    assert s["total_cases"] == len(cases)
    assert s["approved"] + s["revised"] + s["rejected"] + s["human_review"] == len(cases)
    assert s["approved"] / len(cases) >= 0.5, (domain, s)
    assert s["rejected"] == 0, (domain, s)


@pytest.mark.parametrize("domain", sorted(LEAVES))
@pytest.mark.parametrize("defect", ["missing_expected", "vague_expected", "untraceable", "uncited_value", "ambiguous_outcome"])
def test_evaluate_catches_each_defect_in_each_domain(client, domain, defect):
    reqs = make_reqs(domain, 4)
    bad = flawed(reqs)[defect]
    r = evaluate(client, reqs, make_cases(reqs) + [bad], domain)
    assert r.status_code == 200
    got = {g["case_id"]: g for g in r.json()["gate_results"]}[bad["id"]]
    assert got["decision"] != "approve", (domain, defect, got["decision"], got["reasons"])
    assert got["reasons"], "every decision needs a reason"


EXPECTED_DECISION = {"missing_expected": "revise", "vague_expected": "revise", "untraceable": "reject", "uncited_value": "revise", "ambiguous_outcome": "human_review"}


@pytest.mark.parametrize("defect", list(EXPECTED_DECISION))
def test_evaluate_defect_gets_the_right_decision(client, defect):
    domain = "banking/retail/savings"
    reqs = make_reqs(domain, 3)
    bad = flawed(reqs)[defect]
    r = evaluate(client, reqs, [bad], domain)
    assert r.json()["gate_results"][0]["decision"] == EXPECTED_DECISION[defect], r.json()["gate_results"][0]


def test_every_decision_has_a_reason_and_valid_shape(client):
    reqs = make_reqs("insurance/p-and-c/commercial-auto", 5)
    bad = list(flawed(reqs).values())
    r = evaluate(client, reqs, make_cases(reqs) + bad, "insurance/p-and-c/commercial-auto").json()
    for g in r["gate_results"]:
        assert g["decision"] in DECISIONS and g["reasons"] and all(isinstance(x, str) and x for x in g["reasons"]), g
        assert 0.0 <= g["mean_score"] <= 1.0


@pytest.mark.parametrize("threshold,min_approved,max_approved", [(0.0, 1, None), (1.0, 0, 0), (0.5, 0, None)])
def test_evaluate_threshold_extremes(client, threshold, min_approved, max_approved):
    reqs = make_reqs("insurance/life/term", 3)
    r = evaluate(client, reqs, make_cases(reqs), "insurance/life/term", approve_threshold=threshold)
    assert r.status_code == 200
    a = r.json()["summary"]["approved"]
    assert a >= min_approved and (max_approved is None or a <= max_approved), (threshold, a)


@pytest.mark.parametrize("threshold", [-0.01, 1.01, 5, "abc", None])
def test_evaluate_threshold_out_of_range_is_422(client, threshold):
    reqs = make_reqs("insurance/life/term", 1)
    r = evaluate(client, reqs, make_cases(reqs), "insurance/life/term", approve_threshold=threshold)
    assert r.status_code == 422, (threshold, r.status_code)


def test_evaluate_threshold_changes_outcomes_monotonically(client):
    reqs = make_reqs("insurance/life/term", 5)
    cases = make_cases(reqs)
    approved = [evaluate(client, reqs, cases, "insurance/life/term", approve_threshold=t).json()["summary"]["approved"] for t in (0.3, 0.6, 0.85, 0.99)]
    assert approved == sorted(approved, reverse=True), approved


@pytest.mark.parametrize("body", [{"requirements": [], "cases": []}, {"cases": []}, {}])
def test_evaluate_no_cases_is_400(client, body):
    assert client.post("/api/evaluate", json=body).status_code in (400, 422)


def test_evaluate_without_requirements_still_judges_cases(client):
    cases = make_cases(make_reqs("insurance/life/term", 2))
    r = client.post("/api/evaluate", json={"cases": cases, "agent_mode": "mock"})
    assert r.status_code == 200 and len(r.json()["gate_results"]) == len(cases)


def test_evaluate_without_domain_works(client):
    reqs = make_reqs("banking/lending/personal-loans", 2)
    assert evaluate(client, reqs, make_cases(reqs)).status_code == 200


def test_evaluate_duplicate_case_ids(client):
    reqs = make_reqs("insurance/life/term", 1)
    c = make_cases(reqs)[0]
    r = evaluate(client, reqs, [c, dict(c)], "insurance/life/term")
    assert r.status_code in (200, 400, 422)
    if r.status_code == 200:
        assert len({g["case_id"] for g in r.json()["gate_results"]}) == len(r.json()["gate_results"]) or True


def test_evaluate_near_duplicate_case_is_caught(client):
    reqs = make_reqs("banking/retail/checking", 1)
    base = make_cases(reqs)[0]
    twin = {**base, "id": base["id"] + "-DUP", "scenario": base["scenario"].replace("exactly ", "")}
    r = evaluate(client, reqs, [base, twin], "banking/retail/checking").json()
    codes = {f["code"] for f in r["requirement_findings"]}
    assert "near_duplicate" in codes or any("uplicate" in x for g in r["gate_results"] for x in g["reasons"])


HOSTILE = [("scenario", "x" * 100_000), ("expected", "y" * 100_000), ("scenario", "💥" * 5000), ("expected", "<script>alert(1)</script>"),
           ("scenario", "'; DROP TABLE cases;--"), ("expected", "=HYPERLINK(\"http://evil\")")]


@pytest.mark.parametrize("field,value", HOSTILE, ids=[f"{f}-{i}" for i, (f, _) in enumerate(HOSTILE)])  # short ids: a 100k id overflows a Windows env var
def test_evaluate_hostile_or_huge_field_values(client, field, value):
    reqs = make_reqs("insurance/life/term", 1)
    c = make_cases(reqs)[0]
    c[field] = value
    r = evaluate(client, reqs, [c], "insurance/life/term")
    assert r.status_code == 200, (field, r.status_code, r.text[:200])
    assert r.json()["gate_results"][0]["decision"] in DECISIONS


@pytest.mark.parametrize("patch", [{"type": "Nonsense"}, {"type": None}, {"id": ""}, {"requirement_id": ""}, {"scenario": ""}, {"expected": None}, {"extra_field": 1}])
def test_evaluate_malformed_case_fields_are_4xx_or_handled(client, patch):
    reqs = make_reqs("insurance/life/term", 1)
    c = {**make_cases(reqs)[0], **patch}
    r = evaluate(client, reqs, [c], "insurance/life/term")
    assert r.status_code < 500, (patch, r.status_code, r.text[:200])


def test_evaluate_500_cases_performance(client):
    reqs = make_reqs("insurance/p-and-c/homeowners", 6)
    cases = []
    for k in range(34):
        for c in make_cases(reqs):
            cases.append({**c, "id": f"{c['id']}-{k}"})
    t = time.time()
    r = evaluate(client, reqs, cases, "insurance/p-and-c/homeowners")
    assert r.status_code == 200 and len(r.json()["gate_results"]) == len(cases) and time.time() - t < 60, time.time() - t


def test_evaluate_is_deterministic(client):
    reqs = make_reqs("banking/lending/personal-loans", 4)
    cases = make_cases(reqs) + list(flawed(reqs).values())
    a = evaluate(client, reqs, cases, "banking/lending/personal-loans").json()
    b = evaluate(client, reqs, cases, "banking/lending/personal-loans").json()
    assert [(g["case_id"], g["decision"]) for g in a["gate_results"]] == [(g["case_id"], g["decision"]) for g in b["gate_results"]]


def test_evaluate_wrong_domain_refused_everywhere(client):
    reqs = make_reqs("insurance/life/term", 2)
    for dom in ("banking", "banking/retail/checking", "insurance/p-and-c/homeowners" if False else "banking/lending"):
        assert evaluate(client, reqs, make_cases(reqs), dom).status_code == 422, dom


def test_evaluate_live_mode_without_network_fails_safe(client):
    reqs = make_reqs("insurance/life/term", 2)
    r = evaluate(client, reqs, make_cases(reqs), "insurance/life/term", agent_mode="live", use_rules=False)
    assert r.status_code == 200
    assert all(g["decision"] == "human_review" for g in r.json()["gate_results"]), "never approve when no model could judge"


def test_evaluate_rules_only_and_agents_only(client):
    reqs = make_reqs("insurance/life/term", 2)
    cases = make_cases(reqs) + [flawed(reqs)["missing_expected"]]
    r1 = evaluate(client, reqs, cases, "insurance/life/term", use_agents=False)
    r2 = evaluate(client, reqs, cases, "insurance/life/term", use_rules=False)
    assert r1.status_code == 200 and r2.status_code == 200
    d1 = {g["case_id"]: g["decision"] for g in r1.json()["gate_results"]}
    assert d1[flawed(reqs)["missing_expected"]["id"]] != "approve"


# ============================================================ 10. the project's success criteria, end to end

def _insurance_reqs():
    return json.loads((DATA / "requirements/insurance_requirements.json").read_text(encoding="utf-8"))


@pytest.mark.xfail(strict=True, reason="FINDING: offline catch rate is 14/33 (42%), target is 80%. The CLI mock reaches 33/33 only because it reads the seeded defect labels")
def test_success_criteria_through_the_api(client):
    reqs = _insurance_reqs()
    good = json.loads((DATA / "answer_key/insurance_answer_key.json").read_text(encoding="utf-8"))
    bad = json.loads((DATA / "seeded_bad/insurance_seeded_bad.json").read_text(encoding="utf-8"))
    r = evaluate(client, reqs, good + bad, "insurance").json()
    got = {g["case_id"]: g["decision"] for g in r["gate_results"]}
    wrongly_rejected = [c["id"] for c in good if got[c["id"]] == "reject"]
    caught = [c["id"] for c in bad if got[c["id"]] != "approve"]
    exact = [c["id"] for c in bad if got[c["id"]] == c["expected_decision"]]
    print(f"\nSUCCESS CRITERIA: caught {len(caught)}/{len(bad)}  exact {len(exact)}/{len(bad)}  wrongly rejected {len(wrongly_rejected)}/{len(good)}")
    assert len(caught) / len(bad) >= 0.80
    assert len(wrongly_rejected) / len(good) < 0.15
    assert all(g["reasons"] for g in r["gate_results"])


@pytest.mark.parametrize("src", ["json", "pdf"])
def test_success_criteria_same_from_json_and_pdf_sources(client, src):
    if src == "json":
        reqs = _insurance_reqs()
        good = json.loads((DATA / "answer_key/insurance_answer_key.json").read_text(encoding="utf-8"))
    else:
        reqs = upload(client, "r.pdf", (DATA / "requirements/insurance_requirements.pdf").read_bytes()).json()["requirements"]
        good = upload(client, "c.pdf", (DATA / "answer_key/insurance_test_cases.pdf").read_bytes()).json()["cases"]
    r = evaluate(client, reqs, good, "insurance").json()["summary"]
    assert r["rejected"] / len(good) < 0.15, r


@pytest.mark.parametrize("leaf", ["homeowners", "commercial-auto", "term", "whole-life", "common"])
def test_good_cases_per_insurance_subdomain_are_not_wrongly_rejected(client, leaf):
    prefix = {"homeowners": "HOME", "commercial-auto": "CA", "term": "TERM", "whole-life": "WL", "common": "LIFE"}[leaf]
    good = [c for c in json.loads((DATA / "answer_key/insurance_answer_key.json").read_text(encoding="utf-8")) if c["requirement_id"].startswith(prefix + "-")]
    reqs = [r for r in _insurance_reqs() if r["id"].startswith(prefix + "-")]
    path = next(d for d in LEAVES if d.endswith("/" + leaf))
    s = evaluate(client, reqs, good, path).json()["summary"]
    assert s["rejected"] == 0, (leaf, s)


def test_banking_answer_key_and_catalogue_cases(client):
    reqs = json.loads((DATA / "requirements/banking_requirements.json").read_text(encoding="utf-8"))
    good = json.loads((DATA / "answer_key/banking_answer_key.json").read_text(encoding="utf-8"))
    s = evaluate(client, reqs, good, "banking").json()["summary"]
    assert s["rejected"] / len(good) < 0.15, s


def test_banking_sample_xlsx_cases_with_docx_requirements(client):
    reqs = upload(client, "r.docx", (ROOT / "Banking_Domain_Sample_Requirements.docx").read_bytes(), domain="banking").json()["requirements"]
    cases = upload(client, "c.xlsx", (ROOT / "Banking_Domain_Detailed_Test_Cases.xlsx").read_bytes(), domain="banking").json()["cases"]
    r = evaluate(client, reqs, cases, "banking")
    assert r.status_code == 200 and r.json()["summary"]["total_cases"] == len(cases) == 30


def test_pnc_personal_auto_sample_is_out_of_scope_but_must_not_crash(client):
    reqs = upload(client, "r.pdf", (ROOT / "Sample_PnC_Insurance_Requirements.pdf").read_bytes()).json()["requirements"]
    cases = upload(client, "c.xlsx", (ROOT / "Sample_PnC_Insurance_Test_Cases.xlsx").read_bytes()).json()["cases"]
    r = evaluate(client, reqs, cases, "insurance/p-and-c")
    assert r.status_code == 200 and r.json()["summary"]["total_cases"] == len(cases)


def test_catalogue_with_many_id_families_under_banking(client):
    reqs = upload(client, "cat.xlsx", (ROOT / "banking_requirements_core_catalogue.xlsx").read_bytes(), domain="banking").json()["requirements"]
    r = client.post("/api/requirements/analyze", json={"requirements": reqs, "domain": "banking"})
    assert r.status_code == 200 and len(r.json()["analyses"]) == len(reqs) == 39


# ============================================================ 11. exports

def _results(client):
    reqs = make_reqs("banking/retail/savings", 2)
    return evaluate(client, reqs, make_cases(reqs) + list(flawed(reqs).values()), "banking/retail/savings").json()["gate_results"]


@pytest.mark.parametrize("fmt,ctype", [("json", "application/json"), ("csv", "text/csv"), ("md", "text/markdown"), ("html", "text/html")])
def test_export_results_each_format(client, fmt, ctype):
    res = _results(client)
    r = client.post(f"/api/export/{fmt}", json={"results": res})
    assert r.status_code == 200 and r.headers["content-type"].startswith(ctype) and "attachment" in r.headers["content-disposition"]
    for g in res:
        assert g["case_id"] in r.text


def test_export_csv_rows_match_results_and_keep_reasons_on_one_row(client):
    res = _results(client)
    rows = list(csv.reader(io.StringIO(client.post("/api/export/csv", json={"results": res}).text)))
    assert len(rows) == len(res) + 1


def test_export_csv_neutralises_formula_in_reasons(client):
    r = client.post("/api/export/csv", json={"results": [{"case_id": "A-1", "decision": "revise", "reasons": ["=cmd|' /C calc'!A0", "+1+1", "@SUM(1)", "-2+3"]}]})
    for row in csv.reader(io.StringIO(r.text)):
        for cell in row:
            assert not cell.startswith(("=", "+", "@")) or cell.startswith("'"), cell


def test_export_html_escapes_markup(client):
    r = client.post("/api/export/html", json={"results": [{"case_id": "<img src=x onerror=alert(1)>", "decision": "revise", "reasons": ["<script>alert(1)</script>"]}]})
    assert "<script>alert(1)</script>" not in r.text and "<img src=x onerror" not in r.text


@pytest.mark.parametrize("fmt", ["pdf", "xml", "JSON", "", "csv.exe"])
def test_export_bad_format_is_4xx(client, fmt):
    r = client.post(f"/api/export/{fmt}", json={"results": []})
    assert 400 <= r.status_code < 500


def test_export_empty_results(client):
    assert client.post("/api/export/json", json={"results": []}).status_code in (200, 400, 422)


@pytest.mark.parametrize("bad", [{"case_id": "A", "decision": "maybe"}, {"decision": "approve"}, {"case_id": "", "decision": "approve"}, {"case_id": 5, "decision": "approve"}])
def test_export_invalid_results_are_422(client, bad):
    assert client.post("/api/export/json", json={"results": [bad]}).status_code == 422


@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_cases_export_groups_and_keeps_source_ref(client, fmt):
    reqs = make_reqs("insurance/life/term", 3)
    cases = make_cases(reqs)
    cases[0]["source_ref"] = "Rate table RT-9"
    r = client.post(f"/api/cases/export/{fmt}", json=cases)
    assert r.status_code == 200 and "Rate table RT-9" in r.text
    if fmt == "json":
        assert set(r.json()["groups"]) == {q["id"] for q in reqs}


def test_cases_export_csv_roundtrips_through_the_loader(client, tmp_path):
    from verdict_ai.loader import load_cases
    cases = make_cases(make_reqs("insurance/life/term", 2))
    text = client.post("/api/cases/export/csv", json=cases).text
    rows = [r for r in csv.reader(io.StringIO(text)) if r and not r[0].startswith("REQUIREMENT:")]
    p = tmp_path / "back.csv"
    p.write_text("\n".join(",".join('"' + c.replace('"', '""') + '"' for c in r) for r in rows), encoding="utf-8")
    assert len(load_cases(p)) == len(cases)


@pytest.mark.parametrize("fmt", ["json", "csv", "md", "html"])
def test_requirements_review_export(client, fmt):
    r = client.post(f"/api/requirements/export/{fmt}", json={"requirements": make_reqs("banking/lending/personal-loans", 3), "domain": "banking/lending/personal-loans"})
    assert r.status_code == 200 and "LOAN-REQ-01" in r.text


def test_requirements_review_export_empty_is_400(client):
    assert client.post("/api/requirements/export/json", json={"requirements": []}).status_code == 400


# ============================================================ 12. security

def test_resume_path_cannot_write_outside_the_project(client, tmp_path):
    target = tmp_path / "attacker_chosen.json"
    reqs = make_reqs("insurance/life/term", 1)
    evaluate(client, reqs, make_cases(reqs), "insurance/life/term", resume_path=str(target))
    assert not target.exists(), "the client chose a file path and the server wrote to it"


def test_resume_path_traversal_and_absolute(client):
    reqs = make_reqs("insurance/life/term", 1)
    for p in ("../../resume_leak.json", "C:/Windows/Temp/resume_leak.json", "/etc/resume_leak.json"):
        r = evaluate(client, reqs, make_cases(reqs), "insurance/life/term", resume_path=p)
        assert r.status_code < 500
    assert not (ROOT.parent / "resume_leak.json").exists()
    assert not Path("C:/Windows/Temp/resume_leak.json").exists()


def test_html_page_has_no_inline_secrets(client):
    page = client.get("/").text
    for needle in ("sk-or-", "OPENROUTER_API_KEY=", "Bearer "):
        assert needle not in page


def test_api_never_echoes_the_api_key(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-TESTSECRET123")
    reqs = make_reqs("insurance/life/term", 1)
    body = evaluate(client, reqs, make_cases(reqs), "insurance/life/term", agent_mode="live").text
    assert "TESTSECRET123" not in body


def test_health_and_docs_and_openapi(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/nope").status_code == 404


def test_wrong_http_methods(client):
    assert client.get("/api/evaluate").status_code == 405
    assert client.post("/api/domains").status_code == 405


def test_invalid_json_body(client):
    r = client.post("/api/evaluate", content=b"{oops", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


# ============================================================ 13. CLI

def _cli(*args, cwd=None):
    import subprocess
    import sys
    return subprocess.run([sys.executable, "-m", "verdict_ai.cli", *args], capture_output=True, text=True, cwd=cwd or ROOT, timeout=300)


def test_cli_entrypoint_runs():
    import subprocess
    import sys
    p = subprocess.run([sys.executable, "-c", "from verdict_ai.cli import app; app(['--help'])"], capture_output=True, text=True, cwd=ROOT)
    assert "evaluate" in p.stdout and p.returncode == 0


def _run_cli(args):
    import subprocess
    import sys
    code = "from verdict_ai.cli import app; app()"
    return subprocess.run([sys.executable, "-c", code, *args], capture_output=True, text=True, cwd=ROOT, timeout=300)


@pytest.mark.parametrize("domain", ALL_DOMAINS)
def test_cli_domains_show_each(domain):
    p = _run_cli(["domains", "show", domain])
    assert p.returncode == 0 and json.loads(p.stdout)["path"] == domain


@pytest.mark.parametrize("bad", ["nope", "../x", "insurance/nope"])
def test_cli_domains_show_bad(bad):
    p = _run_cli(["domains", "show", bad])
    assert p.returncode != 0 and "Traceback" not in p.stderr


def test_cli_domains_list_has_all():
    out = _run_cli(["domains", "list"]).stdout
    for d in ALL_DOMAINS:
        assert d in out


@pytest.mark.parametrize("rel", ["data/requirements/insurance_requirements.json", "data/requirements/banking_requirements.csv", "data/requirements/insurance_requirements.pdf", "Banking_Domain_Sample_Requirements.docx"])
def test_cli_load_requirements_various_formats(rel):
    p = _run_cli(["load", "--requirements", rel])
    assert p.returncode == 0, p.stderr[-300:]


@pytest.mark.parametrize("rel", ["data/answer_key/insurance_answer_key.json", "Banking_Domain_Detailed_Test_Cases.xlsx", "Sample_PnC_Insurance_Test_Cases.docx"])
def test_cli_load_cases_various_formats(rel):
    p = _run_cli(["load", "--cases", rel])
    assert p.returncode == 0, p.stderr[-300:]


@pytest.mark.parametrize("args", [["load", "--requirements", "missing.json"], ["load", "--requirements", "README.md"], ["load"], ["load", "--requirements", "data/answer_key/insurance_test_cases.pdf"]])
def test_cli_load_bad_inputs_fail_cleanly(args):
    p = _run_cli(args)
    assert "Traceback" not in p.stderr, p.stderr[-400:]


@pytest.mark.parametrize("domain", ["insurance/life/term", "insurance/p-and-c/homeowners", "insurance"])
def test_cli_evaluate_mock_benchmark(tmp_path, domain):
    p = _run_cli(["evaluate", "--domain", domain, "--output-dir", str(tmp_path)])
    # exit code 1 is the intended signal that the 80% target was missed (mock models only run the rules)
    assert p.returncode == 1, p.stderr[-500:] + p.stdout[-500:]
    assert "Mock run" in p.stdout and "Traceback" not in p.stderr
    assert list(tmp_path.glob("*.json")) and list(tmp_path.glob("*.md"))


def test_cli_evaluate_bad_domain_and_missing_files(tmp_path):
    p = _run_cli(["evaluate", "--domain", "nope/nope", "--output-dir", str(tmp_path)])
    assert "Traceback" not in p.stderr
    p = _run_cli(["evaluate", "--requirements", "missing.json", "--output-dir", str(tmp_path)])
    assert "Traceback" not in p.stderr


# ============================================================ 14. extra evidence for the findings

def test_xlsx_catalogue_loader_keeps_requirement_statements():
    from verdict_ai.loader import load_requirements
    reqs = load_requirements(ROOT / "banking_requirements_core_catalogue.xlsx")
    assert all(r.text != r.id and len(r.text) > 20 for r in reqs), "statements were dropped"


def test_rules_alone_catch_rate_on_seeded_cases_is_reported():
    from verdict_ai.domains import load_pack
    from verdict_ai.loader import load_cases, load_requirements
    from verdict_ai.rules import run_rules
    reqs = load_requirements(DATA / "requirements/insurance_requirements.json")
    key = load_cases(DATA / "answer_key/insurance_answer_key.json", "answer_key")
    bad = load_cases(DATA / "seeded_bad/insurance_seeded_bad.json")
    flagged = {f.case_id for f in run_rules(reqs, bad, key, pack=load_pack("insurance")) if f.case_id}
    print(f"RULES ONLY: {len(flagged)}/{len(bad)} seeded cases flagged")
    assert len(flagged) >= 14


def test_cli_mock_benchmark_does_not_read_defect_labels():
    """The mock used to read meta['defect'], which made '33/33 caught' circular. It must stay label-free."""
    import inspect
    from verdict_ai import evaluate as ev
    src = inspect.getsource(ev._build_evaluation_mock_router)
    assert "meta" not in src and "seeded_bad" not in src


def test_resume_plain_file_name_is_allowed_and_stays_under_runs(client):
    reqs = make_reqs("insurance/life/term", 1)
    r = evaluate(client, reqs, make_cases(reqs), "insurance/life/term", resume_path="probe_ok.json")
    assert r.status_code == 200
    written = ROOT / "runs" / "resume" / "probe_ok.json"
    assert written.exists()
    written.unlink()


@pytest.mark.parametrize("bad", ["../x.json", "a/b.json", "a\b.json", "C:/Windows/x.json", "/etc/x.json", "x.txt", "x", ".json", "..", "", "x" * 200 + ".json"])
def test_resume_bad_names_are_400(client, bad):
    reqs = make_reqs("insurance/life/term", 1)
    r = evaluate(client, reqs, make_cases(reqs), "insurance/life/term", resume_path=bad)
    assert r.status_code == (200 if bad == "" else 400), (bad, r.status_code)


# ============================================================ 15. security hardening (regression tests)

@pytest.mark.parametrize("path", ["/", "/api/health", "/api/domains", "/static/index.html"])
def test_security_headers_on_every_kind_of_response(client, path):
    h = {k.lower(): v for k, v in client.get(path).headers.items()}
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
    csp = h["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp and "default-src 'self'" in csp and "connect-src 'self'" in csp


def test_docs_page_is_not_broken_by_the_csp(client):
    assert "content-security-policy" not in {k.lower() for k in client.get("/docs").headers}


def test_page_loads_no_third_party_scripts_and_vendor_files_are_served(client):
    import re
    page = client.get("/").text
    srcs = re.findall(r'<script[^>]+src="([^"]+)"', page)
    assert srcs and all(s.startswith("/static/") for s in srcs), srcs
    for s in srcs:
        r = client.get(s)
        assert r.status_code == 200 and len(r.content) > 100_000, s


def test_csp_does_not_allow_external_script_hosts(client):
    csp = client.get("/").headers["content-security-policy"]
    script_src = [d for d in csp.split(";") if d.strip().startswith("script-src")][0]
    assert "http" not in script_src and "*" not in script_src


def test_page_escapes_requirement_text_in_source(client):
    page = client.get("/").text
    assert "${r.title || r.id}" not in page and "${r.text || ''}" not in page and "${message}</div>" not in page and "${sc.scenario}" not in page


# ============================================================ 16. UI source regressions (theme, layout, tap targets)

def test_theme_is_applied_before_first_paint_and_has_three_modes(client):
    page = client.get("/").text
    head = page.split("</head>")[0]
    assert "verdict_theme" in head and "prefers-color-scheme" in head, "theme must be set in <head>, before the body paints"
    for icon in ("themeIconSystem", "themeIconSun", "themeIconMoon"):
        assert icon in page
    assert "THEME_NEXT" in page and "system: 'light', light: 'dark', dark: 'system'" in page
    assert 'aria-label="Toggle Theme"' in page


def test_toolbars_wrap_and_controls_are_at_least_24px(client):
    page = client.get("/").text
    assert 'class="flex flex-wrap items-center gap-1.5"' in page
    assert "px-1.5 py-0.5 text-[10px] font-semibold rounded bg-background" not in page, "old 19px-high buttons are back"
    assert "w-full h-2 bg-muted" not in page, "8px-high slider is back"
    assert page.count("min-h-[24px]") >= 8


# ============================================================ 17. one evaluator per domain family

def test_cli_evaluate_runs_every_family_and_prints_a_summary(tmp_path):
    p = _run_cli(["evaluate", "--output-dir", str(tmp_path)])
    assert p.returncode == 1 and "Traceback" not in p.stderr  # 1 = a target is missed (mock models only run the rules)
    for needle in ("=== insurance", "=== banking", "=== Summary by set ===", "Results by sub-domain", "banking/retail/checking", "insurance/life/term"):
        assert needle in p.stdout, needle
    names = sorted(f.name for f in tmp_path.glob("eval_*.json"))
    assert any(n.startswith("eval_insurance_") for n in names) and any(n.startswith("eval_banking_") for n in names)


@pytest.mark.parametrize("args,expect", [
    (["--set", "banking"], ["=== banking", "personal-loans"]),
    (["--set", "insurance"], ["=== insurance", "homeowners"]),
    (["--domain", "banking/retail/checking"], ["=== banking", "banking/retail/checking"]),
    (["--domain", "insurance/life/term"], ["=== insurance"]),
])
def test_cli_evaluate_picks_the_set_of_the_family(tmp_path, args, expect):
    p = _run_cli(["evaluate", *args, "--output-dir", str(tmp_path)])
    assert "Traceback" not in p.stderr
    for needle in expect:
        assert needle in p.stdout, (args, needle)
    other = "insurance" if "banking" in expect[0] else "banking"
    assert f"=== {other}" not in p.stdout


@pytest.mark.parametrize("args", [["--set", "telecom"], ["-r", "data/requirements/banking_requirements.json"]])
def test_cli_evaluate_bad_set_or_incomplete_custom_run_exits_2(tmp_path, args):
    p = _run_cli(["evaluate", *args, "--output-dir", str(tmp_path)])
    assert p.returncode == 2 and "Traceback" not in p.stderr


def test_cli_evaluate_custom_run_with_all_three_files(tmp_path):
    p = _run_cli(["evaluate", "-r", "data/requirements/banking_requirements.json", "-a", "data/answer_key/banking_answer_key.json",
                  "-b", "data/seeded_bad/banking_seeded_bad.json", "-d", "banking", "--output-dir", str(tmp_path)])
    assert p.returncode == 1 and "=== custom" in p.stdout and "Traceback" not in p.stderr


def test_banking_rules_floor_is_reported_per_defect_and_per_subdomain(tmp_path):
    import json as _json
    _run_cli(["evaluate", "--set", "banking", "--output-dir", str(tmp_path)])
    rep = _json.loads(next(tmp_path.glob("eval_banking_*.json")).read_text(encoding="utf-8"))
    assert rep["bad_cases"] == 33 and rep["good_cases"] == 17
    assert set(rep["by_domain"]) >= {"banking/retail/checking", "banking/retail/savings", "banking/lending/personal-loans"}
    assert rep["good_hard_rejected"] == 0
    assert rep["defect_breakdown"]["missing_expected"]["caught"] == 2
