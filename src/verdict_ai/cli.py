"""Command line. Phase 1 commands: load, domains list, domains show."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from . import domains as dom
from .loader import LoadError, load_cases, load_requirements

app = typer.Typer(help="Verdict AI: a test case gate.", no_args_is_help=True)
domains_app = typer.Typer(help="Browse domain packs.", no_args_is_help=True)
app.add_typer(domains_app, name="domains")


def _root(domains_dir: Path | None) -> Path | None:
    return domains_dir


@domains_app.command("list")
def domains_list(domains_dir: Path = typer.Option(None, "--domains-dir")) -> None:
    """Print the domain tree."""
    def show(nodes: list[dict], depth: int = 0) -> None:
        for n in nodes:
            typer.echo(f"{'  ' * depth}{n['path']}  ({n['name']})")
            show(n["children"], depth + 1)
    try:
        show(dom.tree(_root(domains_dir)))
    except dom.DomainError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1)


@domains_app.command("show")
def domains_show(path: str, domains_dir: Path = typer.Option(None, "--domains-dir")) -> None:
    """Print the merged pack for a path such as insurance/life/term."""
    try:
        typer.echo(json.dumps(dom.load_pack(path, _root(domains_dir)).model_dump(), indent=2))
    except dom.DomainError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1)


@app.command()
def load(
    requirements: Path = typer.Option(None, "--requirements", "-r", help="PDF, CSV or JSON of requirements"),
    cases: Path = typer.Option(None, "--cases", "-c", help="PDF, CSV or JSON of test cases"),
    domain: str = typer.Option(None, "--domain", "-d", help="Domain path, e.g. insurance/life/term"),
    domains_dir: Path = typer.Option(None, "--domains-dir"),
) -> None:
    """Read requirements and/or cases from a file and print what was found."""
    if not requirements and not cases:
        typer.secho("Give --requirements and/or --cases.", fg="red", err=True)
        raise typer.Exit(2)
    try:
        if domain:
            dom.load_pack(domain, _root(domains_dir))  # fail early on a bad path
        if requirements:
            reqs = load_requirements(requirements)
            typer.echo(f"{len(reqs)} requirements")
            for r in reqs:
                typer.echo(f"  {r.id}  {r.title}")
        if cases:
            tcs = load_cases(cases)
            typer.echo(f"{len(tcs)} test cases")
            by_req: dict[str, int] = {}
            for c in tcs:
                by_req[c.requirement_id] = by_req.get(c.requirement_id, 0) + 1
            for rid, n in by_req.items():
                typer.echo(f"  {rid}: {n}")
    except (LoadError, dom.DomainError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1)


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Bind host"),
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload for development"),
) -> None:
    """Launch the Verdict AI web interface."""
    try:
        import uvicorn
        typer.secho(f"Starting Verdict AI WebUI at http://{host}:{port}", fg="green")
        uvicorn.run("verdict_ai.web.app:app", host=host, port=port, reload=reload)
    except ImportError:
        typer.secho("uvicorn or fastapi not installed. Run: pip install verdictai-gate", fg="red", err=True)
        raise typer.Exit(1)


@app.command()
def evaluate(
    eval_set: str = typer.Option("all", "--set", "-s", help="Evaluation set: insurance, banking or all. Each has its own data, pack and report."),
    requirements: Path = typer.Option(None, "--requirements", "-r", help="Custom run: requirements file (give all of -r, -a, -b)"),
    answer_key: Path = typer.Option(None, "--answer-key", "-a", help="Custom run: known-good cases"),
    seeded_bad: Path = typer.Option(None, "--seeded-bad", "-b", help="Custom run: labelled flawed cases"),
    domain: str = typer.Option(None, "--domain", "-d", help="Domain pack for the model context, e.g. banking/retail/checking. Picks the set of its family."),
    threshold: float = typer.Option(0.85, "--threshold", "-t"),
    mode: str = typer.Option("mock", "--mode", "-m", help="Evaluation mode: mock or live"),
    output_dir: Path = typer.Option("runs", "--output-dir", "-o"),
    resume: Path | None = typer.Option(None, "--resume", help="Path to resume file for checkpointing (one file per set)"),
) -> None:
    """Run the benchmark: known-good cases must be approved, seeded bad cases must not be."""
    from .evaluate import EVAL_SETS, format_markdown_report, run_evaluation, save_evaluation

    custom = [p for p in (requirements, answer_key, seeded_bad) if p is not None]
    if custom and len(custom) < 3:
        typer.secho("A custom run needs all three of --requirements, --answer-key and --seeded-bad.", fg="red", err=True)
        raise typer.Exit(2)
    if custom:
        runs = {"custom": {"requirements": str(requirements), "answer_key": str(answer_key), "seeded_bad": str(seeded_bad),
                           "domain": domain or "insurance"}}
    else:
        if domain and eval_set == "all":
            eval_set = domain.split("/")[0]
        names = list(EVAL_SETS) if eval_set == "all" else [eval_set]
        unknown = [n for n in names if n not in EVAL_SETS]
        if unknown:
            typer.secho(f"Unknown evaluation set '{unknown[0]}'. Choose from: {', '.join(EVAL_SETS)}, all.", fg="red", err=True)
            raise typer.Exit(2)
        runs = {n: {**EVAL_SETS[n], **({"domain": domain} if domain else {})} for n in names}

    summary, all_met = [], True
    try:
        for name, cfg in runs.items():
            typer.secho(f"\n=== {name} (mode: {mode}, domain pack: {cfg['domain']}) ===", fg="cyan")
            resume_for_set = resume.with_name(f"{resume.stem}_{name}{resume.suffix}") if resume and len(runs) > 1 else resume
            report = run_evaluation(
                requirements_path=cfg["requirements"],
                answer_key_path=cfg["answer_key"],
                seeded_bad_path=cfg["seeded_bad"],
                domain=cfg["domain"],
                approve_threshold=threshold,
                agent_mode="mock" if mode == "mock" else "live",
                resume_path=resume_for_set,
            )
            json_p, md_p = save_evaluation(report, output_dir=output_dir, name=name)
            typer.echo(format_markdown_report(report))
            typer.secho(f"Results saved to:\n  {json_p}\n  {md_p}", fg="green")
            summary.append((name, report))
            all_met = all_met and report.targets_met
        if len(summary) > 1:
            typer.secho("\n=== Summary by set ===", fg="cyan")
            typer.echo(f"{'set':10s} {'caught':>14s} {'good not approved':>20s} {'hard rejects':>14s}  targets")
            for name, r in summary:
                typer.echo(f"{name:10s} {r.bad_caught:>3d}/{r.bad_cases:<3d} {r.catch_rate * 100:5.1f}% "
                           f"{r.good_wrongly_rejected:>4d}/{r.good_cases:<3d} {r.false_reject_rate * 100:5.1f}% "
                           f"{r.good_hard_rejected:>7d}/{r.good_cases:<3d}  {'PASS' if r.targets_met else 'FAIL'}")
        if not all_met:
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as exc:
        typer.secho(f"Evaluation failed: {exc}", fg="red", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()


