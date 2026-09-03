"""The executable policy engine package.

Re-exports the public API previously provided by tools.arch.policy module.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..common import CONTRACT, DERIVED, KB, REPO, load
from ..deltas import compile_edges
from .ast_guards import _gb_checks, _terminal_post_writers
from .checks_impl import _coverage, _tests_defined, _verification_matrix_test_names, _verification_persists
from .exceptions import _apply_exceptions, _approved, blocking
from .ratchets_check import _declared_numbers_forbidden, _ratchet_drift, _side_effect_ratchet
from .rules import BLOCKING, INFO, WARNING, Finding, RULES, Rule, _f, missing_canonical

__all__ = [
    "BLOCKING",
    "INFO",
    "WARNING",
    "Finding",
    "RULES",
    "Rule",
    "blocking",
    "check",
    "missing_canonical",
]


def check(derived_dir: Path | None = None) -> list[Finding]:
    """Evaluate every rule. Returns findings (already exception-filtered).

    `= None`, not `= DERIVED`: default args bind ONCE at import and cannot be redirected by the
    selftest fixture. See drift.stale_artifacts() for the control that trap silently defeated.
    """
    derived_dir = derived_dir or DERIVED
    out: list[Finding] = []

    # *** GOV-001 — THE PRECONDITION, EVALUATED FIRST AND SHORT-CIRCUITING. ***
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
        declared_env = set(load(kb_cfg).get("env_vars", {}))
        derived_env = set(cfg["env_vars"])
        undeclared = sorted(derived_env - declared_env)
        if undeclared:
            out.append(_f("ARCH-003",
                          f"{len(undeclared)} environment variable(s) are READ by the code but not "
                          f"declared in kb/configuration.json.",
                          [f"{v}  read at {', '.join(cfg['env_vars'][v]['read_at'][:2])}" for v in undeclared]))
        phantom = sorted(declared_env - derived_env)
        if phantom:
            out.append(_f("ARCH-003",
                          f"{len(phantom)} environment variable(s) are DECLARED in kb/configuration.json "
                          f"but read by nothing (phantom switch: the reader was removed, or the name "
                          f"never had one).",
                          [f"{v}  declared in kb/configuration.json, no os.getenv in the tree"
                           for v in phantom]))

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

    # ARCH-007 — the layering ratchet (GB-1, mechanized)
    baseline = _approved("must_stay_lazy", default=None)
    if baseline is not None:
        pinned = {(e[0], e[1]) for e in baseline}
        hoisted = sorted(pinned & compile_edges(deps))
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

    if cs.get("available"):
        out += _coverage(cs)

    out += _ratchet_drift(rat)
    out += _gb_checks()
    out += _side_effect_ratchet(D("side_effects"))
    out += _declared_numbers_forbidden("ARCH-008")

    from ..registries import unknown_growth
    open_, ceiling = unknown_growth()
    if open_ > ceiling:
        out.append(_f("ARCH-005",
                      f"Open UNKNOWNs grew to {open_}, above the approved ceiling of {ceiling}. "
                      f"Raising the ceiling is a statement that the system is LESS understood than "
                      f"it was — that should be hard to do quietly.",
                      [f"open: {open_}", f"approved ceiling: {ceiling}"]))

    out += _verification_persists()

    return _apply_exceptions(out)


# Baseline assembly imports these private helpers; keep stable names.
__all__ += ["_terminal_post_writers", "_verification_matrix_test_names", "_tests_defined"]
