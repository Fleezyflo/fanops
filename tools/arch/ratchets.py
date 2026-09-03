"""Unified parsing of ratchet test files and verification matrix test names.

The CI ratchet tests are the canonical owners of print/swallow budgets; this module is the single
reader so generate, policy, and baseline do not each walk the same AST independently.
"""
from __future__ import annotations

import ast
from pathlib import Path

from .common import CONTRACT, REPO, TESTS, load


def declared_ratchets(tests: Path | None = None) -> dict:
    """Read the baselines the CI ratchet tests actually enforce."""
    tests = tests or TESTS
    out: dict = {"print": {}, "swallow": {}, "unsupported": []}

    p = tests / "test_internal_prints_routed.py"
    if p.exists():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name == "_CLI_PRINT_COUNT" and isinstance(node.value, ast.Constant):
                    out["print"]["cli_print_count"] = node.value.value
                if name == "_INTERNAL_MODULES" and isinstance(node.value, (ast.Tuple, ast.List)):
                    out["print"]["zero_print_modules"] = sorted(
                        e.value for e in node.value.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str))

    s = tests / "test_swallow_ratchet.py"
    if s.exists():
        tree = ast.parse(s.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_baseline_silent_swallows":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Dict):
                        base = {}
                        for k, v in zip(sub.keys, sub.values):
                            if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                                base[k.value] = v.value
                        if base:
                            out["swallow"]["baseline"] = dict(sorted(base.items()))
                        break
    if "baseline" not in out["swallow"]:
        out["unsupported"].append({
            "kind": "unparsed_swallow_baseline",
            "evidence": "tests/test_swallow_ratchet.py::_baseline_silent_swallows",
            "why": "the baseline dict literal could not be read; the budget cannot be cross-checked",
        })
    return out


def tests_defined(repo: Path | None = None) -> set[str]:
    """Every `def test_*` actually defined under tests/."""
    out: set[str] = set()
    tests = (repo or REPO) / "tests"
    if not tests.exists():
        return out
    for py in tests.rglob("test_*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name.startswith("test_"):
                out.add(node.name)
    return out


def verification_matrix_test_names(contract: Path | None = None) -> set[str]:
    """Every test NAME the verification matrix requires."""
    vm = (contract or CONTRACT) / "verification_matrix.json"
    if not vm.exists():
        return set()
    names: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            n = node.get("name")
            if isinstance(n, str) and n.startswith("test_"):
                names.add(n)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(load(vm))
    return names
