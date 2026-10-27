# Verdict AI: a test case gate

Know which AI-written tests to trust. A Python tool that reads requirements, drafts or reads test cases, rates each case, and returns approve / revise / reject / human_review with a reason. The model reasons; plain code decides.

Last updated: 2026-10-26. Keep this file current as work lands.

## Names
- Brand: Verdict AI. PyPI package: `verdictai-gate`. Command: `verdict-ai`. Python import: `verdict_ai`.
- Project folder: `C:\Verdict-Ai`. GitHub: `blskumar/Verdict-Ai`.

## Decisions already made
- Inputs are files or pasted text: PDF, CSV, JSON, DOCX and XLSX. No ALM, Jira or tracker integration for now. (The loader reads DOCX and XLSX; this replaces the older "PDF, CSV or JSON only".)
- Domains: insurance (`p-and-c/homeowners`, `p-and-c/commercial-auto`, `life/term`, `life/whole-life`, `life/common`) and banking (`retail/checking`, `retail/savings`, `lending/personal-loans`). Personal Auto is out of scope. Packs live in `src/verdict_ai/domains/` (`_domain.yaml` per folder, `<leaf>.yaml` for leaves), inherited by path: context joined parent first; glossary and `obsolete_terms` child wins; lists combined and de-duplicated; thresholds child overrides.
- Pack fields: `name, description, context, glossary, obsolete_terms, risk_tags, boundary_hints, vague_terms, ambiguous_phrases, id_prefixes, source_markers, thresholds`. Do not put real customer data in packs: the context goes to the models on every call.
- Starting thresholds (tune only after a live evaluation run): revise below 0.70, human-review band 0.70 to 0.85, similarity 0.85.
- Success criteria: catch at least 80% of seeded bad cases, wrongly reject under 15% of good cases, a reason on every decision.
- Reports: Markdown, HTML and JSON, plus a CSV export of decisions.
- Web view: FastAPI plus one HTML page. Theme is System / Light / Dark: follows the device by default, saved choice applied in `<head>` before first paint. Domain dropdown built from the domain tree.
- The owner has delegated decisions ("choose what is best"). Record each decision here.

