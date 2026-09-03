"""The executable policy engine.

Every architectural and implementation rule that this repository actually intends to hold, as a
predicate a machine evaluates — not a paragraph a reviewer is trusted to remember.

Each rule declares: id · rationale · scope · severity · enforcement · exception process · remediation.

──────────────────────────────────────────────────────────────────────────────────────────────
A WARNING, WRITTEN INTO THE ENGINE ITSELF

The tempting rule "every compile-time import points to a LOWER layer level" CANNOT FAIL. `level`
is *defined* as `1 + max(level(targets))` over the compile graph, so every compile edge points
down by construction (excepting within-SCC edges). A rule that cannot fail is not a rule; it is
decoration that makes a dashboard green.

This is exactly the trap Cycle 5 fell into and had to retract (`C5-SC-3`: "the condensed graph
CANNOT contain a violation by construction"). So `GB-1` — the boundary that forbids hoisting a
lazy import — is mechanized the only honest way: as a PINNED RATCHET over the edges that must
STAY lazy (ARCH-007). That is the same shape as the swallow and print ratchets this repo already
sustains in CI, and it actually fires.
──────────────────────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from .common import ARCH, CONTRACT, DERIVED, GOVERNANCE, KB, REPO, SRC, load

# Artifacts that are HISTORY, not live claims. A corrections record must keep saying what was true
# when it was written — retroactively editing an erratum so it matches today destroys the only
# account of what went wrong, which is the one thing an erratum is for. Named explicitly, never a
# wildcard, so this cannot quietly become an escape hatch for a doc that simply went stale.
# NAMED historical records — files that snapshot a PAST cycle's state and must keep saying what was true when
# they were written. Retroactively rewriting them to match today destroys the only account of what went wrong,
# and (because only the regex-matching line gets rewritten) leaves a document contradicting its own acceptance
# criteria — exactly the defect found in C6-S08/C6-S09, whose table row was rewritten to the current value
# while their acceptance criterion still read the Cycle-6 value. These carry their original values as PROSE.
# This is a small, EXPLICITLY NAMED list, never a wildcard, so it cannot become a loophole: every LIVE
# governance surface is still scanned, and a NEW prompt that pins a current value is still held to the test.
_HISTORY = frozenset({"CYCLE6_CORRECTIONS.md", "IMPLEMENTATION_READINESS.md", "C6-S08.md", "C6-S09.md"})

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


def check(derived_dir: Path | None = None) -> list[Finding]:
    """Evaluate every rule. Returns findings (already exception-filtered).

    `= None`, not `= DERIVED`: default args bind ONCE at import and cannot be redirected by the
    selftest fixture. See drift.stale_artifacts() for the control that trap silently defeated.
    """
    derived_dir = derived_dir or DERIVED
    out: list[Finding] = []

    # *** GOV-001 — THE PRECONDITION, EVALUATED FIRST AND SHORT-CIRCUITING. ***
    # Every check below reads a canonical artifact. If one is absent, the honest answer is FAILURE,
    # not "no findings". Returning early is deliberate: continuing would emit a handful of vacuous
    # passes alongside the real error and let a reader conclude the tree was mostly fine.
    gone = missing_canonical()
    if gone or not derived_dir.exists():
        if not derived_dir.exists():
            gone.append(f"{derived_dir.name}/  (the generated architecture — run `{'python -m tools.arch regen'}`)")
        return [_f("GOV-001",
                   f"{len(gone)} canonical artifact(s) this policy set depends on are ABSENT. Every "
                   f"check that reads them would otherwise SKIP SILENTLY and this gate would report "
                   f"success while verifying nothing.", gone)]

    D = lambda n: load(derived_dir / f"{n}.json")  # noqa: E731

    mods = D("modules")
    deps = D("dependencies")
    cfg = D("configuration")
    rat = D("ratchets")
    cs = D("contract_surface")
    uns = D("unsupported")

    # ARCH-001 / ARCH-002 — partition totality
    if mods["unassigned_modules"]:
        out.append(_f("ARCH-001",
                      f"{len(mods['unassigned_modules'])} module(s) belong to NO subsystem. "
                      f"kb/subsystems.json asserts a TOTAL partition, but the tree has "
                      f"{mods['totals']['modules']} module(s), {len(mods['unassigned_modules'])} unowned.",
                      [f"unassigned: {m}" for m in mods["unassigned_modules"]]))
    if mods["ghost_modules"]:
        out.append(_f("ARCH-002", f"{len(mods['ghost_modules'])} declared module(s) do not exist.",
                      mods["ghost_modules"]))

    # ARCH-003 — env vars declared
    kb_cfg = KB / "configuration.json"
    if kb_cfg.exists():
        # `env_vars` is a sorted ARRAY of declared names. It used to be an object whose rows mirrored
        # `read_at`/`reader_count` from derived/ — pure AST facts, frozen at Cycle 5 and stale in 75 of 79
        # rows within 200 commits. The duplicate was deleted rather than regenerated (a mechanism whose only
        # job is keeping a copy correct should not exist); read sites come from derived/, which regen owns.
        # set() over a list of names is the same set this line always built over the object's keys.
        declared_env = set(load(kb_cfg).get("env_vars", {}))
        derived_env = set(cfg["env_vars"])
        undeclared = sorted(derived_env - declared_env)
        if undeclared:
            out.append(_f("ARCH-003",
                          f"{len(undeclared)} environment variable(s) are READ by the code but not "
                          f"declared in kb/configuration.json.",
                          [f"{v}  read at {', '.join(cfg['env_vars'][v]['read_at'][:2])}" for v in undeclared]))
        # The reverse direction. Without it a declared name outlives its last reader silently — exactly how
        # FANOPS_HASHTAG_TRENDS survived with zero readers anywhere in the tree. This mirrors the stale-doc
        # half below, which has always checked its own reverse; only the kb side was one-directional.
        phantom = sorted(declared_env - derived_env)
        if phantom:
            out.append(_f("ARCH-003",
                          f"{len(phantom)} environment variable(s) are DECLARED in kb/configuration.json "
                          f"but read by nothing (phantom switch: the reader was removed, or the name "
                          f"never had one).",
                          [f"{v}  declared in kb/configuration.json, no os.getenv in the tree"
                           for v in phantom]))

    # ARCH-003 (G2) — the OPERATOR doc's env surface must ALSO match the reads. kb/configuration.json is the
    # machine-declared surface (above); docs/CONFIG.md is the hand-maintained operator reference and it rots
    # INDEPENDENTLY — a FANOPS_ var whose reader was deleted lingers as a stale row; a new read is never
    # documented. Compare the FANOPS_ names the doc MENTIONS to the FANOPS_ names actually read. A prose
    # mention IS a claim (a reader greps the doc by name), so the doc must name only REAL, read vars — this
    # also forbids narrating a nonexistent switch ("there is no FANOPS_X"), which still reads as real.
    config_md = REPO / "docs" / "CONFIG.md"
    if config_md.exists():
        read_fanops = {v for v in cfg["env_vars"] if v.startswith("FANOPS_")}
        doc_fanops = set(re.findall(r"FANOPS_[A-Z0-9_]+", config_md.read_text()))
        undocumented = sorted(read_fanops - doc_fanops)
        stale_doc = sorted(doc_fanops - read_fanops)
        if undocumented:
            out.append(_f("ARCH-003",
                          f"{len(undocumented)} FANOPS_* var(s) are READ but never named in docs/CONFIG.md.",
                          [f"{v}  read at {', '.join(cfg['env_vars'][v]['read_at'][:2])}" for v in undocumented]))
        if stale_doc:
            out.append(_f("ARCH-003",
                          f"{len(stale_doc)} FANOPS_* var(s) are named in docs/CONFIG.md but READ nowhere "
                          f"(stale doc: the reader was removed, or the doc names a var that never existed).",
                          [f"{v}  named in docs/CONFIG.md, no os.getenv in the tree" for v in stale_doc]))

    # ARCH-004 — no new compile-time cycle
    approved_cycles = _approved("approved_compile_cycles",
                                default=[["fanops.persona_research", "fanops.persona_store", "fanops.personas"]])
    actual_cycles = [sorted(c) for c in deps["G1_non_trivial_sccs"]]
    new_cycles = [c for c in actual_cycles if c not in [sorted(a) for a in approved_cycles]]
    if new_cycles:
        out.append(_f("ARCH-004",
                      f"{len(new_cycles)} NEW compile-time import cycle(s). A cycle here is a hard "
                      f"load-order constraint and can become an ImportError at process start.",
                      [" ↔ ".join(c) for c in new_cycles]))

    # ARCH-006 — generated artifacts unedited (byte-reproducibility) is checked by drift.py,
    # which owns regenerate-and-compare. Recorded here so the rule set is complete.

    # ARCH-007 — the layering ratchet (GB-1, mechanized)
    baseline = _approved("must_stay_lazy", default=None)
    if baseline is not None:
        pinned = {(e[0], e[1]) for e in baseline}
        compile_edges = {(s, t) for s, d in deps["edges"].items() for t in d["compile"]}
        hoisted = sorted(pinned & compile_edges)
        if hoisted:
            out.append(_f("ARCH-007",
                          f"{len(hoisted)} import(s) pinned as must-stay-LAZY are now MODULE-LEVEL. "
                          f"The 11-level DAG exists only because these are deferred to call time.",
                          [f"{s} -> {t}  (HOISTED)" for s, t in hoisted]))

    # ARCH-009 — a declared artifact may not restate a derived number
    out += _declared_numbers_forbidden("ARCH-009")

    # ARCH-010 — unsupported constructs (informational, but never hidden)
    if uns["totals"]["unsupported_constructs"]:
        out.append(_f("ARCH-010",
                      f"{uns['totals']['unsupported_constructs']} construct(s) the extractor cannot "
                      f"statically resolve. Recorded, not dropped.",
                      [f"{c['module']}:{c.get('line','?')} {c['kind']}" for c in uns["constructs"]]))

    # ── implementation contract ─────────────────────────────────────────────────────────────
    if cs.get("available"):
        missing = sorted(f for f, v in cs["files"].items() if v["missing"])
        if missing:
            out.append(_f("IMPL-001", f"{len(missing)} file(s) owned by a slice do not exist.", missing))

        prose = cs["unresolved_boundaries"]
        if prose:
            out.append(_f("IMPL-002",
                          f"{len(prose)} slice boundary/-ies are PROSE, not machine-checkable "
                          f"predicates. They cannot be enforced by CI as written.",
                          [f"{b['slice']} {b['file']}: {b['entry']!r}" for b in prose]))

        if not cs["dag"]["acyclic"]:
            out.append(_f("IMPL-003", "The implementation ordering graph contains a cycle.",
                          [" -> ".join(c) for c in cs["dag"]["cycles"]]))

        # IMPL-008 — every slice traces to a root cause, or is explicitly PROPOSED/BLOCKED
        untraced = []
        for sid, s in cs["slices"].items():
            if s["root_causes"]:
                continue
            status = (s.get("status") or "").upper()
            if "PROPOSED" in status or "BLOCKED" in status:
                continue
            untraced.append(sid)
        if untraced:
            out.append(_f("IMPL-008",
                          f"{len(untraced)} slice(s) trace to no approved root cause and are not "
                          f"marked PROPOSED. That is a hidden scope expansion.", untraced))

    # IMPL-004 — no orphaned root cause
    tr = CONTRACT / "traceability.json"
    if tr.exists():
        t = load(tr)
        mapped = set()
        for row in t.get("root_cause_to_completion", []):
            for k in ("slice", "slices"):
                v = row.get(k)
                if isinstance(v, str):
                    mapped.add(row["root_cause"])
                elif isinstance(v, list) and v:
                    mapped.add(row["root_cause"])
        all_rcs = {row["root_cause"] for row in t.get("root_cause_to_completion", [])}
        orphans = sorted(all_rcs - mapped)
        if orphans:
            out.append(_f("IMPL-004", f"{len(orphans)} root cause(s) map to no slice.", orphans))

    # IMPL-005 — every non-blocked slice has a rollback class AND a verification set
    if cs.get("available"):
        out += _coverage(cs)

    # IMPL-007 — the contract's ratchet copy vs the test's baseline
    out += _ratchet_drift(rat)

    # IMPL-009 / IMPL-010 — the two GB boundaries that are statically checkable
    out += _gb_checks()

    # ARCH-008 — side-effect ceiling ratchet + no census restated in kb/
    out += _side_effect_ratchet(D("side_effects"))
    out += _declared_numbers_forbidden("ARCH-008")

    # ARCH-005 — UNKNOWNs may not grow past the approved ceiling
    from .registries import unknown_growth
    open_, ceiling = unknown_growth()
    if open_ > ceiling:
        out.append(_f("ARCH-005",
                      f"Open UNKNOWNs grew to {open_}, above the approved ceiling of {ceiling}. "
                      f"Raising the ceiling is a statement that the system is LESS understood than "
                      f"it was — that should be hard to do quietly.",
                      [f"open: {open_}", f"approved ceiling: {ceiling}"]))

    # IMPL-006 — a required verification may not disappear
    out += _verification_persists()

    return _apply_exceptions(out)


def _verification_matrix_test_names() -> set[str]:
    """Every test NAME the verification matrix requires."""
    vm = CONTRACT / "verification_matrix.json"
    if not vm.exists():
        return set()
    names: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            n = node.get("name")
            if isinstance(n, str) and n.startswith("test_"):
                names.add(n)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(load(vm))
    return names


def _tests_defined() -> set[str]:
    """Every `def test_*` actually defined under tests/."""
    out: set[str] = set()
    tests = REPO / "tests"
    if not tests.exists():
        return out
    for py in tests.rglob("test_*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name.startswith("test_"):
                out.add(node.name)
    return out


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
    gone = sorted(set(baseline) - _tests_defined())
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


# ── ARCH-008 side-effect ratchet (GB-6 shape: ceiling + allowlist vs derived census) ────────
#
# Ceiling keys match derived/side_effects.json → totals. Section keys are the per-module maps.
# The ceiling is DECLARED POLICY (like governance/unknowns.json `approved_ceiling`); the census
# stays in derived/. Do not put per-site counts in the declared file — those are AST-computable
# and belong only in derived/.
_SIDE_EFFECT_RATCHET_KEYS = (
    ("subprocess_sites", "subprocess"),
    ("network_sites_literal_requests", "network"),
    ("ledger_transaction_sites", "ledger_transaction"),
    ("mkdtemp_sites", "mkdtemp"),
    ("rmtree_sites", "rmtree"),
    ("env_write_sites", "env_writes"),
)


def _side_effect_ratchet(se: dict) -> list[Finding]:
    """ARCH-008: derived census totals may not exceed the declared ceiling; every module that
    carries a site must be listed. Raising the ceiling without listing a new module (or listing
    without raising) still fails — both halves of the human decision must be visible in the diff.
    """
    path = GOVERNANCE / "side_effect_ratchet.json"
    # No `.exists()` guard: GOV-001 short-circuits on a missing required artifact first.
    doc = load(path)
    ceilings = doc.get("ceilings") or {}
    approved = doc.get("approved_sites") or {}
    totals = se.get("totals") or {}
    out: list[Finding] = []

    missing_ceilings = [k for k, _ in _SIDE_EFFECT_RATCHET_KEYS if k not in ceilings]
    if missing_ceilings:
        out.append(_f("ARCH-008",
                      f"{len(missing_ceilings)} side-effect ceiling(s) missing from "
                      f"governance/side_effect_ratchet.json. A key without a ceiling is unenforced.",
                      [f"missing ceiling: {k}" for k in missing_ceilings]))
        return out

    for key, section in _SIDE_EFFECT_RATCHET_KEYS:
        ceiling = ceilings[key]
        if not isinstance(ceiling, int) or isinstance(ceiling, bool):
            out.append(_f("ARCH-008",
                          f"Ceiling for {key} must be a structured integer, not prose.",
                          [f"{key}: {ceiling!r}"]))
            continue
        actual = int(totals.get(key, 0))
        modules = set((se.get(section) or {}).keys())
        allowed_raw = approved.get(key, [])
        allowed: set[str] = set()
        for entry in allowed_raw:
            if isinstance(entry, str):
                allowed.add(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("module"), str):
                allowed.add(entry["module"])
        undeclared = sorted(modules - allowed)
        if actual <= ceiling and not undeclared:
            continue
        evidence = [f"ceiling: {ceiling}", f"census: {actual}"]
        if undeclared:
            evidence += [f"undeclared module: {m}" for m in undeclared]
        else:
            # Over ceiling inside already-listed modules — name every module so the PR can see where.
            evidence += [f"module: {m}" for m in sorted(modules)]
        out.append(_f("ARCH-008",
                      f"Side-effect census for {key} is {actual}, above the approved ceiling of "
                      f"{ceiling}." if actual > ceiling else
                      f"{len(undeclared)} undeclared {key} module(s) — list each new module in "
                      f"governance/side_effect_ratchet.json when raising the ceiling.",
                      evidence))
    return out


# ── ARCH-008 / ARCH-009: derived facts may not be restated in a DECLARED artifact ───────────
#
# (rule id, kb file, block, human label). The blocks a hand-authored artifact used to mirror a
# derived census in. There is deliberately NO table of which numbers to check: whatever number the
# block contains is the violation, so the rule's coverage is the block's contents and cannot be
# narrowed by editing the block. What this replaced compared seven hand-paired keys under an
# `if declared_key in block` guard — five of kb/dependencies.json's twelve totals were never paired
# at all (two of those five had silently rotted), and deleting a paired key deleted its enforcement
# while the gate stayed green. A rule that stops firing is indistinguishable from one that passes.
#
# ARCH-008 ALSO runs `_side_effect_ratchet` above. The restatement forbid below is the half that
# keeps a derived number out of kb/; the ratchet is the half that fails a new site.
_FORBIDDEN_RESTATEMENT_BLOCKS = (
    ("ARCH-009", "dependencies.json", "totals", "kb/dependencies.json `totals`"),
    ("ARCH-009", "subsystems.json", "totality", "kb/subsystems.json `totality`"),
    ("ARCH-008", "side_effects.json", "counts_AST_verified",
     "kb/side_effects.json `counts_AST_verified`"),
)

# Keys whose CONTENT is a derived number narrated as prose, which the numeric test above cannot see.
# `updated_at_HEAD` was a single-line append-only changelog of graph deltas: every graph-touching PR
# appended a sentence to the same line, so it conflicted on every one of them and the resolution was
# semantic. `what_did_NOT_move` asserted a set of counts had held — and had already rotted uncaught
# ("lateral stays 51" against a derived 46). Git history is the changelog and cannot conflict with
# itself. An EXPLICITLY NAMED list, never a wildcard: a name added here only widens this rule, so
# unlike the pair table it replaced it has no failure mode where the rule quietly checks less.
_FORBIDDEN_DELTA_KEYS = frozenset({"updated_at_HEAD", "what_did_NOT_move"})


def _restated_facts(node: object, path: str) -> list[tuple[str, str]]:
    """Every place `node` restates a derived fact, as (dotted path, what it is)."""
    if isinstance(node, dict):
        out: list[tuple[str, str]] = []
        for k, v in node.items():
            here = f"{path}.{k}"
            if k in _FORBIDDEN_DELTA_KEYS:
                out.append((here, "a prose changelog of derived deltas"))
            else:
                out += _restated_facts(v, here)
        return out
    if isinstance(node, list):
        return [hit for i, v in enumerate(node) for hit in _restated_facts(v, f"{path}[{i}]")]
    if isinstance(node, bool):
        return []                                 # a JSON literal, not a census
    if isinstance(node, (int, float)):
        return [(path, f"a derived number ({node})")]
    return []


def _declared_numbers_forbidden(rule_id: str) -> list[Finding]:
    """ARCH-008 / ARCH-009: a DECLARED artifact may not restate a number the regen derives.

    The predicate is PRESENCE, not agreement. A comparison keeps the copy alive — it has to, or it
    has nothing to compare — and a live copy conflicts on every PR that moves the graph and rots
    between conflicts. Absence has neither failure mode, and it cannot be disarmed by deleting the
    field it guards, because the field's absence IS the passing state.

    No `.exists()` guard: GOV-001 evaluates first and short-circuits on a missing canonical
    artifact, so a skip here could only re-introduce the vacuous pass this whole rule set exists to
    make impossible.
    """
    out: list[Finding] = []
    for rid, fname, block, label in _FORBIDDEN_RESTATEMENT_BLOCKS:
        if rid != rule_id:
            continue
        bad = _restated_facts(load(KB / fname).get(block, {}), block)
        if bad:
            out.append(_f(rid,
                          f"{len(bad)} derived fact(s) restated in {label}. A number copied out of "
                          f"the code is wrong the moment the code moves, and it conflicts on every "
                          f"PR that moves it. The derived twin does neither.",
                          [f"{where}: {what}" for where, what in sorted(bad)]))
    return out


def _ratchet_drift(rat: dict) -> list[Finding]:
    out: list[Finding] = []
    ic = CONTRACT / "implementation_contract.json"
    if not ic.exists():
        return out
    gb6 = load(ic).get("GLOBAL_BOUNDARIES", {}).get("GB-6_ast_ratchet_budgets", {})
    declared_cli = rat["declared_by_the_ci_tests"]["print"].get("cli_print_count")

    # *** READ THIS BEFORE YOU "SIMPLIFY" IT. ***
    # The first version of this parser split the sentence on '=' and took the first token where
    # `.isdigit()` was true. In the real contract the number is written as a `_CLI_PRINT_COUNT`
    # assignment INSIDE BACKTICKS — so the token carried a trailing backtick, `.isdigit()` was False,
    # and the parser extracted NOTHING. The rule silently no-opped. It would NEVER have caught the
    # stale-copy drift that motivated this entire cycle; that was found by hand.
    #
    # A negative control (NC-15) is the only reason anybody knows. This is `AR-03` — "a check whose
    # name promises what its assertion does not deliver" — occurring INSIDE THE GOVERNANCE SYSTEM.
    # The root cause is that the number lives in PROSE. The regex is the patch; the fix is the
    # migration (store ratchet budgets as structured fields, never as a sentence).
    pr = str(gb6.get("print_ratchet", {}).get("mechanism_B", ""))
    m = re.search(r"_CLI_PRINT_COUNT\s*=\s*(\d+)", pr)
    contract_num = int(m.group(1)) if m else None
    if contract_num is None and pr:
        out.append(_f("IMPL-007",
                      "The contract's print-ratchet budget could not be PARSED at all. A budget "
                      "nobody can read is a budget nobody enforces — and a rule that silently "
                      "extracts nothing reports success. Store it as a structured field.",
                      [f"unparseable: {pr[:90]}"]))

    # *** EVERY LIVE COPY, not just the one this rule happened to know about. ***
    #
    # The rule originally read ONLY contract/implementation_contract.json. The number turned out to
    # exist in NINE places across the KB, holding FOUR DIFFERENT VALUES — and the two worst were
    # `contract/prompts/C6-S08.md` and `C6-S09.md`, the LIVE IMPLEMENTATION PROMPTS handed to whoever
    # builds those slices. They pinned a `_CLI_PRINT_COUNT` assignment to a now-stale value and told the
    # implementer NOT to change the count. An implementer obeying that prompt writes the wrong constant
    # and CI goes red for a reason unrelated to their change — the precise failure GB-6/IR-4 prevents.
    # A rule named "the ratchet budgets the contract COPIES must match the tests that ENFORCE them"
    # was reporting green throughout. Checking ONE copy of a duplicated number is not enforcement;
    # it is a rule scoped to the place its author happened to remember.
    #
    # THE ASSIGNMENT FORM IS A LIVE CLAIM. `_CLI_PRINT_COUNT = N` anywhere in a declared artifact
    # asserts a current fact and is held to the test. Prose *about* the past ("the contract once
    # pinned it at one four seven") is narrative and is not a claim — write history as prose.
    #
    # _HISTORY is excluded because a corrections record MUST keep saying what was true when it was
    # written; retroactively editing an erratum to match today destroys the only account of what
    # went wrong. It is a small, named list, not a wildcard, so it cannot become a loophole.
    #
    # G1: the scan was `.reports/architecture/`-ONLY, so the engine COULD NOT SEE ITSELF — a stale
    # `_CLI_PRINT_COUNT = <n>` assignment in tools/arch/'s own rationales, or in docs/, went unwatched
    # (the very thing the rule checks for, in the file that does the checking). Widened to tools/arch/
    # and docs/, and to .py, so every live copy is held to the test. `selftest.py` is EXCLUDED because
    # it INJECTS a deliberately-wrong assignment as the NC-15 fixture — scanning it would fire the rule
    # on the negative control's own payload. docs/ is scanned too: if any doc states a stale copy of
    # the budget, this rule catches it.
    _scan_exclude = _HISTORY | {"selftest.py"}
    _scanned: set = set()
    for root in (ARCH, REPO / "tools" / "arch", REPO / "docs"):
        if not root.exists():
            continue                                  # a fixture may copy only a subset of the roots
        for path in sorted(root.rglob("*")):
            if path in _scanned or path.suffix not in (".json", ".md", ".py") or not path.is_file():
                continue
            _scanned.add(path)
            if path.name in _scan_exclude or DERIVED in path.parents:
                continue
            try:
                blob = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for found in sorted({int(x) for x in re.findall(r"_CLI_PRINT_COUNT\s*=\s*(\d+)", blob)}):
                if declared_cli is None or found == declared_cli:
                    continue
                out.append(_f("IMPL-007",
                              "A copy of the cli.py print budget is STALE. It is pinned as a "
                              "load-bearing, exact-equality budget shared by three slices (GB-6 / IR-4) "
                              "— a wrong value makes the boundary unenforceable and would fail a slice "
                              "for a reason unrelated to its change.",
                              [f"{path.relative_to(REPO).as_posix()} says _CLI_PRINT_COUNT = {found}",
                               f"tests/test_internal_prints_routed.py says {declared_cli}",
                               f"measured in src/fanops/cli.py: {declared_cli}",
                               "the TEST is authoritative; the declared copy rotted"]))

    # the per-file swallow ceilings the contract restates
    ceilings = gb6.get("swallow_ratchet", {}).get("budget_ceiling__must_not_exceed", {})
    base = rat["declared_by_the_ci_tests"]["swallow"].get("baseline", {})
    bad = [(f, spec.get("baseline"), base.get(f))
           for f, spec in sorted(ceilings.items())
           if isinstance(spec, dict) and f in base and spec.get("baseline") != base[f]]
    if bad:
        out.append(_f("IMPL-007",
                      "The contract's swallow ceilings disagree with the test that enforces them.",
                      [f"{f}: contract {a}, test {b}" for f, a, b in bad]))
    return out


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


# ── the approved baselines (DECLARED, human-owned, reviewed) ─────────────────────────────────
def _approved(key: str, default):
    p = GOVERNANCE / "baselines.json"
    if not p.exists():
        return default
    return load(p).get(key, default)


# ── exceptions ──────────────────────────────────────────────────────────────────────────────
def _apply_exceptions(findings: list[Finding]) -> list[Finding]:
    from .registries import active_exceptions
    active = active_exceptions()
    for f in findings:
        for exc in active:
            if exc["rule"] != f.rule:
                continue
            scope = exc.get("scope", "")
            if scope in ("*", "") or any(scope in e for e in f.evidence) or scope in f.detail:
                f.suppressed_by = exc["id"]
                f.severity = INFO
                break
    return findings


def blocking(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == BLOCKING and not f.suppressed_by]
