"""Policy rule registry and finding types."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..common import ARCH

BLOCKING = "BLOCKING"
WARNING = "WARNING"
INFO = "INFO"


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    rationale: str
    scope: str
    severity: str
    enforcement: str
    remediation: str
    exception_process: str = "Add an entry to .reports/architecture/governance/exceptions.json " \
                             "with owner, justification, risk, mitigation, expiry and removal plan. " \
                             "An undocumented suppression is forbidden."


@dataclass
class Finding:
    rule: str
    severity: str
    title: str
    detail: str
    evidence: list[str] = field(default_factory=list)
    remediation: str = ""
    suppressed_by: str | None = None


RULES: dict[str, Rule] = {r.id: r for r in [
    # ── THE PRECONDITION ────────────────────────────────────────────────────────────────────
    Rule("GOV-001", "The canonical artifacts must be PRESENT",
         "*** A GATE THAT PASSES BECAUSE ITS INPUTS ARE MISSING IS THE WORST KIND OF DECORATION: it "
         "is a green check that checks nothing, and it is indistinguishable from a real one. *** "
         "This is not hypothetical. `.reports/architecture/` was in .gitignore — the ENTIRE "
         "knowledge base of Cycles 1-6 was NOT IN THE REPOSITORY. CI would have found no kb/, no "
         "contract/, no derived/, silently skipped every check that reads them, and gone GREEN.",
         ".reports/architecture/{kb,contract,governance,derived}", BLOCKING,
         "every canonical artifact this policy set reads must exist; a missing one is a FAILURE, "
         "never a skip",
         "The artifacts are tracked. Restore them (`git checkout .reports/architecture/`), and "
         "confirm .gitignore still carries the `!.reports/architecture/` negation."),

    # ── ARCHITECTURE ────────────────────────────────────────────────────────────────────────
    Rule("ARCH-001", "The subsystem partition is TOTAL",
         "kb/subsystems.json asserts every module is in exactly one subsystem. A module with no "
         "subsystem has no owner, no risk profile, and no reviewer — it is invisible to every "
         "claim the KB makes.",
         "kb/subsystems.json vs derived/modules.json", BLOCKING,
         "derived module set == declared partition domain",
         "Assign the module to a subsystem in kb/subsystems.json, then re-run the regeneration."),

    Rule("ARCH-002", "No ghost modules",
         "A module the KB declares but which does not exist on disk means every downstream claim "
         "about it is about nothing.",
         "kb/subsystems.json", BLOCKING,
         "declared partition domain ⊆ derived module set",
         "Remove the module from kb/subsystems.json."),

    Rule("ARCH-003", "The declared environment surface equals the one the code reads",
         "The env surface is a trust boundary and the operator is a documented hand-editor (AR-09). "
         "An undeclared var is an undocumented input to a live system; a declared var nothing reads "
         "is a phantom switch an operator will try to set. Both directions are checked — the "
         "one-directional version let FANOPS_HASHTAG_TRENDS sit declared and unread for a year.",
         "kb/configuration.json vs derived/configuration.json", BLOCKING,
         "derived env-read set == declared env set",
         "Add the variable to kb/configuration.json (and docs/CONFIG.md), or remove the read; "
         "for a phantom, delete the name from kb/configuration.json."),

    Rule("ARCH-004", "No new compile-time import cycle",
         "A compile-time cycle is a HARD load-order constraint that can become an ImportError at "
         "process start. Exactly one exists today (personas ↔ persona_store ↔ persona_research, "
         "UNK-C5-1) and it is undefended. A second one must not appear silently.",
         "derived/dependencies.json G1", BLOCKING,
         "the set of non-trivial G1 SCCs must equal the approved set",
         "Break the cycle, or defer one import into a function body (and pin it in ARCH-007's baseline)."),

    Rule("ARCH-005", "UNKNOWNs cannot grow without approval",
         "Every UNKNOWN is tracked architectural debt. Silent growth converts an audit into a backlog.",
         "governance/unknowns.json", BLOCKING,
         "open unknown count <= approved ceiling",
         "Close the unknown, or raise the ceiling explicitly in governance/unknowns.json with a rationale."),

    Rule("ARCH-006", "Generated artifacts are never hand-edited",
         "A generated file that has been hand-edited is a fork of the truth that regeneration will "
         "silently destroy — or worse, that nobody regenerates because the diff is noisy.",
         "derived/**", BLOCKING,
         "regeneration is byte-identical to the committed bytes (drift.stale_artifacts over "
         "derived/ — the only generated surface since the governance doc's deletion, 2026-07)",
         "Re-run `python -m tools.arch regen` and commit. Never edit derived/ by hand."),

    Rule("ARCH-007", "A lazy import may not be hoisted to module level (GB-1)",
         "Many lazy (in-function) import edges point to an equal-or-higher layer level, and dozens are "
         "STRICTLY UPWARD. The layered DAG holds ONLY because those imports are deferred to call time — "
         "a low, heavily-depended-on module like `config` reaches UP to `accounts`. Hoisting any one "
         "LOOKS LIKE A CLEANUP and can break the process at start. (The exact counts live in "
         "derived/dependencies.json; a number copied into this prose is the very defect this system "
         "exists to catch, so none is written here.)",
         "governance/layering_baseline.json vs derived/dependencies.json", BLOCKING,
         "no edge pinned as must-stay-lazy may appear in the COMPILE graph",
         "Keep the import inside the function body. If the hoist is genuinely correct, accept a new "
         "baseline deliberately: `python -m tools.arch baseline --accept` (a reviewed change)."),

    Rule("ARCH-008", "Every side effect stays under an approved ceiling",
         "Subprocess, network, ledger-transaction, mkdtemp, rmtree and env-write sites are the "
         "system's blast radius. derived/side_effects.json counts them on every regen. "
         "governance/side_effect_ratchet.json holds the GB-6 shape — an approved ceiling plus a "
         "declared module allowlist — as structured fields (not prose scraped by regex). A new site "
         "raises the census past the ceiling (or lands in an unlisted module) and fails CI until a "
         "human raises the ceiling and lists the module in the same PR. "
         "*** WHAT THIS RULE STILL FORBIDS: restating an AST-computable census in "
         "kb/side_effects.json `counts_AST_verified`. The ceiling is declared POLICY; the census "
         "stays machine-derived. Do not restore the hand-typed mirror #875 deleted. ***",
         "governance/side_effect_ratchet.json + kb/side_effects.json `counts_AST_verified`", BLOCKING,
         "each census total ≤ its ceiling; every module with a site is listed; counts_AST_verified "
         "holds no JSON number at any depth",
         "Raise the matching ceiling and list the module in governance/side_effect_ratchet.json, or "
         "remove the site. Never copy a census total into kb/ — record reviewed verdicts as PROSE."),

    Rule("ARCH-009", "A DECLARED artifact never restates a derived number",
         "The repository's signature defect, found in every one of five audit cycles, is: THE DOC "
         "NAMES A MECHANISM THAT DOES NOT EXIST. A number copied from code into prose is that "
         "defect in its cheapest form. This rule used to CROSS-CHECK seven hand-paired numbers and "
         "fail when they disagreed — which kept the copy alive, conflicting on every graph-touching "
         "PR, and (because the comparison was guarded on the key being PRESENT) let anyone disarm a "
         "pair by deleting it. Forbidding the copy is the only version that cannot be quietly "
         "narrowed. The same forbidding covers a number narrated as PROSE — an append-only changelog "
         "of derived deltas is a copy that every graph-touching PR must edit, so it conflicts every "
         "time and rots between conflicts. Implementation wins over prose; derived/ is the only "
         "reader.",
         "kb/dependencies.json `totals`, kb/subsystems.json `totality`", BLOCKING,
         "the watched block holds no JSON number at any depth, and no derived-delta changelog key",
         "Delete the number from the declared artifact and read the derived twin."),

    Rule("ARCH-010", "Unsupported constructs are recorded, never omitted",
         "A census is only as good as its query. An omitted construct is indistinguishable from an "
         "absent one — the failure that made Cycle 5 report 39 network sites when there were 15.",
         "derived/unsupported.json", INFO,
         "every construct the extractor cannot resolve is enumerated with evidence",
         "Extend the extractor, or accept the construct as an UNKNOWN in governance/unknowns.json."),

    # ── IMPLEMENTATION CONTRACT ─────────────────────────────────────────────────────────────
    Rule("IMPL-001", "A slice owns only the files the contract grants it",
         "Two slices editing one file with no declared partition is how one silently widens into "
         "the other. File-level ownership must be total and unambiguous.",
         "contract/file_ownership.json", BLOCKING,
         "every changed file in a slice's diff appears in that slice's allowance",
         "Add the file to the slice's allowance in file_ownership.json (a reviewed scope change), "
         "or take it out of the diff."),

    Rule("IMPL-002", "A slice boundary must be a machine-checkable predicate, not prose",
         "`permitted_functions: ['the daemon tick loop (:1300-1313)']` cannot be enforced by any "
         "machine. A boundary that only a human can evaluate is enforced by attention, and this "
         "codebase has taught us exactly what attention is worth.",
         "contract/file_ownership.json partitions", WARNING,
         "every permitted_functions entry resolves to a function that exists (or is marked planned)",
         "Rewrite the entry as a bare function identifier. See the migration plan in the runbook."),

    Rule("IMPL-003", "The implementation DAG is acyclic",
         "An ordering cycle makes the sequence unexecutable. Note that CO-REQUIREMENTS are NOT "
         "ordering edges — modelling them as such would MANUFACTURE a cycle that does not exist "
         "(the C5-SC-2 error, applied to the implementation graph).",
         "contract/implementation_contract.json", BLOCKING,
         "Tarjan over the ordering edges returns only singleton SCCs",
         "Remove the back edge, or re-model it as a co-requirement if that is what it is."),

    Rule("IMPL-004", "No orphaned root cause",
         "Cycle 4 had ten root causes and ten slices and it LOOKED like a bijection. RC-4+RC-5 "
         "collapse into S01, so RC-9 mapped to NOTHING — deferred, then simply untracked. A "
         "deferral is not a discharge.",
         "contract/traceability.json", BLOCKING,
         "every root cause maps to >=1 slice, or to a recorded human decision to defer",
         "Add a slice (a GUARD slice suffices for an unreachable root cause), or record the deferral."),

    Rule("IMPL-005", "Every slice has a rollback class and a verification set",
         "'Revert' is not one thing. CODE_REVERSIBLE, DATA_IRREVERSIBLE and WORLD_IRREVERSIBLE are "
         "different promises, and two of this program's slices are not simply revertible.",
         "contract/rollback_matrix.json, contract/verification_matrix.json", BLOCKING,
         "every non-blocked slice appears in both matrices",
         "Add the slice's rollback class and its verification set."),

    Rule("IMPL-006", "Required verification cannot disappear",
         "A test that vanishes takes its invariant with it, silently.",
         "contract/verification_matrix.json", BLOCKING,
         "every INVARIANT test named by the matrix still exists once its slice is merged",
         "Restore the test, or record its removal as an explicit contract change."),

    Rule("IMPL-007", "The ratchet budgets the contract COPIES must match the tests that ENFORCE them",
         "The contract pins the cli.py print budget as a load-bearing, exact-equality budget shared "
         "across three slices. Its copy once went stale in a single commit while the enforcing test "
         "moved on — which is the whole reason this rule exists. The authoritative number lives in the "
         "CI test and in derived/ratchets.json; it is deliberately NOT written here as an assignment. "
         "SOURCE OF TRUTH (one chain, no second opinion): measured in src/fanops/cli.py -> declared ONCE in "
         "tests/test_internal_prints_routed.py -> generated into derived/ratchets.json -> mirrored in exactly "
         "ONE declared contract copy (contract/implementation_contract.json GB-6) which this rule holds to the "
         "test. Every other LIVING governance document references it symbolically and carries no literal to "
         "rot; NAMED Cycle-6 historical snapshots (_HISTORY) keep their ORIGINAL value as prose and are never "
         "rewritten when cli.py changes. The assignment form remains a LIVE CLAIM everywhere else.",
         "contract/implementation_contract.json GB-6 vs derived/ratchets.json", BLOCKING,
         "contract's declared ratchet numbers == the numbers in the CI test files",
         "Update the contract's GB-6 block from derived/ratchets.json. The TEST is authoritative."),

    Rule("IMPL-008", "A slice must trace to an approved root cause",
         "Every code modification must be traceable to an approved root cause, or it is a hidden "
         "scope expansion. S12 traces to AR-04, a RISK — which is why it is marked PROPOSED and "
         "gated on PD-5 rather than smuggled into the program.",
         "contract/implementation_contract.json", BLOCKING,
         "slice.root_causes is non-empty, or slice.status is PROPOSED/BLOCKED",
         "Add the root cause, or mark the slice PROPOSED and surface it as a product decision."),

    Rule("IMPL-009", "No new unguarded door to a terminal Post state (GB-4)",
         "The R1 published-URL invariant fires at CONSTRUCTION only. `model_copy` and `setattr` both "
         "bypass it. Four manual call-site guards hold the line. A FIFTH door saves cleanly and then "
         "BRICKS THE NEXT Ledger.load — taking down the daemon and every Studio page at once.",
         "src/fanops/**", BLOCKING,
         "the set of write sites to PostState.published/analyzed equals the approved guarded set",
         "Add an explicit non-empty public_url guard at the call site, and pin it in the baseline."),

    Rule("IMPL-010", "No ledger model may set extra='forbid' (GB-3)",
         "Forward-compat holds by pydantic's DEFAULT, not by declaration. Setting `forbid` — a change "
         "that LOOKS LIKE TIGHTENING — turns a forward-rolled ledger into a hard ControlFileError and "
         "bricks every reader.",
         "src/fanops/models.py", BLOCKING,
         "no ConfigDict/model_config in models.py sets extra='forbid'",
         "Remove the setting. Forward-compat is load-bearing (SHIM-005)."),
]}


# ── the checks ──────────────────────────────────────────────────────────────────────────────
def _f(rule: str, detail: str, evidence: list[str] | None = None) -> Finding:
    r = RULES[rule]
    return Finding(rule=r.id, severity=r.severity, title=r.title, detail=detail,
                   evidence=evidence or [], remediation=r.remediation)


_REQUIRED_ARTIFACTS = (
    ("kb/subsystems.json", "the subsystem partition"),
    ("kb/dependencies.json", "the declared dependency model"),
    ("kb/configuration.json", "the declared env surface"),
    ("kb/side_effects.json", "the declared side-effect census"),
    ("contract/file_ownership.json", "the slice/file partition"),
    ("contract/implementation_contract.json", "the implementation contract"),
    ("contract/rollback_matrix.json", "the rollback matrix"),
    ("contract/verification_matrix.json", "the verification matrix"),
    ("contract/traceability.json", "root-cause traceability"),
    ("governance/baselines.json", "the pinned ratchet baselines"),
    ("governance/side_effect_ratchet.json", "the side-effect ceiling + allowlist"),
    ("governance/unknowns.json", "the UNKNOWN registry"),
    ("governance/exceptions.json", "the exception registry"),
)


def missing_canonical() -> list[str]:
    """Canonical artifacts this policy set READS and cannot function without."""
    return [f"{rel}  ({what})" for rel, what in _REQUIRED_ARTIFACTS
            if not (ARCH / rel).exists()]
