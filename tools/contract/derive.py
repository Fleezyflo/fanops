"""R5–R8, R11, R12 — every repository fact, resolved uniformly as `(input, port) -> value + evidence`.

Each derivation either produces a value WITH the source that proves it, or returns a NAMED problem
the caller records in `Derived.unverifiable`. There is no third outcome, and in particular no
plausible default: rule `ST-7` reads `unverifiable`, so an input that could not be resolved can never
be mistaken for an input that was resolved and came back clean.

Note the shape every function here shares — `-> (value, problems)`. It is not decoration. A helper
that returned only the value would have to swallow its own failure to keep that signature, and a
swallowed failure in THIS package produces the exact outcome the package exists to prevent: a
`continue` issued because a check silently did not run.

Three of ADR-0105 §12's four gaps close here, and two were smaller than the ADR's CLI-level reading
suggested:

  G2  path→module→subsystem   — `subsystem_of` is total (134/134, `partition_is_total: true`), so
                                the transform is CHECKABLE AGAINST ITS OWN OUTPUT (`AC-11`).
  G3  reverse closure         — `dependencies.json` `edges[*].compile` is the complete forward graph
                                for all 134 modules, so the closure is invert + BFS, not new
                                extraction. `fan_in_compile` stores counts only, which is what the
                                ADR read as "not computable"; the identities were always in `edges`.
  G4  dead triggers           — resolved in `tools/arch/verifymap.py`, not here (operator decision
                                D-2). This module CONSUMES `required_for` and adds the trait
                                obligations to it; it does not reimplement selection, because
                                ADR-0105 §9 forbids a second selector.
"""
from __future__ import annotations

from collections import deque

from .adapters import PortError
from .classify import module_of

# ADR-0105 §5.1's trait table, as obligation records. The UNION over the trait set is the obligation
# set — never the max, never a worst-wins pick. `obligations()` below is what makes that literal.
TRAIT_OBLIGATIONS = {
    "cross-system": (("OB-IMPACT", "`python -m tools.arch impact` for this diff"),
                     ("OB-VERIFYMAP", "the `verifymap` requirements this diff arms"),
                     ("OB-BLAST", "`blast_radius` — modules reachable from the change"),
                     ("OB-LAWS", "the affected `LAW-*` invariants")),
    "governance": (("OB-ARCH-CI", "`python -m tools.arch ci` green"),
                   ("OB-CI-STATIC", "`python -m tools.ci static` green"),
                   ("OB-NEG-CONTROL", "a firing negative control for every new rule"),
                   ("OB-C18", "Constitution C18.1 / the ADR process"),
                   ("OB-REVERIFY", "re-verify what the change bears on — no evidence reuse")),
    "live": (("OB-OPERATOR", "operator authorization, always"),
             ("OB-EXEC-GATE", "a separate execution gate"),
             ("OB-PREIMAGE", "pre-image capture"),
             ("OB-ROLLBACK-REHEARSAL", "rollback rehearsal"),
             ("OB-REPROBE", "post-mutation re-probe")),
}


# ── R5 · ownership (G2) ─────────────────────────────────────────────────────────────────────
def owners_for(paths, modules_artifact: dict):
    """Return (path→subsystem pairs, sorted subsystem ids, problems).

    ADR-0105 §7: `subsystem_of` is AUTHORITATIVE for source files, and *"filename convention is not
    scope proof"* — a path glob declares intent, the check resolves through the derived map. Where a
    path has no module (docs, config, workflows) ownership is DECLARED and reviewed, never inferred,
    so those paths are simply absent here rather than guessed at.
    """
    sub_of = modules_artifact.get("subsystem_of", {})
    problems: list[str] = []
    if not modules_artifact.get("partition_is_total", False):
        problems.append("modules.json reports partition_is_total: false — ownership is not total, "
                        "and an unowned module has no reviewer, no risk profile and no rules")
    pairs: list[tuple[str, str]] = []
    for p in paths:
        m = module_of(p)
        if m is None:
            continue
        sid = sub_of.get(m)
        if sid is None:
            problems.append(f"{p} maps to module {m}, which no subsystem owns")
            continue
        pairs.append((p, sid))
    return pairs, sorted({s for _, s in pairs}), problems


