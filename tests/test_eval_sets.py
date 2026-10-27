"""Each domain family has its own evaluation set. These tests enforce the new-domain checklist (docs/add-a-domain.md)."""
import json
from pathlib import Path

import pytest
import yaml

from verdict_ai import domains as dom
from verdict_ai.evaluate import EVAL_SETS
from verdict_ai.loader import load_cases, load_requirements
from verdict_ai.models import SeededBadCase

ROOT = Path(__file__).resolve().parents[1]


def _bad(name):
    return [SeededBadCase(**r) for r in json.loads((ROOT / EVAL_SETS[name]["seeded_bad"]).read_text(encoding="utf-8"))]


def test_every_domain_family_has_an_evaluation_set():
    families = {p.name for p in dom.default_root().iterdir() if p.is_dir()}
    assert families == set(EVAL_SETS), f"packs {sorted(families)} vs evaluation sets {sorted(EVAL_SETS)}"


@pytest.mark.parametrize("name", list(EVAL_SETS))
def test_set_files_exist_and_load(name):
    cfg = EVAL_SETS[name]
    for key in ("requirements", "answer_key", "seeded_bad"):
        assert (ROOT / cfg[key]).exists(), cfg[key]
    reqs = load_requirements(ROOT / cfg["requirements"])
    good = load_cases(ROOT / cfg["answer_key"])
    bad = _bad(name)
    assert reqs and good and bad
    assert dom.load_pack(cfg["domain"]).name


@pytest.mark.parametrize("name", list(EVAL_SETS))
def test_set_is_consistent(name):
    cfg = EVAL_SETS[name]
    reqs = {r.id: r for r in load_requirements(ROOT / cfg["requirements"])}
    good = load_cases(ROOT / cfg["answer_key"])
    bad = _bad(name)
    assert all(c.requirement_id in reqs for c in good), "good case points at a missing requirement"
    assert len({c.id for c in good + bad}) == len(good) + len(bad), "case ids must be unique across good and bad"
    good_ids = {c.id for c in good}
    for b in bad:
        if b.defect != "untraceable":
            assert b.requirement_id in reqs, (b.id, b.requirement_id)
        else:
            assert b.requirement_id not in reqs, f"{b.id} is labelled untraceable but its requirement exists"
        assert b.base_id is None or b.base_id in good_ids, (b.id, b.base_id)
        assert b.defect_note.strip() and b.expected_decision
    family = cfg["domain"].split("/")[0]
    for r in reqs.values():
        assert r.domain and r.domain.startswith(family), (r.id, r.domain)
        assert r.domain in dom.list_domains(), (r.id, "domain has no pack", r.domain)


def test_all_sets_cover_the_same_defect_types_with_the_same_labels():
    labels = {}
    for name in EVAL_SETS:
        labels[name] = {b.defect: b.expected_decision for b in _bad(name)}
    first = next(iter(labels.values()))
    assert len(first) >= 12
    for name, got in labels.items():
        assert got == first, f"{name}: defect types or decision labels differ from the other sets"


def test_a_set_has_enough_cases_to_mean_something():
    for name in EVAL_SETS:
        assert len(_bad(name)) >= 30, name
        assert len(load_cases(ROOT / EVAL_SETS[name]["answer_key"])) >= 15, name


def test_domain_pack_template_is_valid():
    data = yaml.safe_load((ROOT / "docs/domain-pack-template.yaml").read_text(encoding="utf-8"))
    pack = dom.Pack(path="example/leaf", **data)
    assert pack.name and pack.id_prefixes and pack.boundary_hints and pack.risk_tags
    assert set(data) <= set(dom.Pack.model_fields), "template uses a field the pack does not have"
