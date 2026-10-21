# Verdict AI — User Guide

> **Automated gatekeeper & quality rating system for AI-generated test cases.**

Verdict AI evaluates candidate software test cases against requirements specifications, detecting flaws before tests are committed to an ALM or test suite. It combines deterministic rule-based checks with a multi-agent LLM consensus pool.

---

## Table of Contents
1. [Installation & Setup](#1-installation--setup)
2. [Command Line Interface (CLI)](#2-command-line-interface-cli)
3. [FastAPI Web Interface (WebUI)](#3-fastapi-web-interface-webui)
4. [Multi-Agent Consensus Architecture](#4-multi-agent-consensus-architecture)
5. [Domain Packs & Inheritance](#5-domain-packs--inheritance)
6. [Gate Decision Engine & Thresholds](#6-gate-decision-engine--thresholds)
7. [Reports & Exports](#7-reports--exports)
8. [Security & Privacy](#8-security--privacy)

---

## 1. Installation & Setup

### Prerequisites
- Python 3.11+ (Python 3.12 or 3.13 recommended)
- Optional: OpenRouter API key for live LLM routing

### Setup Steps
```bash
# 1. Clone repository
git clone https://github.com/verdict-ai/verdict-ai.git
cd verdict-ai

# 2. Create virtual environment
python -m venv .venv
.\.venv\Scripts\activate   # Windows (or: source .venv/bin/activate on Linux/macOS)

# 3. Install package with dev and web dependencies
pip install -e ".[dev]"

# 4. (Optional) Set OpenRouter API key for live multi-agent calls
echo OPENROUTER_API_KEY=your_key_here > .env
```

### Run Tests
```bash
python -m pytest -q
# Output: 83 passed in ~8s
```

---

## 2. Command Line Interface (CLI)

The CLI tool is installed as `verdict-ai` (or via `python -m verdict_ai.cli`).

### Inspect Requirements & Test Cases (`load`)
Inspects input files (PDF, CSV, JSON) and prints parsed requirement & case statistics:
```bash
# Load requirements and test cases
verdict-ai load -r data/requirements/insurance_requirements.json -c data/answer_key/insurance_answer_key.json

# Read directly from PDF documents
verdict-ai load -r data/requirements/insurance_requirements.pdf -c data/answer_key/insurance_test_cases.pdf
```

### Browse Domain Packs (`domains`)
Domain packs contain industry-specific rules, boundary constants, and risk indicators:
```bash
# List all available domain packs in hierarchical tree format
verdict-ai domains list

# Inspect merged configuration for a specific domain
verdict-ai domains show insurance/life/term
```

### Launch Web Interface (`web`)
Launches the FastAPI web app with Tailwind and Shadcn UI:
```bash
# Start on default port 8000
verdict-ai web

# Start on custom host or port
verdict-ai web --host 0.0.0.0 --port 8080 --reload
```

### Run Benchmark Bake-off (`evaluate`)
Evaluates the gatekeeper against the 75 known-good test cases and 33 seeded flawed cases:
```bash
# Run in fast offline mock mode
verdict-ai evaluate --mode mock

# Run with live OpenRouter models configured in models.yaml
verdict-ai evaluate --mode live --threshold 0.85
```

---

## 3. FastAPI Web Interface (WebUI)

Launch the web app with `verdict-ai web` and open **`http://127.0.0.1:8000`** in your browser.

### Key Web Features
1. **Shadcn & Tailwind UI**: Responsive typography, card layouts, and subtle borders.
2. **Theme Switcher**: Instant Dark / Light mode toggle with persistent preference in `localStorage`.
3. **1-Click Sample Data**: Click **"Load Sample Data"** to immediately populate the interface with sample insurance requirements and candidate test cases.
4. **File Upload Dropzone**: Upload `.pdf`, `.csv`, or `.json` requirements and candidate test cases.
5. **Interactive Controls**:
   - **Domain Pack Selector**: Select active domain (e.g. `insurance/life/term`).
   - **Approval Threshold Slider**: Adjust minimum confidence score (default 85%).
   - **Engine Mode**: Choose between Fast Engine (Mock Router) or Live LLM Router.
6. **Real-time KPI Dashboard**:
   - Total cases scanned
   - Count & percentage for Approved, Revise, Reject, and Human Review
   - Average quality score
7. **Filterable Results Table**:
   - Filter by verdict (`All`, `Approve`, `Revise`, `Reject`, `Human Review`).
   - Expand rows to inspect individual check scores, model attributions, and defect reasons.
8. **Export Reports**: Instant one-click download for **JSON**, **CSV**, **Markdown**, and **HTML**.

---

## 4. Multi-Agent Consensus Architecture

Verdict AI employs 5 specialized AI roles orchestrated through `src/verdict_ai/orchestrator.py`:

```
                    Requirements & Test Cases
                               │
            ┌──────────────────┴──────────────────┐
            ▼                                     ▼
     Deterministic Rules                   Analyst Agent
  (syntax, duplicates, gaps)          (evaluates req quality)
            │                                     │
            └──────────────────┬──────────────────┘
                               │
               ┌───────────────┴───────────────┐
               ▼                               ▼
        Reviewer Agent                   Checker Agent
 (coverage & testability)         (traceability & hallucinations)
               │                               │
               └───────────────┬───────────────┘
                               │
                       Divergence Check
                  (disagree or gap >= 0.30?)
                     /                   \
                   Yes                    No
                   /                       \
             Arbiter Agent               Gate Engine
        (resolves conflict)                   │
                   \                         /
                    └──────────┬────────────┘
                               ▼
                        Gate Decision
           (Approve / Revise / Reject / Human Review)
```

- **Analyst Agent (`analyst.py`)**: Reviews requirement specifications and flags untestable criteria.
- **Generator Agent (`generator.py`)**: Automatically drafts candidate test cases when coverage gaps are detected.
- **Reviewer Agent (`reviewer.py`)**: Inspects each test case for requirement coverage, boundary validity, and arithmetic correctness.
- **Checker Agent (`checker.py`)**: Detects hallucinated fields, unreferenced variables, and untraceable requirement IDs.
- **Arbiter Agent (`arbiter.py`)**: Triggered only when Reviewer and Checker diverge significantly in score or pass/fail decisions.

---

## 5. Domain Packs & Inheritance

Domain packs reside in `domains/` and inherit attributes along the directory path. For example:
- `domains/insurance/domain.yaml` (Base Insurance pack)
  - `domains/insurance/life/domain.yaml` (Life Insurance extensions)
    - `domains/insurance/life/term/domain.yaml` (Term Life product rules)

Child packs inherit and override rules, boundary definitions, and vocabulary from parents.

---

## 6. Gate Decision Engine & Thresholds

The Gate Engine (`src/verdict_ai/gate.py`) aggregates findings into one of four standard decisions:

| Decision | Meaning | Typical Trigger |
|---|---|---|
| **Approve** | Test case is sound, traceable, and executable. | Overall mean score >= 0.85 with no defect findings. |
| **Revise** | Test case needs minor clarification or correction. | Score < 0.70, vague expected result, or compound actions. |
| **Reject** | Test case contains severe defects or hallucinations. | Untraceable requirement, wrong arithmetic, duplicate, or hallucinated fields. |
| **Human Review** | Ambiguous outcome or unresolved model disagreement. | Requirement coverage gap or score between 0.70 and 0.84. |

### Decision Priority
```
reject > revise > human_review > approve
```
Any single `reject` defect immediately marks the test case as rejected, regardless of other scores.

### Threshold Configuration (`gate.yaml`)
Thresholds can be adjusted globally in `gate.yaml` or per-domain without modifying code:
```yaml
thresholds:
  approve: 0.85
  human_review_min: 0.70
  revise_max: 0.70
```

---

## 7. Reports & Exports

Verdict AI generates reports in four formats:
- **Markdown (`.md`)**: GitHub-ready tables with WCAG-compliant status badges.
- **HTML (`.html`)**: Self-contained web report with embedded styling.
- **JSON (`.json`)**: Machine-readable full inspection trace for CI/CD integration.
- **CSV (`.csv`)**: Spreadsheet-friendly summary of decisions, scores, and reasons.

To export via CLI:
```bash
verdict-ai evaluate --output-dir runs/
```

---

## 8. Security & Privacy

Verdict AI is built with privacy-first and secure-by-default principles:
- **Zero ALM / Production Network Access**: Operates entirely over local files (`.pdf`, `.csv`, `.json`).
- **Safe File Uploads**: Upload filenames are sanitized with strict basename extraction (`Path.name`), preventing directory traversal attacks.
- **Isolated Temp Directories**: Uploaded files are processed in ephemeral `tempfile.TemporaryDirectory` sandboxes.
- **Disk Caching**: Model query caches use SHA-256 hashes without storing user credentials.
- **Credential Protection**: API tokens are sourced solely from environment variables (`OPENROUTER_API_KEY`) and are blocked by `.gitignore`.
