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

WHICH BYTES ARE AUTHORITATIVE. The gate-facing verbs (`verify`, `scope`) read the contract BLOB at
the head — ADR-0105 §11.1 makes the blob at the approved head authoritative, and the working tree is
never authoritative for a gate. The authoring verbs (`compile`, `digest`, `state`) read the working
tree on purpose: they exist to help someone write a contract that does not exist at any ref yet.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import classify, derive, lifecycle, report, validate
from .adapters import REPO, ArtifactPort, ImpactPort, PortError, RegistryPort, RepoPort, ReviewPort
from .decide import HEAD, MERGE, PRE, decide
from .model import EXIT_CONTINUE, EXIT_UNTRUSTWORTHY, DecisionInput, Derived
from .parse import BOUNDARY, digest as digest_of, parse

CONTRACTS = "docs/contracts"


class Ports:
    """The five ports, together. `selftest` substitutes fakes wholesale rather than monkeypatching."""

    def __init__(self, repo=None, impact=None, artifacts=None, registry=None, reviews=None) -> None:
        self.repo = repo or RepoPort()
        self.impact = impact or ImpactPort()
        self.artifacts = artifacts or ArtifactPort()
        self.registry = registry or RegistryPort()
        self.reviews = reviews or ReviewPort()


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


def run(ports: Ports, path: str, *, base: str, head: str | None, pr: int | None, phase: str,
        impact_override: dict | None = None):
    """S1–S8. Returns (decision, context) or raises PortError for the exit-2 class."""
    raw, note = _load(ports, path, head)
    if raw is None:
        raise PortError(note)

    decl = parse(raw, path=path)                                    # S2 + S3
    stem = Path(path).stem
    unverifiable: list[str] = []
    evidence: list[str] = [note]

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
    pairs, subsystems, own_problems = derive.owners_for(changed or [], modules_art)
    unverifiable += own_problems

    hot, hot_problems = classify.hot_files_from(REPO / ".agents" / "lanes.json")
    unverifiable += hot_problems

    non_monotone = _non_monotone_contracts(ports, changed or [], base, head_ref)
    declared_live = "live" in decl.traits
    trigs = classify.triggers(changed, impact_classification=classification, hot_files=hot,
                              contract_ops_non_monotone=non_monotone,
                              operator_required=_declares_t6(decl), subsystems=subsystems)
    fired = {t.id: t.fired for t in trigs}
    traits = classify.traits_from(fired, declared_live)
    tier = classify.risk_tier(traits)

    seeds = [m for m in (classify.module_of(p) for p in (changed or [])) if m]
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

    surfaces = [r.get("path", "") for r in (decl.value("expected_surfaces") or [])]
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
    main_blob = ports.repo.blob("origin/main", path) if head is not None else None
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
    pr_num = pr if pr is not None else _pr_of(decl)
    reviews, review_problem = lifecycle.read_reviews(ports.reviews, pr_num)
    if review_problem:
        diags += validate.v_dependency([review_problem])
    head_sha = ports.repo.resolve(head_ref) or ""
    merged = ports.repo.contains("origin/main", path)
    gates = lifecycle.gates(decl, decl.events, head_sha=head_sha, pr=pr_num, reviews=reviews,
                            main_has_contract=merged)
    state = lifecycle.state(decl, decl.events, gates, merged=merged,
                            ci_green=_ci_green(decl), head_sha=head_sha, pr_open=pr_num is not None,
                            mandatory_ok=not any(d.code in ("FIELD-MISSING", "FIELD-EMPTY")
                                                 for d in diags))

    # ── S8 decide ───────────────────────────────────────────────────────────────────────────
    di = DecisionInput(declaration=decl, derived=derived, gates=gates, state=state,
                       diagnostics=tuple(diags), phase=phase)
    return decide(di), {"decl": decl, "derived": derived, "gates": gates, "state": state}


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
            "content_approval": g.content_approval, "exact_head_approval": g.exact_head_approval,
            "acceptance": g.acceptance}, "detail": list(g.detail)}))
        return EXIT_CONTINUE
    print(f"## state: `{ctx['state']}`\n")
    print(f"  content approval     {g.content_approval}")
    print(f"  exact-head approval  {g.exact_head_approval}")
    print(f"  acceptance           {g.acceptance}")
    for line in g.detail: print(f"    · {line}")
    print("\n  `merged` NEVER implies `accepted` — acceptance is a separate operator decision.")
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
        if base: s.add_argument("--base", default="origin/main")
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