def totality_holds(modules_artifact: dict) -> bool:
    """`AC-11`: every module the transform can name is a module the canonical set knows."""
    return (bool(modules_artifact.get("partition_is_total"))
            and not modules_artifact.get("unassigned_modules")
            and not modules_artifact.get("ghost_modules"))


# ── R7 · blast radius (G3) ──────────────────────────────────────────────────────────────────
def blast_radius(seed_modules, dependencies_artifact: dict) -> tuple[str, ...]:
    """Reverse-reachable closure over COMPILE edges only, by BFS on the inverted graph.

    Compile-only is deliberate. A `lazy` edge is a DELIBERATE DEFERRAL, already pinned by the
    `must_stay_lazy` ratchet (107 edges, `ARCH-007`) precisely so it stays deferred; treating it as
    reachability would report most of the graph as the blast radius of almost any change, and a
    blast radius that is always "everything" tells a reviewer nothing.
    """
    edges = dependencies_artifact.get("edges", {})
    rev: dict[str, set[str]] = {}
    for src, kinds in edges.items():
        for dst in kinds.get("compile", ()):
            rev.setdefault(dst, set()).add(src)
    seeds = {m for m in seed_modules if m}
    seen, q = set(), deque(sorted(seeds))
    while q:
        cur = q.popleft()
        for dep in sorted(rev.get(cur, ())):
            if dep not in seen:
                seen.add(dep); q.append(dep)
    return tuple(sorted(seen - seeds))


# ── R8 · obligations (G4) ───────────────────────────────────────────────────────────────────
def obligations(impact: dict | None, traits):
    """`verifymap.required_for` ∪ the trait table → (obligations, problems).

    ADR-0105 §9: DO NOT BUILD A SECOND SELECTOR. Trait obligations are ADDED to `required_for`'s
    output, never substituted for it. The result is de-duplicated by obligation id and sorted, so
    the same inputs always produce the same list — `AC-3` depends on that.

    An unreachable `verifymap` is returned as a problem rather than absorbed. Absorbing it would
    silently shrink the obligation set, which is the subtraction ADR-0105 §5.1 forbids, arriving by
    the back door as an import error nobody sees.
    """
    out: dict[str, str] = {}
    problems: list[str] = []
    if impact is not None:
        try:
            from tools.arch import verifymap
        except Exception as exc:
            problems.append(f"tools.arch.verifymap is unimportable ({type(exc).__name__}: {exc}) — "
                            f"the impact-side obligations could not be selected")
        else:
            try:
                for r in verifymap.required_for(impact):
                    out[f"OB-VM-{r.trigger}"] = f"{r.verification} (armed by `{r.trigger}`)"
            except Exception as exc:
                problems.append(f"verifymap.required_for failed ({type(exc).__name__}: {exc})")
    for t in sorted(traits):
        for oid, what in TRAIT_OBLIGATIONS.get(t, ()):
            out[oid] = what
    return tuple(sorted(out.items())), problems


def obligations_are_monotone(smaller, larger) -> bool:
    """`AC-5`: obligations(T) ⊆ obligations(T ∪ {t}). Adding a trait may only ADD obligations."""
    return {oid for oid, _ in smaller} <= {oid for oid, _ in larger}


# ── R11 · authority ─────────────────────────────────────────────────────────────────────────
_NAMESPACES = ("LAW-", "STD-", "ADR-", "DC-", "GOV-", "ARCH-", "IMPL-", "NC-", "SCHEMA")


def authority_namespace_ok(cid: str) -> bool:
    """`C*` (Constitution) ids are `C` + a digit; everything else carries an explicit prefix."""
    return cid.startswith(_NAMESPACES) or (cid[:1] == "C" and cid[1:2].isdigit())


