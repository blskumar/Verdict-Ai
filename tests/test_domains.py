from pathlib import Path

import pytest
from typer.testing import CliRunner

from verdict_ai import domains as d
from verdict_ai.cli import app

ROOT = Path(__file__).resolve().parents[1] / "domains"


def test_tree_has_two_branches():
    t = d.tree(ROOT)
    paths = [n["path"] for n in t]
    assert "insurance" in paths
    assert "banking" in paths
    ins = next(n for n in t if n["path"] == "insurance")
    branches = {c["path"]: [k["path"] for k in c["children"]] for c in ins["children"]}
    assert branches == {
        "insurance/life": ["insurance/life/common", "insurance/life/term", "insurance/life/whole-life"],
        "insurance/p-and-c": ["insurance/p-and-c/commercial-auto", "insurance/p-and-c/homeowners"],
    }


def test_merge_rules_inheritance():
    term = d.load_pack("insurance/life/term", ROOT)
    # context: parent first
    assert term.context.index("Insurance software") < term.context.index("Life policies") < term.context.index("Term life covers")
    # lists combined and de-duplicated
    assert "money" in term.risk_tags and "free_look" in term.risk_tags
    assert len(term.risk_tags) == len(set(term.risk_tags))
    # thresholds: child level overrides parent (life sets similarity too; revise_below inherited)
    assert term.thresholds["revise_below"] == 0.70
    assert term.id_prefixes == ["TERM"]


def test_child_glossary_wins_over_parent():
    layers = [{"name": "A", "glossary": {"x": "parent"}, "thresholds": {"t": 1}},
              {"name": "B", "glossary": {"x": "child"}, "thresholds": {"t": 2}}]
    p = d.merge("a/b", layers)
    assert p.glossary["x"] == "child" and p.thresholds["t"] == 2


def test_unknown_and_too_deep_paths():
    with pytest.raises(d.DomainError, match="Unknown domain"):
        d.load_pack("insurance/life/bogus", ROOT)
    with pytest.raises(d.DomainError, match="deeper than"):
        d.load_pack("a/b/c/d", ROOT)


def test_prefix_to_domain():
    assert d.domain_for_prefix("HOME", ROOT) == "insurance/p-and-c/homeowners"
    assert d.domain_for_prefix("WL", ROOT) == "insurance/life/whole-life"
    assert d.domain_for_prefix("NOPE", ROOT) is None


def test_cli_domains_list_and_load():
    r = CliRunner()
    out = r.invoke(app, ["domains", "list"])
    assert out.exit_code == 0 and "insurance/life/term" in out.output
    out = r.invoke(app, ["load", "-r", str(ROOT.parent / "data/requirements/insurance_requirements.pdf"),
                         "-d", "insurance/life"])
    assert out.exit_code == 0 and "25 requirements" in out.output
    out = r.invoke(app, ["load", "-r", "x.pdf", "-d", "banking"])
    assert out.exit_code == 1


def test_domain_path_cannot_leave_the_domains_folder():
    import pytest
    from verdict_ai.domains import DomainError, load_pack
    for bad in ("../domains/insurance", "insurance/../insurance", "insurance\life"):
        with pytest.raises(DomainError):
            load_pack(bad)
