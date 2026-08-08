# tests/test_post_state_census_ratchet.py — MOL-815: hand-rolled PostState censuses stay out of product code.
"""Binary AST gate: zero `Counter(… .state …)` over posts outside the ledger owner.

MOL-763 / MOL-811 rewired diagnostic censuses onto `Ledger.state_histogram()` / owned predicates.
This file is the permanent mechanical check that those hand-rolled `Counter(p.state …)` sites
cannot return. Removal condition: permanent by policy — the gate is the product, not a temporary
ratchet toward a cleanup.

Flagged shape: a `Counter(...)` whose comprehension/generator iterates `led.posts` /
`posts.values()` (or a bare `posts` name) and whose element reads `.state` (including
`.state.value`). That is the census shape that produced the A/B/C cross-surface disagreement.

Deliberately NOT flagged (must stay green):
- `len(led.posts_in_state(...))` and `led.state_histogram()` — owned Ledger APIs
- per-post predicates (`p.state is PostState.X` for editability / filters / list comps)
- `Counter` over accounts or other non-state fields (`Counter(p.account for p in …)`)
- clip / moment / source state counters (`Counter(c.state for c in led.clips.values())`)
- worklist/time arms that inspect `p.state` without building a state Counter

No comment-allowlist. No per-file baseline. Assert zero outside the owner.
"""
from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "fanops"
# ledger.py is the owner of the raw PostState census (`state_histogram`). Exempt only the owner —
# not a site allowlist of violators.
_EXEMPT = frozenset({"src/fanops/ledger.py"})
_FIX = "read Ledger.state_histogram() / posts_in_state(), do not Counter(p.state …) over posts"


def _is_counter_call(node: ast.Call) -> bool:
    f = node.func
    if isinstance(f, ast.Name) and f.id == "Counter":
        return True
    return isinstance(f, ast.Attribute) and f.attr == "Counter"


def _is_posts_iterable(node: ast.AST) -> bool:
    """True for `led.posts`, `self.posts`, `posts`, or any of those `.values()`."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "values":
        return _is_posts_iterable(node.func.value)
    if isinstance(node, ast.Attribute) and node.attr == "posts":
        return True
    return isinstance(node, ast.Name) and node.id == "posts"


def _target_name(target: ast.AST) -> str | None:
    return target.id if isinstance(target, ast.Name) else None


def _elt_reads_state(elt: ast.AST, loop_var: str) -> bool:
    """True when the comprehension element reaches `<loop_var>.state` (or `.state.value`)."""
    for n in ast.walk(elt):
        if isinstance(n, ast.Attribute) and n.attr == "state":
            if isinstance(n.value, ast.Name) and n.value.id == loop_var:
                return True
    return False


def _comprehension_is_post_state_census(comp: ast.comprehension, elt: ast.AST) -> bool:
    var = _target_name(comp.target)
    if var is None:
        return False
    return _is_posts_iterable(comp.iter) and _elt_reads_state(elt, var)


def _call_is_hand_rolled_post_state_census(node: ast.Call) -> bool:
    if not _is_counter_call(node):
        return False
    for arg in node.args:
        if isinstance(arg, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
            if any(_comprehension_is_post_state_census(g, arg.elt) for g in arg.generators):
                return True
        elif isinstance(arg, ast.DictComp):
            # Counter({p.state: … for p in posts}) — rare, still a state census key.
            if any(_comprehension_is_post_state_census(g, arg.key) for g in arg.generators):
                return True
    return False


def _count_hand_rolled_post_state_censuses(tree: ast.AST) -> int:
    return sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call) and _call_is_hand_rolled_post_state_census(n))


def _count(path: Path) -> int:
    return _count_hand_rolled_post_state_censuses(
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    )


def _scan() -> dict[str, int]:
    out: dict[str, int] = {}
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        if rel in _EXEMPT:
            continue
        c = _count(path)
        if c:
            out[rel] = c
    return out


def test_zero_hand_rolled_post_state_censuses_outside_ledger():
    """Binary: zero occurrences of the flagged shape outside the owner. Permanent by policy."""
    actual = _scan()
    assert actual == {}, f"hand-rolled PostState Counter census outside ledger.py: {actual} — {_FIX}"


def test_negative_control_planted_counter_is_detected():
    """Negative control: a planted Counter(p.state…) must FAIL the check (count > 0).

    Without this, a finder that always returns 0 would pass green forever. The plant is a source
    string — not committed product code — so the tree stays clean while the detector is proven.
    """
    planted = (
        "from collections import Counter\n"
        "st = Counter(p.state for p in led.posts.values())\n"
        "st2 = Counter(p.state.value for p in led.posts.values())\n"
    )
    assert _count_hand_rolled_post_state_censuses(ast.parse(planted)) == 2


def test_owned_apis_and_predicates_are_not_flagged():
    """Boundary proof: owned APIs, per-post predicates, non-state Counters, clip censuses stay green."""
    allowed = (
        "n = len(led.posts_in_state(PostState.queued))\n"
        "st = led.state_histogram()\n"
        "ok = p.state is PostState.awaiting_approval\n"
        "posts = [p for p in led.posts.values() if p.state is PostState.queued]\n"
        "by_account = Counter(p.account for p in led.posts.values())\n"
        "clip_st = Counter(c.state for c in led.clips.values())\n"
        "mom_st = Counter(m.state.value for m in led.moments.values())\n"
    )
    assert _count_hand_rolled_post_state_censuses(ast.parse(allowed)) == 0
