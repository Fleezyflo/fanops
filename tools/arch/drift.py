"""Drift detection: regenerate, byte-compare, and EXPLAIN the semantic difference.

Never rely on a textual diff alone. A textual diff tells you a byte moved; it does not tell you
that a module lost its owner, that a state machine gained a transition, or that a slice boundary
widened. Every drift here is CLASSIFIED, and the classification is what CI acts on.
"""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .common import DERIVED, SRC
from .generate import generate

# Architecture drift classes
ARCH_CLASSES = ("ownership", "dependency", "lifecycle", "persistence", "configuration",
                "side_effects", "public_surface", "external_integration", "invariants", "unknowns")
# Implementation drift classes
IMPL_CLASSES = ("slice_dag", "slice_ownership", "slice_boundary", "implementation_sequence",
                "verification_mapping", "rollback_mapping", "merge_gates", "preserved_behaviors")


@dataclass
class Drift:
    kind: str            # "generated_artifact_stale" | "semantic"
    dimension: str       # one of ARCH_CLASSES / IMPL_CLASSES
    artifact: str
    detail: str
    evidence: list[str] = field(default_factory=list)


def stale_artifacts(derived_dir: Path | None = None) -> list[Drift]:
    """Regenerate into a temp dir and byte-compare against what is committed.

    This is the single check that makes every other one trustworthy: if the committed derived
    artifacts do not equal what the generator produces, then every claim downstream of them is
    a claim about a file somebody hand-edited.

    `= None`, not `= DERIVED`: a default argument is bound ONCE at import, so a module-level
    default cannot be redirected by the negative-control fixture (which isolates the checkers by
    reassigning these globals). That trap silently defeated NC-23. Resolve globals at CALL time.
    """
    derived_dir = derived_dir or DERIVED
    out: list[Drift] = []
    tmp = Path(tempfile.mkdtemp(prefix="arch-drift-"))
    try:
        generate(src=SRC, out=tmp)
        committed = {p.name: p.read_text(encoding="utf-8") for p in derived_dir.glob("*.json")} \
            if derived_dir.exists() else {}
        fresh = {p.name: p.read_text(encoding="utf-8") for p in tmp.glob("*.json")}

        for name in sorted(set(committed) | set(fresh)):
            a, b = committed.get(name), fresh.get(name)
            if a == b:
                continue
            if a is None:
                out.append(Drift("generated_artifact_stale", "public_surface", name,
                                 "derived artifact is MISSING from the repository", []))
            elif b is None:
                out.append(Drift("generated_artifact_stale", "public_surface", name,
                                 "committed derived artifact is no longer produced by the generator", []))
            else:
                out.append(Drift("generated_artifact_stale", _dimension_of(name), name,
                                 "committed bytes differ from regeneration — the file is STALE or "
                                 "was HAND-EDITED",
                                 _explain(name, load_str(a), load_str(b))))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def stale_docs() -> list[Drift]:
    """The GENERATED docs must equal what the renderer produces, byte for byte.

    `docs/ARCHITECTURE_GOVERNANCE.md` is generated exclusively from the machine artifacts, but it
    does not live under `derived/`, so `stale_artifacts` never looked at it. Without this check a
    hand-edit to the governance doc merges green and the human-readable account of the
    architecture silently diverges from the machine-readable one — which is precisely how a doc
    comes to "name a mechanism that does not exist" (AR-03), the defect this repo keeps shipping.
    """
    from .render import expected
    out: list[Drift] = []
    for path, want in expected().items():
        have = path.read_text(encoding="utf-8") if path.exists() else None
        if have == want:
            continue
        rel = path.name
        out.append(Drift(
            "generated_artifact_stale", "public_surface", rel,
            "generated doc is MISSING — run `python -m tools.arch docs`" if have is None else
            "generated doc was HAND-EDITED or is STALE — it no longer equals what the renderer "
            "produces from the canonical artifacts. Run `python -m tools.arch docs`.",
            [] if have is None else _line_delta(have, want)))
    return out


def _line_delta(have: str, want: str, limit: int = 6) -> list[str]:
    import difflib
    d = [ln for ln in difflib.unified_diff(have.splitlines(), want.splitlines(),
                                           "on-disk", "regenerated", lineterm="", n=0)
         if ln and ln[0] in "+-" and not ln.startswith(("+++", "---"))]
    # Lead with lines that carry TEXT. A drift whose first reported line is a bare "-" (a blank
    # line moved) reads as no evidence at all, and a finding without legible evidence is a finding
    # nobody acts on. Whitespace-only diffs still report — they just do not get to go first.
    d = [ln for ln in d if ln[1:].strip()] or d
    return d[:limit] + ([f"… and {len(d) - limit} more line(s)"] if len(d) > limit else [])