### Decisions log
- 2026-10-24: contracts reopened to add `TestCase.source_ref` in `models.py` (a citation for a figure in an expected result).
- 2026-10-24: an unusable model reply is "unjudged": `passed=False`, score 0.72, with a reason, never neutral and never clamped. It reaches `human_review` only while the revise cutoff is at most 0.72. Keep that true if thresholds are tuned, or change the convention.
- 2026-10-24: a figure in an expected result must be traceable to the scenario, requirement or a sum/difference/product/ratio of two of its numbers, or be cited: otherwise `uncited_value` (decision `revise`). It checks that a source is named, not that the source says that.
- 2026-10-24: domain context sent to models comes from `domains.context_text(pack)`. Packs may add `source_markers`.
- 2026-10-25: `/api/export/{fmt}` returns 422 for a missing `case_id` or an unknown decision (no invented ids).
- 2026-10-25: a model reply is cached only after it validates (`validate` argument of `ModelRouter.chat`).
- 2026-10-26: **domain guard.** Data that belongs to another domain than the selected one is refused (HTTP 422 with a message naming the right domain) on upload, analyze, generate and evaluate, and the page checks on dropdown change (`POST /api/domain/check`). Match rule: same domain, a parent, a child, or the same branch (first two path segments). So term and whole-life fit together; checking and personal-loans do not. Evidence order: item `domain`, ID prefix, key words. Content from a domain with no pack (telecom, healthcare) is not detected.
- 2026-10-26: **domain review** (`domain_review.py`): possibly outdated items (generic markers such as "deprecated", pack `obsolete_terms`, cases whose requirement is gone), proposed requirements (multi-word `risk_tags` no requirement mentions), scenarios still needed (`boundary_hints` matching a requirement but no case). It proposes; a person decides. Starter `obsolete_terms` are in the banking base, checking, savings, personal-loans, insurance base, life base and commercial-auto packs (see 2026-10-27); the owner should review them.
- 2026-10-26: **the offline benchmark is label-free.** The evaluation mock used to read the seeded `defect` labels (reported 33/33, circular). It now approves everything, so offline numbers are the rule-checks floor: 14/33 caught (42.4%), 0/75 wrongly rejected. `verdict-ai evaluate` exits 1 when a target is missed.
- 2026-10-26: security hardening: response headers and CSP; Tailwind 3.4.17 and Lucide 1.61.0 are served from `static/vendor/` (the Tailwind CDN sends no CORS headers, so a CDN script with an integrity hash does not load); requirement and toast text is escaped in the page; `resume_path` must be a plain `name.json`, stored under `runs/resume/`; `pillow>=13.0.0` and `certifi>=2026.10.23` floors.
- 2026-10-26: every decision carries a reason, including approvals and score-floor decisions.
- 2026-10-27: **first live bake-off** (nemotron-3-ultra, laguna-s-2.1, nemotron-3.5-lightning; 108 cases): catch rate 93.9% (31/33, only `near_duplicate` low at 1/3), but 31 of 75 good cases were not approved (14 revise, 17 human_review, 0 reject). Cause found: the CLI gave every case a Term Life context, and good cases outside Term Life were flagged (TERM 0/15, CA 13/15, WL 8/15, LIFE 6/15, HOME 4/15); the reviewer prompt also scored a case down for not covering the whole requirement. Fixed: `evaluate` defaults to domain `insurance`; reviewer prompt now judges each case on its own. Not yet rerun.
- 2026-10-27: **one evaluator per domain family.** `EVAL_SETS` in `evaluate.py` maps `insurance` and `banking` to their own requirements, answer key, seeded-bad file and pack. `verdict-ai evaluate` runs every set (or `--set`, or the family of `--domain`), prints a summary by family, saves `eval_<set>_<time>`, and each report has a table by sub-domain. The engine (rules, router, gate, report) stays shared; per-domain model chains only if a bake-off shows they help. New domains follow `docs/add-a-domain.md`; `tests/test_eval_sets.py` enforces it (a family folder without an evaluation set fails).
- 2026-10-27: banking seeded-bad set `data/seeded_bad/banking_seeded_bad.json`: 33 invented cases, the same 12 defect types and decision labels as insurance (labels provisional until the owner reviews them). Banking now has 10 requirements, 17 good cases, 33 bad cases; 17 good cases is a small sample for the false-flag rate.
- 2026-10-27: rules: `appropriately` and `calculated/handled/processed/computed fine` count as vague.
- 2026-10-27: the report says how many good cases were hard-rejected versus merely not approved. "False Reject Rate" still counts every good case that is not approved (revise, human_review or reject); hard rejects are shown beside it.
- 2026-10-27: **router fix.** `ModelRouter.chat()` used `validate` only for caching, so an empty or invalid reply from the first model was returned with no failover (9 of 10 flagged banking good cases were empty arbiter replies). Now an unusable reply moves to the next model, two in a row cool a model down, and if every model fails the last reply is returned so the caller marks the case unjudged. An HTTP 200 without `choices` (provider error body) is a failover error, not a crash. The arbiter prompt now judges each case on its own, like the reviewer.
- 2026-10-27: **second live bake-off, all targets met in both families** (nemotron-3-ultra, nemotron-3.5-lightning, laguna-s-2.1): insurance 30/33 caught (90.9%), 7/75 good not approved (9.3%), 0 hard rejects; banking 30/33 caught (90.9%), 1/17 good not approved (5.9%), 0 hard rejects. Only `near_duplicate` is missed (0/3 in each); every other defect type is caught 100%. Reports are in `runs/`. Thresholds were not changed.
- 2026-10-27: Starter `obsolete_terms` added 2026-10-27 from public standards: retired or replaced technology (TLS 1.0/1.1, SHA-1, 3DES, PCI DSS 3.x, 3-D Secure 1.0, SMS-only codes), financial rules (LIBOR, the $100,000 deposit limit, SWIFT MT103, the repealed CFPB $5 overdraft rule, the Reg D six-withdrawal limit, the incurred-loss model) and insurance standards (ICD-9, X12 4010, 2001/1980 CSO, 2001 VBT, AOBRD and paper logs). Matching is a lowercase substring, so terms are specific phrases. The owner should review them and add company-specific ones.

