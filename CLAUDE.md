# Verdict AI: a test case gate

Know which AI-written tests to trust. Python tool: generates test cases from requirements with an LLM, rates each one, returns approve / revise / reject / human_review with a reason. The model reasons; plain code decides.

## Names
- Brand: Verdict AI. PyPI package: `verdictai-gate`. CLI: `verdict-ai`. Import: `verdict_ai`.

## Decisions already made
- Inputs are PDF, CSV or JSON files only. No ALM, Jira or tracker integration for now.
- Domain: insurance only for now, two branches: `insurance/p-and-c` (homeowners, commercial-auto) and `insurance/life` (term, whole-life, common). Personal Auto is out of scope.
- Domain packs are YAML in `domains/`, inherited by folder path (context joined parent first; glossary child wins; lists combined; thresholds child overrides).
- Starting thresholds (assumptions): revise below 0.70, human-review band 0.70 to 0.85, similarity 0.85.
- Success criteria: catch at least 80% of seeded bad cases, wrongly reject under 15% of good cases, a reason for every decision.
- Web view (later): FastAPI plus one HTML page, light/dark/system toggle, domain dropdowns from the domain tree.

## Done (Phase 1)
Data contracts (`src/verdict_ai/models.py`), loaders (`loader.py`), domain packs (`domains.py`, `domains/`), CLI `load` and `domains`, answer key (75 good cases) and 33 labelled seeded bad cases in `data/`. 22 tests pass (`python -m pytest`).

## Next (Phase 2)
Rule checks: requirement with no cases, duplicates, missing or vague expected results, ambiguous phrases, coverage gaps; measure them against `data/seeded_bad/` and `data/answer_key/`. Then model checks, the gate engine, reporter, web view.

## Before publishing anything
Run the Cloudflare security-audit skill (github.com/cloudflare/security-audit-skill) first.

## Setup
python -m venv .venv; activate; pip install -e ".[dev]"; python -m pytest
