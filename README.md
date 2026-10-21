# Verdict AI — Test Case Gatekeeper

[![Tests](https://img.shields.io/badge/pytest-83%20passed-brightgreen.svg)](file:///C:/Verdict-Ai/tests)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![Tailwind CSS](https://img.shields.io/badge/TailwindCSS-v3-38B2AC.svg)](https://tailwindcss.com/)

**Know which AI-written test cases to trust.**

Verdict AI is an automated gatekeeper for software test cases. It inspects candidate test cases against requirements specifications using deterministic rules and a multi-agent LLM consensus pool, assigning one of four verdicts: **Approve**, **Revise**, **Reject**, or **Human Review**.

---

## Key Features

- **Multi-Format Ingestion**: Ingests requirements and candidate test cases from **PDF**, **CSV**, or **JSON** documents directly.
- **Deterministic Rule Engine**: Instantly flags missing outcomes, vague wording, compound actions, duplicates, coverage gaps, and untraceable requirement IDs without calling an LLM.
- **5-Role Multi-Agent Consensus**:
  - **Analyst**: Evaluates requirement testability.
  - **Generator**: Synthesizes test cases for uncovered requirements.
  - **Reviewer**: Evaluates scenario coverage and expected outcomes.
  - **Checker**: Catches hallucinations, invented fields, and untraceable variables.
  - **Arbiter**: Automatically resolves disagreements between Reviewer and Checker.
- **Failover LLM Router**: OpenRouter integration with ordered failover chains, cooldown tracking, disk caching, and request caps.
- **FastAPI Web Application**: Modern Tailwind CSS + Shadcn UI with light/dark theme toggle, 1-click sample data loader, and instant report export (JSON, CSV, Markdown, HTML).
- **Domain Pack Inheritance**: Hierarchical domain knowledge packs (e.g. `insurance/life/term`) defining product-specific rules and vocabulary.
- **Comprehensive Benchmark Suite**: Evaluates against 75 known-good test cases and 33 seeded defective cases with **100% defect catch rate** and **0% false rejection**.

---

## Architecture

```
                    Requirements & Candidate Test Cases
                                     │
            ┌────────────────────────┴────────────────────────┐
            ▼                                                 ▼
     Rule Engine (0ms)                                 Multi-Agent LLM
  - Vague expected results                          - Analyst (Requirements)
  - Missing expected results                        - Reviewer (Coverage)
  - Compound test cases                             - Checker (Hallucinations)
  - Duplicate test cases                            - Arbiter (Disagreements)
  - Untraceable requirements                                  │
            │                                                 │
            └────────────────────────┬────────────────────────┘
                                     ▼
                                Gate Engine
                  (Aggregates scores, applies thresholds)
                                     │
        ┌──────────────┬─────────────┴─────────────┬──────────────┐
        ▼              ▼                           ▼              ▼
     Approve         Revise                     Reject       Human Review
    (>= 85%)      (< 70% score)               (Severe Defect)  (Disputed / Gap)
```

---

## Quickstart

### 1. Installation
```bash
# Clone and enter directory
git clone https://github.com/verdict-ai/verdict-ai.git
cd verdict-ai

# Create virtual environment
python -m venv .venv
.\.venv\Scripts\activate   # Windows (or: source .venv/bin/activate on Linux/macOS)

# Install package with dependencies
pip install -e ".[dev]"
```

### 2. Run the Web Interface
```bash
verdict-ai web
```
Open **`http://127.0.0.1:8000`** in your browser. Click **"Load Sample Data"** to load the sample insurance test suite and run an instant evaluation!

### 3. Run Benchmark Bake-off
```bash
verdict-ai evaluate --mode mock
```

Output:
```
# Verdict AI — Evaluation Bake-off Report [PASS]
Total cases evaluated: 108
Catch Rate (Defects):  100.0% (33/33) — Target: >= 80% [PASS]
False Reject Rate:       0.0% (0/75)  — Target: < 15%  [PASS]
Reason Compliance:     100.0%         — Target: 100%   [PASS]
```

### 4. Run Pytest Suite
```bash
pytest -q
# 83 passed, 0 failed
```

---

## CLI Commands

| Command | Description | Example |
|---|---|---|
| `verdict-ai load` | Inspect requirements and test cases in PDF, CSV, or JSON | `verdict-ai load -r reqs.pdf -c cases.pdf` |
| `verdict-ai domains list` | Display hierarchical domain pack tree | `verdict-ai domains list` |
| `verdict-ai domains show` | Display merged domain configuration | `verdict-ai domains show insurance/life/term` |
| `verdict-ai web` | Launch the FastAPI WebUI server | `verdict-ai web --port 8000` |
| `verdict-ai evaluate` | Run benchmark bake-off on answer_key and seeded_bad | `verdict-ai evaluate --mode mock` |

---

## Documentation

For full configuration, threshold tuning, multi-agent prompts, and API reference, see:
- [User Guide](docs/user-guide.md)
- [Project Status](C:/Users/shara/.gemini/antigravity-ide/brain/1df45cb8-011d-4d88-a77d-b48e453d5df7/project_status.md)
- Interactive Swagger API: `http://127.0.0.1:8000/docs`

---

## License

MIT License. See [LICENSE](LICENSE) for details.
