# Verdict AI: plan to finish

Last updated: 2026-10-26. Read `CLAUDE.md` first (decisions, state, open items).

## Where things stand

| Package | What | Status |
|---|---|---|
| Step 0 | Rule checks, shared result types | **Done.** Includes `uncited_value`, pack vague terms and ambiguous phrases, no-cases and coverage-gap checks. |
| A | Model router with failover (`llm.py`, `models.yaml`) | **Done.** Offline tests simulate 401, 402, 429, 5xx, timeout, bad JSON; replies are cached only after validation. |
| B | Role agents and orchestrator | **Done**, with resume and "unjudged" handling. Not yet run on live models. |
| C | Gate engine and reports | **Done.** Every decision carries a reason. |
| D | Web view | **Done.** Domain guard and domain review added. System/light/dark theme, works offline, tested on 17 browser and device profiles. |
| E | Evaluation and model bake-off | **One evaluator per family** (insurance, banking), each with per-sub-domain results. Offline floor: 14/33 caught in each, 0 hard rejects. **Live run 2 (27 Oct): all targets met in both families** (insurance 30/33 caught, 7/75 good not approved; banking 30/33, 1/17). Open: `near_duplicate` 0/3. |
| F | Guide, security, release | **Partly done.** Guide and README updated; hardening done; the Cloudflare audit and PyPI check are still to do. |

## What is next, in order

1. **Live bake-off (package E).** Needs you:
   - Create a key at openrouter.ai and decide on the $10 top-up (the free tier allows about 50 requests a day, too few for 108 cases across several roles).
   - Put `OPENROUTER_API_KEY=...` in `C:\Verdict-Ai\.env`. Do not paste it in chat.
   - Run `verdict-ai evaluate --mode live` (both families; add `--set banking` for one). Free endpoints may log prompts: the data is invented, which is fine.
   - **Done (27 Oct 2026):** both families meet the targets with the current `models.yaml` chains. Rerun after any prompt, model or threshold change.
2. **Remaining weak spot:** `near_duplicate` (0/3 in both families). Otherwise, if a target is missed, tune prompts first, then thresholds. The six defect types the rules cannot see are: contradicts requirement, hallucinated feature, wrong boundary outcome, invented value, near-duplicate, out of scope. Near-duplicates probably need an embedding or model check; word overlap cannot separate them.
3. **Review `obsolete_terms`.** Starter lists are in the banking, insurance, checking, savings, personal-loans, life and commercial-auto packs. Add your company's own outdated terms.
4. **Close the known gaps** (see `CLAUDE.md`, open items): foreign-domain detection, empty requirement, JSON body size cap, inline-script CSP, a `source_ref` input box.
5. **Release (package F).** Run the Cloudflare security-audit skill and fix or accept findings in writing. Check `verdictai-gate` is free on PyPI. Build, then test a fresh install in a new venv.

## Commands

```bash
python -m pytest -q                          # about 700 tests, offline
python scripts/cross_browser_e2e.py          # browsers and devices
python scripts/perf_and_security_probe.py    # performance, headers, dependency scan
verdict-ai evaluate --mode mock              # offline floor (exit 1 while the target is missed)
verdict-ai evaluate --mode live              # real models
verdict-ai web                               # http://127.0.0.1:8000
```

## Rules for working on it
- Each package owns its own files; `models.py` changes only for a recorded decision.
- Run pytest before finishing. Update `CLAUDE.md`, this file and the README when something lands.
- Never commit `.env`. The key stays local.
