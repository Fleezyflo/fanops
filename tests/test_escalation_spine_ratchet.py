# tests/test_escalation_spine_ratchet.py — MOL-961 Wave D: spine broad-except must escalate.
"""Stricter than test_swallow_ratchet: on the unattended progress spine, log/get_logger alone is NOT enough.

Every broad `except Exception` / `BaseException` in the denylist must escalate via at least one of:
  - decide (fanops.escalation) — preferred for progress-critical control-plane outcomes
  - _on_deterministic_fail (responder Wave A wrapper; must itself call decide — asserted below)
  - an escaping `raise` (not nested solely inside `fail_open`)
  - `with fail_open(...)` wrapping real work (policy §2.6 secondary write), or as the *sole* handler body

THEATRE (rejected): `with fail_open(...): raise` then prior continue (Assign/Return/print). fail_open
swallows the raise; control plane is unchanged — that is NOT escalate-OK.
"""
from __future__ import annotations
import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

# Exact Wave D denylist — do not expand to framing/clip/compose/overlay/studio/views.
_SPINE = (
    "src/fanops/responder.py",
    "src/fanops/signals.py",
    "src/fanops/cli.py",
    "src/fanops/doctor.py",
    "src/fanops/pipeline_status.py",
    "src/fanops/escalation.py",
)

_ESCALATE_CALLS = frozenset({"decide", "_on_deterministic_fail"})


def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Call):
        return _call_name(func.func)
    return None


def _is_fail_open_with(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.With):
        return False
    return any(
        isinstance(item.context_expr, ast.Call) and _call_name(item.context_expr.func) == "fail_open"
        for item in stmt.items
    )


def _with_body_only_reraise(w: ast.With) -> bool:
    """True when with-body is solely `raise` (theatre bait when followed by continue)."""
    body = [s for s in w.body if not isinstance(s, ast.Pass)]
    return len(body) == 1 and isinstance(body[0], ast.Raise)


def _handler_escalates(body: list[ast.stmt]) -> bool:
    """True when the except body has a real escalate path — not fail_open-swallow theatre."""
    stmts = [s for s in body if not isinstance(s, ast.Pass)]
    if not stmts:
        return False

    # THEATRE: `with fail_open(...): raise` then more statements → fail_open swallowed; continue is the real path.
    for i, stmt in enumerate(stmts):
        if _is_fail_open_with(stmt) and _with_body_only_reraise(stmt) and i < len(stmts) - 1:
            return _handler_escalates(stmts[i + 1 :])

    for stmt in stmts:
        if isinstance(stmt, ast.Raise):
            return True  # escaping raise
        if _is_fail_open_with(stmt):
            if not _with_body_only_reraise(stmt):
                return True  # §2.6: fail_open wraps real secondary work
            continue
        for sub in ast.walk(stmt):
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and sub is not stmt:
                continue
            if isinstance(sub, ast.Call) and _call_name(sub.func) in _ESCALATE_CALLS:
                return True
            if isinstance(sub, ast.Call) and _call_name(sub.func) == "fail_open":
                return True

    # Sole-body named degrade: only `with fail_open(...): raise` (observability / optional enrichment).
    if all(_is_fail_open_with(s) and _with_body_only_reraise(s) for s in stmts):
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


def _spine_non_escalating() -> list[tuple[str, int]]:
    """Return (relpath, lineno) for spine broad-except handlers that lack a real escalate path."""
    out: list[tuple[str, int]] = []
    for rel in _SPINE:
        path = _ROOT / rel
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _is_broad_except(node) and not _handler_escalates(node.body):
                out.append((rel, node.lineno))
    return out


def test_spine_broad_except_must_escalate():
    offenders = _spine_non_escalating()
    assert offenders == [], (
        "spine broad-except without decide/escaping-raise/honest-fail_open: "
        + ", ".join(f"{p}:{n}" for p, n in offenders)
    )


def test_on_deterministic_fail_calls_decide():
    """Teaching _on_deterministic_fail as escalate-OK must not soft-open: the wrapper still calls decide."""
    path = _ROOT / "src/fanops/responder.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_on_deterministic_fail":
            found = True
            calls = {_call_name(sub.func) for sub in ast.walk(node) if isinstance(sub, ast.Call)}
            assert "decide" in calls, "_on_deterministic_fail must call decide (spine escalate path)"
            break
    assert found, "_on_deterministic_fail missing from responder.py"


def test_log_only_handler_is_not_escalate_ok():
    """Regression shape: log/get_logger continue must NOT satisfy the spine ratchet."""
    tree = ast.parse(
        "try:\n    x()\nexcept Exception:\n    get_logger(cfg)(\"run\", \"-\", \"halted\")\n    return None\n"
    )
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
    assert _is_broad_except(handler)
    assert not _handler_escalates(handler.body)


def test_theatre_fail_open_raise_then_continue_is_not_escalate_ok():
    """THEATRE: with fail_open: raise then prior continue — swallow + unchanged control plane."""
    tree = ast.parse(
        "try:\n    x()\nexcept Exception as e:\n"
        "    with fail_open(\"cli._run_once converge halt nonzero:\"):\n"
        "        raise\n"
        "    print(\"run halted\")\n"
        "    return None\n"
    )
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
    assert _is_broad_except(handler)
    assert not _handler_escalates(handler.body)


def test_decide_then_return_is_escalate_ok():
    tree = ast.parse(
        "try:\n    x()\nexcept Exception as e:\n"
        "    if decide(\"toolchain_run\", 0) is EscalationPosture.nonzero:\n"
        "        return None\n"
        "    raise\n"
    )
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
    assert _handler_escalates(handler.body)


def test_pure_fail_open_sole_body_is_escalate_ok():
    """§2.6 observability: sole `with fail_open(...): raise` (no continue-after) is named degrade."""
    tree = ast.parse(
        "try:\n    x()\nexcept Exception:\n"
        "    with fail_open(\"cli._run_once learn degrade:\"):\n"
        "        raise\n"
    )
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
    assert _handler_escalates(handler.body)
