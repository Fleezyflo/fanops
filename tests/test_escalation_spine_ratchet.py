# tests/test_escalation_spine_ratchet.py — MOL-961 Wave D: spine broad-except must escalate.
"""Stricter than test_swallow_ratchet: on the unattended progress spine, log/get_logger alone is NOT enough.

Every broad `except Exception` / `BaseException` in the denylist must call at least one of:
  - decide (fanops.escalation)
  - fail_open (call or `with fail_open(...)`)
  - raise
  - _on_deterministic_fail (responder Wave A wrapper; must itself call decide — asserted below)

Adding `except Exception: log; return` on a spine file MUST fail this test.
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

# decide / fail_open / raise are the policy verbs. _on_deterministic_fail is the responder's
# sole Wave A decide wrapper (must keep calling decide — see test_on_deterministic_fail_calls_decide).
# Intentionally NOT: get_logger / warning / info / log (swallow ratchet allows those; spine does not).
_ESCALATE_CALLS = frozenset({"decide", "fail_open", "_on_deterministic_fail"})


def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Call):
        return _call_name(func.func)
    return None


def _handler_escalates(body: list[ast.stmt]) -> bool:
    """True when the except body raises, fail_opens, decides, or delegates to the decide wrapper."""
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
            if isinstance(sub, ast.Call) and _call_name(sub.func) in _ESCALATE_CALLS:
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
    """Return (relpath, lineno) for spine broad-except handlers that lack decide/fail_open/raise."""
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
        "spine broad-except without decide/fail_open/raise/_on_deterministic_fail: "
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
