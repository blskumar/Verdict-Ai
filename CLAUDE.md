# Verdict AI: a test case gate

Know which AI-written tests to trust. A Python tool that reads requirements, drafts or reads test cases, rates each case, and returns approve / revise / reject / human_review with a reason. The model reasons; plain code decides.

Last updated: 2026-10-13. Keep this file current as work lands.

## Names
- Brand: Verdict AI. PyPI package: `verdictai-gate`. Command: `verdict-ai`. Python import: `verdict_ai`.
- Project folder: `C:\Verdict-Ai`.

## Decisions already made
- Inputs are PDF, CSV or JSON files only. No ALM, Jira or tracker integration for now.
- First release is insurance only, two branches: `insurance/p-and-c` (homeowners, commercial-auto) and `insurance/life` (term, whole-life, common). Personal Auto is out of scope.
- Domain packs are YAML in `domains/`, inherited by folder path (context joined parent first; glossary child wins; lists combined and de-duplicated; thresholds child overrides).
- Starting thresholds (tune only after the evaluation run): revise below 0.70, human-review band 0.70 to 0.85, similarity 0.85.
- Success criteria: catch at least 80% of seeded bad cases, wrongly reject under 15% of good cases, a reason on every decision.
- Reports: Markdown, HTML and JSON, plus a CSV export of decisions.
- Web view: FastAPI plus one HTML page, light / dark / system toggle (saved choice applied before first paint), domain dropdowns built from the domain tree.
- The owner has delegated decisions ("choose what is best"). Record each decision here.

## Models: OpenRouter, free tier first
- Provider: OpenRouter (OpenAI-compatible API). Key in a local `.env` as `OPENROUTER_API_KEY`. Never commit it, never paste it in chat.
- Each role has an ordered list of models in a config file (`models.yaml`). The router moves to the next model on: HTTP 429 (rate limit), 402 (insufficient credits), 5xx, timeout, model unavailable, or two invalid JSON replies in a row. A failed model gets a cool-down.
- Record which model produced every decision and show it in the report.
- If every model for a role is used up: save progress, stop, allow resume. Unjudged cases come back as `human_review`. Never guess.
- Batch several cases per call, cache replies on disk (keyed by model + prompt hash), count requests against a configurable cap.
- OpenRouter free limits at last check: 20 requests a minute, 50 a day; 1,000 a day after a $10 top-up. They change often, so read the live limit from `GET /api/v1/key` when possible.
- Free endpoints may log prompts. Use only sanitised or invented data.
- Do not rely on structured-output support; ask for JSON, validate it in code, retry once.

## Runtime roles and first-choice models (starting guesses; the bake-off decides)
| Role | Job | First choice | Backups |
|---|---|---|---|
| Orchestrator | Plans the run, batches, routes, tracks budget, handles failover. Plain code. | none | none |
| Requirement analyst | Flags vague or untestable requirements, extracts stated rules and values | apodex/apodex-1.1-mini:free | qwen/qwen3.8-27b:free, google/gemma-4-31b-it:free |
| Test case generator | Drafts test cases | google/gemma-4-31b-it:free | qwen/qwen3.8-27b:free, nvidia/nemotron-3-super-120b-a12b:free |
| Scenario reviewer | Checks each case vs its requirement: coverage, correctness, testability | nvidia/nemotron-3-super-120b-a12b:free | apodex/apodex-1.1-mini:free, qwen/qwen3.8-27b:free |
| Hallucination and traceability checker | Invented fields or values, cases with no requirement | qwen/qwen3.8-27b:free | apodex/apodex-1.1-mini:free, google/gemma-4-31b-it:free |
| Arbiter | Only when reviewers disagree; unresolved goes to human_review | apodex/apodex-1.1-mini:free | nvidia/nemotron-3-super-120b-a12b:free |

Use different model families for the generator and the judging roles where possible. The final decision is always made in code (`gate.py`), never by a model.

## Done
Phase 1: data contracts (`models.py`), loaders (`loader.py`), domain packs (`domains.py`, `domains/`), CLI `load` and `domains`, answer key (75 good cases) and 33 labelled seeded bad cases in `data/`. 22 tests pass.

## In progress
Phase 2: rule checks in `src/verdict_ai/rules.py` with `tests/test_rules.py` (started in Claude Code; check its state before continuing).

## Next
See `PLAN.md` for the work packages, their order and the done-when for each.

## Data
- `data/requirements/` the 25 sample requirements (PDF and JSON).
- `data/answer_key/` the 75 known-good test cases (PDF and JSON).
- `data/seeded_bad/` 33 flawed cases, each labelled with a defect and the decision the gate should reach (`insurance_seeded_bad.json`; `..._review.csv` is the same for Excel). The labels are provisional until the owner reviews them.
- Several sample expected results say "configured" without a value, or "blocked or referred" without saying which. The gate should treat these as weak and flag them.

## Working rules
- Run the Cloudflare security-audit skill (github.com/cloudflare/security-audit-skill) before publishing anything.
- Run `python -m pytest` before finishing any task. Do not break the 22 existing tests.
- Each work package owns its own files. Change `models.py` only in the contracts step.
- Update this file and the live blueprint doc when something lands.

## Setup
`python -m venv .venv`, activate, `pip install -e ".[dev]"`, `python -m pytest`.
