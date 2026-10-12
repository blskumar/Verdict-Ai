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


if __name__ == "__main__":
    app()
