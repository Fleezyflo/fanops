# tests/test_retirement_writer_ratchet.py — T3.10: direct retirement writes must not grow.
"""Retirement has exactly one owner, and this counter is what keeps it that way.

Lineage suppression is DERIVED, never stored: `Ledger.is_suppressed` walks an entity's ancestry at
read time (and `Ledger.can_seed` / `Ledger.can_promote` are its callers), so a retired parent
suppresses its descendants without any module writing `retired` onto them. The only retirement a
module may STORE is INTRINSIC — this entity itself is retired — and the Ledger owns that write
through its own `set_source_state` / `set_moment_state` / `set_clip_state` / `set_post_state` /
`retire_clip` setters. Every write that bypasses those setters re-creates the copied-state bug this
track deleted, which is why the ratchet counts the bypassing forms rather than the routed calls.
It exists because #825, #826, #827 and #829 were four separate re-discoveries of one rule that had
no owner: each patch re-derived "a retired parent must suppress its children" and stored the answer
somewhere new. A rule nobody owns gets re-broken; this file owns it.
"""
from __future__ import annotations
import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "fanops"
# ledger.py IS the owner: its setters are the sanctioned direct writes, so counting them would pin
# the owner's own internals and pressure the exact shape everyone else is supposed to route through.
_EXEMPT = frozenset({"src/fanops/ledger.py"})
_STATE_ENUMS = frozenset({"SourceState", "MomentState", "ClipState", "PostState"})
_FIX = "route the write through Ledger.set_post_state / retire_clip / set_moment_state, or extend the owner"


def _is_retired_enum(node: ast.AST) -> bool:
    """True for a literal `<StateEnum>.retired` — the value both direct forms store."""
    return isinstance(node, ast.Attribute) and node.attr == "retired" and isinstance(node.value, ast.Name) and node.value.id in _STATE_ENUMS


def _count_direct_retirement_writes(tree: ast.AST) -> int:
    """Count the two AST-detectable forms that bypass the Ledger's setters.

    1. `ast.Assign` whose value is `<StateEnum>.retired` — e.g. `p.state = PostState.retired`.
    2. `ast.Call` with `update=<dict>` mapping `"state"` to `<StateEnum>.retired` — e.g.
       `led.posts[x] = cur.model_copy(update={"state": PostState.retired, ...})`.

    A routed call — `led.set_moment_state(mid, MomentState.retired)`, `led.retire_clip(cid)` — is
    deliberately NOT counted: going through the owner is the correct shape, not a violation.
    """
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_retired_enum(node.value):
            n += 1
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "update" and isinstance(kw.value, ast.Dict):
                    for k, v in zip(kw.value.keys, kw.value.values):
                        if isinstance(k, ast.Constant) and k.value == "state" and _is_retired_enum(v): n += 1
    return n


def _count(path: Path) -> int:
    return _count_direct_retirement_writes(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))


def _scan_direct_retirement_writes() -> dict[str, int]:
    out: dict[str, int] = {}
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        if rel in _EXEMPT:
            continue  # the owner itself; never ratchet-counted
        c = _count(path)
        if c:
            out[rel] = c
    return out


# Measured after MOL-779: every non-owner retirement write routes through Ledger.set_*_state /
# retire_clip. The canary compound write (state + error_reason) now goes through
# set_post_state(..., error_reason=...); the exemption is gone. Census-at-zero is the regression
# gate that keeps zero at zero (delete the ratchet only with MOL-819's frozen-model negative control
# — that deletion is MOL-821).
_BASELINE = {}


def test_retirement_writes_do_not_escape_the_ledger():
    actual = _scan_direct_retirement_writes()
    baseline = _BASELINE
    new_files = sorted(set(actual) - set(baseline))
    assert new_files == [], f"new direct-retirement-write file(s) not in baseline: {new_files} — {_FIX}"
    regressions = {f: (actual[f], baseline[f]) for f in baseline if actual.get(f, 0) > baseline[f]}
    assert regressions == {}, f"direct retirement write count grew: {regressions} — {_FIX}"


def test_the_counter_actually_detects_a_write():
    """Negative control: without it a counter that always returns 0 would pass green forever."""
    planted = (
        "p.state = PostState.retired\n"
        'led.posts[pid] = cur.model_copy(update={"state": PostState.retired, "error_reason": bounded})\n'
    )
    assert _count_direct_retirement_writes(ast.parse(planted)) == 2


def test_routed_writes_are_not_counted():
    """The whole point: going through the owner is correct, so the ratchet must not pressure it."""
    routed = (
        "led.set_post_state(x, PostState.retired)\n"
        "led.retire_clip(cid)\n"
        "led.set_moment_state(mid, MomentState.retired)\n"
    )
    assert _count_direct_retirement_writes(ast.parse(routed)) == 0
