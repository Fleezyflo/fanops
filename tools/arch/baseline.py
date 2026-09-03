"""Approved governance baseline assembly.

`baseline --accept` is a deliberate, reviewed act — this module owns the payload shape; the CLI
owns argparse and file write.
"""
from __future__ import annotations

from . import policy as policy_mod
from .common import DERIVED, GOVERNANCE, load
from .deltas import lazy_only_edges


def build() -> dict:
    deps = load(DERIVED / "dependencies.json")
    must_stay_lazy = [list(e) for e in lazy_only_edges(deps)]

    _prev = load(GOVERNANCE / "baselines.json") if (GOVERNANCE / "baselines.json").exists() else {}
    approved_breaking = _prev.get("approved_breaking_changes", [])
    return {
        "$schema": "fanops-arch/governance/baselines/v1",
        "owner": "architecture governance (see governance/field_authority.json)",
        "how_to_change": "python -m tools.arch baseline --accept  — then explain WHY in the PR. "
                         "This file is a RATCHET. Re-accepting it silently defeats its purpose.",
        "approved_compile_cycles": [c for c in deps["G1_non_trivial_sccs"]],
        "approved_compile_cycles_note":
            "The ONLY compile-time import cycle in the tree. Load-order sensitive and UNDEFENDED "
            "(no comment, no test, no ADR). Its intentionality is UNKNOWN (UNK-C5-1). It is "
            "baselined because it EXISTS, not because it is endorsed.",
        "must_stay_lazy": must_stay_lazy,
        "must_stay_lazy_note":
            f"{len(deps['lazy_upward'])} strictly-upward + {len(deps['lazy_lateral'])} lateral "
            f"in-function imports. The SCC-condensed compile graph is an 11-level DAG ONLY because "
            f"these are deferred to call time. Hoisting any one to module level LOOKS LIKE A CLEANUP "
            f"and can break the process at start. This is GB-1, mechanized (rule ARCH-007).",
        "approved_breaking_changes": approved_breaking,
        "approved_breaking_changes_note":
            "Breaking facts a REVIEWED PR declared on purpose — each line is the reason string "
            "`impact` itself printed, verbatim. `impact --strict` fails on the breaking facts NOT "
            "listed here, so DELETING is allowed and deleting SILENTLY is not. UNKNOWN_IMPACT is "
            "never declarable. Entries self-retire: a removal reason can only be produced by the "
            "diff that performs the removal, so a stale line cannot mask a later change — it "
            "remains as the record of a deliberate break. THE ONLY AUTHOR-DECLARED KEY IN THIS "
            "FILE; every other one is derived, and `baseline --accept` carries this one forward.",
        "approved_terminal_post_writers": policy_mod._terminal_post_writers(),
        "approved_terminal_post_writers_note":
            "Every site WRITING PostState.published / PostState.analyzed with a LITERAL value. The "
            "R1 invariant fires at CONSTRUCTION ONLY; model_copy and setattr both bypass it, and "
            "four manual call-site guards hold the line. A FIFTH door saves cleanly and then BRICKS "
            "THE NEXT Ledger.load. This is GB-4, mechanized (rule IMPL-009). *** IT DOES NOT COVER "
            "THE DYNAMIC DOORS — PostState(<runtime>), model_copy(update=…), setattr(…). That blind "
            "spot is reported on EVERY run rather than hidden. ***",
        "required_verifications_present": sorted(
            policy_mod._verification_matrix_test_names() & policy_mod._tests_defined()),
        "required_verifications_note":
            "The tests the Cycle-6 verification matrix requires AND which currently EXIST. *** THIS "
            "IS EMPTY TODAY: no slice in the program has been implemented, so none of its ~25 "
            "required tests exists yet, and rule IMPL-006 is therefore ARMED ON NOTHING. *** Stated "
            "out loud rather than hidden. It ARMS ITSELF: the moment a slice lands and its tests "
            "appear, re-accept this baseline and their removal becomes CI-red.",
    }
