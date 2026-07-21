"""`python -m tools.contract <verb>` — the compiler and verifier command surface, and the pipeline.

    triggers    do I need a contract at all?  (the first thing an agent runs)
    compile     resolve the derivable fields and print them for the author
    verify      the full pipeline to a decision
    scope       ADR-0105 §5.3 alone — the anti-silent-scope-expansion check
    state       derived lifecycle state and per-gate status
    digest      `D` — the value an operator's approval names
    template    print the declaration skeleton to stdout
    selftest    run the negative controls

EVERY VERB IS READ-ONLY. Nothing here writes into the repository — deliberately unlike `tools/arch`,
whose `regen`, `docs` and `baseline --accept` do. A tool that can write the artifact it validates
would let an agent satisfy the gate by editing the evidence, and this tool's entire value is that it
cannot be satisfied that way.

NINE STAGES, and determinism lives at S8's input boundary: every piece of I/O happens upstream and is
frozen into plain data before `decide()` sees it.

    S1 load · S2 split+digest · S3 parse · S4 admit · S5 derive · S6 validate · S7 gates ·
    S8 decide · S9 report

WHICH BYTES ARE AUTHORITATIVE, STATED EXACTLY. `verify` and `scope` read the contract BLOB at `--head`
when one is given — ADR-0105 §11.1 makes the blob at the approved head authoritative for a GATE — and
read the WORKING TREE when it is not, because `--head` defaults to `None`. Both paths are supported
and neither is a mistake: a gate passes `--head`, an author checking their edits does not.

An earlier wording of this paragraph said only the first half, and that omission was load-bearing.
`main_blob` — the landed copy every append-only check compares against — used to be fetched only
`if head is not None`, which read as consistent with a docstring claiming `--head` was always in play.
It was not: the DEFAULT command took the working-tree path with the comparison silently disabled, so
`LIFECYCLE-REWRITTEN` and `DECL-DIVERGED` could not be produced by the shipped CLI at all. The landed
copy is now read INDEPENDENTLY of `head`, because *which artifact is under evaluation* and *whether a
landed copy exists to compare it against* are different questions and coupling them lost one of them.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import classify, derive, lifecycle, report, validate
from .adapters import (REPO, ArtifactPort, ImpactPort, MergeFactsPort, PortError, RegistryPort,
                       RepoPort, required_contexts_at, workflow_job_binding)
from .decide import HEAD, MERGE, PRE, decide
from .model import (CheckRun, CI_REGISTRY_PATH, EXIT_CONTINUE, EXIT_UNTRUSTWORTHY,
                    MAIN_REF as MODEL_MAIN_REF, DecisionInput, Derived, MergeFacts)
from .parse import BOUNDARY, digest as digest_of, parse

CONTRACTS = "docs/contracts"

# The landed record every append-only check compares against (ADR-0105 §3.6, §4.4). A remote-TRACKING
# ref, so the read is git-local: no network, no `gh`, no GitHub dependence in a git integrity rule.
MAIN_REF = MODEL_MAIN_REF                          # re-exported; defined in `model` (imports nothing)


class Ports:
    """The five ports, together. `selftest` substitutes fakes wholesale rather than monkeypatching."""

    def __init__(self, repo=None, impact=None, artifacts=None, registry=None,
                 merge_facts=None) -> None:
        self.repo = repo or RepoPort()
        self.impact = impact or ImpactPort()
        self.artifacts = artifacts or ArtifactPort()
        self.registry = registry or RegistryPort()
        # Constructed LAZILY: `MergeFactsPort.__init__` reads the git remote to derive the slug and
        # raises `PortError` without one. Building it eagerly would make every verb fail in a clone
        # with no remote, including verbs that never consult the platform.
        self._merge_facts = merge_facts

    @property
    def merge_facts(self):
        if self._merge_facts is None:
            self._merge_facts = MergeFactsPort()
        return self._merge_facts


def _load(ports: Ports, path: str, ref: str | None) -> tuple[bytes | None, str]:
    """S1. Returns (bytes, note). `None` means the contract is absent where it must be present."""
    if ref is None:
        p = REPO / path
        if not p.exists():
            return None, f"{path} does not exist in the working tree"
        return p.read_bytes(), f"read from the working tree ({path})"
    blob = ports.repo.blob(ref, path)
    if blob is None:
        return None, f"{path} does not exist at {ref} — the authoritative blob is absent"
    return blob, f"read the blob at {ref} (ADR-0105 §11.1)"


def run(ports: Ports, path: str, *, base: str | None, head: str | None, pr: int | None, phase: str,
        impact_override: dict | None = None):
    """S1–S8. Returns (decision, context) or raises PortError for the exit-2 class."""
    raw, note = _load(ports, path, head)
    if raw is None:
        raise PortError(note)

    decl = parse(raw, path=path)                                    # S2 + S3
    stem = Path(path).stem
    unverifiable: list[str] = []
    evidence: list[str] = [note]

    # ── S4a THE BASE ANCHOR ─────────────────────────────────────────────────────────────────
    # THE CONTRACT NAMES ITS OWN BASE; the tool must not guess one. `--base` defaulted to
    # `origin/main`, which is the right comparison only while the change is still in flight. Once
    # the contract has LANDED, `origin/main` IS the head — the diff is empty, no trait derives, and
    # the declaration is reported as differing from a derived set that was never computed. Every
    # landed contract answered `CL-2` for a reason having nothing to do with the contract, and the
    # only way to get a true answer was to know to pass `--base` by hand.
    #
    # AN EMPTY DIFF IS NOT EVIDENCE THAT NOTHING CHANGED. It is evidence of a base that was never
    # the base — the same vacuous zero this tool refuses everywhere else, arriving through the
    # default value of a flag rather than through a failed read.
    #
    # `created.base_sha` is the commit the contract itself declares it started from. §4.3a already
    # anchors the required-context set to it, and already confirms it against the platform's own
    # `base.sha` before that registry is read, so this consumes an anchor the system already
    # verifies rather than introducing a second one. An EXPLICIT `--base` still wins: a gate passes
    # one, and an author comparing against something else is entitled to.
    if base is None:
        claimed = next((e.get("base_sha") for e in decl.events
                        if e.kind == "created" and e.get("base_sha")), "")
        if not claimed:
            base = MAIN_REF
            evidence.append(f"the contract declares no `created.base_sha`, so the changed-file set "
                            f"is computed against {MAIN_REF}")
        elif ports.repo.resolve(claimed) is None:
            # NOT a silent fallback. Comparing against `MAIN_REF` anyway would recompute the exact
            # empty diff this change exists to stop producing, and then report it as a finding about
            # the declaration. Unresolvable is `unverifiable` — the tool could not look.
            base = MAIN_REF
            unverifiable.append(f"the declared base {claimed} is unresolvable here, so the changed-"
                                f"file set could not be computed against the base the contract "
                                f"names")
        else:
            base = claimed
            evidence.append(f"base {claimed} read from the contract's own `created.base_sha`")
    else:
        evidence.append(f"base {base} was given explicitly")

    # ── S5 derive ───────────────────────────────────────────────────────────────────────────
    head_ref = head or "HEAD"
    try:
        changed = ports.repo.diff_names(base, head_ref)
    except PortError as exc:
        changed, unverifiable = None, [*unverifiable, f"the diff could not be enumerated: {exc}"]

    impact: dict | None = impact_override
    if impact is None:
        try:
            impact = ports.impact.report(base)
        except PortError as exc:
            unverifiable.append(f"impact analysis unavailable: {exc}")
    classification = (impact or {}).get("classification", "")

    try:
        modules_art = ports.artifacts.modules()
    except PortError as exc:
        modules_art = {}
        unverifiable.append(f"derived/modules.json: {exc}")
    # ── S5a THE CLASSIFICATION PATH SET (ADR-0105 §1a) ──────────────────────────────────────
    # A CONTRACT IS WRITTEN BEFORE IMPLEMENTATION (§3), SO AT `pre` THE DIFF CANNOT CONTAIN THE
    # CHANGE. It contains the contract and nothing else. Classifying that diff answered `0
    # subsystem(s) spanned` for a change the contract itself says spans two, so declaring the true
    # trait diverged from the derived set and every cross-system contract answered `CL-2` before it
    # was implemented. The only way to a `continue` at `pre` was to implement first — which is the
    # one thing the phase exists to forbid. The phase was unreachable by the rule it enforces.
    #
    # SO `pre` CLASSIFIES INTENT AND `head`/`merge` CLASSIFY THE DIFF. Intent is not a weaker diff;
    # it is the only evidence that exists yet, it lives inside digest `D` (so approval binds it),
    # and it is held to a STRICTER resolution than a diff ever is — see `derive.intended_paths`.
    # The diff stays authoritative wherever it exists: scope, `ST-1`, and the final trait equality
    # below all still read `changed`, unchanged.
    surfaces = [r.get("path", "") for r in (decl.value("expected_surfaces") or [])]
    intent_rows: tuple = ()
    if phase == PRE:
        class_paths, intent_rows, intent_problems = derive.intended_paths(surfaces, modules_art)
        unverifiable += intent_problems
        path_source = "the contract's `expected_surfaces`"
        evidence.append(f"`pre` classified {len(surfaces)} intended path(s) from "
                        f"`expected_surfaces`; the diff is not evidence of an implementation that "
                        f"does not exist yet")
    else:
        class_paths, path_source = changed, "the diff"

    # `owners_for` on a set `intended_paths` already PROVED resolvable. Its silent skip cannot hide
    # anything here, and reusing it keeps ONE path→module→subsystem transform (ADR-0105 §9 forbids a
    # second selector) rather than a parallel one that would drift.
    pairs, subsystems, own_problems = derive.owners_for(class_paths or [], modules_art)
    unverifiable += own_problems

    hot, hot_problems = classify.hot_files_from(REPO / ".agents" / "lanes.json")
    unverifiable += hot_problems

    # The landed copy, read ONCE and read INDEPENDENTLY of `head`. Three outcomes, and the third is
    # the one the old wiring collapsed into the second:
    #
    #   ref resolves, path present  → compare (the append-only check actually runs)
    #   ref resolves, path absent   → the contract has not landed; nothing to compare; NOT a failure
    #   ref does not resolve        → we cannot know which of the two it is → UNVERIFIABLE, fail closed
    #
    # `blob()` alone cannot separate rows 2 and 3 — it answers `None` to both — so `resolve()` is a
    # second read of a DIFFERENT fact, not a duplicate of the same one. S7's `merged` then reuses this
    # result rather than asking git a third time, so the tool cannot answer "has this landed?" two
    # different ways in one run.
    main_blob: bytes | None = None
    try:
        if ports.repo.resolve(MAIN_REF) is None:
            unverifiable.append(f"{MAIN_REF} is unresolvable, so the landed lifecycle cannot be "
                                f"compared — landed-record integrity is UNVERIFIABLE, which ADR-0105 "
                                f"§10 does not treat as satisfied")
        else:
            main_blob = ports.repo.blob(MAIN_REF, path)
    except PortError as exc:
        unverifiable.append(f"the landed contract at {MAIN_REF} could not be read: {exc}")

    # ── the platform merge facts (ADR-0105 §4.3a) ───────────────────────────────────────────
    #
    # READ HERE, BEFORE `Derived` IS FROZEN. `unverifiable` is snapshotted by `tuple(...)` at the
    # `Derived(...)` construction below; anything appended after it still reaches the dependency
    # validator but NEVER reaches `ST-7`, which reads the frozen tuple. A failed platform read that
    # landed after that point would produce a diagnostic no rule consumes — a silent fail-open, which
    # is the exact failure this tool exists to make unreachable.
    #
    # Attempted only for a landed contract with a governed PR: before the merge there is nothing to
    # rederive, and asking would spend a network call to learn nothing.
    # THE GOVERNED PR IS RESOLVED HERE, NOT AT S7. Guarding this read on the explicit `pr` argument
    # meant the ordinary command — `verify <contract>` with no `--pr` — silently skipped the platform
    # read entirely and every post-merge check with it, while still reporting a confident verdict. The
    # contract's own `binding.pr` is the governed PR; requiring it to be re-supplied on the command
    # line made correctness depend on how the tool was invoked.
    pr_num = pr if pr is not None else _pr_of(decl)

    mf = None
    if main_blob is not None and pr_num is not None:
        try:
            d = ports.merge_facts.pull(pr_num)
            claimed_base = next((e.get("base_sha") for e in decl.events
                                 if e.kind == "created" and e.get("base_sha")), "")
            if not claimed_base:
                raise PortError("the contract records no `created.base_sha`, so the required-context "
                                "set cannot be pinned to the commit this change was written against")
            # THE ANCHOR IS EXTERNAL. `created.base_sha` is written by the agent, outside `D`, and it
            # selects which commit's registry supplies the required set — so left unchecked an agent
            # could name an older, weaker registry commit and the bar would follow it there. The
            # platform's own `base.sha` is the answer that was not written by the thing being judged.
            #
            # A DISAGREEMENT IS A FINDING, NOT UNAVAILABILITY. Both SHAs were read successfully; they
            # simply differ. So the registry is left UNREAD (the required set stays empty) and
            # `_acceptance` reports the mismatch itself — raising here would spend `ST-7` on a
            # completed read, which is the exact confusion §4.3a exists to prevent.
            base_ok = bool(d["base_sha"]) and d["base_sha"] == claimed_base
            contexts: tuple = ()
            provenance: tuple = ()
            if base_ok:
                reg = ports.repo.blob(claimed_base, CI_REGISTRY_PATH)
                if reg is None:
                    raise PortError(f"{CI_REGISTRY_PATH} is absent at the verified base "
                                    f"{claimed_base[:12]}")
                names, by_ctx = required_contexts_at(reg)
                contexts, provenance = tuple(names), tuple(sorted(by_ctx.items()))
            raw_runs = ports.merge_facts.check_runs(d["merge_sha"]) if d["merge_sha"] else []
            runs = [CheckRun(**r) for r in raw_runs]
            # CHRONOLOGY MUST COME FROM THE SERVER OR NOT AT ALL. Without `started_at` the question
            # "was this the latest attempt at the moment of acceptance" has no answer in the data,
            # and answering it from id size would be inferring time from an opaque identifier.
            undated = [r.id for r in runs if r.name in contexts and not r.started_at]
            if undated:
                raise PortError(f"check run(s) {', '.join(undated)} carry no `started_at`, so the "
                                f"ordering acceptance depends on cannot be established")
            # THE DOCUMENTED JOIN: check run <- job.check_run_url -> workflow run -> workflow path.
            join: dict[str, tuple[str, str, str]] = {}
            if d["merge_sha"] and contexts:
                for wr in ports.merge_facts.workflow_runs(d["merge_sha"]):
                    for job in ports.merge_facts.jobs(wr["id"]):
                        crid = job["check_run_id"]
                        if not crid:
                            continue
                        if crid in join and join[crid] != (job["name"], wr["id"], wr["path"]):
                            raise PortError(f"check run {crid} joins to more than one job, so its "
                                            f"provenance is ambiguous")
                        join[crid] = (job["name"], wr["id"], wr["path"])
            # A workflow edited inside the change it certifies is not evidence about that change.
            # Gated on MERGED, like the tree reads: before the merge there is no landed content to
            # compare and acceptance is not sought, so demanding the comparison would turn every
            # pre-merge verification of an open PR into an unavailability.
            stable: dict[str, bool] = {}
            binding: dict[str, tuple[str, str]] = {}
            for ctx, (wf_path, job_key) in (dict(provenance).items() if d["merged"] else ()):
                at_base = ports.repo.blob(claimed_base, wf_path)
                at_head = ports.repo.blob(d["pr_head"], wf_path) if d["pr_head"] else None
                if at_base is None or at_head is None:
                    raise PortError(f"the governing workflow {wf_path} could not be read at both the "
                                    f"verified base and the PR head, so whether it changed inside "
                                    f"this change is unknown")
                stable[wf_path] = at_base == at_head
                # The job KEY is resolved AGAINST THE BASE BLOB, the same commit that supplied the
                # required set. Resolving it at the head would let the change under review rename the
                # job that certifies it and still satisfy the binding.
                binding[ctx] = workflow_job_binding(at_base, job_key)
            # Trees are read HERE, where a failed read can still reach `unverifiable`. Resolving them
            # inside the gate would turn "could not read" into "did not match" — an unavailability
            # wearing the costume of a finding.
            pr_tree = ports.repo.tree_of(d["pr_head"]) if d["pr_head"] else None
            merge_tree = ports.repo.tree_of(d["merge_sha"]) if d["merge_sha"] else None
            if d["merged"] and (pr_tree is None or merge_tree is None):
                which = "PR head" if pr_tree is None else "merge commit"
                raise PortError(f"the {which} tree could not be resolved, so the landed content "
                                f"cannot be compared to the authorized content")
            # The contract AS IT STOOD at the final pre-merge PR head. `None` means the ref or object
            # could not be read (unavailability); `b""` means the read succeeded and the contract was
            # ABSENT there. The gate must be able to tell those apart, so no `or` collapses them.
            head_blob: bytes | None = None
            if d["pr_head"]:
                if ports.repo.resolve(d["pr_head"]) is None:
                    raise PortError(f"the PR head {d['pr_head'][:12]} is unresolvable, so the "
                                    f"contract as it stood there cannot be read")
                b = ports.repo.blob(d["pr_head"], path)
                head_blob = b if b is not None else b""
            mf = MergeFacts(read_ok=True, pr_head=d["pr_head"], merge_sha=d["merge_sha"],
                            merged_at=d["merged_at"], merged=d["merged"], base_sha=d["base_sha"],
                            required_contexts=contexts, context_provenance=provenance,
                            check_runs=tuple(runs), run_provenance=tuple(sorted(join.items())),
                            workflow_stable=tuple(sorted(stable.items())),
                            job_binding=tuple(sorted(binding.items())),
                            pr_tree=pr_tree or "", merge_tree=merge_tree or "",
                            pr_head_blob=head_blob)
        except PortError as exc:
            mf = MergeFacts(read_ok=False)
            unverifiable.append(f"the platform merge facts for PR #{pr_num} could not be read "
                                f"({exc}); post-merge authorization and acceptance are UNVERIFIABLE, "
                                f"which ADR-0105 §4.3a does not treat as a negative finding")

    non_monotone = _non_monotone_contracts(ports, changed or [], base, head_ref)
    declared_live = "live" in decl.traits
    trigs = classify.triggers(class_paths, impact_classification=classification, hot_files=hot,
                              contract_ops_non_monotone=non_monotone,
                              operator_required=_declares_t6(decl), subsystems=subsystems,
                              path_source=path_source)
    fired = {t.id: t.fired for t in trigs}
    traits = classify.traits_from(fired, declared_live)
    tier = classify.risk_tier(traits)

    seeds = [m for m in (classify.module_of(p) for p in (class_paths or [])) if m]
    blast: tuple[str, ...] = ()
    if "cross-system" in traits and seeds:
        try:
            blast = derive.blast_radius(seeds, ports.artifacts.dependencies())
        except PortError as exc:
            unverifiable.append(f"derived/dependencies.json: {exc}")

    obligations, ob_problems = derive.obligations(impact, traits)
    unverifiable += ob_problems

    try:
        generated = ports.artifacts.generated_paths()
    except PortError as exc:
        generated = set(); unverifiable.append(f"generated-artifact set: {exc}")
    try:
        stale = ports.artifacts.stale()
    except PortError as exc:
        stale = []; unverifiable.append(f"regeneration proof unavailable: {exc}")

    try:
        control_ids = ports.registry.control_ids()
    except PortError as exc:
        control_ids = None; unverifiable.append(str(exc))

    allow = list(decl.value("incidental_allowlist") or [])
    labelled = classify.labels(changed or [], expected_surfaces=surfaces,
                               incidental_allowlist=allow, generated_paths=generated)
    unauthorized = classify.unauthorized(labelled)

    auth_rows = decl.value("authority") or []
    auth_state, auth_problems = derive.authority_state(auth_rows, ports.repo, head_ref, control_ids)
    ev_problems = derive.evidence_state(decl.value("reusable_evidence") or [], ports.repo, head_ref,
                                        traits)

    gs = classify.governance_surface_findings(
        changed or [], base_has=lambda p: _exists(ports, base, p),
        declared_governance_paths=surfaces if "governance" in traits else ())

    derived = Derived(triggers=trigs, traits=traits, risk_tier=tier, owners=tuple(subsystems),
                      path_owner=tuple(pairs), blast_radius=blast, obligations=obligations,
                      labels=tuple(labelled), unauthorized=unauthorized,
                      changed_files=tuple(changed or ()), impact_classification=classification,
                      authority_blobs=tuple(auth_state), unverifiable=tuple(unverifiable),
                      evidence=tuple(evidence))

    # ── S6 validate ─────────────────────────────────────────────────────────────────────────
    diags = (validate.v_schema(decl, stem, path.startswith(CONTRACTS + "/"))
             + validate.v_semantic(decl, generated)
             + validate.v_authority(auth_problems)
             + lifecycle.validate_events(decl.events, main_blob=main_blob,
                                         decl_bytes=decl.decl_bytes,
                                         life_bytes=raw.partition(BOUNDARY)[2])
             + validate.v_scope(labelled, owners_declared=decl.value("owners") or [],
                                owners_derived=subsystems, stale_artifacts=stale)
             + validate.v_evidence(ev_problems)
             + validate.v_dependency(unverifiable)
             + gs)

    # ── S7 gates ────────────────────────────────────────────────────────────────────────────
    head_sha = ports.repo.resolve(head_ref) or ""
    merged = main_blob is not None                 # the S5 read; never a second, disagreeable one
    # NO review read and NO principal read. Merge authorization is the operator's own parent-bound
    # event. The one network read that now exists carries merge facts and check runs — `MergeFacts`
    # has no field for a review, so a dead, denied or empty review API still cannot change a verdict,
    # because no verdict consults one and no type here can carry one.
    gates = lifecycle.gates(decl, decl.events, head_sha=head_sha, pr=pr_num,
                            main_has_contract=merged, repo=ports.repo, path=path, raw=raw, mf=mf)
    proposals = [e for e in decl.events if e.kind == "head_proposed"]
    proposal_bound = bool(proposals) and lifecycle.parent_binds(
        proposals[-1], repo=ports.repo, path=path, head_sha=head_sha, raw=raw)[0]
    state = lifecycle.state(decl, decl.events, gates, merged=merged,
                            ci_green=_ci_green(decl), proposal_bound=proposal_bound,
                            pr_open=pr_num is not None,
                            mandatory_ok=not any(d.code in ("FIELD-MISSING", "FIELD-EMPTY")
                                                 for d in diags))

    # ── S8 decide ───────────────────────────────────────────────────────────────────────────
    di = DecisionInput(declaration=decl, derived=derived, gates=gates, state=state,
                       diagnostics=tuple(diags), phase=phase)
    return decide(di), {"decl": decl, "derived": derived, "gates": gates, "state": state,
                        "intent_paths": intent_rows, "path_source": path_source}


def _exists(ports: Ports, ref: str, path: str) -> bool:
    try:
        return ports.repo.contains(ref, path)
    except PortError:
        return False


def _non_monotone_contracts(ports: Ports, changed, base: str, head: str) -> list[str]:
    """ADR-0105 §3.6 — which `docs/contracts/**` edits are governance-sensitive (`T3`)."""
    out = []
    for p in changed:
        if not p.startswith(CONTRACTS + "/"):
            continue
        try:
            a, b = ports.repo.blob(base, p), ports.repo.blob(head, p)
        except PortError:
            out.append(p)                     # unreadable ⇒ FAIL CLOSED, never assume monotone
            continue
        if not classify.t3_operation_is_monotone(p, a, b, BOUNDARY):
            out.append(p)
    return out


def _declares_t6(decl) -> bool:
    return any(isinstance(s, str) and s.strip().startswith("T6:")
               for s in (decl.value("stop_conditions") or []))


def _pr_of(decl) -> int | None:
    v = lifecycle.binding_of(decl.events, "pr")
    return int(v) if v.isdigit() else None


def _ci_green(decl) -> bool:
    return any(e.kind == "head_proposed" and e.get("ci") == "green" for e in decl.events)


# ── verbs ───────────────────────────────────────────────────────────────────────────────────
def _emit(args, decision, ctx) -> int:
    d, g = ctx["derived"], ctx["gates"]
    kw = {"contract_id": ctx["decl"].id, "digest": ctx["decl"].digest, "traits": d.traits,
          "risk_tier": d.risk_tier, "gates": g, "state": ctx["state"]}
    if args.json:
        print(report.as_json(report.payload(decision, **kw)))
    elif not args.quiet:
        print(report.render(decision, derived=d, **kw))
    return decision.exit_class


def cmd_verify(args) -> int:
    phase = {"pre": PRE, "head": HEAD, "merge": MERGE}[args.phase]
    decision, ctx = run(Ports(), args.path, base=args.base, head=args.head, pr=args.pr, phase=phase,
                        impact_override=_impact_json(args))
    return _emit(args, decision, ctx)


def cmd_scope(args) -> int:
    """ADR-0105 §5.3 alone: *"Phase 3 should implement this first — highest value, zero prerequisites."*"""
    decision, ctx = run(Ports(), args.path, base=args.base, head=args.head, pr=None, phase=HEAD,
                        impact_override=_impact_json(args))
    d = ctx["derived"]
    if args.json:
        print(report.as_json({"unauthorized": list(d.unauthorized),
                              "labels": [{"path": p, "label": lab} for p, lab in d.labels]}))
        return EXIT_CONTINUE if not d.unauthorized else 1
    print(f"## Scope — {len(d.changed_files)} changed file(s)\n")
    for p, lab in d.labels:
        print(f"  {lab:<22} {p}")
    print()
    if d.unauthorized:
        print(f"  {len(d.unauthorized)} UNAUTHORIZED — amend the declaration and re-approve, or "
              f"revert the file.")
        return 1
    print("  no unauthorized surface")
    return EXIT_CONTINUE


def cmd_triggers(args) -> int:
    decision, ctx = run(Ports(), args.path, base=args.base, head=args.head, pr=None, phase=PRE)
    d = ctx["derived"]
    if args.json:
        print(report.as_json({"required": any(t.fired for t in d.triggers),
                              "traits": sorted(d.traits), "risk_tier": d.risk_tier,
                              "triggers": [{"id": t.id, "fired": t.fired, "reason": t.reason,
                                            "evidence": list(t.evidence)} for t in d.triggers]}))
        return EXIT_CONTINUE
    need = [t for t in d.triggers if t.fired]
    print(f"## A contract is {'REQUIRED' if need else 'NOT required'}\n")
    for t in d.triggers:
        print(f"  {'FIRED  ' if t.fired else '  -    '} {t.id}  {t.reason}")
    print(f"\n  traits: {sorted(d.traits) or 'contained (empty set)'}   risk_tier: {d.risk_tier}")
    if not need:
        print("\n  This is the DEFAULT PATH and it must stay free (ADR-0105 §1).")
    return EXIT_CONTINUE


def cmd_preflight(args) -> int:
    """ADR-0105 §1a — "do I need a contract?", asked BEFORE a contract or an implementation exists.

    THE ONE-WAY RULE. This answers `REQUIRED` or `UNDETERMINED` and can never answer `NOT REQUIRED`.
    Paths settle `T1`, `T3` and `T5`. They cannot settle `T2` — architectural impact is a property of
    code that has not been written — and they cannot settle `T4` or `T6`, which are human facts no
    tool derives. A `NOT REQUIRED` here would be a claim about three triggers that were never
    evaluated, and it would be the single most load-bearing false negative the system could emit:
    the answer that sends an agent past the gate entirely. `UNDETERMINED` says the true thing —
    nothing here fired, and something not visible from paths still might.
    """
    ports = Ports()
    problems: list[str] = []
    try:
        modules_art = ports.artifacts.modules()
    except PortError as exc:
        modules_art = {}; problems.append(f"derived/modules.json: {exc}")
    hot, hot_problems = classify.hot_files_from(REPO / ".agents" / "lanes.json")
    problems += hot_problems

    paths, rows, path_problems = derive.intended_paths(list(args.paths), modules_art)
    problems += path_problems
    _, subsystems, own_problems = derive.owners_for(paths or [], modules_art)
    problems += own_problems

    trigs = classify.triggers(paths, impact_classification="", hot_files=hot,
                              contract_ops_non_monotone=[], operator_required=False,
                              subsystems=subsystems, path_source="the supplied path set")
    # READ ONLY THE PATH-DERIVED TRIGGERS. `classify.triggers` is the single selector (ADR-0105 §9
    # forbids a second), so `T2`/`T4`/`T6` come back "did not fire" — which here means "was not
    # evaluated". The verdict must not read them, and the output must not let a reader mistake one
    # for the other, so they are reported as `unevaluable` by name rather than silently dropped.
    fired = {t.id: t.fired for t in trigs}
    path_derived = ("T1", "T3", "T5")
    required = any(fired.get(t) for t in path_derived)
    fail_closed = paths is None
    verdict = "REQUIRED" if required else "UNDETERMINED"
    traits = sorted(classify.traits_from({k: fired.get(k, False) for k in path_derived}, False))
    unevaluable = {
        "T2": "architectural impact requires an implementation diff — `python -m tools.arch impact`",
        "T4": "live/destructive is human-declared, never derived",
        "T6": "an operator requirement is an operator fact",
    }
    if args.json:
        print(report.as_json({
            "verdict": verdict, "fail_closed": fail_closed, "traits": traits,
            "paths": [{"path": p, "kind": k, "detail": d} for p, k, d in rows],
            "triggers": [{"id": t.id, "evaluated": t.id in path_derived, "fired": t.fired,
                          "reason": t.reason, "evidence": list(t.evidence)} for t in trigs],
            "unevaluable": unevaluable, "problems": problems}))
        return EXIT_UNTRUSTWORTHY if fail_closed else EXIT_CONTINUE
    print(f"## A contract is {verdict}\n")
    for p, kind, detail in rows:
        print(f"  {kind:<12} {p}\n               {detail}")
    print()
    for t in trigs:
        if t.id in path_derived:
            print(f"  {'FIRED  ' if t.fired else '  -    '} {t.id}  {t.reason}")
        else:
            print(f"  UNEVAL   {t.id}  {unevaluable[t.id]}")
    print(f"\n  path-derived traits: {traits or 'none from paths'}")
    for p in problems:
        print(f"  ! {p}")
    if fail_closed:
        print("\n  FAIL CLOSED — an intended path did not resolve, so no classification was "
              "\n  computed. This is NOT `contained` and is NOT `not required`.")
        return EXIT_UNTRUSTWORTHY
    if not required:
        print("\n  UNDETERMINED, never `NOT REQUIRED`: `T2`, `T4` and `T6` were not evaluated here."
              "\n  Re-run `triggers` against a real diff, and declare `T4`/`T6` yourself.")
    return EXIT_CONTINUE


def cmd_compile(args) -> int:
    """Resolve what a contract can derive, and print it FOR THE AUTHOR. It writes nothing."""
    decision, ctx = run(Ports(), args.path, base=args.base, head=None, pr=None, phase=PRE)
    d = ctx["derived"]
    fields = {"traits": sorted(d.traits), "owners": list(d.owners),
              "blast_radius": list(d.blast_radius),
              "verification": [{"obligation_id": o, "control_or_requirement": w,
                                "distinct_boundary": ""} for o, w in d.obligations]}
    if args.json:
        print(report.as_json(fields))
        return EXIT_CONTINUE
    print("## Derived fields — copy these into the declaration\n")
    print(f"### traits\n\n{', '.join(fields['traits']) or '(empty — `contained`)'}\n")
    print("### owners\n")
    for s in fields["owners"]: print(f"| {s} |  |")
    print(f"\n### blast_radius\n\n{', '.join(fields['blast_radius']) or '(none)'}\n")
    print("### verification\n")
    for o, w in d.obligations: print(f"| {o} | {w} |  |")
    return EXIT_CONTINUE


def cmd_state(args) -> int:
    decision, ctx = run(Ports(), args.path, base=args.base, head=None, pr=args.pr, phase=HEAD)
    g = ctx["gates"]
    if args.json:
        print(report.as_json({"state": ctx["state"], "gates": {
            "content_approval": g.content_approval,
            "merge_authorization": g.merge_authorization,
            "acceptance": g.acceptance}, "detail": list(g.detail)}))
        return EXIT_CONTINUE
    print(f"## state: `{ctx['state']}`\n")
    print(f"  content approval     {g.content_approval}")
    print(f"  merge authorization  {g.merge_authorization}")
    print(f"  acceptance           {g.acceptance}")
    for line in g.detail: print(f"    · {line}")
    print("\n  `merged` NEVER implies `accepted`, and an `accepted` ROW never implies `accepted`")
    print("  either — acceptance is verified against the platform, never asserted (§4.3a).")
    return EXIT_CONTINUE


def cmd_digest(args) -> int:
    p = REPO / args.path
    if not p.exists():
        print(f"{args.path} does not exist", file=sys.stderr)
        return EXIT_UNTRUSTWORTHY
    raw = p.read_bytes()
    decl = raw.split(BOUNDARY, 1)[0]
    if BOUNDARY not in raw:
        print(f"{args.path} has no `## Lifecycle` boundary — `D` is undefined", file=sys.stderr)
        return EXIT_UNTRUSTWORTHY
    print(digest_of(decl))
    return EXIT_CONTINUE


def cmd_template(args) -> int:
    print(report.render_template(), end="")
    return EXIT_CONTINUE


def cmd_selftest(args) -> int:
    from . import selftest
    return selftest.run(verbose=not args.quiet)


def _impact_json(args) -> dict | None:
    if not getattr(args, "impact_json", None):
        return None
    import json
    return json.loads(Path(args.impact_json).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tools.contract", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="structured output")
    p.add_argument("--quiet", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, *, path=True, base=True, head=False, pr=False, impact=False, help_=""):
        s = sub.add_parser(name, help=help_)
        # `--json` / `--quiet` are accepted on BOTH sides of the verb. Argparse puts top-level
        # optionals before the subcommand, which is not how anyone types a CLI: `verify <path>
        # --json` is the natural form and it would otherwise be an unrecognized-argument error.
        s.add_argument("--json", action="store_true", help="structured output")
        s.add_argument("--quiet", action="store_true")
        if path: s.add_argument("path", help=f"path to the contract, e.g. {CONTRACTS}/<id>.md")
        # DEFAULT `None`, NOT a ref. `run()` then reads the base off the contract's own
        # `created.base_sha`. A ref default was `origin/main`, which equals the head on a landed
        # contract and yields an empty diff — see the S4a note in `run()`.
        if base: s.add_argument("--base", default=None,
                                help="compare against this commit instead of the contract's own "
                                     "`created.base_sha`")
        if head: s.add_argument("--head", default=None)
        if pr: s.add_argument("--pr", type=int, default=None)
        if impact: s.add_argument("--impact-json", default=None,
                                  help="consume a precomputed impact report instead of recomputing")
        return s

    v = add("verify", head=True, pr=True, impact=True, help_="the full pipeline to a decision")
    v.add_argument("--phase", choices=("pre", "head", "merge"), default="head")
    v.set_defaults(fn=cmd_verify)
    add("scope", head=True, impact=True, help_="ADR-0105 §5.3 alone").set_defaults(fn=cmd_scope)
    add("triggers", head=True, help_="do I need a contract?").set_defaults(fn=cmd_triggers)
    pf = sub.add_parser("preflight", help="do I need a contract? — asked from INTENDED paths, "
                                          "before any contract or implementation exists")
    pf.add_argument("paths", nargs="+", help="the exact repository paths you intend to change")
    pf.add_argument("--json", action="store_true", help="structured output")
    pf.add_argument("--quiet", action="store_true")
    pf.set_defaults(fn=cmd_preflight)
    add("compile", help_="resolve the derivable fields").set_defaults(fn=cmd_compile)
    add("state", pr=True, help_="derived lifecycle state").set_defaults(fn=cmd_state)
    add("digest", base=False, help_="`D` — what an approval names").set_defaults(fn=cmd_digest)
    sub.add_parser("template", help="print the declaration skeleton").set_defaults(fn=cmd_template)
    st = sub.add_parser("selftest", help="run the negative controls")
    st.add_argument("--quiet", action="store_true")
    st.add_argument("--json", action="store_true")
    st.set_defaults(fn=cmd_selftest)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except PortError as exc:
        _fail(args, "a required input was unavailable", str(exc))
        return EXIT_UNTRUSTWORTHY
    except Exception as exc:                      # noqa: BLE001 — see below
        # An unhandled exception is EXIT 2, never a decision. Letting it propagate would print a
        # traceback and exit 1, which is the class reserved for "verification completed and the
        # decision does not permit continuation" — a crash would then be indistinguishable from a
        # deliberate `stop`, and worse, a caller keying on exit 1 would treat it as a real verdict.
        _fail(args, f"internal error: {type(exc).__name__}", str(exc))
        return EXIT_UNTRUSTWORTHY


def _fail(args, reason: str, detail: str) -> None:
    if getattr(args, "json", False):
        print(report.as_json(report.untrustworthy(reason, detail)))
    else:
        print(f"EXIT 2 — {reason}: {detail}", file=sys.stderr)
        print("No trustworthy decision was reached. This is NOT an advisory pass and must never be "
              "converted to `continue`.", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
