"""The E2E relevance predicate — proven in BOTH directions, and proven fail-safe.

The dangerous failure is a docs-shaped predicate that quietly matches a runtime path and buys a
green E2E context that never ran. So the tests that matter here are the ones asserting the lane
still RUNS: unknown paths, mixed changes, an unresolvable diff, a new top-level directory.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "ci_e2e_relevance", Path(__file__).resolve().parents[1] / "scripts" / "ci_e2e_relevance.py")
rel = importlib.util.module_from_spec(_SPEC)
# `@dataclass` resolves annotations through `sys.modules[cls.__module__]`, so a path-loaded module
# must be registered BEFORE exec or the decorator raises on `Decision`.
sys.modules[_SPEC.name] = rel
_SPEC.loader.exec_module(rel)


# ---- the fast lane: docs and governance records only -------------------------------------------

def test_docs_only_change_does_not_run_e2e():
    d = rel.decide(["docs/ci/CI_CONTROL_INVENTORY.md", "docs/adr/0101-x.md"])
    assert d.run is False
    assert "documentation or governance" in d.reason


def test_governance_contract_publication_does_not_run_e2e():
    """The exact shape of PRs #711 and #713 — a lifecycle row appended to a contract."""
    assert rel.decide(["docs/contracts/CC-2026-07-21-preflight-classification.md"]).run is False


def test_root_markdown_is_inert():
    assert rel.decide(["README.md", "AGENTS.md", "CLAUDE.md"]).run is False


# ---- the slow lane: everything else ------------------------------------------------------------

def test_source_change_runs_e2e():
    d = rel.decide(["src/fanops/clip.py"])
    assert d.run is True
    assert "src/fanops/clip.py" in d.reason


def test_one_runtime_path_among_many_docs_still_runs_e2e():
    """The mixed case — the whole point of the predicate is that ONE live path wins."""
    paths = [f"docs/note{i}.md" for i in range(20)] + ["src/fanops/post/run.py"]
    d = rel.decide(paths)
    assert d.run is True
    assert "src/fanops/post/run.py" in d.reason


def test_workflow_change_runs_e2e():
    """A change to the lane itself must exercise the lane."""
    assert rel.decide([".github/workflows/ci.yml"]).run is True


def test_ci_registry_change_runs_e2e():
    assert rel.decide([".github/ci-control-registry.yml"]).run is True


def test_integration_test_change_runs_e2e():
    assert rel.decide(["tests/integration/test_e2e_real.py"]).run is True


def test_packaging_change_runs_e2e():
    assert rel.decide(["pyproject.toml"]).run is True
    assert rel.decide(["requirements/ci-e2e.txt"]).run is True


def test_scripts_change_runs_e2e():
    assert rel.decide(["scripts/ci_e2e_relevance.py"]).run is True


# ---- fail-safe polarity ------------------------------------------------------------------------

def test_unknown_top_level_directory_runs_e2e():
    """A path class this module has never seen must not buy a fast lane."""
    assert rel.decide(["newthing/whatever.py"]).run is True
    assert rel.decide(["vendor/x/y.so"]).run is True


def test_unresolvable_diff_runs_e2e():
    d = rel.decide([])
    assert d.run is True
    assert "rather than assuming" in d.reason


def test_blank_lines_do_not_count_as_inert():
    d = rel.decide(["", "   ", ""])
    assert d.run is True


def test_push_event_always_runs_e2e():
    """Post-merge on main is full verification regardless of what changed."""
    assert rel.decide(["docs/x.md"], event="push").run is True


def test_force_flag_runs_e2e_even_for_docs():
    d = rel.decide(["docs/x.md"], forced=True)
    assert d.run is True
    assert "forced" in d.reason


def test_nested_md_outside_docs_is_not_inert():
    """Only ROOT-level markdown is inert; `src/fanops/notes.md` sits beside code."""
    assert rel.is_inert("README.md") is True
    assert rel.is_inert("src/fanops/notes.md") is False


def test_prefix_match_cannot_leak_past_a_directory_boundary():
    """`docs-live/` is not `docs/` — a bare startswith on 'docs' would wrongly admit it."""
    assert rel.is_inert("docs-live/runtime.py") is False
    assert rel.is_inert("dockerfiles/Dockerfile") is False
