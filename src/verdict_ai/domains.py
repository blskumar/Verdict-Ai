"""Domain packs: YAML files in a folder tree. A pack inherits from its parents by path.

domains/insurance/_domain.yaml            level 1 (insurance)
domains/insurance/life/_domain.yaml       level 2 (life)
domains/insurance/life/term.yaml          level 3 (term)

Merge rules: context joined parent first; glossary child wins; list fields combined and
de-duplicated; thresholds child overrides parent.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

MAX_DEPTH = 3
LIST_FIELDS = ("risk_tags", "boundary_hints", "vague_terms", "ambiguous_phrases", "id_prefixes")


class DomainError(ValueError):
    pass


class Pack(BaseModel):
    path: str
    name: str
    description: str = ""
    context: str = ""
    glossary: dict[str, str] = Field(default_factory=dict)
    risk_tags: list[str] = Field(default_factory=list)
    boundary_hints: list[str] = Field(default_factory=list)
    vague_terms: list[str] = Field(default_factory=list)
    ambiguous_phrases: list[str] = Field(default_factory=list)
    id_prefixes: list[str] = Field(default_factory=list)
    thresholds: dict = Field(default_factory=dict)


def default_root() -> Path:
    here = Path(__file__).resolve()
    pkg_domains = here.parent / "domains"
    if pkg_domains.is_dir():
        return pkg_domains
    for base in (Path.cwd(), *here.parents):
        if (base / "domains").is_dir():
            return base / "domains"
    raise DomainError("No 'domains' folder found. Run from the project folder or pass --domains-dir.")


def _read(file: Path) -> dict:
    data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise DomainError(f"{file} must contain a YAML mapping")
    return data


def _files_for(path: str, root: Path) -> list[Path]:
    parts = [p for p in path.strip("/").split("/") if p]
    if not parts:
        raise DomainError("Domain path is empty. Example: insurance/life/term")
    if any(p in (".", "..") or "\\" in p for p in parts):
        raise DomainError(f"Domain path '{path}' is not valid")
    if len(parts) > MAX_DEPTH:
        raise DomainError(f"Domain path '{path}' is deeper than {MAX_DEPTH} levels")
    files, folder = [], root
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        folder = folder / part
        leaf = folder.with_suffix(".yaml")
        if folder.is_dir():
            marker = folder / "_domain.yaml"
            if marker.exists():
                files.append(marker)
            elif last:
                raise DomainError(f"'{path}' is a group without _domain.yaml")
        elif last and leaf.exists():
            files.append(leaf)
        else:
            raise DomainError(f"Unknown domain '{path}'. Run: verdict-ai domains list")
    return files


def merge(path: str, layers: list[dict]) -> Pack:
    out: dict = {"path": path.strip("/"), "name": "", "context": ""}
    contexts: list[str] = []
    for layer in layers:
        out["name"] = layer.get("name", out["name"])
        out["description"] = layer.get("description", out.get("description", ""))
        if layer.get("context"):
            contexts.append(" ".join(str(layer["context"]).split()))
        out.setdefault("glossary", {}).update(layer.get("glossary") or {})
        for f in LIST_FIELDS:
            merged = out.setdefault(f, [])
            for item in layer.get(f) or []:
                if item not in merged:
                    merged.append(item)
        out.setdefault("thresholds", {}).update(layer.get("thresholds") or {})
    out["context"] = "\n\n".join(contexts)
    return Pack(**out)


def load_pack(path: str, root: Path | None = None) -> Pack:
    root = root or default_root()
    return merge(path, [_read(f) for f in _files_for(path, root)])


def list_domains(root: Path | None = None) -> list[str]:
    """All selectable paths, depth first. A group is selectable too (it has a _domain.yaml)."""
    root = root or default_root()
    found: list[str] = []

    def walk(folder: Path, prefix: str, depth: int) -> None:
        for item in sorted(folder.iterdir()):
            if item.is_dir() and (item / "_domain.yaml").exists():
                found.append(f"{prefix}{item.name}")
                if depth < MAX_DEPTH:
                    walk(item, f"{prefix}{item.name}/", depth + 1)
            elif item.suffix == ".yaml" and not item.name.startswith("_"):
                found.append(f"{prefix}{item.stem}")

    walk(root, "", 1)
    return found


def tree(root: Path | None = None) -> list[dict]:
    """Nested structure for the web dropdowns: [{path, name, children: [...]}]."""
    root = root or default_root()

    def build(folder: Path, prefix: str, depth: int) -> list[dict]:
        nodes = []
        for item in sorted(folder.iterdir()):
            if item.is_dir() and (item / "_domain.yaml").exists():
                kids = build(item, f"{prefix}{item.name}/", depth + 1) if depth < MAX_DEPTH else []
                nodes.append({"path": f"{prefix}{item.name}", "name": _read(item / "_domain.yaml").get("name", item.name),
                              "children": kids})
            elif item.suffix == ".yaml" and not item.name.startswith("_"):
                nodes.append({"path": f"{prefix}{item.stem}", "name": _read(item).get("name", item.stem), "children": []})
        return nodes

    return build(root, "", 1)


def domain_for_prefix(prefix: str, root: Path | None = None) -> str | None:
    """Find the domain path whose pack lists this ID prefix (e.g. HOME -> insurance/p-and-c/homeowners)."""
    for path in list_domains(root):
        if prefix in load_pack(path, root).id_prefixes and not _has_children(path, root):
            return path
    return None


def _has_children(path: str, root: Path | None) -> bool:
    root = root or default_root()
    return (root / path).is_dir() and any(
        c for c in (root / path).iterdir() if c.is_dir() or (c.suffix == ".yaml" and not c.name.startswith("_")))
