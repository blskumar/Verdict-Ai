# Add a domain: checklist

Verdict AI has **one engine** and **one evaluator per domain family** (today `insurance` and `banking`). A new family needs its own pack and its own evaluation set, so its quality is measured on its own and not hidden in a combined number. `tests/test_eval_sets.py` fails until every item marked **(tested)** is done.

Use only invented data. The pack text goes to the models on every call, and free model endpoints may log prompts.

## A. New sub-domain inside an existing family (for example `insurance/health`)

1. **Pack.** Copy `docs/domain-pack-template.yaml` to `src/verdict_ai/domains/insurance/health.yaml` and fill it in. If it has its own sub-areas, make a folder with a `_domain.yaml` instead. **(tested: the template is valid)**
2. **ID prefix.** Put the prefix in `id_prefixes` (for example `HLTH`). The domain guard uses it to refuse data from other domains. Pick one no other pack uses.
3. **Requirements.** Add at least 4 requirements to the family's requirements file (`data/requirements/<family>_requirements.json`) with `"domain": "insurance/health"` and ids like `HLTH-REQ-01`. Each needs a title, text and acceptance criteria that state concrete limits. **(tested: every requirement has a domain that has a pack)**
4. **Good cases.** Add at least 3 cases per requirement to the family's answer key. Include positive, negative and boundary cases. Every figure must trace to the requirement or name a source (`source_ref`). **(tested: they point at real requirements)**
5. **Bad cases.** Add flawed cases to the family's seeded-bad file, one per defect type you want measured, each with `defect`, `defect_note`, `base_id` (the good case it was bent from, if any) and `expected_decision`. Keep the labels the same as the other sets. **(tested: same defect types and labels in every family)**
6. **Check.** Run `python -m pytest tests/test_eval_sets.py`, then `verdict-ai evaluate --set insurance` (mock) and read the "Results by sub-domain" table for the new sub-domain. A sub-domain with no cases shows nothing: add some.
7. **Live run.** `verdict-ai evaluate --set insurance --mode live`. Look at good cases that were not approved: they show whether the pack context confuses the models.
8. **Record it.** Add a line to the decisions log in `CLAUDE.md` with the date and the result.

## B. New family (for example `telecom`)

Do A for the first sub-domain, plus:

1. **Folders.** Create `src/verdict_ai/domains/telecom/_domain.yaml` (the family pack: shared context, glossary, vague terms, thresholds). **(tested: every family folder has an evaluation set)**
2. **Evaluation set.** Create the three data files for the family:
   - `data/requirements/telecom_requirements.json`
   - `data/answer_key/telecom_answer_key.json`
   - `data/seeded_bad/telecom_seeded_bad.json` (at least 30 cases, the same 12 defect types as the others) **(tested)**
3. **Register it.** Add an entry to `EVAL_SETS` in `src/verdict_ai/evaluate.py` with the three paths and the family pack name. `verdict-ai evaluate` then runs it with the others and shows it in the summary table. **(tested)**
4. **Sample data for the page.** `Load Sample Data` picks files by family in `web/app.py` (`get_sample_data`): add the family there.
5. **Guard vocabulary.** Add a few words that mark the family to `_FAMILY_HINTS` in `src/verdict_ai/domains.py`, so unlabelled text from it is recognised.
6. **Browser test.** Add the new domain to the checks in `scripts/cross_browser_e2e.py` if it needs different data.
7. **Docs.** Add the family to the README, the user guide and `CLAUDE.md`.

## What to check in the first live result

- Catch rate by defect type: a type at 0% in one family and fine in another points at the pack or the data, not the engine.
- Good cases not approved, by sub-domain: if one sub-domain is much worse than the rest, read its reasons. Last time the cause was a wrong domain context, not the models.
- Hard rejects among the good cases should be 0.
- A domain with fewer than 15 good cases gives a noisy false-flag rate. Add cases before trusting it.
