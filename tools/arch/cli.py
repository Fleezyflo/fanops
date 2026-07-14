"""`python -m tools.arch <verb>` — the one deterministic command surface.

    regen        regenerate every DERIVED artifact (byte-deterministic)
    check        evaluate every executable policy rule
    drift        regenerate, byte-compare against the repo, and EXPLAIN the difference
    impact       architectural + implementation impact report for a diff (PR gate)
    verify       which verification classes a diff REQUIRES
    registries   validate the exception + unknown registries
    baseline     accept the current derived state as the approved ratchet baseline (REVIEWED)
    selftest     negative controls — prove the validators detect what they claim to
    docs         render human-readable docs FROM the canonical machine artifacts
    ci           the composite gate: regen-is-clean + policy + registries  (exit 1 on BLOCKING)
"""
from __future__ import annotations

import argparse
import sys

from . import drift as drift_mod
from . import impact as impact_mod
from . import policy as policy_mod
from . import registries as reg
from . import render as render_mod
from . import selftest as selftest_mod
from . import verifymap
from .common import DERIVED, GOVERNANCE, dumps, write
from .generate import generate

_C = {"BLOCKING": "\x1b[31m", "WARNING": "\x1b[33m", "INFO": "\x1b[36m", "OK": "\x1b[32m", "_": "\x1b[0m"}


