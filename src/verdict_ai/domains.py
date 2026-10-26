"""Domain packs: YAML files in a folder tree. A pack inherits from its parents by path.

domains/insurance/_domain.yaml            level 1 (insurance)
domains/insurance/life/_domain.yaml       level 2 (life)
domains/insurance/life/term.yaml          level 3 (term)

Merge rules: context joined parent first; glossary child wins; list fields combined and
de-duplicated; thresholds child overrides parent.
"""
from __future__ import annotations

from pathlib import Path

import re

import yaml
from pydantic import BaseModel, Field

MAX_DEPTH = 3
LIST_FIELDS = ("risk_tags", "boundary_hints", "vague_terms", "ambiguous_phrases", "id_prefixes", "source_markers")


class DomainError(ValueError):
    pass


class Pack(BaseModel):
    path: str
    name: str
    description: str = ""
    context: str = ""
    glossary: dict[str, str] = Field(default_factory=dict)
    obsolete_terms: dict[str, str] = Field(default_factory=dict)  # outdated term or rule -> why / what replaced it
    risk_tags: list[str] = Field(default_factory=list)
    boundary_hints: list[str] = Field(default_factory=list)
    vague_terms: list[str] = Field(default_factory=list)
    ambiguous_phrases: list[str] = Field(default_factory=list)
    id_prefixes: list[str] = Field(default_factory=list)
    source_markers: list[str] = Field(default_factory=list)  # extra phrases that count as a citation
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
        out.setdefault("obsolete_terms", {}).update(layer.get("obsolete_terms") or {})
        for f in LIST_FIELDS:
            merged = out.setdefault(f, [])
            for item in layer.get(f) or []:
                if item not in merged:
                    merged.append(item)
        out.setdefault("thresholds", {}).update(layer.get("thresholds") or {})
    out["context"] = "\n\n".join(contexts)
    return Pack(**out)


def context_text(pack: Pack) -> str:
    """The text the model roles receive about a domain: what it is, its terms, where the risk is."""
    parts = [f"Domain: {pack.name}"]
    if pack.context:
        parts.append(pack.context)
    if pack.glossary:
        parts.append("Glossary:\n" + "\n".join(f"- {k}: {v}" for k, v in pack.glossary.items()))
    if pack.obsolete_terms:
        parts.append("Outdated terms (flag if used):" + chr(10) + chr(10).join(f"- {k}: {v}" for k, v in pack.obsolete_terms.items()))
    if pack.risk_tags:
        parts.append("Risk areas: " + ", ".join(pack.risk_tags))
    if pack.boundary_hints:
        parts.append("Boundaries worth testing:\n" + "\n".join(f"- {h}" for h in pack.boundary_hints))
    return "\n\n".join(parts)


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


# shortcut: a few hand-picked words per family for files with no domain or ID prefix (pasted text, PDFs);
# upgrade to terms drawn from the packs when more families are added.
_FAMILY_HINTS = {
    "insurance": {"policy", "premium", "underwriting", "claim", "insured", "beneficiary", "coverage",
                  "deductible", "endorsement", "quote", "bind", "peril", "rider"},
    "banking": {"account", "loan", "deposit", "overdraft", "kyc", "aml", "ledger", "transfer", "interest",
                "apr", "ach", "withdrawal", "branch", "customer", "balance"},
}


def _compatible(item_path: str, selected: str) -> bool:
    """True when a domain path belongs with the selected one: same, parent, child, or same branch."""
    key = lambda p: "/".join(p.split("/")[:2])  # noqa: E731
    return (item_path == selected or item_path.startswith(selected + "/")
            or selected.startswith(item_path + "/") or key(item_path) == key(selected))


def domain_mismatch(selected: str | None, requirements: list[dict], cases: list[dict],
                    root: Path | None = None) -> str | None:
    """Return a message when the data clearly belongs to another domain than the selected one, else None.

    Evidence, strongest first: the item's own `domain`, the ID prefix (HOME-REQ-01 -> HOME), then words.
    """
    if not selected:
        return None
    root = root or default_root()
    prefix_to_path: dict[str, str] = {}
    for path in list_domains(root):
        for p in _read(_files_for(path, root)[-1]).get("id_prefixes", []) or []:
            prefix_to_path.setdefault(p, path)

    def evidence(item: dict, id_key: str) -> str | None:
        if item.get("domain"):
            return str(item["domain"])
        return prefix_to_path.get(str(item.get(id_key) or "").split("-")[0])

    offenders: list[tuple[str, str]] = []  # (item id, domain it belongs to)
    seen_evidence = False
    for items, id_key in ((requirements, "id"), (cases, "requirement_id")):
        for it in items:
            found = evidence(it, id_key)
            if found:
                seen_evidence = True
                if not _compatible(found, selected):
                    offenders.append((str(it.get("id") or it.get(id_key)), found))
    if offenders:
        parts_ = [d.split("/") for _, d in offenders]
        common = [seg for seg, *rest in zip(*parts_) if all(seg == r for r in rest)]
        belongs = ["/".join(common)] if common else sorted({d for _, d in offenders})
        ids = ", ".join(i for i, _ in offenders[:3]) + (" ..." if len(offenders) > 3 else "")
        return (f"{len(offenders)} item(s) ({ids}) belong to {', '.join(belongs)}, but the selected domain is "
                f"{selected}. Select the matching domain, then load them again.")
    if seen_evidence:
        return None

    words = re.findall(r"[a-z]+", " ".join(
        f"{x.get('title', '')} {x.get('text', '')} {x.get('scenario', '')} {x.get('expected', '')}"
        for x in [*requirements, *cases]).lower())
    hits = {fam: sum(w in hints for w in words) for fam, hints in _FAMILY_HINTS.items()}
    own = selected.split("/")[0]
    other = max((f for f in hits if f != own), key=lambda f: hits[f], default=None)
    if other and hits[other] >= 3 and hits[other] > 2 * hits.get(own, 0):
        return (f"The content reads like {other}, but the selected domain is {selected}. "
                f"Select a {other} domain, then load it again.")
    return None