def authority_state(rows, repo, ref: str, control_ids: set[str] | None):
    """Resolve each cited authority: does the id exist, and has its file blob moved since approval?

    ADR-0105 §4.4 is explicit that a blob mismatch **FLAGS for re-confirmation and does not
    auto-void**. File granularity is coarse — per-rule granularity needs an extractor that does not
    exist — so voiding on it would invalidate every open contract whenever anyone edited
    `ARCHITECTURAL_LAWS.md`. Flag-not-void has no false negative: nothing is missed, a human simply
    confirms.
    """
    out, problems = [], []
    for row in rows:
        cid, src, recorded = row.get("id", ""), row.get("source_file", ""), row.get("blob_sha", "")
        if not authority_namespace_ok(cid):
            problems.append(("AUTH-NAMESPACE", cid,
                             f"{cid!r} is not a `C*` / `LAW-*` / `STD-*` / control / `ADR-NNNN` id"))
        if control_ids is not None and cid.startswith(("DC-", "GOV-", "SCHEMA")) \
                and cid not in control_ids:
            problems.append(("AUTH-UNKNOWN", cid, f"control id {cid!r} is in no registry row"))
        now, resolved = None, True
        try:
            now = repo.blob_sha(ref, src) if src else None
        except PortError as exc:
            resolved = False
            problems.append(("AUTH-UNVERIFIABLE", cid, str(exc)))
        if resolved:
            if src and now is None:
                problems.append(("AUTH-MISSING-FILE", cid, f"{src} does not exist at {ref}"))
            elif now is not None and recorded and now != recorded:
                problems.append(("AUTH-BLOB-MOVED", cid,
                                 f"{src} blob is {now[:12]}…, contract recorded {recorded[:12]}…"))
        out.append((cid, now or "", now is not None))
    return out, problems


# ── R12 · evidence reuse (ADR-0105 §8) ──────────────────────────────────────────────────────
def evidence_state(rows, repo, ref: str, traits):
    """`I1`, `I2`, `I4`, `I5`. `I3` is judgement and is RECORDED, never decided.

    Two freshness regimes and NO wall-clock constant, exactly as §8 requires: source-bound evidence
    is fresh while its blob is unchanged, with no expiry — a fact proven about a file that has not
    changed does not become false with time — and live-bound evidence is re-proven immediately
    before the mutation it authorizes, which `I4` already compels. A constant such as "evidence
    expires after 7 days" is refused deliberately: it would be arbitrary, would rot, and would
    itself become the stale prose number `LAW-SOT-03` governs.
    """
    problems, claims = [], {}
    live = "live" in traits
    for row in rows:
        claim, binding = row.get("claim", ""), row.get("binding", "")
        proven_by, proven_at = row.get("proven_by", ""), row.get("proven_at", "")
        if not (claim and proven_by and proven_at and binding):
            problems.append(("EV-SHAPE", claim,
                             "a record needs all of {claim, proven_by, proven_at, binding}"))
            continue
        if claim in claims and claims[claim] != (proven_by, binding):
            problems.append(("I2", claim, f"two records make conflicting claims about {claim!r} — "
                                          f"both are recorded; same precedence ⇒ escalate"))
        claims[claim] = (proven_by, binding)
        if live:
            problems.append(("I4", claim, "the `live` trait invalidates reuse REGARDLESS OF AGE; "
                                          "re-prove immediately before execution"))
            continue
        kind, _, target = binding.partition(":")
        if kind in ("blob", "file") and target.strip():
            path = target.strip()
            try:
                now = repo.blob_sha(ref, path)
            except PortError as exc:
                problems.append(("EV-UNVERIFIABLE", claim, str(exc)))
                continue
            if now is None:
                problems.append(("I1", claim, f"the bound source {path} does not exist at {ref}"))
            elif proven_at and now != proven_at:
                problems.append(("I1", claim, f"the bound source {path} changed since the proof "
                                              f"({proven_at[:12]}… → {now[:12]}…) — invalid FOR THE "
                                              f"CHANGED PART ONLY"))
    return problems
