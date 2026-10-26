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
| E | Evaluation and model bake-off | **Mock only.** Offline floor: 14/33 caught (42.4%), 0/75 wrongly rejected. **The live bake-off is not done.** |
| F | Guide, security, release | **Partly done.** Guide and README updated; hardening done; the Cloudflare audit and PyPI check are still to do. |

## What is next, in order

1. **Live bake-off (package E).** Needs you:
   - Create a key at openrouter.ai and decide on the $10 top-up (the free tier allows about 50 requests a day, too few for 108 cases across several roles).
   - Put `OPENROUTER_API_KEY=...` in `C:\Verdict-Ai\.env`. Do not paste it in chat.
   - Run `verdict-ai evaluate --mode live`. Free endpoints may log prompts: the data is invented, which is fine.
   - Done when the report shows at least 80% caught, under 15% wrongly rejected, a reason on every decision, and `models.yaml` holds the winning order.
2. **If the target is missed,** tune prompts first, then thresholds. The six defect types the rules cannot see are: contradicts requirement, hallucinated feature, wrong boundary outcome, invented value, near-duplicate, out of scope. Near-duplicates probably need an embedding or model check; word overlap cannot separate them.
3. **Fill `obsolete_terms`** in each domain pack with real outdated terms (you know which). Until then "possibly outdated" only reacts to words like "deprecated".
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
