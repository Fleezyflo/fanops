"""IMPL-005/006 implementation contract checks."""
from __future__ import annotations

from ..common import CONTRACT, load
from ..ratchets import tests_defined, verification_matrix_test_names
from .exceptions import _approved
from .rules import Finding, _f

def _verification_matrix_test_names() -> set[str]:
    return verification_matrix_test_names()


def _tests_defined() -> set[str]:
    return tests_defined()


def _verification_persists() -> list[Finding]:
    """IMPL-006: a verification the contract requires, ONCE IT EXISTS, may not vanish.

    *** HONEST STATUS: this rule is currently ARMED ON ZERO TESTS. *** No slice in the Cycle-6
    program has been implemented, so none of the ~25 tests the verification matrix requires exists
    yet. The baseline is therefore empty and the rule cannot fire today.

    That is stated out loud rather than hidden, because a rule that silently protects nothing while
    APPEARING enforced is precisely the defect this system exists to catch (and which it caught in
    its own IMPL-007). This rule ARMS ITSELF automatically: the moment a slice lands and its tests
    appear, `python -m tools.arch baseline --accept` pins them, and their removal goes CI-red.
    """
    baseline = _approved("required_verifications_present", default=None)
    if not baseline:
        return []
    gone = sorted(set(baseline) - tests_defined())
    if gone:
        return [_f("IMPL-006",
                   f"{len(gone)} required verification(s) DISAPPEARED. A test that vanishes takes "
                   f"its invariant with it, silently.", gone)]
    return []


def _coverage(cs: dict) -> list[Finding]:
    """IMPL-005 / IMPL-006: a slice with no rollback class, or no verification, is a slice whose
    failure mode nobody has thought about."""
    out: list[Finding] = []
    rb = CONTRACT / "rollback_matrix.json"
    vm = CONTRACT / "verification_matrix.json"
    if not rb.exists() or not vm.exists():
        return out
    roll = set(load(rb).get("slices", {}))
    ver = set(load(vm).get("slices", {}))
    missing: list[str] = []
    for sid, s in cs["slices"].items():
        status = (s.get("status") or "").upper()
        if "BLOCKED" in status or "PROPOSED" in status:
            continue     # a blocked slice has no plan BY DESIGN — writing one would presuppose the decision
        if sid not in roll:
            missing.append(f"{sid}: no entry in rollback_matrix.json")
        if sid not in ver:
            missing.append(f"{sid}: no entry in verification_matrix.json")
    if missing:
        out.append(_f("IMPL-005",
                      f"{len(missing)} slice/matrix gap(s). A slice without a rollback CLASS has an "
                      f"unexamined failure mode; a slice without verification has an unproven one.",
                      missing))
    return out
