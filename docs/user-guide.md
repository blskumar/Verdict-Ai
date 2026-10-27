# Verdict AI — User Guide

> A gate for AI-written test cases: it rates each case and returns approve, revise, reject or human review, with a reason.

Verdict AI compares candidate test cases with requirements. Plain-code rules and (optionally) language models review each case. **Plain code makes the final decision**, never a model.

Last updated: 2026-10-26.

---

## Contents
1. [Install and set up](#1-install-and-set-up)
2. [Command line](#2-command-line)
3. [The web page](#3-the-web-page)
4. [Domains: what they do](#4-domains-what-they-do)
5. [Domain packs](#5-domain-packs)
6. [Checks and decisions](#6-checks-and-decisions)
7. [Reports and exports](#7-reports-and-exports)
8. [Security and privacy](#8-security-and-privacy)
9. [Testing](#9-testing)
10. [Known limits](#10-known-limits)

---

## 1. Install and set up

You need Python 3.11 or later. An OpenRouter key is optional (only for live models).

```bash
git clone https://github.com/blskumar/Verdict-Ai.git
cd Verdict-Ai
python -m venv .venv
.\.venv\Scripts\activate        # Windows (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"
python -m pytest -q
```

For live models, create a file named `.env` in the project folder with one line, `OPENROUTER_API_KEY=your_key`. The file is git-ignored. Never commit it or share it. Free OpenRouter endpoints may log prompts, so use invented data only.

---

## 2. Command line

The command is `verdict-ai`.

| Command | Use |
|---|---|
| `verdict-ai load -r <file> -c <file>` | Read requirements and/or cases (PDF, CSV, JSON, DOCX, XLSX) and print what was found. |
| `verdict-ai domains list` | Show the domain tree. |
| `verdict-ai domains show <path>` | Show the merged pack, for example `banking/retail/checking`. |
| `verdict-ai web [--port 8000]` | Start the web page. Run it from the project folder. |
| `verdict-ai evaluate [--set insurance\|banking\|all] [--domain <path>] [--mode mock\|live]` | One evaluator per domain family. Each has its own requirements, known-good cases and 33 labelled flawed cases (insurance: 25 requirements and 75 good cases; banking: 10 and 17). Prints per-sub-domain results and a summary by family; reports are saved as `eval_<set>_<time>.md/.json`. Exits with code 1 when a target is missed. |

`--mode mock` is offline and uses stand-in models that approve everything, so it shows what the plain-code rules alone achieve. `--mode live` uses the models in `models.yaml` (check them with `python scripts/check_models.py`). Give `--domain banking/retail/checking` to pick the banking set and use that pack as model context. A custom run needs all of `-r`, `-a` and `-b`. To add a domain, follow `docs/add-a-domain.md`.

---

## 3. The web page

Run `verdict-ai web` and open **http://127.0.0.1:8000**.

1. **Pick a domain** in the Domain Pack dropdown. Parent choices (for example `insurance`) accept everything below them.
2. **Load data:** *Load Sample Data* (data for the chosen domain), *Upload Files* (up to 10 files, 25 MB each; PDF, CSV, JSON, DOCX, XLSX) or *Paste*.
3. **Analyze** rates each requirement for testability, flags vague or ambiguous wording (including the domain's own terms) and fills the **Domain review** panel.
4. **Generate Tests** drafts candidate cases from the requirements.
5. **Run Verdict Gate** rates every case. Use the threshold slider (default 85%) and the engine choice: *Fast Engine* (mock, offline) or *Live LLM Router*.
6. Read the results table: filter by verdict, open a row to see its scores, models and reasons.
7. **Export** requirements review and cases as CSV, Markdown or JSON; results as JSON, CSV, Markdown or HTML.

The theme button cycles **System → Light → Dark**. System follows the device and is the default.

The page works on phones and tablets and with no internet (all scripts are served by the app).

### If you see "belong to … but the selected domain is …"
The data you loaded or uploaded looks like another domain than the one selected. Select the matching domain and load it again. See [section 4](#4-domains-what-they-do).

---

## 4. Domains: what they do

The selected domain is used in five ways.

1. **Guard.** Data that belongs to another domain is refused with a message naming the right one. The app decides from each item's `domain` field, then its ID prefix (`HOME-REQ-01` is homeowners), then key words in the text. The rule is: same domain, a parent or child, or the same branch. Term life fits whole life; checking does not fit personal loans.
2. **Vague and ambiguous wording.** The pack's `vague_terms` and `ambiguous_phrases` are checked in requirements and expected results, on top of the built-in lists.
3. **Domain review** (after Analyze):
   - *Possibly outdated:* text containing "deprecated", "superseded", "retired" and similar, the pack's `obsolete_terms`, and cases whose requirement is no longer in the set.
   - *Proposed requirements:* the pack's multi-word `risk_tags` that no requirement mentions. **Add** puts a draft into the list for you to edit.
   - *Scenarios still needed:* a pack `boundary_hint` that matches a requirement but no case of that requirement exercises.
   These are prompts for a person. They are keyword matches, not judgements.
4. **Context for the models.** In live mode each model receives the domain name, context, glossary, outdated terms, risk areas and boundary hints.
5. **Thresholds.** A pack can override the revise cutoff and the review band.

Content from a domain that has no pack (for example telecom) is not recognised as foreign.

---

## 5. Domain packs

Packs are YAML files in `src/verdict_ai/domains/`, inherited by folder path:

```
domains/insurance/_domain.yaml                      base insurance pack
domains/insurance/life/_domain.yaml                 life insurance
domains/insurance/life/term.yaml                    term life (a leaf)
```

A child pack adds to its parents: contexts are joined parent first, the glossary and `obsolete_terms` are merged with the child winning, lists are combined without duplicates, and thresholds are overridden by the child.

| Field | Meaning |
|---|---|
| `name`, `description`, `context` | What the domain is. Sent to the models. |
| `glossary` | Terms and meanings. |
| `obsolete_terms` | `outdated term: why or what replaced it`. Flagged when used. Packs ship with starter terms for retired or replaced standards (for example LIBOR, TLS 1.0, PCI DSS 3.2.1, 2001 CSO, AOBRD). Review them and add yours. Matching is a lowercase substring, so use specific phrases. |
| `risk_tags` | Risk areas. Multi-word tags (`overdraft_fee`) drive proposed requirements. |
| `boundary_hints` | Limits worth testing. Drive "scenarios still needed". |
| `vague_terms`, `ambiguous_phrases` | Wording to flag. |
| `id_prefixes` | ID prefixes that belong to this domain (used by the guard). |
| `source_markers` | Extra phrases that count as naming a source for a figure. |
| `thresholds` | `revise_below`, `human_review_band`, `similarity`. |

Add a domain by adding a folder and a YAML file, then an evaluation set: see `docs/add-a-domain.md` (checklist) and `docs/domain-pack-template.yaml` (template). Use invented data only.

---

## 6. Checks and decisions

### Rule checks (plain code)
| Code | Meaning | Decision |
|---|---|---|
| `untraceable` | The case's requirement does not exist. | reject |
| `near_duplicate` | Almost the same scenario and result as another case of the same requirement. | reject |
| `missing_expected` | No expected result. | revise |
| `vague_expected` | "Behaves correctly", "works fine", or a domain vague term, with no concrete figure. | revise |
| `compound_case` | Several actions bundled into one case. | revise |
| `uncited_value` | A figure in the expected result is not in the scenario or requirement and no source is named (`source_ref`, or words such as "rate table"). | revise |
| `ambiguous_outcome` | "Blocked or referred" without saying which, unless it says "per" or "according to" a configured rule. | human_review |
| `no_cases`, `coverage_gap` | A requirement has no cases, or a high-risk one has no negative or boundary case. | human_review |

The rules check that a source is **named**, not that the source says what the case claims.

### Model roles (live mode)
Analyst (requirement testability), generator (drafts cases), reviewer (coverage and correctness), checker (invented values, traceability), arbiter (only when reviewer and checker disagree). Models run through `llm.py` with ordered failover per role. If every model for a role is used up, progress is saved and the cases come back as `human_review`.

**A reply that cannot be used** (empty, not JSON, a score outside 0 to 1, a wrong case id) makes the case "unjudged": a failed check with score 0.72, which goes to human review. A case is never approved because a model failed.

### Decisions
The gate (`gate.py`) picks the strictest result: `reject > revise > human_review > approve`. A case with no triggers is judged on its mean score against the thresholds in `gate.yaml`:

```yaml
thresholds:
  approve: 0.85          # at or above: approve
  human_review_min: 0.70 # from here up to approve: human review
  revise_max: 0.70       # below: revise
```
Keep the revise cutoff at or below 0.72, or unjudged cases stop reaching human review. Every decision has a reason, including approvals.

### What is measured today
Offline, the rules alone catch 14 of 33 seeded bad cases (42.4%) in each family and hard-reject none of the good ones. Live run of 27 Oct 2026 (free-tier models): insurance caught 30/33 and did not approve 7/75 good cases; banking caught 30/33 and did not approve 1/17; no good case was hard-rejected. All three targets are met. The one defect type still missed is `near_duplicate` (0/3 in each family).

---

## 7. Reports and exports

- **Markdown, HTML, JSON, CSV** for gate results. CSV cells starting with `=`, `+`, `-` or `@` get a leading apostrophe so a spreadsheet does not run them.
- **Cases CSV/JSON** grouped by requirement, with a `source_ref` column.
- **Requirements review** (CSV, Markdown, HTML, JSON): quality score, issues and suggestions.
- The export endpoint returns **422** if a result has no `case_id` or an unknown decision.
- `verdict-ai evaluate --output-dir runs/` saves the benchmark report (JSON and Markdown). A mock report says so at the top.

---

## 8. Security and privacy

- Works on local files only; no tracker or ALM connection.
- The server listens on `127.0.0.1` by default. Do not expose it to a network without adding sign-in first: there is none.
- Responses carry `Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and `Permissions-Policy`. The CSP still allows inline scripts.
- Page scripts (Tailwind 3.4.17, MIT; Lucide 1.61.0, ISC) are served from `static/vendor/`. Fonts are loaded from Google Fonts; if blocked, the page falls back to system fonts.
- Text from uploaded files is escaped before display.
- Upload names are reduced to a base name; files are processed in a temporary folder. Limits: 10 files, 25 MB each. JSON request bodies have no cap yet.
- The resume file for an evaluation must be a plain `name.json` and is stored under `runs/resume/`.
- Model replies are cached on disk (`.cache/`, git-ignored) only after they validate. The cache holds prompts: delete it if it contains anything sensitive.
- The API key comes only from the environment or `.env`, which is git-ignored.
- `pillow` and `certifi` have minimum versions because older ones have published advisories. Re-scan with `python scripts/perf_and_security_probe.py`.
- The Cloudflare security-audit skill has not been run yet. Run it before publishing a package.

---

## 9. Testing

```bash
python -m pytest -q                          # about 700 tests, offline
python scripts/cross_browser_e2e.py          # 17 browser/device profiles x 18 checks
python scripts/perf_and_security_probe.py    # latency, load, headers, dependency scan
```

- Tests never reach the network: a guard in `tests/conftest.py` fails any real HTTP call.
- `tests/test_exhaustive_e2e.py` uses every sample file, generated data for every sub-domain, hostile input, exports, the CLI and security checks. Known gaps are marked as strict expected failures with the reason, so fixing one makes the test fail until the marker is removed.
- The browser script runs Chromium, installed Chrome and Edge, Firefox, WebKit (stands in for Safari), iPhone, iPad, Pixel and Galaxy profiles. Device profiles are emulations, not physical devices. Screenshots go to `runs/compat/`.
- Measured on one laptop in mock mode: 108 cases in about 50 ms; 5,000 cases in about 6 s; about 5 to 6 requests a second in total, however many users.

---

## 10. Known limits

- Near-duplicate cases are not caught (0/3 in each family). Offline, without models, the rules alone catch 42.4% of the seeded bad cases. Banking has only 17 good cases, so its false-flag rate is a small sample.
- Domain review and the guard use keywords. They can miss things and can suggest things that are not needed.
- The starter outdated terms are a general list from public standards, not your company's; review and extend them.
- Content from a domain with no pack is accepted under any selected domain.
- One request is handled at a time; very large runs (20,000 cases) take about two minutes.
- Tested on emulated devices, not physical phones or a real Safari.
