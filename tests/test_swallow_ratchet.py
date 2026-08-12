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
    """True when the except body logs, re-raises, or delegates to a known logging helper.

    Known helpers: fail_open (always logs), get_logger/getLogger + logging levels + log (event log),
    _quarantine (pipeline: always logs before stamping error state),
    _capture_poll_exc (reconcile: defers into results; apply re-raises and logs).
    """
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
                if n in ("fail_open", "getLogger", "get_logger", "warning", "debug", "info", "error", "exception", "critical", "log", "_quarantine", "_capture_poll_exc"):
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
    """Measured after Wave 3 cheap fail_open cluster (−6): agentstep/artifacts/cutover/pipeline_status/audit.
    Wave 2b breadcrumbed the unattended pipeline spine; Wave 2b leftover pass dropped pipeline/reconcile/produce
    to 0 by teaching the ratchet house helpers (_quarantine, log, _capture_poll_exc) and narrowing _parked_age.
    Wave 2c breadcrumbed the remainder to 0 (daemon/post/*/framing/moments/compose/stitch_render/health*/
    studio leftovers) and narrowed pure parse/import probes (llm/pipeline_run/timeutil/transcribe/validation_gate/
    secret_provider). Wave 2c leftovers cleared: persona_research already breadcrumbs via get_logger; views_common
    already breadcrumbs via _log.warning — both taught by existing helpers, baseline empty."""
    return {}


def test_silent_swallow_count_does_not_exceed_baseline():
    actual = _scan_silent_swallows()
    baseline = _baseline_silent_swallows()
    new_files = sorted(set(actual) - set(baseline))
    assert new_files == [], f"new silent-swallow file(s) not in baseline: {new_files}"
    regressions = {f: (actual[f], baseline[f]) for f in baseline if actual.get(f, 0) > baseline[f]}
    assert regressions == {}, f"silent swallow count grew: {regressions}"
