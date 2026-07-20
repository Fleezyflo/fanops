"""R9 (triggers `T1`–`T6`) and R10 (traits, `risk_tier`, per-file labels).

This module's output is an INPUT to `derive.py`, not a peer of it. Traits select obligations, so
traits must be settled before obligations are resolved; merging the two would hide a one-directional
ordering the union rule (ADR-0105 §5.1) depends on, and a later reader would have no way to see that
reversing it is a defect rather than a refactor.

ADDITIVE, NEVER SUBTRACTIVE. Traits are a set, obligations are the union over that set, and no trait
removes another's obligation. `risk_tier` exists ONLY to choose the breach response in §10 — it
never selects obligations, because under a worst-wins model a `{governance, live}` change would be
classified `live` and silently lose every governance obligation. That is not hypothetical: ADR-0105
§5.2(c) records it as the defect the section exists to remove, and it is Phase 5's actual work.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .model import Diagnostic, Trigger, UNKNOWN

# The `T3` governance-surface set, transcribed from ADR-0105 §1. IT LIVES ONLY THERE AND HERE, and
# `NC-C27` pins this copy against the ADR body so the two cannot drift: a rule that reads its own
# governing document must have that document pinned, or the rule quietly stops meaning what the
# document says.
#
# `ADR_0105_DIGEST` pins the exact approved body this transcription was taken from. Recomputing it
# is how `NC-C27` proves the two have not diverged: the ADR is the only authority for `T3`, and a
# copy of an authority that can drift from it silently is not a copy, it is a second authority.
ADR_0105_DIGEST = "sha256:815635d3fd95efb9e5be0637bcb68c2ab7a1e638ff1ede16c62c257b1e2e6a3a"

T3_PATTERNS = ("docs/REPOSITORY_CONSTITUTION.md", "docs/ARCHITECTURAL_LAWS.md",
               "docs/ENGINEERING_STANDARDS.md", "docs/adr/**", "docs/governance/**",
               ".github/ci-control-registry.yml", ".github/workflows/**", "tools/arch/**",
               "tools/ci/**", "tools/contract/**", ".agents/lanes.json", ".orchestration/**")

CONTRACTS_DIR = "docs/contracts/"

# ADR-0105 §T5 reuses `.orchestration/SPEC.md`'s calibrated threshold VERBATIM. Not a new number:
# the repository already paid to calibrate this one, with a recorded rationale (CI cannot catch an
# implementer grading their own homework, so an independent read is bought where that costs most).
BREADTH_THRESHOLD = 5

_RX_CACHE: dict[str, re.Pattern] = {}


def matches(path: str, pattern: str) -> bool:
    """Glob match where `**` crosses `/` and `*` does not. `fnmatch` conflates the two."""
    rx = _RX_CACHE.get(pattern)
    if rx is None:
        out, i = [], 0
        while i < len(pattern):
            if pattern.startswith("**", i): out.append(".*"); i += 2
            elif pattern[i] == "*": out.append("[^/]*"); i += 1
            elif pattern[i] == "?": out.append("[^/]"); i += 1
            else: out.append(re.escape(pattern[i])); i += 1
        rx = _RX_CACHE[pattern] = re.compile("^" + "".join(out) + "$")
    return bool(rx.match(path))


def any_match(path: str, patterns) -> bool:
    return any(matches(path, p) for p in patterns)


def module_of(path: str) -> str | None:
    """G2 — the path→module transform ADR-0105 §12 records as missing.

    `subsystem_of` is module-keyed and diffs are path-keyed, so nothing could join them. The
    convention is `generate.py:225-227`'s, including its `__init__` special case: a package's
    `__init__.py` IS the package, not a submodule of it.
    """
    if not path.startswith("src/fanops/") or not path.endswith(".py"):
        return None
    rel = path[len("src/fanops/"):-len(".py")]
    parts = [p for p in rel.split("/") if p]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(["fanops", *parts])


# ── triggers ────────────────────────────────────────────────────────────────────────────────
def t3_operation_is_monotone(path: str, base_blob: bytes | None, head_blob: bytes | None,
                             boundary: bytes) -> bool:
    """ADR-0105 §3.6: `docs/contracts/**` is CONDITIONALLY outside `T3`.

    Monotone (does NOT trigger `T3`): creating a contract; appending a valid lifecycle event.
    Non-monotone (DOES trigger): editing a declaration, moving the boundary, rewriting or reordering
    lifecycle history, deleting the file.

    The asymmetry is the whole point — writing the record is routine, rewriting the record is
    governance. An append is auditable because nothing already written changes meaning; every
    non-monotone operation can make an earlier authorization say something it did not say when it
    was given.
    """
    if base_blob is None:
        return head_blob is not None                 # creation is monotone; nothing else can be
    if head_blob is None:
        return False                                 # deletion destroys the audit trail
    a = base_blob.split(boundary, 1)
    b = head_blob.split(boundary, 1)
    if len(a) != 2 or len(b) != 2:
        return False                                 # a boundary appeared or vanished
    if a[0] != b[0]:
        return False                                 # the declaration changed: `D` moved
    return b[1].startswith(a[1])                     # lifecycle must be a byte prefix-extension


def triggers(changed: list[str] | None, *, impact_classification: str, hot_files,
             contract_ops_non_monotone: list[str], operator_required: bool,
             subsystems: list[str]) -> tuple[Trigger, ...]:
    """`T1`–`T6`. A trigger that did NOT fire still carries its reason, so a contract can show why."""
    out: list[Trigger] = []

    spans = sorted(set(subsystems))
    out.append(Trigger("T1", len(spans) > 1,
                       f"{len(spans)} subsystem(s) spanned: {', '.join(spans) or 'none'}",
                       tuple(spans)))

    above = impact_classification in ("MIGRATION_REQUIRED", "BREAKING_CHANGE", "UNKNOWN_IMPACT")
    out.append(Trigger("T2", above, f"impact classification is {impact_classification or 'unknown'}",
                       (impact_classification,) if impact_classification else ()))

    if changed is None:
        out.append(Trigger("T3", False, "the diff could not be enumerated", ()))
    else:
        hits = [p for p in changed if any_match(p, T3_PATTERNS)]
        hits += [p for p in contract_ops_non_monotone if p not in hits]
        out.append(Trigger("T3", bool(hits),
                           f"{len(hits)} governance-surface path(s) changed" if hits
                           else "no governance surface changed", tuple(sorted(hits))))

    # `T4` is HUMAN-DECLARED and is not derived here. `side_effects.json` censuses where side-effect
    # CODE lives; it cannot know that this task intends to RUN it, and no tool distinguishes "edits
    # the publish path" from "publishes". Deriving it would manufacture exactly the false confidence
    # ADR-0105 §1 T4 warns is the load-bearing risk of the whole model.
    out.append(Trigger("T4", False, "live/destructive is human-declared, never derived", ()))

    if changed is None:
        out.append(Trigger("T5", True, "the changed-file set could not be enumerated — FAIL CLOSED",
                           ()))
    else:
        hot = sorted(p for p in changed if p in hot_files)
        broad = len(changed) > BREADTH_THRESHOLD
        out.append(Trigger("T5", bool(hot) or broad,
                           f"{len(changed)} file(s) changed; {len(hot)} hot-file contact(s)",
                           tuple(hot)))

    out.append(Trigger("T6", operator_required, "operator requirement", ()))
    return tuple(out)


def traits_from(fired: dict[str, bool], declared_live: bool) -> frozenset[str]:
    """`cross-system` = T1 ∨ T2 · `governance` = T3 · `live` = T4, which only a human can set."""
    t = set()
    if fired.get("T1") or fired.get("T2"): t.add("cross-system")
    if fired.get("T3"): t.add("governance")
    if declared_live or fired.get("T4"): t.add("live")
    return frozenset(t)


def risk_tier(traits: frozenset[str]) -> str:
    """live > governance > cross-system > none. SELECTS ONLY THE BREACH RESPONSE (§10)."""
    for t in ("live", "governance", "cross-system"):
        if t in traits:
            return t
    return "none"


# ── per-file labels (ADR-0105 §5.3) ─────────────────────────────────────────────────────────
DECLARED, GENERATED, INCIDENTAL, UNAUTHORIZED = ("declared", "generated-consequence", "incidental",
                                                 "unauthorized")


def labels(changed: list[str], *, expected_surfaces, incidental_allowlist,
           generated_paths) -> list[tuple[str, str]]:
    """The four labels, in ADR order. Labels never alter traits; traits never relabel a file."""
    out = []
    for p in changed:
        if any_match(p, expected_surfaces): out.append((p, DECLARED))
        elif p in generated_paths or any_match(p, generated_paths): out.append((p, GENERATED))
        elif any_match(p, incidental_allowlist): out.append((p, INCIDENTAL))
        else: out.append((p, UNAUTHORIZED))
    return out


def unauthorized(labelled: list[tuple[str, str]]) -> tuple[str, ...]:
    """The anti-silent-scope-expansion set. ADR-0105 §5.3: *"Phase 3 should implement this first."*"""
    return tuple(p for p, lab in labelled if lab == UNAUTHORIZED)


# ── D-7: mechanical `T3` completeness ───────────────────────────────────────────────────────
def governance_surface_findings(changed: list[str], *, base_has,
                                declared_governance_paths=()) -> list[Diagnostic]:
    """`GS-1` and `GS-2`. NO SECOND REGISTRY — ADR-0105 §1's `T3` list stays the sole definition.

    `GS-1` is deliberately NARROW AND STRUCTURAL: the signal is a new PACKAGE under `tools/`, which
    is a fact about the diff, not an inference from a name. `tools/` today contains exactly `arch`
    and `ci`, both governance surfaces, both in `T3`; a third package is the shape a new validator
    actually takes here. Filenames such as `policy.py` or `governance.py` are NEVER consulted, and
    no universal semantic classifier is attempted — one would be wrong constantly, and a detector
    that fires on ordinary packages would destroy the free default path ADR-0105 §1 insists on
    (`NC-C24` is the positive control that keeps it honest).

    `GS-2` proves a DECLARED surface is in fact covered, and is bounded to `tools/`. It catches the
    case `GS-1` misses: a single-file validator at `tools/newvalidator.py`, which creates no package
    and so trips no `__init__.py` signal, but is exactly ADR-0105 §1's named false negative — *"a new
    validator added under a path not enumerated."*

    WHY `GS-2` IS BOUNDED TO `tools/` — an authority resolution, not a preference.

    The approved design contradicts itself here. §8.6 says `GS-2` checks that EVERY path in
    `expected_surfaces` on a `governance` contract matches a `T3` pattern; §19.2 of the same document
    classifies `tests/**` and `docs/contracts/<id>.md` as explicitly NOT governance surfaces. Both
    are rank 4, so the design cannot resolve its own conflict, and the literal §8.6 reading was
    implemented first and produced a false `stop` on the very patch that introduced it.

    ADR-0105 (rank 3) resolves it at three points, all pointing the same way:

      §3.6  `docs/contracts/**` is CONDITIONALLY OUTSIDE `T3`; creating a contract does not trigger
            it. Flagging a contract's own file therefore contradicts the ADR directly.
      §1    the false negative to be closed is "a new VALIDATOR added under a path not enumerated" —
            a statement about where validators live, not about every file a governance change edits.
      §12   Phase 3 owns "mechanical completeness verification of the `T3` LIST" — that the list is
            complete, not that a change's file set is uniformly governance.

    And §8.6 concedes in its own first sentence that a governance surface outside `tools/` "cannot be
    detected structurally". So the mechanical half is bounded to where the question is structurally
    answerable, and outside it the ADR's own same-change rule — the human declaration route — is the
    mechanism, exactly as the ADR intends. No semantic classifier, no filename heuristic, no second
    registry, no new contract field: the 19-slot model is unchanged.
    """
    out: list[Diagnostic] = []
    for p in changed:
        parts = p.split("/")
        if len(parts) >= 3 and parts[0] == "tools" and parts[2] == "__init__.py" and len(parts) == 3:
            pkg = f"tools/{parts[1]}/"
            if base_has(f"{pkg}__init__.py"):
                continue                              # the package already existed; not a creation
            if not any_match(f"{pkg}x.py", T3_PATTERNS):
                out.append(Diagnostic(UNKNOWN, "GS-1",
                                      f"a new governance package `{pkg}` is not covered by the "
                                      f"ADR-0105 §1 T3 path list",
                                      path=p, got=pkg, expected="a T3 pattern covering it",
                                      remediation="add the path to ADR-0105 §1 T3 in THIS change, "
                                                  "recompute `approved_digest`, and obtain renewed "
                                                  "approval — the ADR's own rule",
                                      evidence=(f"T3 patterns: {len(T3_PATTERNS)}",)))
    for p in declared_governance_paths:
        if not p.startswith("tools/"):
            continue                                  # outside `tools/`: the human declaration route
        if not any_match(p, T3_PATTERNS):
            out.append(Diagnostic(UNKNOWN, "GS-2",
                                  f"`{p}` is a declared surface under `tools/` that no ADR-0105 §1 "
                                  f"T3 pattern covers — a validator location outside the list",
                                  path=p, expected="a T3 pattern covering it",
                                  remediation="add it to ADR-0105 §1 T3 in THIS change, recompute "
                                              "`approved_digest`, and obtain renewed approval"))
    return out


def adr_t3_patterns(adr_text: str) -> tuple[str, ...]:
    """Read the `T3` list back out of ADR-0105 so `NC-C27` can prove `T3_PATTERNS` has not drifted.

    A rule that parses its own governing document must have that document PINNED, or the rule
    quietly stops meaning what the document says while continuing to report success. This is the
    pin; `NC-C27` is what makes it fire.
    """
    m = re.search(r"\*\*T3 — Governance surface\.\*\*\s*\n\s*\n- \*\*Predicate:\*\*(.*?)\n- \*\*",
                  adr_text, re.S)
    return tuple(sorted(set(re.findall(r"`([^`]+)`", m.group(1))))) if m else ()


def adr_body_digest(adr_bytes: bytes) -> str:
    """ADR-0105 §Status' own reference implementation: sha256 over every byte after the front matter.

    Reproduced rather than imported because there is nothing to import — the reference lives in the
    ADR's prose. `NC-C27` runs it against the live file and compares to `ADR_0105_DIGEST`.
    """
    parts = adr_bytes.split(b"\n---\n", 1)
    return "sha256:" + hashlib.sha256(parts[1] if len(parts) == 2 else adr_bytes).hexdigest()


def hot_files_from(lanes_json: Path):
    """`.agents/lanes.json` `guard.hot_files` → (paths, problems). The declared single source of truth.

    Returns the problem rather than an empty set, because an unreadable `lanes.json` would otherwise
    turn the hot-file half of `T5` silently off — the change would read as "touches no hot file" when
    the truth is "nobody checked". `ST-7` is where that lands instead.
    """
    try:
        raw = json.loads(lanes_json.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set(), [f"{lanes_json} is absent — T5's hot-file predicate cannot be evaluated"]
    except (OSError, json.JSONDecodeError) as exc:
        return set(), [f"{lanes_json} is unreadable ({type(exc).__name__}: {exc}) — T5's hot-file "
                       f"predicate cannot be evaluated"]
    hot = raw.get("guard", {}).get("hot_files", {})
    if not isinstance(hot, dict):
        return set(), [f"{lanes_json} `guard.hot_files` is {type(hot).__name__}, expected an object"]
    return set(hot), []
