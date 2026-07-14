"""Deterministic generation of every DERIVED canonical artifact.

Running this twice without a code change MUST produce byte-identical output. That is a hard
requirement, not an aspiration — it is what makes drift detection meaningful. If regeneration
were non-deterministic, every run would produce a diff, reviewers would learn to ignore the
diff, and the gate would be decorative.

What this module does NOT do: it does not touch `kb/` or `contract/`. Those are the CANONICAL
DECLARED artifacts of Cycles 5 and 6. They are validated against `derived/`, never overwritten.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from .common import (CONTRACT, DERIVED, KB, REPO, SRC, TESTS, dumps, load, provenance,
                     sha256_text, write)
from .extract import extract
from .graph import build, tarjan_scc

_S = "fanops-arch/derived"

# Mirrors tests/test_swallow_ratchet.py::_EXEMPT — errors.py HOUSES the fail_open implementation
# and is never ratchet-counted. Diverging from the test here would make this census disagree with
# the gate it is meant to cross-check, which is worse than not measuring it at all.
_SWALLOW_EXEMPT = frozenset({"src/fanops/errors.py"})


# ── the ratchets: parse the TEST FILES, which are the canonical enforcement ──────────────────
def _declared_ratchets() -> dict:
    """Read the baselines the CI ratchet tests actually enforce.

    These tests are the canonical owners of the print/swallow budgets. The implementation
    contract *copies* their numbers into prose (`GB-6`), and a copy is a thing that rots. This
    reads the source of truth so the copy can be checked against it.
    """
    out: dict = {"print": {}, "swallow": {}, "unsupported": []}

    p = TESTS / "test_internal_prints_routed.py"
    if p.exists():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name == "_CLI_PRINT_COUNT" and isinstance(node.value, ast.Constant):
                    out["print"]["cli_print_count"] = node.value.value
                if name == "_INTERNAL_MODULES" and isinstance(node.value, (ast.Tuple, ast.List)):
                    out["print"]["zero_print_modules"] = sorted(
                        e.value for e in node.value.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str))

    s = TESTS / "test_swallow_ratchet.py"
    if s.exists():
        tree = ast.parse(s.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_baseline_silent_swallows":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Dict):
                        base = {}
                        for k, v in zip(sub.keys, sub.values):
                            if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                                base[k.value] = v.value
                        if base:
                            out["swallow"]["baseline"] = dict(sorted(base.items()))
                        break
    if "baseline" not in out["swallow"]:
        out["unsupported"].append({
            "kind": "unparsed_swallow_baseline",
            "evidence": "tests/test_swallow_ratchet.py::_baseline_silent_swallows",
            "why": "the baseline dict literal could not be read; the budget cannot be cross-checked",
        })
    return out


# ── the implementation contract, resolved against the real AST ───────────────────────────────
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _functions_in(path: Path) -> set[str]:
    """Every function/method name defined in a file, at any nesting depth."""
    if not path.exists():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _parse_permitted(entry: str, present: set[str]) -> tuple[list[str], str | None]:
    """Pull identifiers out of a `permitted_functions` entry.

    The Cycle-6 contract writes these as PROSE — e.g. "the daemon tick loop (:1300-1313)" and
    "a NEW `cmd_clean` + its argparse registration". Prose is not mechanically enforceable. We
    resolve every identifier we can find against the file's real function set; anything we cannot
    resolve is returned as an UNKNOWN with evidence, never silently dropped.
    """
    hits = [t for t in _IDENT.findall(entry) if t in present]
    if hits:
        return sorted(set(hits)), None
    return [], entry


def _contract_surface() -> dict:
    """Resolve the DECLARED implementation contract against the code that actually exists.

    This is the bridge between §4 of the governance brief ("extract slices, boundaries, touched
    files, touched functions") and reality: a declared boundary that names a file or function
    which does not exist is not a boundary, it is a wish.
    """
    fo_path = CONTRACT / "file_ownership.json"
    ic_path = CONTRACT / "implementation_contract.json"
    if not fo_path.exists() or not ic_path.exists():
        return {"available": False}

    fo = load(fo_path)
    ic = load(ic_path)

    files: dict[str, dict] = {}
    unresolved: list[dict] = []

    for rel, spec in sorted(fo.get("ownership", {}).items()):
        path = REPO / rel
        exists = path.exists()
        present = _functions_in(path) if exists else set()
        owner_raw = str(spec.get("owner", ""))
        partition = spec.get("partition") or {}
        owners = sorted(partition) if partition else \
            sorted(set(_IDENT.findall(owner_raw)) & _KNOWN_SLICES(ic))

        regions: dict[str, dict] = {}
        for slice_id, part in sorted(partition.items()):
            resolved, unres = [], []
            for entry in part.get("permitted_functions", []) or []:
                fns, bad = _parse_permitted(str(entry), present)
                resolved += fns
                if bad is not None:
                    unres.append(bad)
                    unresolved.append({"file": rel, "slice": slice_id, "entry": bad,
                                       "why": "no identifier in this entry names a function that "
                                              "exists in the file; the boundary is PROSE, not a "
                                              "machine-checkable predicate"})
            regions[slice_id] = {"functions": sorted(set(resolved)),
                                 "unresolved_entries": sorted(unres)}

        # A slice may legitimately own a file that does not exist YET (S11/S12 are GUARD slices
        # whose entire deliverable is a NEW test file). "Planned" and "missing" are different
        # facts and collapsing them would either hide a real break or cry wolf on a real plan.
        planned_new = "NEW FILE" in str(spec.get("status", "")).upper()

        files[rel] = {
            "exists": exists,
            "planned_new": planned_new,
            "missing": (not exists) and (not planned_new),
            "owners": owners,
            "is_partitioned": bool(partition),
            "regions": regions,
            "declared_ratchets": spec.get("ratchets", {}),
            "function_count": len(present),
        }

    slices = {}
    for s in ic.get("slices", []):
        slices[s["id"]] = {
            "class": s.get("class"),
            "root_causes": sorted(s.get("root_causes", []) or []),
            "prerequisites": sorted(s.get("prerequisites", []) or []),
            "co_required_with": sorted(s.get("co_required_with", []) or []),
            "status": s.get("status"),
            "self_merge_on_green": s.get("self_merge_on_green"),
            "approvals_required": sorted(s.get("approvals_required", []) or []),
            "owned_files": sorted(r for r, f in files.items() if s["id"] in f["owners"]),
        }

    # the slice DAG, derived — and its acyclicity PROVEN, not asserted
    dag = ic.get("implementation_dag", {})
    edges = [(e["from"], e["to"]) for e in dag.get("ordering_edges", [])]
    adj: dict[str, list[str]] = {sid: [] for sid in slices}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, [])
    sccs = tarjan_scc(sorted(adj), {k: sorted(v) for k, v in adj.items()})
    cycles = [c for c in sccs if len(c) > 1]

    return {
        "available": True,
        "files": files,
        "slices": dict(sorted(slices.items())),
        "dag": {
            "ordering_edges": sorted([list(e) for e in edges]),
            "co_requirements": sorted(
                [sorted([p["a"], p["b"]]) for p in
                 dag.get("co_requirements_NOT_ordering_edges", {}).get("pairs", [])]),
            "acyclic": not cycles,
            "cycles": [sorted(c) for c in cycles],
            "declared_acyclic": dag.get("acyclic"),
        },
        "unresolved_boundaries": sorted(unresolved, key=lambda d: (d["file"], d["slice"], d["entry"])),
    }


def _KNOWN_SLICES(ic: dict) -> set[str]:
    return {s["id"] for s in ic.get("slices", [])}


# ── generation ──────────────────────────────────────────────────────────────────────────────
def generate(src: Path | None = None, out: Path | None = None) -> dict[str, bool]:
    # `= None`, not `= SRC` / `= DERIVED`: default args bind ONCE at import, so a module-level
    # default cannot be redirected by the selftest fixture. See drift.stale_artifacts().
    src = src or SRC
    out = out or DERIVED
    ex = extract(src)
    g = build(ex["edges"])
    mf = ex["module_facts"]
    changed: dict[str, bool] = {}

    def emit(name: str, payload: dict, *, inputs: list[str], confidence: str = "CERTAIN") -> None:
        body = {**provenance(f"{_S}/{name}", inputs=inputs, confidence=confidence), **payload}
        changed[name] = write(out / f"{name}.json", body)

    # -- modules + the subsystem partition (DECLARED) checked for TOTALITY -------------------
    declared_partition: dict[str, str] = {}
    sub_path = KB / "subsystems.json"
    if sub_path.exists():
        for sid, spec in load(sub_path)["subsystems"].items():
            for m in spec["modules"]:
                declared_partition[f"fanops.{m}" if m != "__init__" else "fanops"] = sid
    modules = ex["modules"]
    assigned = {m: declared_partition.get(m) for m in modules}
    unassigned = sorted(m for m, s in assigned.items() if s is None)
    ghosts = sorted(set(declared_partition) - set(modules))

    emit("modules", {
        "totals": {"modules": len(modules), "assigned": len(modules) - len(unassigned),
                   "unassigned": len(unassigned), "ghosts": len(ghosts)},
        "modules": modules,
        "subsystem_of": dict(sorted(assigned.items())),
        "partition_is_total": not unassigned and not ghosts,
        "unassigned_modules": unassigned,
        "ghost_modules": ghosts,
        "note": "The subsystem partition is DECLARED in kb/subsystems.json (an analytic overlay "
                "imposed by Cycle 5, enforced by nothing in the code). The MODULE SET is DERIVED. "
                "Totality is therefore a CHECKABLE property, and this is where it is checked.",
    }, inputs=["src/fanops/**/*.py", ".reports/architecture/kb/subsystems.json"])

    # -- the dependency graph ----------------------------------------------------------------
    emit("dependencies", {
        **g,
        "edges": ex["edges"],
        "external_packages": ex["external"],
        "metric_definitions": {
            "compile_edges_G1": "unique (source, target) pairs where the import is at MODULE LEVEL. "
                                "`from P import n` yields an edge to P (P/__init__.py must execute) "
                                "AND to P.n when n is itself a module.",
            "lazy_edges": "unique (source, target) pairs where the import sits inside a FunctionDef. "
                          "Materializes only if the function is CALLED.",
            "level": "longest-path depth over the SCC-CONDENSATION of G1 (a DAG by construction). "
                     "level(X)=0 if X imports nothing internal; else 1+max(level(targets)).",
            "lazy_edges_strictly_upward": "lazy edges where level(target) > level(source) — TRUE "
                                          "layering inversions, legal ONLY because deferred to call time.",
            "lazy_edges_lateral": "lazy edges where level(target) == level(source). NOT inversions.",
        },
    }, inputs=["src/fanops/**/*.py"])

    # -- side effects ------------------------------------------------------------------------
    def sites(attr: str) -> dict:
        return {m: [dict(s) for s in getattr(f, attr)] for m, f in sorted(mf.items())
                if getattr(f, attr)}

    emit("side_effects", {
        "totals": {
            "subprocess_sites": sum(len(f.subprocess_sites) for f in mf.values()),
            "network_sites_literal_requests": sum(len(f.network_sites) for f in mf.values()),
            "ledger_transaction_sites": sum(len(f.txn_sites) for f in mf.values()),
            "lock_sites": sum(len(f.lock_sites) for f in mf.values()),
            "mkdtemp_sites": sum(len(f.mkdtemp_sites) for f in mf.values()),
            "rmtree_sites": sum(len(f.rmtree_sites) for f in mf.values()),
            "env_write_sites": sum(len(f.env_writes) for f in mf.values()),
        },
        "subprocess": sites("subprocess_sites"),
        "network": sites("network_sites"),
        "ledger_transaction": sites("txn_sites"),
        "locks": sites("lock_sites"),
        "mkdtemp": sites("mkdtemp_sites"),
        "rmtree": sites("rmtree_sites"),
        "env_writes": sites("env_writes"),
        "known_blind_spot": "meta_graph uses an INJECTABLE `get` (so tests never touch the network). "
                            "It is a real network seam and a literal `requests.*` census does NOT see it. "
                            "Recorded here so the census is not mistaken for the whole truth.",
    }, inputs=["src/fanops/**/*.py"])

    # -- configuration -----------------------------------------------------------------------
    env_reads: dict[str, list[str]] = {}
    for m, f in sorted(mf.items()):
        for r in f.env_reads:
            env_reads.setdefault(r["var"], []).append(f"{m}:{r['line']}")
    emit("configuration", {
        "totals": {"env_vars_read": len(env_reads),
                   "env_write_sites": sum(len(f.env_writes) for f in mf.values())},
        "env_vars": {k: {"read_at": sorted(v), "reader_count": len(v)}
                     for k, v in sorted(env_reads.items())},
        "env_writes": {m: [dict(w) for w in f.env_writes] for m, f in sorted(mf.items()) if f.env_writes},
    }, inputs=["src/fanops/**/*.py"])

    # -- surfaces: routes + CLI verbs, WITH their definitions ---------------------------------
    routes = sorted(([dict(r, module=m) for m, f in mf.items() for r in f.routes]),
                    key=lambda r: (r["path"], r["methods"], r["module"]))
    verbs = sorted({v["verb"] for f in mf.values() for v in f.cli_verbs})
    verb_sites = sorted(([dict(v, module=m) for m, f in mf.items() for v in f.cli_verbs]),
                        key=lambda v: (v["verb"], v["module"], v["line"]))
    emit("surfaces", {
        "totals": {
            "route_endpoints": len(routes),
            "route_endpoints_mutating": sum(1 for r in routes if r["mutating"]),
            "route_unique_paths": len({r["path"] for r in routes}),
            "cli_verbs_unique": len(verbs),
            "cli_add_parser_sites": len(verb_sites),
        },
        "metric_definitions": {
            "route_endpoints": "one per (method, path) — the Flask unit. `/golive/live` has a GET "
                               "page AND a POST that flips the system to LIVE PUBLISHING; keying on "
                               "PATH collapses them into one, which is why the endpoint is canonical.",
            "route_unique_paths": "distinct URL paths. Cycle 5's '149 routes' is THIS metric.",
            "cli_verbs_unique": "distinct verb names.",
            "cli_add_parser_sites": "add_parser() call sites — higher, because nested subparsers "
                                    "(e.g. `recover audit`) register additional verbs. Cycle 5's "
                                    "`verbs_total: 59` is THIS metric, while its enumerated list is the 54.",
        },
        "routes": routes,
        "cli_verbs": verbs,
        "cli_verb_sites": verb_sites,
    }, inputs=["src/fanops/**/*.py"])

    # -- ratchets: measured AND declared, so the copy can be checked against the original ------
    declared = _declared_ratchets()
    measured_prints = {m: len(f.print_calls) for m, f in sorted(mf.items()) if f.print_calls}
    # Paths are made relative to the SRC TREE'S OWN ROOT, not to the checkout — because `impact`
    # regenerates against a HISTORICAL tree unpacked into a temp dir, and hard-coding REPO here
    # made that crash. (It then surfaced as UNKNOWN_IMPACT, which is a checker lying about why it
    # failed.)
    tree_root = src.parents[1] if src.name == "fanops" and src.parent.name == "src" else REPO
    measured_swallows = {}
    for m, f in sorted(mf.items()):
        if f.silent_broad_excepts:
            p = Path(f.path)
            rel = p.relative_to(tree_root).as_posix() if p.is_absolute() and p.is_relative_to(tree_root) \
                else p.as_posix()
            if rel in _SWALLOW_EXEMPT:
                continue   # mirrors tests/test_swallow_ratchet.py::_EXEMPT — errors.py HOUSES fail_open
            measured_swallows[rel] = len(f.silent_broad_excepts)
    emit("ratchets", {
        "measured": {
            "print_calls_by_module": measured_prints,
            "silent_broad_except_by_file": dict(sorted(measured_swallows.items())),
        },
        "declared_by_the_ci_tests": declared,
        "note": "The two AST ratchet TESTS are the canonical enforcement. This block MEASURES the "
                "same properties so a third party can check the numbers the implementation contract "
                "COPIED from them. A copied number is a number that rots.",
    }, inputs=["src/fanops/**/*.py", "tests/test_internal_prints_routed.py",
               "tests/test_swallow_ratchet.py"])

    # -- entities ----------------------------------------------------------------------------
    emit("entities", {
        "enums": {m: [dict(e) for e in f.enums] for m, f in sorted(mf.items()) if f.enums},
        "models": {m: [dict(x) for x in f.models] for m, f in sorted(mf.items()) if f.models},
    }, inputs=["src/fanops/**/*.py"])

    # -- the implementation contract, resolved against the AST --------------------------------
    cs = _contract_surface()
    emit("contract_surface", cs,
         inputs=[".reports/architecture/contract/file_ownership.json",
                 ".reports/architecture/contract/implementation_contract.json",
                 "src/fanops/**/*.py"],
         confidence="CERTAIN" if cs.get("available") else "UNKNOWN")

    # -- unsupported constructs: NEVER silently omitted ---------------------------------------
    unsupported = sorted(
        ([dict(u, module=m) for m, f in mf.items() for u in f.unsupported]
         + [dict(u, module="<ratchets>") for u in declared["unsupported"]]),
        key=lambda u: (u["module"], u["kind"], u.get("line", 0)))
    emit("unsupported", {
        "totals": {"unsupported_constructs": len(unsupported)},
        "constructs": unsupported,
        "contract_boundaries_that_are_prose_not_predicates": cs.get("unresolved_boundaries", []),
        "note": "A construct this extractor cannot resolve is recorded, never dropped. An omitted "
                "construct is indistinguishable from an absent one.",
    }, inputs=["src/fanops/**/*.py", ".reports/architecture/contract/file_ownership.json"])

    # -- the fingerprint over every derived artifact ------------------------------------------
    files = sorted(p for p in out.glob("*.json") if p.name != "MANIFEST.json")
    digests = {p.name: sha256_text(p.read_text(encoding="utf-8")) for p in files}
    manifest = {
        **provenance(f"{_S}/MANIFEST", inputs=["src/fanops/**/*.py"]),
        "artifacts": digests,
        "fingerprint": sha256_text(dumps(digests)),
        "determinism_contract": "Regenerating with no source change MUST reproduce these digests "
                                "byte-for-byte. Nothing here stamps a wall-clock time: a generated "
                                "artifact that changes on every run trains reviewers to ignore its diff.",
    }
    changed["MANIFEST"] = write(out / "MANIFEST.json", manifest)
    return changed