## Models: OpenRouter, free tier first
- Provider: OpenRouter (OpenAI-compatible API). Key in a local `.env` as `OPENROUTER_API_KEY`. Never commit it, never paste it in chat.
- Each role has an ordered list of models in `models.yaml`. The router moves to the next model on: HTTP 429, 402, 5xx, timeout, model unavailable, or two invalid JSON replies in a row. A failed model gets a cool-down. A 401 stops live calls and is reported.
- Record which model produced every decision and show it in the report.
- If every model for a role is used up: save progress, stop, allow resume. Unjudged cases come back as `human_review`. Never guess.
- Batch several cases per call, cache replies on disk (keyed by model + prompt hash, validated first), count requests against a cap.
- OpenRouter free limits at last check: 20 requests a minute, 50 a day; 1,000 a day after a $10 top-up. Read the live limit from `GET /api/v1/key` when possible.
- Free endpoints may log prompts. Use only sanitised or invented data.
- Do not rely on structured-output support; ask for JSON, validate it in code, retry once.

## Runtime roles and model chains (checked 2026-10-27; the bake-off decides)
The chains live in `src/verdict_ai/models.yaml` (copy to `models.yaml` in the project folder to override). Free models first, then one cheap paid model as a last resort so a run does not stop when free endpoints are busy. Model ids change often: run `python scripts/check_models.py` (add `--ping` to send one tiny request to each; it uses the key and quota).

| Role | Job | Chain (first to last) |
|---|---|---|
| Orchestrator | Plans the run, batches, routes, tracks budget, handles failover. Plain code. | none |
| Requirement analyst | Flags vague or untestable requirements, extracts stated rules and values | nvidia/nemotron-3.5-lightning:free, google/gemma-4-26b-a4b-it:free, qwen/qwen3.7-flash (paid) |
| Test case generator | Drafts test cases | google/gemma-4-31b-it:free, nvidia/nemotron-3.5-lightning:free, mistralai/mistral-nemo (paid) |
| Scenario reviewer | Checks each case vs its requirement: coverage, correctness, testability | nvidia/nemotron-3-ultra-550b-a55b:free, poolside/laguna-s-2.1:free, openai/gpt-oss-120b (paid) |
| Hallucination and traceability checker | Invented fields or values, cases with no requirement | poolside/laguna-s-2.1:free, nvidia/nemotron-3.5-lightning:free, openai/gpt-oss-20b (paid) |
| Arbiter | Only when reviewers disagree; unresolved goes to human_review | nvidia/nemotron-3-ultra-550b-a55b:free, poolside/laguna-s-2.1:free, openai/gpt-oss-120b (paid) |

Notes from the 2026-10-27 check: the old first choice `minimax/minimax-m3.1-flash:free` no longer exists on OpenRouter; `thinkingmachines/inkling:free` returns 403 (agentic use only); Google's and Poolside's free endpoints often return 429, so the paid last resort matters. Reasoning models need room to think: a small `max_tokens` gives an empty reply.

Use different model families for the generator and the judging roles where possible. The final decision is always made in code (`gate.py`), never by a model.

## State of the work (2026-10-26)
Done: data contracts, loaders (PDF, CSV, JSON, DOCX, XLSX), domain packs, rule checks, model router with failover, five role agents, orchestrator with resume, gate engine, reports, CLI (`load`, `domains`, `web`, `evaluate`), web page, domain guard and review. About 700 tests pass offline (`python -m pytest`); 5 known gaps are strict expected failures in `tests/test_exhaustive_e2e.py`.

