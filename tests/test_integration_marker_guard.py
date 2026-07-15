# tests/test_integration_marker_guard.py — every test under tests/integration/ MUST carry the
# `integration` marker. The `unit` lane selects `-m "not integration and not slow"`, so an unmarked
# module here would be SELECTED into that required fast lane and run WITHOUT its real ffmpeg/whisper
# toolchain (a crash, or worse a false pass). This is a static AST ratchet — like test_swallow_ratchet
# and test_internal_prints_routed — so it needs no toolchain and runs in the very lane it protects.
# It accepts BOTH marking styles present in the tree: module-level `pytestmark = pytest.mark.integration`
# (scalar or inside a list/tuple), and a per-function decorator `@pytest.mark.integration` or `@<alias>`
# where `<alias> = pytest.mark.integration` (e.g. test_variation_render.py's `REQUIRE`).
from __future__ import annotations
import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_INTEGRATION = _ROOT / "tests" / "integration"


def _is_integration_marker(node: ast.AST) -> bool:
    """True for the expression `pytest.mark.integration`."""
    return (isinstance(node, ast.Attribute) and node.attr == "integration"
            and isinstance(node.value, ast.Attribute) and node.value.attr == "mark"
            and isinstance(node.value.value, ast.Name) and node.value.value.id == "pytest")


def _integration_aliases(tree: ast.Module) -> set[str]:
    """Top-level names bound to `pytest.mark.integration` (bare or inside a list/tuple), e.g.
    `REQUIRE = pytest.mark.integration` or `pytestmark = [pytest.mark.integration, pytest.mark.asr]`."""
    aliases: set[str] = set()
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        value = stmt.value
        exprs = value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]
        if any(_is_integration_marker(e) for e in exprs):
            aliases.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
    return aliases


def _module_is_marked(tree: ast.Module, aliases: set[str]) -> bool:
    """True when a module-level `pytestmark` resolves to the integration marker — which applies it
    to EVERY test in the module (scalar, list/tuple member, or an alias name)."""
    for stmt in tree.body:
        if not (isinstance(stmt, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets)):
            continue
        value = stmt.value
        exprs = value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]
        for e in exprs:
            if _is_integration_marker(e) or (isinstance(e, ast.Name) and e.id in aliases):
                return True
    return False


def _fn_is_decorated(fn: ast.AST, aliases: set[str]) -> bool:
    for dec in getattr(fn, "decorator_list", []):
        if _is_integration_marker(dec) or (isinstance(dec, ast.Name) and dec.id in aliases):
            return True
    return False


def _unmarked_tests(path: Path) -> list[str]:
    """Test functions in `path` that would NOT carry the integration marker at collection time."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    aliases = _integration_aliases(tree)
    if _module_is_marked(tree, aliases):
        return []  # module-level pytestmark covers every test in the file
    return [n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name.startswith("test_") and not _fn_is_decorated(n, aliases)]


def test_every_integration_test_carries_the_marker():
    assert _INTEGRATION.is_dir(), f"missing {_INTEGRATION}"
    bad = {path.relative_to(_ROOT).as_posix(): offenders
           for path in sorted(_INTEGRATION.rglob("test_*.py"))
           if (offenders := _unmarked_tests(path))}
    assert bad == {}, (
        "test(s) under tests/integration/ lack the `integration` marker and would run in the required "
        "fast `unit` lane without a real toolchain. Add module-level `pytestmark = pytest.mark.integration` "
        f"or decorate each with @pytest.mark.integration: {bad}"
    )
