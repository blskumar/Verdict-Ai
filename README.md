# Verdict AI: a test case gate

Know which AI-written tests to trust. Phase 1 (this folder): data contracts, file loaders and domain packs.

- Inputs: PDF, CSV or JSON only. No ALM or tracker connections.
- Install: `python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"`
- Commands: `verdict-ai domains list`, `verdict-ai domains show insurance/life/term`,
  `verdict-ai load -r data/requirements/insurance_requirements.pdf -c data/answer_key/insurance_test_cases.pdf`
- Tests: `python -m pytest`
- PyPI package name: `verdictai-gate`. CLI: `verdict-ai`. Import: `verdict_ai`.

## Layout
- `domains/insurance/{p-and-c,life}/...`: domain packs, inherited by path.
- `data/requirements`: the 25 sample requirements (PDF and JSON).
- `data/answer_key`: the 75 known-good test cases (PDF and JSON).
- `data/seeded_bad`: 33 labelled flawed cases, each with the decision the gate should reach.