def load_str(text: str) -> dict:
    import json
    return json.loads(text)


_DIM = {
    "modules.json": "ownership",
    "dependencies.json": "dependency",
    "side_effects.json": "side_effects",
    "configuration.json": "configuration",
    "surfaces.json": "public_surface",
    "ratchets.json": "preserved_behaviors",
    "entities.json": "lifecycle",
    "contract_surface.json": "slice_ownership",
    "unsupported.json": "unknowns",
    "MANIFEST.json": "public_surface",
}


def _dimension_of(name: str) -> str:
    return _DIM.get(name, "public_surface")


def _explain(name: str, old: dict, new: dict) -> list[str]:
    """Say WHAT changed, semantically — not that a byte moved."""
    ev: list[str] = []

    if name == "modules.json":
        ev += _set_delta("module", set(old.get("modules", [])), set(new.get("modules", [])))
        o_un, n_un = set(old.get("unassigned_modules", [])), set(new.get("unassigned_modules", []))
        ev += [f"module lost its subsystem: {m}" for m in sorted(n_un - o_un)]
        ev += [f"module gained a subsystem: {m}" for m in sorted(o_un - n_un)]

    elif name == "dependencies.json":
        for k, ov in (old.get("totals") or {}).items():
            nv = (new.get("totals") or {}).get(k)
            if nv != ov:
                ev.append(f"{k}: {ov} -> {nv}")
        oc = {(s, t) for s, d in old.get("edges", {}).items() for t in d["compile"]}
        nc = {(s, t) for s, d in new.get("edges", {}).items() for t in d["compile"]}
        ev += [f"NEW compile-time edge: {s} -> {t}" for s, t in sorted(nc - oc)]
        ev += [f"REMOVED compile-time edge: {s} -> {t}" for s, t in sorted(oc - nc)]

    elif name == "configuration.json":
        ev += _set_delta("env var", set(old.get("env_vars", {})), set(new.get("env_vars", {})))

    elif name == "surfaces.json":
        o = {(r["path"], tuple(r["methods"])) for r in old.get("routes", [])}
        n = {(r["path"], tuple(r["methods"])) for r in new.get("routes", [])}
        ev += [f"NEW route: {' '.join(m)} {p}" for p, m in sorted(n - o)]
        ev += [f"REMOVED route: {' '.join(m)} {p}" for p, m in sorted(o - n)]
        ev += _set_delta("CLI verb", set(old.get("cli_verbs", [])), set(new.get("cli_verbs", [])))

    elif name == "side_effects.json":
        for k, ov in (old.get("totals") or {}).items():
            nv = (new.get("totals") or {}).get(k)
            if nv != ov:
                ev.append(f"{k}: {ov} -> {nv}")

    elif name == "ratchets.json":
        op = (old.get("measured") or {}).get("print_calls_by_module", {})
        np_ = (new.get("measured") or {}).get("print_calls_by_module", {})
        for m in sorted(set(op) | set(np_)):
            if op.get(m, 0) != np_.get(m, 0):
                ev.append(f"print() count {m}: {op.get(m, 0)} -> {np_.get(m, 0)}")

    elif name == "contract_surface.json":
        os_, ns = old.get("slices", {}), new.get("slices", {})
        ev += _set_delta("slice", set(os_), set(ns))
        for sid in sorted(set(os_) & set(ns)):
            a, b = set(os_[sid]["owned_files"]), set(ns[sid]["owned_files"])
            ev += [f"slice {sid} GAINED file {f}" for f in sorted(b - a)]
            ev += [f"slice {sid} LOST file {f}" for f in sorted(a - b)]

    if not ev:
        ev.append("bytes differ but no modelled dimension changed — inspect the raw diff")
    return ev[:40]


def _set_delta(label: str, old: set, new: set) -> list[str]:
    return ([f"NEW {label}: {x}" for x in sorted(new - old)]
            + [f"REMOVED {label}: {x}" for x in sorted(old - new)])


def all_stale(derived_dir: Path | None = None) -> list[Drift]:
    """EVERY generated file, in one place: the derived JSON *and* the generated docs.

    There is exactly one caller-facing entry point for generated-artifact integrity, because the
    previous shape had two: `cmd_drift` called `stale_artifacts()` directly while a tidy-looking
    `report()` — which nobody called — was the only thing that would have combined them. Adding a
    check to the uncalled function is indistinguishable from not adding it at all. If a check does
    not run in the gate, it does not exist.
    """
    return stale_artifacts(derived_dir) + stale_docs()