**All three targets met in the second live run in both families** (insurance 30/33 caught (90.9%), 7/75 good not approved (9.3%), 0 hard rejects; banking 30/33 caught (90.9%), 1/17 good not approved (5.9%), 0 hard rejects). Offline, rules alone catch 14 of 33 seeded bad cases. Not caught without models: `contradicts_requirement` 0/4, `hallucinated_feature` 0/4, `wrong_boundary_outcome` 0/3, `invented_value` 0/3, `near_duplicate` 0/3 (paraphrases score 0.46 to 0.68 on word overlap while good cases reach 0.63), `out_of_scope` 0/2. Next: `near_duplicate` is 0/3 in both families and needs a similarity check in code (word overlap cannot separate paraphrases from good cases); look at the 5 `life/common` good cases still not approved; the arbiter is called on about a third of cases. Keep the thresholds as they are until a bigger good-case sample exists (banking has 17).

## Open items
1. Read the per-case reasons for the remaining good cases not approved (`life/common` 5/15, `homeowners` 2/15, banking savings 1/1), and add a code check for `near_duplicate` (0/3 in both sets).
2. Review the starter `obsolete_terms` and add company-specific outdated terms (homeowners has none of its own).
3. Known gaps (strict xfail in `tests/test_exhaustive_e2e.py`): content from a domain with no pack is not detected as foreign; an empty requirement object is analysed; the 80% catch rate offline.
4. No size cap on JSON request bodies; one request is handled at a time (about 5 to 6 per second).
5. The CSP still needs `'unsafe-inline'` for scripts (the page has inline code).
6. Calculation cases in the sample answer key state the figure in the scenario and do not cite a table; consider adding `source_ref`.
7. Run the Cloudflare security-audit skill before publishing. Check `verdictai-gate` is free on PyPI.
8. `source_ref` is accepted by `/api/evaluate`, shown on case cards and exported; there is no input box for it on the page.

## Data
- `data/requirements/` sample requirements (insurance PDF and JSON; banking CSV and JSON).
- `data/answer_key/` known-good cases (75 insurance in JSON and PDF; 17 banking).
- `data/seeded_bad/` 33 flawed cases per family (`insurance_seeded_bad.json`, `banking_seeded_bad.json`; a CSV for Excel for insurance), each labelled with a defect and the decision the gate should reach. Labels are provisional until the owner reviews them.
- Root sample files: a Personal Auto P&C sample (`Sample_PnC_*`, out of scope, ignored by git), an expanded insurance pack, a banking sample (DOCX, PDF, XLSX) and a 39-requirement banking catalogue (`banking_requirements_core_catalogue.*`). The loader reads them; the catalogue's ID families (GOV, CUS, DEP, ...) match no pack.
- Several sample expected results say "configured" without a value, or "blocked or referred" without saying which. The gate treats these as weak, unless the text says "per" or "according to" a configured rule.

## Working rules
- Run `python -m pytest` before finishing any task.
- Run the Cloudflare security-audit skill (github.com/cloudflare/security-audit-skill) before publishing anything.
- Each work package owns its own files. `models.py` is changed only for a recorded decision.
- Do not rewrite whole files to change line endings. `web/app.py`, `evaluate.py` and `tests/test_agents.py` use Windows line endings; edit lines in place.
- Never put a backslash escape through a shell heredoc into a Python or regex edit (a `\b` became a backspace character twice). Use the editor tools.
- Test the page in more than Chromium: `python scripts/cross_browser_e2e.py`. A CDN script with an integrity hash will not load if the CDN sends no CORS headers.
- Update this file, `PLAN.md` and the README when something lands.

## Setup
`python -m venv .venv`, activate, `pip install -e ".[dev]"`, `python -m pytest`. Start the page with `verdict-ai web` from the project folder.
