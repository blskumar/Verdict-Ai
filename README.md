# Verdict AI — Test Case Gatekeeper
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)

**Know which AI-written test cases to trust.**

Verdict AI reads requirements and candidate test cases, rates each case, and returns one of four verdicts with a reason: **Approve**, **Revise**, **Reject** or **Human Review**. The models reason; plain code makes every decision.

> **Status (26 Oct 2026).** Plain-code rule checks are done and measured. The 80% defect-catch target has **not** been reached or measured with real models yet: offline, the rules alone catch 14 of 33 seeded bad cases (42.4%) and wrongly reject 0 of 75 good ones. The remaining defect types need live models. See [What is measured](#what-is-measured).

---

## Key features

- **Inputs:** requirements and test cases from **PDF, CSV, JSON, DOCX and XLSX** files, or pasted text. No ALM or tracker connection.
- **Domain packs:** insurance (`p-and-c/homeowners`, `p-and-c/commercial-auto`, `life/term`, `life/whole-life`, `life/common`) and banking (`retail/checking`, `retail/savings`, `lending/personal-loans`). Packs are YAML, inherited by folder path.
- **Domain guard:** if loaded or uploaded data belongs to another domain than the one selected, the app refuses it at once and names the domain to pick. It decides from each item's `domain`, then its ID prefix (`HOME-REQ-01`), then key words.
- **Domain review:** after **Analyze**, the page lists possibly outdated items, proposed requirements (domain risk areas no requirement covers) and scenarios still needed (domain boundary rules no case exercises). The pack's vague terms and ambiguous phrases are used too.
- **Rule engine (no model call):** missing, vague or ambiguous expected results, compound cases, near-duplicates, untraceable requirement IDs, requirements with no cases, coverage gaps, and figures with no source (`uncited_value`).
- **Five model roles:** analyst, generator, reviewer, checker, arbiter, run through an OpenRouter router with ordered failover, cool-downs, a disk cache and a request cap.
- **Safe by default:** an unusable model reply is "unjudged" and goes to Human Review. A case is never approved because a model failed to answer.
- **Web page:** FastAPI plus one HTML page. System / light / dark theme (follows the device by default), works offline (no third-party scripts), reports as JSON, CSV, Markdown and HTML.

---

## Architecture

```
                 Requirements & candidate test cases
                                  │
              ┌───────────────────┴───────────────────┐
              ▼                                       ▼
        Rule engine                          Model roles (via router)
   (plain code, 0 model calls)          analyst · reviewer · checker · arbiter
              │                                       │
              └───────────────────┬───────────────────┘
                                  ▼
                            Gate engine
                (plain code: scores + thresholds)
                                  │
         ┌─────────────┬──────────┴───────────┬─────────────┐
         ▼             ▼                      ▼             ▼
      Approve        Revise                Reject      Human Review
```

---

## Quickstart

```bash
git clone https://github.com/blskumar/Verdict-Ai.git
cd Verdict-Ai
python -m venv .venv
.\.venv\Scripts\activate        # Windows (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"
python -m pytest -q
```

### Run the web page
```bash
verdict-ai web
```
Open **http://127.0.0.1:8000**, pick a domain, click **Load Sample Data**, then **Analyze** and **Run Verdict Gate**. Start the server from the project folder.

### Use live models (optional)
Put `OPENROUTER_API_KEY=...` in a local `.env` file (it is git-ignored; never commit or paste it). Free endpoints may log prompts, so use invented data only.

### Run the benchmark
```bash
verdict-ai evaluate --mode mock    # offline: rules only, models are stubs
verdict-ai evaluate --mode live    # real models from models.yaml
```
The command exits with code 1 when a target is missed. Mock output today:

```
Catch Rate (Defects)   42.4% (14/33)   target >= 80%   FAIL
False Reject Rate       0.0% (0/75)    target < 15%    PASS
Reason Compliance     100.0%           target 100%     PASS
```

---

## What is measured

| Claim | Status |
|---|---|
| Wrongly rejects under 15% of good cases | Met: 0 of 75, by rules alone and across generated data for all 8 sub-domains. |
| A reason on every decision | Met, including approvals. |
| Catches at least 80% of seeded bad cases | **Not met offline (42.4%).** Caught by rules: vague, missing or ambiguous expected result, compound case, untraceable, wrong arithmetic. Not caught without models: contradicts requirement, hallucinated feature, wrong boundary outcome, invented value, near-duplicate, out of scope. The live-model run has not been done. |

The offline benchmark used to read the seeded defect labels and reported 33 of 33. That was circular and has been removed.

---

## Testing

```bash
python -m pytest -q                          # about 700 tests, offline (real HTTP is blocked in tests)
python scripts/cross_browser_e2e.py          # 17 browser/device profiles, 18 checks each
python scripts/perf_and_security_probe.py    # latency, load, headers, dependency scan
```
`tests/test_exhaustive_e2e.py` drives the real app with every sample file (PDF, DOCX, XLSX, CSV, JSON), generated data for each sub-domain, hostile input, exports, the CLI and security checks. Five known gaps are marked as expected failures with the reason.

**Browsers and devices checked** (WebKit stands in for Safari; device profiles are emulations, not physical phones): Chromium, installed Chrome and Edge, Firefox, WebKit, iPhone 15 and SE, iPad Pro, Pixel 7, Galaxy S9+ and Tab S4.

**Performance** (one laptop, mock mode): 108 cases in about 50 ms; 5,000 cases in about 6 s; one request is handled at a time, so about 5 to 6 requests/s whatever the load. Page is 102 KB, sent uncompressed.

---

## Security notes

- Responses carry CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy` and `Permissions-Policy`. The CSP still allows inline scripts (the page has some).
- All page scripts are served by the app (`static/vendor/`): Tailwind 3.4.17 (MIT) and Lucide 1.61.0 (ISC).
- Text from uploaded files is escaped before it is shown. Exports neutralise spreadsheet formulas.
- The client can name a resume file but not a path: files go under `runs/resume/`.
- Uploads are limited to 25 MB and 10 files. There is no cap on JSON request bodies yet.
- Dependencies have floors for `pillow` and `certifi`. Run `python scripts/perf_and_security_probe.py` to re-scan.
- The Cloudflare security-audit skill (github.com/cloudflare/security-audit-skill) has **not** been run; do it before publishing a package.

---

## CLI

| Command | What it does | Example |
|---|---|---|
| `verdict-ai load` | Read requirements or cases and print what was found | `verdict-ai load -r reqs.xlsx -c cases.docx` |
| `verdict-ai domains list` | Show the domain tree | `verdict-ai domains list` |
| `verdict-ai domains show` | Show a merged pack | `verdict-ai domains show banking/retail/checking` |
| `verdict-ai web` | Start the web page | `verdict-ai web --port 8000` |
| `verdict-ai evaluate` | Benchmark on `data/answer_key` and `data/seeded_bad` | `verdict-ai evaluate --mode mock` |

---

## Documentation

- [User guide](docs/user-guide.md)
- [Project notes and decisions](CLAUDE.md), [plan](PLAN.md)
- Swagger API while the server runs: http://127.0.0.1:8000/docs

## License

MIT. See [LICENSE](LICENSE).
