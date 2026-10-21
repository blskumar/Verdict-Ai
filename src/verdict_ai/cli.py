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
        typer.secho("uvicorn or fastapi not installed. Run: pip install 'verdictai-gate[web]'", fg="red", err=True)
        raise typer.Exit(1)


@app.command()
def evaluate(
    requirements: Path = typer.Option("data/requirements/insurance_requirements.json", "--requirements", "-r"),
    answer_key: Path = typer.Option("data/answer_key/insurance_answer_key.json", "--answer-key", "-a"),
    seeded_bad: Path = typer.Option("data/seeded_bad/insurance_seeded_bad.json", "--seeded-bad", "-b"),
    domain: str = typer.Option("insurance/life/term", "--domain", "-d"),
    threshold: float = typer.Option(0.85, "--threshold", "-t"),
    mode: str = typer.Option("mock", "--mode", "-m", help="Evaluation mode: mock or live"),
    output_dir: Path = typer.Option("runs", "--output-dir", "-o"),
) -> None:
    """Run benchmark bake-off over answer_key and seeded_bad test cases."""
    from .evaluate import format_markdown_report, run_evaluation, save_evaluation
    try:
        typer.secho(f"Running evaluation benchmark (mode: {mode})...", fg="cyan")
        report = run_evaluation(
            requirements_path=requirements,
            answer_key_path=answer_key,
            seeded_bad_path=seeded_bad,
            domain=domain,
            approve_threshold=threshold,
            agent_mode="mock" if mode == "mock" else "live",
        )
        json_p, md_p = save_evaluation(report, output_dir=output_dir)
        typer.echo(format_markdown_report(report))
        typer.secho(f"Results saved to:\n  {json_p}\n  {md_p}", fg="green")
        if not report.targets_met:
            raise typer.Exit(1)
    except Exception as exc:
        typer.secho(f"Evaluation failed: {exc}", fg="red", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()


