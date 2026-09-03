"""IMPL-009/010 AST guard checks (GB-3, GB-4)."""
from __future__ import annotations

import ast

from ..common import REPO, SRC
from .exceptions import _approved
from .rules import WARNING, Finding, _f

def _gb_checks() -> list[Finding]:
    """GB-3 and GB-4 — the two global boundaries that a static check can actually decide."""
    out: list[Finding] = []

    # GB-3 / IMPL-010 — extra="forbid" on a ledger model
    models = SRC / "models.py"
    if models.exists():
        tree = ast.parse(models.read_text(encoding="utf-8"))
        hits = []
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "extra" \
                    and isinstance(node.value, ast.Constant) and node.value.value == "forbid":
                hits.append(f"src/fanops/models.py:{node.value.lineno}")
        if hits:
            out.append(_f("IMPL-010",
                          "A ledger model sets extra='forbid'. Forward-compat (INV-19 / SHIM-005) "
                          "holds by pydantic's DEFAULT. This bricks a forward-rolled ledger.", hits))

    # GB-4 / IMPL-009 — the doors to a terminal Post state
    approved = _approved("approved_terminal_post_writers", default=None)
    if approved is not None:
        found = _terminal_post_writers()
        extra = sorted(set(found) - set(approved))
        if extra:
            out.append(_f("IMPL-009",
                          f"{len(extra)} NEW write path(s) to PostState.published/analyzed. The R1 "
                          f"invariant fires at construction only; model_copy and setattr both bypass "
                          f"it. A door without an explicit non-empty public_url guard saves cleanly "
                          f"and BRICKS THE NEXT Ledger.load.", extra))

    # *** THE COVERAGE BOUNDARY OF IMPL-009, STATED EVERY RUN. ***
    # The rule above sees only LITERAL `PostState.published` writes. It is BLIND to the DYNAMIC
    # shapes — `PostState(<str>)`, `model_copy(update=<var>)`, `setattr(p, <var>, v)` — which are
    # precisely the writers the KB flags as invisible to a literal grep (5 of the 21 PostState
    # writers). Saying "GB-4 is mechanized" without saying this would BE the defect this whole
    # system exists to prevent: naming a mechanism that does not do what its name implies.
    dyn = _dynamic_state_writers()
    if dyn:
        out.append(Finding(
            rule="IMPL-009", severity=WARNING,
            title="IMPL-009's BLIND SPOT — the dynamic doors, stated every run",
            detail=f"IMPL-009 baselines only the LITERAL doors (4 of them). It is BLIND to "
                   f"{len(dyn)} site(s) that write a `.state` field through a shape no static check "
                   f"can decide: PostState(<runtime value>), model_copy(update=…), setattr(…). A new "
                   f"terminal-state door added through ANY of these WILL NOT BE CAUGHT by this rule. "
                   f"This inventory is deliberately OVER-inclusive: understating a blind spot is the "
                   f"exact failure this system exists to prevent. It independently reproduces all "
                   f"five writers kb/ownership.json flags as invisible to a literal grep.",
            evidence=dyn,
            remediation="Review these by hand when changing Post state semantics. Closing this "
                        "properly needs a Post lifecycle state machine — which the contract "
                        "deliberately DEFERS (§10) until S03/S04/S06 settle the semantics, so the "
                        "machine would encode a known-correct contract rather than the current one."))
    return out


def _dynamic_state_writers() -> list[str]:
    """Writers of `Post.state` whose VALUE is not a literal — the doors IMPL-009 cannot see.

    Scoped to modules that actually reference `PostState`. A `setattr` in config.py is not a door
    to a terminal Post state, and reporting it as one would be noise — and noise is how a warning
    becomes something people scroll past.

    This independently reproduces kb/ownership.json's own list of the five GENERIC/DYNAMIC writers
    "a literal grep cannot see" (cli.py:395, actions.py:870, run.py:357, reconcile.py:725,
    pipeline.py:151).
    """
    out: list[str] = []
    for py in sorted(SRC.rglob("*.py")):
        try:
            text = py.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(py))
        except SyntaxError:
            continue
        if "PostState" not in text:
            continue          # the module cannot write a Post state it never names
        rel = py.relative_to(REPO).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                # PostState(<expr>) — constructing the enum from a value known only at runtime
                if isinstance(fn, ast.Name) and fn.id == "PostState" and node.args \
                        and not isinstance(node.args[0], ast.Constant):
                    out.append(f"{rel}:{node.lineno}  PostState(<dynamic>)")
                if isinstance(fn, ast.Attribute) and fn.attr == "model_copy":
                    for kw in node.keywords:
                        if kw.arg != "update":
                            continue
                        if not isinstance(kw.value, ast.Dict):
                            # model_copy(update=<variable>) — payload entirely unknown statically
                            out.append(f"{rel}:{node.lineno}  model_copy(update=<variable>)")
                        else:
                            # model_copy(update={"state": <variable>}) — the shape kb/ownership.json
                            # names at pipeline.py:151. A dict literal whose VALUE is not.
                            for k, v in zip(kw.value.keys, kw.value.values):
                                if isinstance(k, ast.Constant) and k.value == "state" \
                                        and not _is_terminal(v) and not isinstance(v, ast.Constant):
                                    out.append(f"{rel}:{node.lineno}  model_copy(update={{'state': <variable>}})")
                if isinstance(fn, ast.Name) and fn.id == "setattr" and len(node.args) == 3 \
                        and not isinstance(node.args[1], ast.Constant):
                    out.append(f"{rel}:{node.lineno}  setattr(<dynamic attr>)")
            if isinstance(node, ast.Assign) and not _is_terminal(node.value):
                for t in node.targets:
                    if isinstance(t, ast.Attribute) and t.attr == "state" \
                            and isinstance(node.value, ast.Name):
                        out.append(f"{rel}:{node.lineno}  .state = <variable>")
    return sorted(set(out))


def _is_terminal(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr in ("published", "analyzed")
            and isinstance(node.value, ast.Name) and node.value.id == "PostState")


def _terminal_post_writers() -> list[str]:
    """Every site that WRITES PostState.published / .analyzed, as `file:line`.

    WRITES only — a READ (`if p.state is PostState.published`) is not a door. Counting reads
    would make the rule fire on every comparison anyone adds, and a rule that cries wolf is a rule
    somebody mutes. The four shapes that actually write the field:

        p.state = PostState.published                    an assignment
        Post(state=PostState.published, ...)             a construction
        p.model_copy(update={"state": PostState.published})   the validator-bypassing door
        setattr(p, "state", PostState.published)         the dynamic door
    """
    out: list[str] = []
    for py in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:
            continue
        rel = py.relative_to(REPO).as_posix()
        for node in ast.walk(tree):
            # p.state = PostState.published
            if isinstance(node, ast.Assign) and _is_terminal(node.value):
                out.append(f"{rel}:{node.lineno}")
            # Post(state=PostState.published) / model_copy(update=...) via keyword
            elif isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "state" and _is_terminal(kw.value):
                        out.append(f"{rel}:{node.lineno}")
                # setattr(p, "state", PostState.published)
                if isinstance(node.func, ast.Name) and node.func.id == "setattr" \
                        and len(node.args) == 3 and _is_terminal(node.args[2]):
                    out.append(f"{rel}:{node.lineno}")
            # {"state": PostState.published}  — the model_copy(update=) shape
            elif isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value == "state" and _is_terminal(v):
                        out.append(f"{rel}:{node.lineno}")
    return sorted(set(out))
