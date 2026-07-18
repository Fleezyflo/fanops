"""Per-PR architectural and implementation impact report.

Classifies a diff as one of:

    NO_ARCHITECTURAL_CHANGE   nothing this system models moved
    COMPATIBLE_CHANGE         the surface grew; nothing existing was removed or re-owned
    MIGRATION_REQUIRED        persisted shape changed — a schema bump, a new migration step
    BREAKING_CHANGE           something was REMOVED, RE-OWNED, or a hard boundary was crossed
    UNKNOWN_IMPACT            the extractor could not decide

UNKNOWN_IMPACT IS NEVER TREATED AS SAFE. That is the whole point of having the class: a change
whose blast radius we cannot compute is not a change we can wave through, and the honest failure
mode of any static analyser is "I don't know", not "probably fine".
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .common import DERIVED, REPO, SRC, load
from .drift import stale_artifacts
from .generate import generate

NO_CHANGE = "NO_ARCHITECTURAL_CHANGE"
COMPATIBLE = "COMPATIBLE_CHANGE"
MIGRATION = "MIGRATION_REQUIRED"
BREAKING = "BREAKING_CHANGE"
UNKNOWN = "UNKNOWN_IMPACT"

_SEVERITY = {NO_CHANGE: 0, COMPATIBLE: 1, MIGRATION: 2, BREAKING: 3, UNKNOWN: 4}


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                          check=False).stdout.strip()


def changed_files(base: str) -> list[str]:
    raw = _git("diff", "--name-only", f"{base}...HEAD")
    if not raw:
        raw = _git("diff", "--name-only", base)   # fall back to a working-tree diff
    return sorted(f for f in raw.splitlines() if f)


_base_error: str | None = None


def _derived_at(ref: str) -> dict | None:
    """Regenerate the derived artifacts from a historical tree, without touching the checkout.

    The failure REASON is captured, never swallowed. A checker that reports "I don't know" when it
    actually CRASHED is indistinguishable from one that genuinely cannot decide — and this system's
    whole premise is that those two must never be confused.
    """
    global _base_error
    _base_error = None
    tmp = Path(tempfile.mkdtemp(prefix="arch-base-"))
    try:
        tar = subprocess.run(["git", "archive", ref, "src", "tests"], cwd=REPO,
                             capture_output=True, check=False)
        if tar.returncode != 0:
            _base_error = f"`git archive {ref}` failed: {tar.stderr.decode()[:160]}"
            return None
        subprocess.run(["tar", "-x", "-C", str(tmp)], input=tar.stdout, check=True)
        src = tmp / "src" / "fanops"
        if not src.exists():
            _base_error = f"ref {ref!r} has no src/fanops/ tree"
            return None
        out = tmp / "derived"
        generate(src=src, out=out)
        return {p.stem: load(p) for p in out.glob("*.json")}
    except Exception as exc:
        _base_error = f"{type(exc).__name__}: {exc}"
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def report(base: str = "origin/main") -> dict:
    files = changed_files(base)
    touched_src = [f for f in files if f.startswith("src/fanops/")]
    touched_canonical = [f for f in files if f.startswith(".reports/architecture/")]
    touched_derived = [f for f in files if f.startswith(".reports/architecture/derived/")]

    rep: dict = {
        "base": base,
        "changed_files": files,
        "touched_src": touched_src,
        "touched_canonical_artifacts": touched_canonical,
        "architecture": {k: [] for k in
                         ("changed_subsystems", "changed_ownership", "changed_dependencies",
                          "changed_enums", "changed_persistence",
                          "changed_integrations", "changed_side_effects")},
        "implementation": {k: [] for k in
                           ("changed_slices", "changed_boundaries", "changed_verification",
                            "changed_preserved_behaviors", "changed_merge_gates")},
        "reasons": [],
        "classification": NO_CHANGE,
    }

    if not touched_src and not touched_canonical:
        rep["reasons"].append("No file under src/fanops/ or .reports/architecture/ changed.")
        return rep

    head = {p.stem: load(p) for p in DERIVED.glob("*.json")} if DERIVED.exists() else {}
    base_derived = _derived_at(base)

    if base_derived is None:
        rep["classification"] = UNKNOWN
        rep["reasons"].append(
            f"Could not regenerate the derived architecture at base ref {base!r} — {_base_error}. "
            f"The blast radius of this diff is NOT COMPUTABLE, and an uncomputable blast radius is "
            f"never 'safe'.")
        return rep

    worst = NO_CHANGE

    def bump(cls: str, why: str) -> None:
        nonlocal worst
        if _SEVERITY[cls] > _SEVERITY[worst]:
            worst = cls
        rep["reasons"].append(f"[{cls}] {why}")

    # ── modules / subsystem ownership ───────────────────────────────────────────────────────
    om, nm = base_derived.get("modules", {}), head.get("modules", {})
    o_set, n_set = set(om.get("modules", [])), set(nm.get("modules", []))
    for m in sorted(n_set - o_set):
        rep["architecture"]["changed_subsystems"].append(f"NEW module {m}")
        if m in set(nm.get("unassigned_modules", [])):
            bump(UNKNOWN, f"new module {m} belongs to NO subsystem — its ownership, risk profile "
                          f"and reviewer are all undefined")
        else:
            bump(COMPATIBLE, f"new module {m} (subsystem: {nm['subsystem_of'].get(m)})")
    for m in sorted(o_set - n_set):
        rep["architecture"]["changed_subsystems"].append(f"REMOVED module {m}")
        bump(BREAKING, f"module {m} was REMOVED — every KB claim about it is now about nothing")

    o_own, n_own = om.get("subsystem_of", {}), nm.get("subsystem_of", {})
    for m in sorted(set(o_own) & set(n_own)):
        if o_own[m] != n_own[m]:
            rep["architecture"]["changed_ownership"].append(f"{m}: {o_own[m]} -> {n_own[m]}")
            bump(BREAKING, f"module {m} CHANGED SUBSYSTEM ({o_own[m]} -> {n_own[m]})")

    # ── dependencies ────────────────────────────────────────────────────────────────────────
    od, nd = base_derived.get("dependencies", {}), head.get("dependencies", {})
    oc = {(s, t) for s, d in od.get("edges", {}).items() for t in d["compile"]}
    nc = {(s, t) for s, d in nd.get("edges", {}).items() for t in d["compile"]}
    ol = {(s, t) for s, d in od.get("edges", {}).items() for t in d["lazy"]}
    for s, t in sorted(nc - oc):
        rep["architecture"]["changed_dependencies"].append(f"NEW compile-time edge {s} -> {t}")
        if (s, t) in ol:
            bump(BREAKING, f"LAZY IMPORT HOISTED TO MODULE LEVEL: {s} -> {t}. The layer DAG holds "
                           f"only because this was deferred to call time. This can break the "
                           f"process at START (GB-1 / ARCH-007).")
        else:
            bump(COMPATIBLE, f"new compile-time dependency {s} -> {t}")
    for s, t in sorted(oc - nc):
        rep["architecture"]["changed_dependencies"].append(f"REMOVED compile-time edge {s} -> {t}")

    o_cyc = {tuple(sorted(c)) for c in od.get("G1_non_trivial_sccs", [])}
    n_cyc = {tuple(sorted(c)) for c in nd.get("G1_non_trivial_sccs", [])}
    for c in sorted(n_cyc - o_cyc):
        bump(BREAKING, f"NEW compile-time import CYCLE: {' ↔ '.join(c)}. Load-order sensitive; can "
                       f"become an ImportError at process start.")

    # ── enum member sets (the state representation) ─────────────────────────────────────────
    #
    # This dimension replaces `changed_state_machines`, which was INITIALIZED AND NEVER WRITTEN —
    # one of the two permanently dead requirements ADR-0105 §9 records as gap G4. Nothing new is
    # extracted: `entities.json` already carries every enum's member set, and both derived dicts are
    # already in scope here, so the delta is a set comparison.
    #
    # CEILINGED AT COMPATIBLE, DELIBERATELY. `impact --strict` fails the PR on BREAKING_CHANGE or
    # UNKNOWN_IMPACT only (`cli.py:184`), so capping here means `--strict` fails on exactly what it
    # failed on before this dimension existed. Arming a verification requirement must not quietly
    # become an enforcement change: `verify` always exits 0, and this keeps that true. A removed
    # member is the more dangerous edit and it is reported first — but it is still reported as
    # COMPATIBLE, because deciding whether a removal is breaking is a semantic judgement about
    # persisted values that this analyser cannot make and must not pretend to.
    def _enums(d: dict) -> dict:
        return {f"{mod}.{e['name']}": set(e.get("members", []))
                for mod, lst in d.get("entities", {}).get("enums", {}).items() for e in lst}

    oen, nen = _enums(base_derived), _enums(head)
    for name in sorted(set(oen) & set(nen)):
        added, removed = sorted(nen[name] - oen[name]), sorted(oen[name] - nen[name])
        if not (added or removed):
            continue
        delta = "".join([f" +{','.join(added)}" if added else "",
                         f" -{','.join(removed)}" if removed else ""])
        rep["architecture"]["changed_enums"].append(f"{name}:{delta}")
        bump(COMPATIBLE, f"enum {name} changed ({delta.strip()}) — the STATE REPRESENTATION moved. "
                         f"A new member is a new door; prove the illegal source states are REFUSED, "
                         f"not just that the legal one works.")
    for name in sorted(set(nen) - set(oen)):
        rep["architecture"]["changed_enums"].append(f"NEW enum {name}")
        bump(COMPATIBLE, f"new enum {name} ({len(nen[name])} member(s))")
    for name in sorted(set(oen) - set(nen)):
        rep["architecture"]["changed_enums"].append(f"REMOVED enum {name}")
        bump(COMPATIBLE, f"enum {name} was REMOVED")

    # ── configuration ───────────────────────────────────────────────────────────────────────
    oe = set(base_derived.get("configuration", {}).get("env_vars", {}))
    ne = set(head.get("configuration", {}).get("env_vars", {}))
    for v in sorted(ne - oe):
        rep["architecture"]["changed_integrations"].append(f"NEW env var {v}")
        bump(COMPATIBLE, f"new environment variable {v} — must be declared in kb/configuration.json "
                         f"and docs/CONFIG.md")
    for v in sorted(oe - ne):
        rep["architecture"]["changed_integrations"].append(f"REMOVED env var {v}")
        bump(BREAKING, f"environment variable {v} is no longer read — a deployment setting it now "
                       f"has NO EFFECT, silently")

    # ── public surface ──────────────────────────────────────────────────────────────────────
    def routes(d: dict) -> set:
        return {(r["path"], tuple(r["methods"])) for r in d.get("surfaces", {}).get("routes", [])}
    o_r, n_r = routes(base_derived), routes(head)
    for p, m in sorted(n_r - o_r):
        bump(COMPATIBLE, f"new route {' '.join(m)} {p}")
    for p, m in sorted(o_r - n_r):
        bump(BREAKING, f"route REMOVED: {' '.join(m)} {p}")

    ov = set(base_derived.get("surfaces", {}).get("cli_verbs", []))
    nv = set(head.get("surfaces", {}).get("cli_verbs", []))
    for v in sorted(nv - ov):
        bump(COMPATIBLE, f"new CLI verb `{v}`")
    for v in sorted(ov - nv):
        bump(BREAKING, f"CLI verb REMOVED: `{v}`")

    # ── persistence / migration ─────────────────────────────────────────────────────────────
    if any(f.endswith(("ledger.py", "ledger_sqlite.py", "models.py")) for f in touched_src):
        rep["architecture"]["changed_persistence"] += [f for f in touched_src
                                                       if f.endswith(("ledger.py", "ledger_sqlite.py",
                                                                      "models.py"))]
        if _schema_bumped(base):
            bump(MIGRATION, "SCHEMA_VERSION changed — a ledger migration is required, and a NEWER "
                            "on-disk schema is REFUSED, never downgraded (INV-13)")
        else:
            bump(COMPATIBLE, "persistence layer touched without a schema bump")

    # ── side effects ────────────────────────────────────────────────────────────────────────
    ose = base_derived.get("side_effects", {}).get("totals", {})
    nse = head.get("side_effects", {}).get("totals", {})
    for k in sorted(set(ose) | set(nse)):
        if ose.get(k, 0) != nse.get(k, 0):
            rep["architecture"]["changed_side_effects"].append(f"{k}: {ose.get(k,0)} -> {nse.get(k,0)}")
            if k == "network_sites_literal_requests" and nse.get(k, 0) > ose.get(k, 0):
                bump(COMPATIBLE, "NEW network call site(s) — must be registered in kb/side_effects.json")
            elif nse.get(k, 0) != ose.get(k, 0):
                bump(COMPATIBLE, f"side-effect census changed: {k} {ose.get(k,0)} -> {nse.get(k,0)}")

    # ── unsupported constructs introduced ───────────────────────────────────────────────────
    ou = len(base_derived.get("unsupported", {}).get("constructs", []))
    nu = len(head.get("unsupported", {}).get("constructs", []))
    if nu > ou:
        bump(UNKNOWN, f"{nu - ou} new construct(s) the extractor CANNOT statically resolve "
                      f"(dynamic import / computed env key / computed route). The dependency graph "
                      f"is no longer complete for this diff.")

    # ── implementation contract ─────────────────────────────────────────────────────────────
    ocs = base_derived.get("contract_surface", {})
    ncs = head.get("contract_surface", {})
    if ocs.get("available") and ncs.get("available"):
        for sid in sorted(set(ocs["slices"]) | set(ncs["slices"])):
            a = set(ocs["slices"].get(sid, {}).get("owned_files", []))
            b = set(ncs["slices"].get(sid, {}).get("owned_files", []))
            for f in sorted(b - a):
                rep["implementation"]["changed_boundaries"].append(f"slice {sid} GAINED {f}")
                bump(BREAKING, f"slice {sid} silently WIDENED to own {f} — a slice boundary may not "
                               f"expand without a recorded scope change (IMPL-001)")
            for f in sorted(a - b):
                rep["implementation"]["changed_boundaries"].append(f"slice {sid} LOST {f}")
        if ocs["dag"]["acyclic"] and not ncs["dag"]["acyclic"]:
            bump(BREAKING, "the implementation DAG gained a CYCLE — the sequence is unexecutable")

    # ── ratchets / preserved behaviours ─────────────────────────────────────────────────────
    orat = base_derived.get("ratchets", {}).get("measured", {}).get("print_calls_by_module", {})
    nrat = head.get("ratchets", {}).get("measured", {}).get("print_calls_by_module", {})
    if orat.get("fanops.cli") != nrat.get("fanops.cli"):
        rep["implementation"]["changed_preserved_behaviors"].append(
            f"cli.py print() count {orat.get('fanops.cli')} -> {nrat.get('fanops.cli')}")
        bump(COMPATIBLE, f"cli.py print() count changed ({orat.get('fanops.cli')} -> "
                         f"{nrat.get('fanops.cli')}). `_CLI_PRINT_COUNT` is an EXACT-EQUALITY budget "
                         f"shared by three slices — update the test in THIS PR, and only one open PR "
                         f"may move it (GB-6 / IR-4).")

    # "derived/ moved but src/ did not" is a PROXY for "somebody hand-edited a generated file".
    # It is an UNSOUND proxy, and it fires on two perfectly legitimate diffs:
    #
    #   * the artifacts are being ADDED for the first time (this system's own bootstrap PR — there
    #     was nothing to edit), and
    #   * a PR that merely REGENERATES artifacts an earlier PR left stale, which is exactly the
    #     correction we want people to make.
    #
    # And the proxy is unnecessary, because the PROOF is already computed: `stale_artifacts()`
    # regenerates from THIS tree's source and byte-compares. If it is clean, the committed
    # artifacts provably equal what the generator produces — "hand-edited" is not a suspicion that
    # survives that, it is simply false. Only when the byte-compare FAILS is the inference sound,
    # and in that case say what is actually wrong rather than that something "could not be decided".
    #
    # Guessing where a proof is available is how a checker earns a reputation for crying wolf, and
    # a checker nobody believes is a checker nobody reads.
    if touched_derived and not touched_src:
        stale = stale_artifacts()
        if stale:
            bump(UNKNOWN, "derived/ artifacts do NOT match regeneration from this tree's source — "
                          "they were hand-edited or are stale (ARCH-006): "
                          + ", ".join(sorted(d.artifact for d in stale)))
        else:
            bump(COMPATIBLE, "derived/ artifacts changed with no source change, and regeneration "
                             "reproduces them byte-for-byte — a regeneration catching up, not a "
                             "hand-edit.")

    rep["classification"] = worst
    if worst == NO_CHANGE and (touched_src or touched_canonical):
        rep["reasons"].append("Source changed, but no modelled architectural dimension moved.")
    return rep


def _schema_bumped(base: str) -> bool:
    old = _git("show", f"{base}:src/fanops/ledger.py")
    new = (SRC / "ledger.py").read_text(encoding="utf-8") if (SRC / "ledger.py").exists() else ""

    def ver(text: str) -> str | None:
        for line in text.splitlines():
            if "SCHEMA_VERSION" in line and "=" in line:
                return line.split("=", 1)[1].strip()
        return None
    a, b = ver(old), ver(new)
    return a is not None and b is not None and a != b


def render(rep: dict) -> str:
    cls = rep["classification"]
    icon = {NO_CHANGE: "✅", COMPATIBLE: "🟢", MIGRATION: "🟠", BREAKING: "🔴", UNKNOWN: "⚠️"}[cls]
    L = [f"## {icon} Architectural impact: **{cls}**", ""]

    if cls == UNKNOWN:
        L += ["> **UNKNOWN_IMPACT is not a pass.** The blast radius of this diff could not be "
              "computed. It must be resolved, not waived.", ""]

    if rep["reasons"]:
        L += ["### Why", ""]
        L += [f"- {r}" for r in rep["reasons"]]
        L.append("")

    for section, title in (("architecture", "Architecture"), ("implementation", "Implementation contract")):
        rows = [(k, v) for k, v in rep[section].items() if v]
        if not rows:
            continue
        L += [f"### {title}", ""]
        for k, v in rows:
            L.append(f"**{k.replace('_', ' ')}**")
            L += [f"- `{x}`" for x in v[:12]]
            if len(v) > 12:
                L.append(f"- … and {len(v) - 12} more")
            L.append("")

    L += ["<sub>Generated by `python -m tools.arch impact`. "
          f"Base: `{rep['base']}` · {len(rep['changed_files'])} file(s) changed.</sub>"]
    return "\n".join(L)