def _paint(sev: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{_C.get(sev, '')}{text}{_C['_']}"


def _print_findings(findings: list[policy_mod.Finding]) -> int:
    if not findings:
        print(_paint("OK", "  no findings"))
        return 0
    order = {"BLOCKING": 0, "WARNING": 1, "INFO": 2}
    for f in sorted(findings, key=lambda f: (order.get(f.severity, 9), f.rule)):
        tag = f"[{f.severity}]"
        supp = f"  (suppressed by {f.suppressed_by})" if f.suppressed_by else ""
        print(_paint(f.severity, f"  {tag:<10} {f.rule}  {f.title}{supp}"))
        print(f"             {f.detail}")
        for e in f.evidence[:8]:
            print(f"               · {e}")
        if len(f.evidence) > 8:
            print(f"               · … and {len(f.evidence) - 8} more")
        if f.remediation and f.severity == "BLOCKING" and not f.suppressed_by:
            print(f"             fix: {f.remediation}")
        print()
    return len(policy_mod.blocking(findings))


def cmd_regen(_: argparse.Namespace) -> int:
    changed = generate()
    n = sum(1 for v in changed.values() if v)
    for name, ch in sorted(changed.items()):
        print(f"  {'rewritten' if ch else 'unchanged'}  {name}.json")
    print(f"\n{n} artifact(s) changed. Regeneration is deterministic: running this again "
          f"with no source change rewrites nothing.")
    return 0


def cmd_check(_: argparse.Namespace) -> int:
    print("policy:")
    findings = policy_mod.check()
    n = _print_findings(findings)
    print(f"{n} BLOCKING finding(s).")
    return 1 if n else 0


def cmd_drift(_: argparse.Namespace) -> int:
    # all_stale = derived/*.json AND the generated docs. The docs are as generated as the JSON is;
    # they simply do not live in derived/, which is exactly why they went unwatched.
    drifts = drift_mod.all_stale()
    print("generated-artifact integrity:")
    if not drifts:
        print(_paint("OK", "  derived/ + generated docs are byte-identical to regeneration — "
                           "no stale artifact, no hand-edit"))
    for d in drifts:
        print(_paint("BLOCKING", f"  [STALE] {d.artifact}  ({d.dimension})"))
        print(f"          {d.detail}")
        for e in d.evidence:
            print(f"            · {e}")
    print()
    print("policy:")
    findings = policy_mod.check()
    n = _print_findings(findings)
    total = len(drifts) + n
    print(f"{len(drifts)} stale artifact(s), {n} BLOCKING policy finding(s).")
    return 1 if total else 0


def cmd_registries(_: argparse.Namespace) -> int:
    errs = reg.validate()
    exp = reg.expired()
    open_, ceiling = reg.unknown_growth()
    print("registries:")
    for e in errs:
        print(_paint("BLOCKING", f"  [INVALID] {e}"))
    for e in exp:
        print(_paint("WARNING", f"  [EXPIRED] {e['id']} (rule {e['rule']}) expired {e['expiry']} "
                                f"— it no longer suppresses anything. Owner: {e.get('owner')}"))
    over = open_ > ceiling
    sev = "BLOCKING" if over else "OK"
    print(_paint(sev, f"  unknowns: {open_} open / {ceiling} approved ceiling"
                      f"{'  — GROWTH WITHOUT APPROVAL' if over else ''}"))
    if not errs and not exp and not over:
        print(_paint("OK", "  registries valid"))
    return 1 if (errs or over) else 0


def cmd_baseline(args: argparse.Namespace) -> int:
    """Accept the CURRENT derived state as the approved baseline.

    This is a DELIBERATE, REVIEWED act, which is why it is a separate verb and not a side effect
    of `regen`. If accepting a baseline were automatic, the ratchet would re-arm itself around
    whatever was just committed — which is not a ratchet, it is a rubber stamp.
    """
    from .common import load
    deps = load(DERIVED / "dependencies.json")

    # An edge that is ALREADY module-level is not a candidate for "must stay lazy" — it is not
    # lazy. (`persona_store -> personas` appears in BOTH sets: it is part of the one compile-time
    # cycle AND is also imported inside a function.) Pinning such an edge would fire ARCH-007 on
    # the status quo, which is the classic way a ratchet becomes noise and then gets ignored.
    compile_edges = {(s, t) for s, d in deps["edges"].items() for t in d["compile"]}
    lazy_only = sorted({(e["from"], e["to"])
                        for e in deps["lazy_upward"] + deps["lazy_lateral"]} - compile_edges)
    must_stay_lazy = [list(e) for e in lazy_only]
    payload = {
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
    if not args.accept:
        print(dumps(payload))
        print("(dry run — pass --accept to write governance/baselines.json)", file=sys.stderr)
        return 0
    ch = write(GOVERNANCE / "baselines.json", payload)
    print(f"{'wrote' if ch else 'unchanged'}  governance/baselines.json")
    print(f"  approved compile cycles           : {len(payload['approved_compile_cycles'])}")
    print(f"  imports pinned as MUST-STAY-LAZY  : {len(must_stay_lazy)}")
    print(f"  approved terminal-Post writers    : {len(payload['approved_terminal_post_writers'])}")
    return 0


def cmd_impact(args: argparse.Namespace) -> int:
    rep = impact_mod.report(args.base)
    print(impact_mod.render(rep))
    return 1 if rep["classification"] in ("BREAKING_CHANGE", "UNKNOWN_IMPACT") and args.strict else 0


def cmd_verify(args: argparse.Namespace) -> int:
    rep = impact_mod.report(args.base)
    required = verifymap.required_for(rep)
    print(verifymap.render(required, rep))
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    return selftest_mod.run(verbose=not args.quiet)


def cmd_docs(_: argparse.Namespace) -> int:
    for path, changed in render_mod.render_all():
        print(f"  {'rewritten' if changed else 'unchanged'}  {path}")
    return 0


def cmd_ci(_: argparse.Namespace) -> int:
    rc = 0
    print("═══ 1/3  generated-artifact integrity + architecture/implementation drift ═══")
    rc |= cmd_drift(argparse.Namespace())
    print("\n═══ 2/3  registries (exceptions + unknowns) ═══")
    rc |= cmd_registries(argparse.Namespace())
    print("\n═══ 3/3  verdict ═══")
    print(_paint("BLOCKING" if rc else "OK",
                 "  FAIL — see findings above" if rc else "  PASS"))
    return rc


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tools.arch",
                                description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("regen", help="regenerate every DERIVED artifact").set_defaults(fn=cmd_regen)
    sub.add_parser("check", help="evaluate every policy rule").set_defaults(fn=cmd_check)
    sub.add_parser("drift", help="regenerate, compare, explain").set_defaults(fn=cmd_drift)
    sub.add_parser("registries", help="validate exceptions + unknowns").set_defaults(fn=cmd_registries)
    sub.add_parser("docs", help="render human docs from the machine artifacts").set_defaults(fn=cmd_docs)
    sub.add_parser("ci", help="the composite gate").set_defaults(fn=cmd_ci)

    b = sub.add_parser("baseline", help="accept the current derived state as the approved baseline")
    b.add_argument("--accept", action="store_true", help="write it (default: dry run to stdout)")
    b.set_defaults(fn=cmd_baseline)

    i = sub.add_parser("impact", help="architectural impact report for a diff")
    i.add_argument("--base", default="origin/main", help="git ref to diff against")
    i.add_argument("--strict", action="store_true", help="exit 1 on BREAKING / UNKNOWN_IMPACT")
    i.set_defaults(fn=cmd_impact)

    v = sub.add_parser("verify", help="which verification classes a diff requires")
    v.add_argument("--base", default="origin/main")
    v.set_defaults(fn=cmd_verify)

    s = sub.add_parser("selftest", help="negative controls")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(fn=cmd_selftest)

    args = p.parse_args(argv)
    return args.fn(args)
