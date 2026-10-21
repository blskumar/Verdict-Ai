# Verdict AI: plan to finish

Last updated: 2026-10-13. Read `CLAUDE.md` first.

## When you get home (about 15 minutes)
1. Open the Claude desktop app on the home computer and link this chat to it (so I can write to `C:\Verdict-Ai`).
2. Put these two files (`CLAUDE.md`, `PLAN.md`) in `C:\Verdict-Ai`. If a `CLAUDE.md` is already there, keep any lines Claude Code added and replace the rest.
3. Create `C:\Verdict-Ai\.env` with `OPENROUTER_API_KEY=...` (get the key at openrouter.ai; decide on the $10 top-up, recommended). Check `.env` is in `.gitignore`.
4. In the folder: `python -m venv .venv`, activate it, `pip install -e ".[dev]"`, `python -m pytest`. Expect the 22 original tests to pass, plus any rules tests.
5. Start Claude Code in the folder and paste the prompt at the bottom of this file.

## Work packages, in order
Packages A, B and C can run in parallel once step 0 is done. Each package owns only the files named.

### Step 0: finish Phase 2 and freeze the shared shapes
- Files: `src/verdict_ai/rules.py`, `tests/test_rules.py`, `src/verdict_ai/models.py` (add only here).
- Do: finish the rule checks (requirement with no cases, duplicate cases, missing expected result, vague words from the pack, ambiguous phrases such as "blocked or referred", coverage gaps, untraceable requirement IDs). Add the shared result types: `CheckResult` (check name, case id, pass/fail, score 0 to 1, reason, source: rule or model, model name), `GateResult` (case id, decision, reasons, scores, models used).
- Done when: rules run over the 75 good and 33 seeded cases without errors, results are the frozen types, tests pass.

### A: model router with failover
- Files: `src/verdict_ai/llm.py`, `models.yaml`, `tests/test_llm.py`.
- Do: OpenRouter client (chat completions, bearer key from `.env`), ordered model list per role, failover rules and cool-down from `CLAUDE.md`, JSON validate and one retry, disk cache, request cap, resume file, mock model for tests.
- Done when: tests simulate 429, 402, 5xx, timeout, unavailable model and bad JSON and show the router moving to the next model, recording the model used, and stopping cleanly with progress saved when all are exhausted. No real network calls in tests.

### B: role agents and orchestrator
- Files: `src/verdict_ai/agents/` (analyst, generator, reviewer, checker, arbiter), `orchestrator.py`, `tests/test_agents.py`.
- Do: one prompt and one JSON schema per role, domain pack context injected, batching, disagreement handling, orchestrator that runs the roles through the router. Works end to end on the mock model.
- Done when: a run over the answer key and seeded cases on the mock model produces `CheckResult`s for every case.

### C: gate engine and reports
- Files: `src/verdict_ai/gate.py`, `report.py`, `gate.yaml`, `tests/test_gate.py`.
- Do: combine rule and model results, apply thresholds (pack thresholds override defaults, CLI flag overrides all), decide in code, a reason for every decision. Reports: Markdown, HTML, JSON, CSV. Add `verdict-ai run`, `evaluate` commands.
- Done when: every case gets a decision and a reason, thresholds are changeable without code edits, reports open and read cleanly.

### D: web view
- Files: `src/verdict_ai/web/` (FastAPI app, one HTML page), `tests/test_web.py`.
- Do: domain dropdowns from the domain tree, upload requirements and cases, run, show results, light / dark / system toggle with the saved choice applied before first paint, decision badges with icon and label (not colour alone), logo swap by theme, WCAG AA contrast checks, screenshots in both themes.
- Done when: the page works with mock results in both themes and the contrast checks pass.

### E: evaluation and model bake-off (needs A, B, C and the API key)
- Files: `src/verdict_ai/evaluate.py`, `tests/test_evaluate.py`, `runs/` output.
- Do: run the gate over `data/answer_key` (should approve) and `data/seeded_bad` (should reach the labelled decision). Report: catch rate, wrongly-rejected rate, per-defect results, per-model results. Compare candidate models per role and pick the best.
- Done when: the report shows the numbers against the targets (at least 80% caught, under 15% wrongly rejected, a reason on every decision), and `models.yaml` holds the winning order. If targets are missed, tune thresholds and prompts, then re-run.

### F: guide, security, release
- Files: `docs/user-guide.md`, `README.md`, packaging.
- Do: user guide (install, run, read a report, add a domain pack, add a model), run the Cloudflare security-audit skill, fix findings, check `verdictai-gate` is still free on PyPI before publishing.
- Done when: audit findings are fixed or accepted in writing, and a fresh install works from the built package.

## Paste this into Claude Code
Read CLAUDE.md and PLAN.md. Check the current state of src/verdict_ai/rules.py and tests, run pytest, and tell me what is done. Then finish Step 0. After that, work packages A, B and C in parallel using subagents in separate git worktrees, each owning only its listed files. Use the mock model until I add OPENROUTER_API_KEY to .env. Update CLAUDE.md when each package lands, and give me a short summary at the end.
