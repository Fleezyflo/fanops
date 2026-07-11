# tests/test_swallow_ratchet.py — Brief 05: silent `except Exception` handlers must not grow.
from __future__ import annotations
import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "fanops"
_EXEMPT = frozenset({"src/fanops/errors.py"})


def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Call):
        return _call_name(func.func)
    return None


def _handler_non_silent(body: list[ast.stmt]) -> bool:
    """True when the except body logs, re-raises, or delegates to fail_open."""
    for stmt in body:
        for sub in ast.walk(stmt):
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and sub is not stmt:
                continue
            if isinstance(sub, ast.Raise):
                return True
            if isinstance(sub, ast.With):
                for item in sub.items:
                    ctx = item.context_expr
                    if isinstance(ctx, ast.Call) and _call_name(ctx.func) == "fail_open":
                        return True
            if isinstance(sub, ast.Call):
                f = sub.func
                n = _call_name(f)
                if n in ("fail_open", "getLogger", "get_logger", "warning"):
                    return True
                if isinstance(f, ast.Call) and _call_name(f.func) == "get_logger":
                    return True
    return False


def _is_broad_except(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    if t is None:
        return False
    if isinstance(t, ast.Name):
        return t.id in ("Exception", "BaseException")
    if isinstance(t, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id in ("Exception", "BaseException") for e in t.elts)
    return False


def _count_silent_swallows(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and _is_broad_except(node) and not _handler_non_silent(node.body):
            n += 1
    return n


def _scan_silent_swallows() -> dict[str, int]:
    out: dict[str, int] = {}
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        if rel in _EXEMPT:
            continue  # houses fail_open implementation; never ratchet-counted
        c = _count_silent_swallows(path)
        if c:
            out[rel] = c
    return out


def _baseline_silent_swallows() -> dict[str, int]:
    """Measured at HEAD (origin/main): 49 files, 203 handlers; top offenders actions.py (23), views.py (13)."""
    return {
        "src/fanops/accounts.py": 3,
        "src/fanops/agentstep.py": 1,
        "src/fanops/artifacts.py": 1,
        "src/fanops/audit.py": 2,
        "src/fanops/caption.py": 2,
        "src/fanops/cli.py": 3,
        "src/fanops/clip.py": 1,
        "src/fanops/compose.py": 4,
        "src/fanops/crosspost.py": 1,
        "src/fanops/cutover.py": 1,
        "src/fanops/daemon.py": 5,
        "src/fanops/doctor.py": 8,
        "src/fanops/fanops_hashtags.py": 2,
        "src/fanops/framing.py": 9,
        "src/fanops/health.py": 2,
        "src/fanops/health_model.py": 5,
        "src/fanops/llm.py": 2,
        "src/fanops/moments.py": 3,
        "src/fanops/persona_research.py": 1,
        "src/fanops/pipeline.py": 13,
        "src/fanops/pipeline_run.py": 2,
        "src/fanops/pipeline_status.py": 1,
        "src/fanops/post/compress.py": 1,
        "src/fanops/post/postiz.py": 1,
        "src/fanops/post/run.py": 4,
        "src/fanops/post/zernio.py": 3,
        "src/fanops/postiz_lifecycle.py": 2,
        "src/fanops/produce.py": 8,
        "src/fanops/reconcile.py": 9,
        "src/fanops/responder.py": 2,
        "src/fanops/secret_provider.py": 1,
        "src/fanops/stitch_render.py": 4,
        "src/fanops/studio/actions.py": 23,
        "src/fanops/studio/actions_approve.py": 8,
        "src/fanops/studio/actions_casting.py": 2,
        "src/fanops/studio/actions_run.py": 7,
        "src/fanops/studio/actions_segments.py": 2,
        "src/fanops/studio/actions_wipe.py": 3,
        "src/fanops/studio/app.py": 1,
        "src/fanops/studio/golive.py": 15,
        "src/fanops/studio/personas.py": 10,
        "src/fanops/studio/views.py": 13,
        "src/fanops/studio/views_common.py": 2,
        "src/fanops/studio/views_results.py": 3,
        "src/fanops/studio/views_review.py": 1,
        "src/fanops/timeutil.py": 2,
        "src/fanops/timing_bias.py": 1,
        "src/fanops/transcribe.py": 2,
        "src/fanops/validation_gate.py": 1,
    }


def test_silent_swallow_count_does_not_exceed_baseline():
    actual = _scan_silent_swallows()
    baseline = _baseline_silent_swallows()
    new_files = sorted(set(actual) - set(baseline))
    assert new_files == [], f"new silent-swallow file(s) not in baseline: {new_files}"
    regressions = {f: (actual[f], baseline[f]) for f in baseline if actual.get(f, 0) > baseline[f]}
    assert regressions == {}, f"silent swallow count grew: {regressions}"
