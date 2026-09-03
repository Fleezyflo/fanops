"""MOL-817 — one shared failed→queued re-arm write; four public entry points remain.

P1–P4 structural gates. Behavioural coverage for the four verbs stays in test_rearm_refusal /
test_studio_actions / test_studio_gaps_closure — this file only pins the collapse.
"""
from __future__ import annotations

import ast
from pathlib import Path

import fanops.studio.actions as actions
import fanops.studio.actions_recover as actions_recover

_ACTIONS = Path(actions_recover.__file__).resolve()
_SRC = _ACTIONS.read_text()
_TREE = ast.parse(_SRC)


def _funcs():
    return {n.name: n for n in _TREE.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_four_public_rearm_entry_points_survive():
    for name in ("retry_rate_limited_failures", "retry_oversize_failures",
                 "retry_transient_failures", "recover_posts"):
        assert hasattr(actions, name) and callable(getattr(actions, name))


def test_one_rearm_body_owner():
    """P1: the three-field re-arm write lives in exactly one helper; paced rate-limit retry lives in run.py."""
    funcs = _funcs()
    assert "_rearm_to_queued" in funcs, "missing sole re-arm write owner"
    helper = funcs["_rearm_to_queued"]
    # helper owns set_post_state(..., queued, error_reason=None) + submission_id clear
    helper_src = ast.get_source_segment(_SRC, helper) or ""
    assert "set_post_state" in helper_src and "PostState.queued" in helper_src
    assert "error_reason=None" in helper_src
    assert "submission_id" in helper_src
    # no other function in this file may call set_post_state(..., PostState.queued, ...)
    # retry_rate_limited_failures delegates to post.run (paced); three remaining verbs re-arm here.
    queued_owners = []
    for name, node in funcs.items():
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            if isinstance(func, ast.Attribute) and func.attr == "set_post_state":
                args_src = ast.get_source_segment(_SRC, sub) or ""
                if "PostState.queued" in args_src:
                    queued_owners.append(name)
    assert queued_owners == ["_rearm_to_queued"], queued_owners
    # four call sites
    calls = [n for n in ast.walk(_TREE)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_rearm_to_queued"]
    assert len(calls) == 3, f"expected 3 _rearm_to_queued call sites, got {len(calls)}"


def test_refuse_retired_sole_can_promote_and_no_retired_enum():
    """P2/P3/P4: _refuse_retired stays sole; can_promote only there; PostState.retired absent."""
    funcs = _funcs()
    assert list(n for n in funcs if n == "_refuse_retired") == ["_refuse_retired"]
    can_promote_owners = []
    for name, node in funcs.items():
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and sub.attr == "can_promote":
                can_promote_owners.append(name)
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "can_promote":
                can_promote_owners.append(name)
    assert set(can_promote_owners) == {"_refuse_retired"}, can_promote_owners
    assert "PostState.retired" not in _SRC
