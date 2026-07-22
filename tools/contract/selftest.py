"""R16 — the negative controls. PROOF THAT EACH RULE DETECTS THE DEFECT IT CLAIMS TO DETECT.

A validator nobody has tried to fool is a validator nobody should trust. This repository has already
paid for that lesson twice: a green test that ASSERTED a data-loss outcome and called it correct
(`RC-5`), and `IMPL-007`, which sat in the policy set, was reported in the docs, and SILENTLY DID
NOT FIRE because its parser read a number out of a prose sentence and got nothing back. It looked
enforced. It was not. ADR-0105 §12 makes "each rule carrying a firing negative control" Phase 3's
exit criterion for exactly this reason.

METHOD. Each control builds a VALID contract, records the decision before injection, injects exactly
one defect, and asserts the named rule fires — or, for the controls that prove an absence, that it
does NOT. Demanding a change from the baseline is what makes this rigorous: a control that merely
asserted "the rule fires" could pass on a defect that was already there and prove nothing about the
injection.

FAKE PORTS, NOT A REPOSITORY COPY. `tools/arch/selftest.py` has to copy `src/`, `tests/` and the
canonical artifacts into a tempdir because its checkers read the filesystem directly. This package
took its I/O through five narrow ports precisely so its controls would not have to: a fake is a dict
literal, every control runs in microseconds, and the whole suite belongs in the default unmarked set
the `unit` job already collects. The four controls that assert facts about the REAL repository
(`NC-C16`, `NC-C17`, `NC-C27`, `NC-C28`) read it on purpose — a fake cannot prove a fact about the
real artifacts.
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import inspect
import io
import re
from dataclasses import dataclass
from pathlib import Path

from . import classify, derive
from .adapters import REPO, PortError
from .decide import RULE_IDS
from .model import (ACCEPTED_DECISION, CI_REGISTRY_PATH, CLARIFICATION, CONTINUE, ESCALATE, EXPANDED,
                    REFUSE, STOP)
import json as _json

from . import lifecycle as _lc
from .parse import BOUNDARY, digest, parse

_CODE_SHAPE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$")

CONTRACT_PATH = "docs/contracts/CC-2026-07-18-example.md"
ADR_PATH = "docs/adr/0105-reusable-change-contract-architecture.md"
ADR_BLOB = b"adr-body"


def fake_sha(b: bytes) -> str:
    """A DETERMINISTIC stand-in for a git blob id.

    The first version used `hash(bytes)`, which Python randomizes per process unless PYTHONHASHSEED
    is pinned. The fixture's recorded authority SHA then failed to match the fake repository's on
    every run, `ST-2` fired in the baseline, and thirteen controls reported the wrong rule for a
    reason that had nothing to do with the defects they inject. A fixture whose values move between
    runs cannot be a fixture.
    """
    return "blob" + hashlib.sha1(b).hexdigest()[:12]


@dataclass(frozen=True)
class Control:
    id: str
    defect: str
    expect_rule: str          # the decision rule that must fire ("" = not decision-level)
    expect_code: str          # the diagnostic code that must appear ("" = none required)
    layer: str                # grammar | decision | derivation | repository


CONTROLS: list[Control] = [
    # ── grammar: the twelve rejected constructs (operator decision D-1) ──────────────────────
    Control("NC-C01", "an anchor `&name` in the front matter", "A1", "UNSUP-ANCHOR", "grammar"),
    Control("NC-C02", "an alias `*name`", "A1", "UNSUP-ALIAS", "grammar"),
    Control("NC-C03", "a type tag `!!str`", "A1", "UNSUP-TAG", "grammar"),
    Control("NC-C04", "a block scalar `|`", "A1", "UNSUP-BLOCK-SCALAR", "grammar"),
    Control("NC-C05", "a nested mapping", "A1", "UNSUP-NESTED", "grammar"),
    Control("NC-C05b", "a merge key `<<:`", "A1", "UNSUP-MERGE", "grammar"),
    Control("NC-C05c", "a tab character", "A1", "UNSUP-TAB", "grammar"),
    Control("NC-C05d", "a comment `#`", "A1", "UNSUP-COMMENT", "grammar"),
    Control("NC-C05e", "a flow mapping `{ … }`", "A1", "UNSUP-FLOW-MAP", "grammar"),
    Control("NC-C05f", "CRLF line endings", "A1", "UNSUP-CRLF", "grammar"),
    Control("NC-C05g", "a second YAML document", "A1", "UNSUP-MULTIDOC", "grammar"),
    Control("NC-C06", "twelve values that a YAML parser would implicitly type", "", "", "grammar"),
    Control("NC-C07a", "the same key twice", "A1", "DUP-KEY", "grammar"),
    Control("NC-C07b", "a key outside the closed field set (`waives_law`)", "A2", "UNKNOWN-KEY",
            "grammar"),
    Control("NC-C08", "a parse/round-trip that must leave `D` unchanged", "", "", "grammar"),

    # ── the digest split and the three gates ────────────────────────────────────────────────
    Control("NC-C09", "one flipped byte in the declaration voids content approval", "ST-3", "",
            "decision"),
    Control("NC-C10", "a lifecycle append preserves `D` but moves the head off the authorized "
            "parent", "ST-9", "", "decision"),

    # ── single-operator merge authorization (ADR-0105 §4.1a, corrected) ──────────────────────
    # Fourteen controls pinning ONE claim: the operator alone can authorize a merge, and nothing a
    # second person does or fails to do moves the verdict. NC-SO-01 covers required controls 1, 3
    # and 4 in a single run — it authorizes with zero reviews, and the review/principal ports it
    # would have needed no longer exist to be stubbed, so their absence is what makes it pass.
    Control("NC-SO-01", "zero reviews + a valid operator authorization AUTHORIZES the merge",
            "OK", "", "decision"),
    Control("NC-SO-02", "no `merge_approved` event at all stays unauthorized", "ST-9", "",
            "decision"),
    Control("NC-SO-03", "an authorization naming the wrong declaration digest", "ST-9", "",
            "decision"),
    Control("NC-SO-04", "an authorization naming the wrong PR", "ST-9", "", "decision"),
    Control("NC-SO-05", "an authorized parent that is not an ancestor of the head", "ST-9", "",
            "decision"),
    Control("NC-SO-06", "a NON-CONTRACT path moved after the authorized parent", "ST-9", "",
            "decision"),
    Control("NC-SO-07", "the declaration edited after the authorized parent", "ST-9", "",
            "decision"),
    Control("NC-SO-08", "the lifecycle rewritten rather than appended after the parent", "ST-9", "",
            "decision"),
    Control("NC-SO-09", "a LIFECYCLE-ONLY append after the authorized parent still binds", "OK", "",
            "decision"),
    Control("NC-SO-10", "an authorization carrying no operator token", "ST-9", "", "decision"),
    Control("NC-SO-11", "no authorization module reads reviews, reviewer identity or a principal "
            "census", "", "", "structural"),
    # ADR-0105 §4.3a — the eleven required firing controls for verified acceptance.
    Control("NC-AC-01", "an `accepted` row alone does not yield state `accepted`", "", "",
            "decision"),
    Control("NC-AC-02", "an `accepted` row with stale authorization yields `acceptance_claimed`",
            "", "", "decision"),
    Control("NC-AC-03", "authorization rederived across the squash plus valid evidence yields "
            "`accepted`", "", "", "decision"),
    Control("NC-AC-04", "a PR-head/merge-tree mismatch yields `merged_unverified`, not `ST-7`", "",
            "", "decision"),
    Control("NC-AC-05", "an unavailable platform read yields `ST-7`, never a negative finding", "",
            "", "decision"),
    Control("NC-AC-06", "a wrong merge SHA yields `acceptance_claimed`", "ST-10", "", "decision"),
    Control("NC-AC-07", "a wrong `mergedAt` chronology yields `acceptance_claimed`", "", "",
            "decision"),
    Control("NC-AC-08", "a failed, skipped, cancelled or absent required run yields "
            "`acceptance_claimed`", "", "", "decision"),
    Control("NC-AC-09", "zero reviews remains fully valid through acceptance", "", "", "decision"),
    Control("NC-AC-10", "the platform port cannot express a review question", "", "", "structural"),
    Control("NC-AC-11", "`ST-4` remains absent and `ST-10` is not it renumbered", "", "",
            "structural"),
    Control("NC-AC-12", "a `merged` event that names no merge SHA", "A5", "MERGED-INCOMPLETE",
            "decision"),
    Control("NC-AC-13", "the ordinary no---pr command path resolves the governed PR from `binding`",
            "", "", "decision"),
    Control("NC-AC-14", "a later rerun does not disturb an already-recorded verdict", "", "",
            "decision"),
    Control("NC-AC-15", "the required set is pinned to the contract's base commit", "", "",
            "decision"),
    Control("NC-AC-18", "a newer run already present at the acceptance instant controls the evidence",
            "ST-10", "", "decision"),
    Control("NC-AC-19", "a `created.base_sha` naming a weaker registry commit cannot verify",
            "ST-10", "", "decision"),
    Control("NC-AC-20", "a required context produced by the wrong App", "ST-10", "", "decision"),
    Control("NC-AC-21", "a required context produced by the wrong workflow", "ST-10", "", "decision"),
    Control("NC-AC-22", "the governing workflow blob changed between base and PR head", "ST-10", "",
            "decision"),
    Control("NC-AC-23", "a check run that joins to no workflow job", "ST-10", "", "decision"),
    Control("NC-AC-24", "a check run that joins to two workflow jobs is ambiguous, not absent", "",
            "", "decision"),
    Control("NC-AC-25", "full provenance — App, workflow, join and blob stability — verifies", "",
            "", "decision"),
    Control("NC-AC-26", "an `accepted` row whose decision is not `accepted`", "ST-10", "",
            "decision"),
    Control("NC-AC-27", "recorded ids that are non-decimal, duplicated, or unordered", "ST-10", "",
            "decision"),
    Control("NC-AC-28", "the contract is ABSENT at the pre-merge PR head", "", "", "decision"),
    Control("NC-AC-29", "the contract is UNREADABLE at the pre-merge PR head", "ST-7", "",
            "decision"),
    Control("NC-AC-30", "the contract PRESENT at the PR head is the blob parent-binding reads", "",
            "", "decision"),
    Control("NC-AC-32", "every GitHub read is an explicit GET, including the parameterised one",
            "", "", "structural"),
    Control("NC-AC-33", "the registry's pinned job KEY is verified, not merely unpacked", "ST-10",
            "", "decision"),
    Control("NC-AC-34", "pagination completeness is mandatory, not conditional on a usable count",
            "", "", "structural"),
    Control("NC-AC-31", "the derived-state count and the port surfaces are derived, not asserted",
            "", "", "structural"),
    Control("NC-AC-35", "the default base is the contract's own, not a ref that lands on the head",
            "", "", "structural"),
    Control("NC-AC-17", "an `accepted` row recording no `check_runs` is unverified, not malformed",
            "ST-10", "", "decision"),
    Control("NC-AC-16", "an incomplete check-run page is unavailability, not absence", "", "",
            "structural"),
    Control("NC-C10b", "a rewritten lifecycle history", "A5", "LIFECYCLE-REWRITTEN", "decision"),
    Control("NC-C10c", "an `id` that does not match the filename stem", "A4", "ID-FILENAME",
            "decision"),

    # ── the union rule ──────────────────────────────────────────────────────────────────────
    Control("NC-C11", "obligations must be monotone over every trait subset", "", "", "derivation"),
    Control("NC-C12", "a trait that subtracts another trait's obligation; a second `risk_tier` read",
            "", "", "derivation"),

    # ── every decision outcome is reachable ─────────────────────────────────────────────────
    Control("NC-C13a", "a clean contract reaches `continue`", "OK", "", "decision"),
    Control("NC-C13c", "a cited authority's blob moved after approval", "ST-2", "AUTH-BLOB-MOVED",
            "decision"),
    Control("NC-C13d", "two same-precedence authorities conflict", "ES-1", "", "decision"),
    Control("NC-C13e", "reused evidence on a `live` change", "ST-6", "I4", "decision"),
    Control("NC-C13f", "deleting a tracked repository file is NOT `live`", "", "", "decision"),
    Control("NC-C13g", "the right change lies outside `allowed_scope`, declared in advance", "EA-1",
            "", "decision"),
    Control("NC-C13h", "a terminal `refused` event", "RF-3", "", "decision"),
    Control("NC-C13i", "a `live` change with no separate execution gate", "RF-1", "", "decision"),
    Control("NC-C13j", "a `live` change touching an undeclared file", "RF-2", "", "decision"),
    Control("NC-C13k", "the task requires exceeding a `LAW-*`", "ES-2", "", "decision"),
    Control("NC-C13l", "conflicting evidence at the same precedence", "ES-3", "I2", "decision"),
    Control("NC-C13m", "an unfalsifiable `success_condition` declared unfixable", "RF-4", "",
            "decision"),
    Control("NC-C13n", "the declared trait set differs from the derived one", "CL-2", "",
            "decision"),
    Control("NC-C13o", "a `success_condition` naming nothing observable", "CL-3", "UNFALSIFIABLE",
            "decision"),

    Control("NC-C14", "no failure path anywhere produces `continue`", "", "", "decision"),
    Control("NC-C15", "every tool failure exits 2 and emits NO `decision` field", "", "",
            "decision"),

    # ── the derivations ─────────────────────────────────────────────────────────────────────
    Control("NC-C16", "path→module must reproduce the canonical module set", "", "", "repository"),
    Control("NC-C17", "reverse closure must match an independent BFS", "", "", "repository"),
    Control("NC-C18", "an undeclared path in the diff", "ST-1", "SCOPE-UNAUTHORIZED", "decision"),
    Control("NC-C19", "a generated consequence that regeneration does not reproduce", "ST-5",
            "GENERATED-NOT-REPRODUCIBLE", "decision"),
    Control("NC-C20a", "an enum member delta arms `changed_enums`; an unrelated diff does not", "",
            "", "repository"),
    Control("NC-C20b", "the mandatory `rollback` field omitted", "A3", "FIELD-MISSING", "decision"),
    Control("NC-C20c", "a `live` contract carries the rollback-rehearsal obligation", "", "",
            "derivation"),
    Control("NC-C21", "every rule has a control, and no `verifymap` predicate is dead", "", "",
            "repository"),

    # ── T3 completeness (operator decision D-7) ─────────────────────────────────────────────
    Control("NC-C22", "a diff touching `tools/contract/**` must fire `T3`", "", "", "derivation"),
    Control("NC-C23", "a new governance package under `tools/` not covered by T3", "ST-8", "GS-1",
            "decision"),
    Control("NC-C24", "an ordinary new package under `src/fanops/` must NOT fire T3", "", "",
            "derivation"),
    Control("NC-C25", "`merged` never implies `accepted`", "", "", "decision"),
    Control("NC-C26", "each corrupted variant fails with ITS OWN named rule", "", "", "decision"),
    Control("NC-C27", "the ADR-0105 §1 T3 pin still matches the ADR body", "", "", "repository"),
    Control("NC-C28", "neither sibling package imports `tools.contract`", "", "", "repository"),
    Control("NC-C13b", "an unparseable contract reaches `clarification_required`", "A1", "BAD-KEY",
            "decision"),
    Control("NC-C13p", "a required input was unavailable", "ST-7", "UNVERIFIABLE", "decision"),
    Control("NC-C29", "a failed `git rev-parse` must never be read as a blob id", "", "",
            "repository"),
    Control("NC-C30", "a declared validator location under `tools/` outside the T3 list", "ST-8",
            "GS-2", "decision"),
    Control("NC-C31", "every diagnostic code a decision rule READS must be PRODUCED by a control",
            "", "", "repository"),
    # ── production paths: every code a decision rule CONSUMES must be PRODUCED by a control ──
    # `GS-2` hid because coverage was measured over rule ids, and `ST-8` looked covered via `GS-1`.
    # These close the same class everywhere else: each names a code some rule reads, and injects the
    # defect that produces it. `NC-C31` is what keeps the set complete as rules are added.
    Control("NC-C33", "two `## Lifecycle` boundaries", "A1", "MULTI-BOUNDARY", "grammar"),
    Control("NC-C34", "no front matter at all", "A1", "NO-FRONTMATTER", "grammar"),
    Control("NC-C35", "front matter never closed", "A1", "UNCLOSED-FRONTMATTER", "grammar"),
    Control("NC-C36", "a front-matter line with no `key:`", "A1", "NO-COLON", "grammar"),
    Control("NC-C37", "a table row with the wrong cell count", "A1", "BAD-ROW", "grammar"),
    Control("NC-C38", "a reordered table column header", "A1", "BAD-COLUMNS", "grammar"),
    Control("NC-C39", "a `###` field section carrying no table", "A1", "NO-TABLE", "grammar"),
    Control("NC-C40", "a block-list item with no key above it", "A1", "ORPHAN-ITEM", "grammar"),
    Control("NC-C41", "a `###` section that is not a field", "A2", "UNKNOWN-SECTION", "grammar"),
    Control("NC-C42", "a front-matter field written as a `###` section", "A1", "WRONG-LOCATION",
            "grammar"),
    Control("NC-C43", "a reordered lifecycle table header", "A1", "BAD-EVENT-COLUMNS", "grammar"),
    Control("NC-C44", "a lifecycle row with the wrong cell count", "A1", "BAD-EVENT-ROW", "grammar"),
    Control("NC-C45", "a mandatory field present but empty", "A3", "FIELD-EMPTY", "decision"),
    Control("NC-C46", "an `id` outside the `CC-YYYY-MM-DD-slug` grammar", "A4", "ID-FORMAT",
            "decision"),
    Control("NC-C47", "an unknown lifecycle event kind", "A5", "EVENT-KIND", "decision"),
    Control("NC-C48", "a lifecycle timestamp that is not UTC ISO-8601", "A5", "EVENT-TIME",
            "decision"),
    Control("NC-C49", "lifecycle timestamps going backwards", "A5", "EVENT-ORDER", "decision"),
    Control("NC-C50", "an event appended after a terminal one", "A5", "EVENT-AFTER-TERMINAL",
            "decision"),
    Control("NC-C51", "an `accepted` event missing the five required values", "A5",
            "ACCEPT-INCOMPLETE", "decision"),
    Control("NC-C52", "a landed declaration edited in place", "A5", "DECL-DIVERGED", "decision"),
    Control("NC-C53", "a cited authority whose file does not exist", "ST-2", "AUTH-MISSING-FILE",
            "decision"),
    Control("NC-C54", "a cited control id in no registry row", "CL-4", "AUTH-UNKNOWN", "decision"),
    Control("NC-C55", "an evidence record missing a required part", "ST-6", "EV-SHAPE", "decision"),
    Control("NC-C56", "reused evidence whose bound source changed", "ST-6", "I1", "decision"),
    Control("NC-C57", "a parent-bound event that names no `parent_sha`", "A5",
            "PARENT-BIND-INCOMPLETE", "decision"),

    Control("NC-C13r", "a cited authority id in no recognised namespace", "CL-4",
            "AUTH-NAMESPACE", "decision"),
    Control("NC-C13q", "a trait-conditional mandatory field is absent", "CL-1", "TRAIT-CONDITIONAL",
            "decision"),

    # ── ADR-0105 §1a · pre-implementation classification (each paired with its container) ────
    Control("NC-P1", "`pre` classifies the contract-only diff, hiding the intended span", "", "",
            "derivation"),
    Control("NC-P2", "`at-head` classifies intent instead of the diff", "", "", "derivation"),
    Control("NC-P3", "an under-declared contract passes `pre`", "CL-2", "", "decision"),
    Control("NC-P4", "an intended `src/` path that is no module is silently skipped", "", "",
            "derivation"),
    Control("NC-P5", "an intended module no subsystem owns is silently skipped", "", "",
            "derivation"),
    Control("NC-P6", "`T1` fires on a single-subsystem intent set (broadened)", "", "",
            "derivation"),
    Control("NC-P7", "`preflight` needs a contract, or writes something", "", "", "repository"),
    Control("NC-P8", "the front door does not name `preflight`", "", "", "repository"),
    Control("NC-P9", "path-only preflight answers `NOT REQUIRED`", "", "", "repository"),
    Control("NC-P10", "a conservative `pre` declaration is rejected while `T2` is unevaluable", "",
            "", "decision"),
    Control("NC-P11", "an over-declaration tolerated at `pre` survives `at-head`", "", "",
            "decision"),
    Control("NC-P12", "a wildcarded or malformed intended path classifies as contained", "", "",
            "derivation"),
    Control("NC-P13", "an unmapped intended path disappears into `contained`", "", "",
            "derivation"),
    Control("NC-P14", "`pre` halts on an unavailable impact port it does not use", "", "",
            "derivation"),
    Control("NC-P15", "the same unavailable impact stops halting `at-head`", "", "", "derivation"),
    Control("NC-P16", "`T2` stops deriving `cross-system` from a real head diff", "", "",
            "derivation"),

    # ── ADR-0106 · declaration-only contracts ───────────────────────────────────────────────
    #
    # THE RISK THE NEW SHAPE INTRODUCES IS A SILENT PASS, not a rejection. Admitting a file with no
    # `## Lifecycle` boundary means the gate that used to fire on it (`A1` via `NO-BOUNDARY`) is
    # gone, so every control below asks the same question from a different side: does an UNAPPROVED
    # declaration-only contract still stop?
    Control("NC-D1", "a declaration-only contract with no approval reads as approved", "ST-3", "",
            "decision"),
    Control("NC-D2", "an `approved_digest` naming a stale `D` reads as approved", "ST-3", "",
            "decision"),
    Control("NC-D3", "an approval recorded in BOTH routes, so the one read is ambiguous", "A5",
            "APPROVAL-DUAL-ROUTE", "decision"),
    Control("NC-D4", "a digest recorded with no operator token beside it", "A5",
            "APPROVAL-INCOMPLETE", "decision"),
    Control("NC-D5", "recording the approval changes `D`, so approval can never be satisfied", "",
            "", "derivation"),
    Control("NC-D6", "a landed declaration-only contract edited in place", "A5", "DECL-DIVERGED",
            "decision"),
    Control("NC-D7", "a `live` declaration-only contract with no separate execution gate", "RF-1",
            "", "decision"),
    Control("NC-D8", "the legacy digest moved, invalidating every landed approval", "", "",
            "repository"),
    Control("NC-D9", "a declaration-only contract skips the scope check", "ST-1",
            "SCOPE-UNAUTHORIZED", "decision"),
]


# ── fakes ───────────────────────────────────────────────────────────────────────────────────
class FakeRepo:
    def __init__(self, blobs=None, changed=(), head="h" * 40, fail=None, ancestry=(),
                 changed_since=None, trees=None, unreadable=()):
        self.blobs = dict(blobs or {})
        # (ref, path) pairs whose READ FAILS, as distinct from pairs that are simply ABSENT. The
        # difference is the whole point of `pr_head_blob`: absent is a finding, unreadable is ST-7.
        self.unreadable = set(unreadable)
        self.changed = list(changed)
        self.head = head
        self.fail = fail
        self.ancestry = set(ancestry)
        # {ref: tree_sha}. Default: every ref shares ONE tree, so content matches unless a control
        # deliberately says otherwise — the same default-innocent shape as `ancestry`.
        self.trees = dict(trees or {})
        # {base: [paths]} — what moved since ONE specific base. `parent_binds` asks this of the
        # authorized parent, and that answer is not the same as the PR-wide base..head diff.
        self.changed_since = dict(changed_since or {})

    def blob(self, ref, path):
        if self.fail == "blob": raise PortError("git unavailable (injected)")
        if (ref, path) in self.unreadable:
            raise PortError(f"the object {path} at {ref[:12]} could not be read (injected)")
        return self.blobs.get((ref, path))

    def blob_sha(self, ref, path):
        if self.fail == "blob_sha": raise PortError("git unavailable (injected)")
        b = self.blobs.get((ref, path))
        return None if b is None else fake_sha(b)

    def diff_names(self, base, head):
        if self.fail == "diff": raise PortError("the diff could not be enumerated (injected)")
        if base in self.changed_since:
            return sorted(self.changed_since[base])
        return sorted(self.changed)

    def contains(self, ref, path): return (ref, path) in self.blobs

    def tree_of(self, ref):
        if self.fail == "tree": raise PortError("git unavailable (injected)")
        return self.trees.get(ref, "t" * 40)

    def resolve(self, ref): return self.head

    def is_ancestor(self, maybe_ancestor, ref):
        if self.fail == "ancestor": raise PortError("git unavailable (injected)")
        return maybe_ancestor == ref or (maybe_ancestor, ref) in self.ancestry


class FakeImpact:
    def __init__(self, classification="COMPATIBLE_CHANGE", fail=False):
        self.rep = {"classification": classification, "architecture": {}, "implementation": {},
                    "touched_src": [], "changed_files": []}
        self.fail = fail

    def report(self, base):
        if self.fail: raise PortError("impact unavailable (injected)")
        return self.rep


class FakeArtifacts:
    def __init__(self, generated=(), stale=(), fail=None):
        self.generated, self._stale, self.fail = set(generated), list(stale), fail

    def modules(self):
        if self.fail == "modules": raise PortError("derived/modules.json absent (injected)")
        return {"modules": ["fanops", "fanops.example"], "partition_is_total": True,
                "unassigned_modules": [], "ghost_modules": [],
                "subsystem_of": {"fanops": "S01_foundation", "fanops.example": "S01_foundation"}}

    def dependencies(self):
        return {"edges": {"fanops.example": {"compile": ["fanops"], "lazy": [], "optional": [],
                                             "typing": []},
                          "fanops": {"compile": [], "lazy": [], "optional": [], "typing": []}}}

    def entities(self): return {"enums": {}}

    def generated_paths(self): return set(self.generated)

    def stale(self): return list(self._stale)


class FakeRegistry:
    def __init__(self, ids=("DC-1", "DC-5"), fail=False): self.ids, self.fail = set(ids), fail

    def control_ids(self):
        if self.fail: raise PortError("PyYAML absent; control ids unverifiable (injected)")
        return set(self.ids)


# There is deliberately NO FakeReviews. The authorization path reads no review and no principal
# census, so there is nothing to fake — and a fake left standing would let a future edit re-introduce
# the dependency without a single control going red.
#
# `FakeMergeFacts` is NOT a counter-example to that. It fakes merge facts and check runs — the three
# closed reads of `MergeFactsPort` — and, like the real port, it has no method that could answer a
# question about a review or a person. What cannot be faked here is exactly what cannot be read
# there.
class FakeMergeFacts:
    """The four closed platform reads, faked coherently.

    Defaults describe a WELL-FORMED world: one GitHub-Actions run per required context, started and
    completed before the acceptance instant, joined to the workflow the registry pins. Each control
    then breaks exactly one fact, so a red result names the fact it broke.
    """

    def __init__(self, *, pr_head="h" * 40, merge_sha=None, merged_at="2026-07-19T23:19:38Z",
                 merged=True, runs=None, fail=None, base_sha=None, jobs=None, wf_runs=None):
        self.d = {"pr_head": pr_head, "merge_sha": "m" * 40 if merge_sha is None else merge_sha,
                  "merged_at": merged_at, "merged": merged,
                  "base_sha": "b" * 40 if base_sha is None else base_sha}
        self.runs = list(runs) if runs is not None else [_check_run("101")]
        self.wf_runs = wf_runs if wf_runs is not None else [{"id": "9001",
                                                             "path": ".github/workflows/ci.yml",
                                                             "head_sha": self.d["merge_sha"]}]
        self.jobs_by_run = jobs if jobs is not None else {
            "9001": [{"name": "unit", "run_id": "9001", "check_run_id": "101",
                      "conclusion": "success", "status": "completed",
                      "started_at": "2026-07-19T23:00:00Z",
                      "completed_at": "2026-07-19T23:05:00Z"}]}
        self.fail = fail

    def pull(self, pr):
        if self.fail == "pull": raise PortError("the PR could not be read (injected)")
        return dict(self.d)

    def check_runs(self, sha):
        if self.fail == "checks": raise PortError("check runs could not be read (injected)")
        return [dict(r) for r in self.runs]

    def workflow_runs(self, sha):
        if self.fail == "wf": raise PortError("workflow runs could not be read (injected)")
        return [dict(w) for w in self.wf_runs]

    def jobs(self, run_id):
        if self.fail == "jobs": raise PortError("jobs could not be read (injected)")
        return [dict(j) for j in self.jobs_by_run.get(str(run_id), [])]

    # NOTE: no `required_contexts` method. The required set is NOT a platform read — it is pinned to
    # the contract's own `created.base_sha` from the in-repo registry, so live configuration can
    # neither invalidate nor manufacture a historical acceptance. The absence of the method here is
    # the same structural guarantee as the absence of a review reader.


def _check_run(rid, *, name="unit", conclusion="success", status="completed",
         started="2026-07-19T23:00:00Z", completed="2026-07-19T23:05:00Z",
         app_id=None, app_slug=None):
    """One check-run record as the port returns it — GitHub Actions and green unless told otherwise."""
    from .adapters import GH_ACTIONS_APP_ID, GH_ACTIONS_APP_SLUG
    return {"id": rid, "name": name, "conclusion": conclusion, "status": status,
            "started_at": started, "completed_at": completed,
            "app_id": GH_ACTIONS_APP_ID if app_id is None else app_id,
            "app_slug": GH_ACTIONS_APP_SLUG if app_slug is None else app_slug}


# ── the fixture contract ────────────────────────────────────────────────────────────────────
_DECL = """---
id: {cid}
traits: [{traits}]
authorized_actions: [design, implement]
incidental_allowlist: []
blast_radius: []
invariants: [LAW-SOT-01]
stop_conditions: [{stops}]
supersedes: []
---

# {cid}

### objective

Prove the compiler detects what it claims to detect.

### success_condition

`python -m tools.contract selftest` exits 0 with all controls detected.

### rollback

`git revert` the single squash commit; nothing observes the package.

### authority

| id | source_file | blob_sha |
|---|---|---|
| ADR-0105 | {adr} | {adr_sha} |

### owners

| subsystem_id | why_touched |
|---|---|
| S01_foundation | the changed module belongs to it |

### allowed_scope

| glob | why | basis |
|---|---|---|
| src/fanops/example.py | the declared change | declared |
{scope}
### prohibited_scope

| glob | why |
|---|---|
| .github/workflows/** | no workflow change |

### expected_surfaces

| path | kind | why |
|---|---|---|
| src/fanops/example.py | MODIFIED | the declared change |
{surfaces}
### coupling

| what | must_move_with | why |
|---|---|---|

### reusable_evidence

| claim | proven_by | proven_at | binding |
|---|---|---|---|
{evidence}
### verification

| obligation_id | control_or_requirement | distinct_boundary |
|---|---|---|
| OB-ARCH-CI | python -m tools.arch ci | regeneration byte-compare |
"""

_LIFE = """
| timestamp | event | values |
|---|---|---|
| 2026-07-18T10:00:00Z | created | id={cid}; base_sha={base} |
| 2026-07-18T10:05:00Z | approved | digest={D}; token=APPROVE{gate} |
{extra}"""


def build(*, cid="CC-2026-07-18-example", traits="", stops="", adr_sha=None, base="base",
          evidence="", extra="", gate="", approve_digest=None, decl_mutate=None, declare=()) -> bytes:
    """A VALID contract by default. EVERY CONTROL MUTATES EXACTLY ONE THING ABOUT IT.

    `declare` adds paths to BOTH `allowed_scope` and `expected_surfaces`, which is what lets a
    control isolate its own defect: a control probing `GS-1` must not also trip `ST-1`, or it would
    pass on the wrong rule and prove nothing about the rule it names.
    """
    adr_sha = adr_sha or fake_sha(ADR_BLOB)
    scope = "".join(f"| {p} | declared by the control | declared |\n" for p in declare)
    surfaces = "".join(f"| {p} | NEW | declared by the control |\n" for p in declare)
    decl = _DECL.format(cid=cid, traits=traits, stops=stops, adr=ADR_PATH, adr_sha=adr_sha,
                        evidence=evidence, scope=scope, surfaces=surfaces)
    if decl_mutate is not None:
        decl = decl_mutate(decl)
    d = digest(decl.encode().rstrip(b"\n"))
    life = _LIFE.format(cid=cid, D=approve_digest or d, gate=gate, extra=extra, base=base)
    return decl.encode().rstrip(b"\n") + BOUNDARY + life.encode()


def build_decl_only(*, approve=True, approve_digest=None, token="APPROVE", gate=None,
                    trailer="", **kw) -> bytes:
    """The ADR-0106 shape: the same declaration, with NO `## Lifecycle` section at all.

    `approve=True` writes the approval the way an agent does — compute `D` on the unapproved bytes,
    then insert the line — so every control that uses it also exercises the elision property that
    makes writing the approval leave `D` unchanged. A control that hand-computed the digest instead
    would pass even if that property broke.
    """
    kw.pop("extra", None); kw.pop("gate", None); kw.pop("base", None)
    body = build(**kw).split(BOUNDARY, 1)[0] + b"\n" + trailer.encode()
    lines = []
    if approve:
        lines.append(f"approved_digest: {approve_digest or digest(body)}")
        lines.append(f"approval_token: {token}")
    if gate is not None:
        lines.append(f"execution_gate: {gate}")
    if not lines:
        return body
    return body.replace(b"supersedes: []\n", b"supersedes: []\n" + "\n".join(lines).encode() + b"\n", 1)


def _ports(repo=None, impact=None, artifacts=None, registry=None, merge_facts=None):
    from .__main__ import Ports
    return Ports(repo=repo or FakeRepo(), impact=impact or FakeImpact(),
                 artifacts=artifacts or FakeArtifacts(), registry=registry or FakeRegistry(),
                 merge_facts=merge_facts or FakeMergeFacts())


def _run(raw: bytes, *, changed=("src/fanops/example.py",), phase="at-head", pr=None,
         artifacts=None, registry=None, impact=None, main_blob=None, repo_fail=None,
         extra_blobs=None, path=CONTRACT_PATH, ancestry=(), parent_changed=None,
         merge_facts=None, _trees=None, _unreadable=()):
    from .__main__ import run
    head = "h" * 40
    blobs = {(head, path): raw, (head, ADR_PATH): ADR_BLOB,
             ("origin/main", ADR_PATH): ADR_BLOB}
    if main_blob is not None:
        blobs[("origin/main", path)] = main_blob
    blobs.update(extra_blobs or {})
    since = {PARENT: parent_changed if parent_changed is not None else [path]} if ancestry else {}
    repo = FakeRepo(blobs=blobs, changed=changed, head=head, fail=repo_fail,
                    ancestry=ancestry, changed_since=since, trees=_trees, unreadable=_unreadable)
    ports = _ports(repo=repo, impact=impact, artifacts=artifacts, registry=registry,
                   merge_facts=merge_facts)
    return run(ports, path, base="base", head=head, pr=pr, phase=phase)



# One injection per rule-consumed diagnostic code. Kept as a table because each is a single mutation
# and a function apiece would bury the pattern that matters: EVERY code some rule reads is produced
# here by something. `NC-C31` proves the table is complete.
_EV_BAD = "| a claim | a run |  | blob:src/fanops/example.py |\n"
_EV_I1 = "| a claim | a run | blobdeadbeef | blob:src/fanops/example.py |\n"

_CODE_INJECTIONS = {
    "NC-C32": dict(raw=lambda: build().replace(BOUNDARY, b"\n## NotLifecycle\n", 1)),
    "NC-C33": dict(raw=lambda: build() + BOUNDARY + b"| a | b | c |\n"),
    "NC-C34": dict(mut=lambda d: d.replace("---\n", "", 1)),
    "NC-C35": dict(mut=lambda d: d.replace("supersedes: []\n---\n", "supersedes: []\n", 1)),
    "NC-C36": dict(mut=lambda d: d.replace("supersedes: []", "supersedes []", 1)),
    "NC-C37": dict(mut=lambda d: d.replace("| S01_foundation | the changed module belongs to it |",
                                           "| S01_foundation |", 1)),
    "NC-C38": dict(mut=lambda d: d.replace("| subsystem_id | why_touched |",
                                           "| why_touched | subsystem_id |", 1)),
    "NC-C39": dict(mut=lambda d: d.replace("| what | must_move_with | why |\n|---|---|---|\n", "", 1)),
    "NC-C40": dict(mut=lambda d: d.replace("supersedes: []", "supersedes: []\n  - orphan", 1)),
    "NC-C41": dict(mut=lambda d: d.replace("### coupling", "### bogus_section", 1)),
    "NC-C42": dict(mut=lambda d: d.replace("### coupling", "### traits", 1)),
    "NC-C43": dict(life=lambda x: x.replace("| timestamp | event | values |",
                                            "| event | timestamp | values |", 1)),
    "NC-C44": dict(life=lambda x: x + "| 2026-07-18T11:00:00Z | binding |\n"),
    "NC-C45": dict(mut=lambda d: d.replace("invariants: [LAW-SOT-01]", "invariants: []", 1)),
    "NC-C46": dict(kw=dict(cid="CC-not-a-date-slug")),
    "NC-C47": dict(extra="| 2026-07-18T11:00:00Z | teleported | x=1 |\n"),
    "NC-C48": dict(extra="| yesterday | binding | pr=1 |\n"),
    "NC-C49": dict(extra="| 2026-07-17T09:00:00Z | binding | pr=1 |\n"),
    "NC-C50": dict(extra="| 2026-07-18T11:00:00Z | refused | reason=x |\n"
                         "| 2026-07-18T12:00:00Z | binding | pr=1 |\n"),
    "NC-C51": dict(extra="| 2026-07-18T12:00:00Z | accepted | merge_sha=abc |\n"),
    "NC-AC-12": dict(extra="| 2026-07-18T12:00:00Z | merged | note=no sha |\n"),
    "NC-C52": dict(main=lambda: build(decl_mutate=lambda d: d.replace("Prove the", "Proved the", 1))),
    "NC-C53": dict(mut=lambda d: d.replace(ADR_PATH, "docs/adr/does-not-exist.md", 1)),
    "NC-C54": dict(mut=lambda d: d.replace("| ADR-0105 |", "| DC-999 |", 1)),
    "NC-C55": dict(kw=dict(evidence=_EV_BAD)),
    "NC-C56": dict(kw=dict(evidence=_EV_I1)),
    "NC-C57": dict(extra="| 2026-07-18T12:00:00Z | merge_approved | operator=solo |\n"),
}


def _code_control(c: Control) -> tuple[bool, str]:
    spec = _CODE_INJECTIONS[c.id]
    if "raw" in spec:
        return _decides(c, spec["raw"]())
    kw = dict(spec.get("kw", {}))
    if "mut" in spec: kw["decl_mutate"] = spec["mut"]
    if "extra" in spec: kw["extra"] = spec["extra"]
    raw = build(**kw)
    if "life" in spec:
        decl, _, life = raw.partition(BOUNDARY)
        raw = decl + BOUNDARY + spec["life"](life.decode()).encode()
    run_kw = {"main_blob": spec["main"]()} if "main" in spec else {}
    return _decides(c, raw, **run_kw)


# ── the injections ──────────────────────────────────────────────────────────────────────────
def _grammar(line: str):
    return lambda d: d.replace("supersedes: []", line, 1)


_GRAMMAR_INJECTIONS = {
    "NC-C01": _grammar("supersedes: &anchor []"),
    "NC-C02": _grammar("supersedes: *alias"),
    "NC-C03": _grammar("supersedes: !!str []"),
    "NC-C04": _grammar("supersedes: |"),
    "NC-C05": _grammar("supersedes: []\n  nested: 1"),
    "NC-C05b": _grammar("<<: base"),
    "NC-C05c": _grammar("supersedes:\t[]"),
    "NC-C05d": _grammar("supersedes: [] # a comment"),
    "NC-C05e": _grammar("supersedes: { a: 1 }"),
    "NC-C07a": _grammar("supersedes: []\nid: CC-2026-07-18-example"),
}


def detect(c: Control) -> tuple[bool, str]:
    """Run ONE control end to end. THIS IS THE ONLY IMPLEMENTATION — the CLI and pytest both call it.

    `tools/arch/selftest.py` records why that matters: `run()` and the pytest wrapper each had their
    own copy of the detection logic, they drifted, and `selftest` reported 23/23 green while pytest
    failed `NC-23` on the same commit. Two implementations of "does this control detect?" will
    always drift, and the one that drifts is the one nobody watches.
    """
    if c.id in _GRAMMAR_INJECTIONS:
        return _decides(c, build(decl_mutate=_GRAMMAR_INJECTIONS[c.id]))
    if c.id in _CODE_INJECTIONS:
        return _code_control(c)
    fn = globals().get(f"_c_{c.id.replace('-', '_').lower()}")
    if fn is None:
        raise AssertionError(f"no injection defined for {c.id}")
    return fn(c)


def _decides(c: Control, raw: bytes, **kw) -> tuple[bool, str]:
    decision, ctx = _run(raw, **kw)
    codes = {d.code for d in decision.diagnostics}
    if c.expect_code and c.expect_code not in codes:
        return False, f"NOT DETECTED — {c.expect_code} absent (got {sorted(codes)[:6]})"
    if c.expect_rule and decision.rule != c.expect_rule:
        return False, (f"NOT DETECTED — rule {decision.rule} fired, expected {c.expect_rule} "
                       f"(decision {decision.outcome})")
    return True, f"{decision.rule} → {decision.outcome}"


def _c_nc_c05f(c):
    raw = build().replace(b"\n", b"\r\n")
    return _decides(c, raw)


def _c_nc_c05g(c):
    return _decides(c, build(decl_mutate=lambda d: d.replace("\n---\n\n# CC", "\n---\n\n---\n\n# CC", 1)))


def _c_nc_c06(c):
    """No implicit typing: twelve values a YAML parser would coerce must stay literal strings."""
    values = ["true", "false", "null", "~", "yes", "no", "on", "off", "2026-07-18", "0x10",
              "1_000", "NaN"]
    bad = []
    for v in values:
        d = parse(build(decl_mutate=lambda x, v=v: x.replace("id: CC-2026-07-18-example",
                                                             f"id: {v}", 1)))
        got = d.value("id")
        if got != v:
            bad.append(f"{v!r} -> {got!r}")
    if bad:
        return False, "NOT DETECTED — implicit typing occurred: " + "; ".join(bad)
    return True, f"all {len(values)} canonical values parsed as literal strings"


def _c_nc_c08(c):
    """`D` binds to BYTES. A round trip through the parser must not move it by one byte."""
    raw = build()
    before = parse(raw).digest
    after = digest(raw.split(BOUNDARY, 1)[0])
    if before != after:
        return False, f"NOT DETECTED — digest moved {before} -> {after}"
    appended = raw + b"| 2026-07-18T11:00:00Z | binding | pr=1 |\n"
    if parse(appended).digest != before:
        return False, "NOT DETECTED — a lifecycle append changed `D`"
    return True, f"`D` stable across parse and append: {before[:24]}…"


def _c_nc_c09(c):
    """One flipped declaration byte ⇒ `D` moves ⇒ the `approved` event no longer names it."""
    raw = build()
    d0 = parse(raw).digest
    mutated = build(decl_mutate=lambda x: x.replace("Prove the compiler", "Prove  the compiler", 1),
                    approve_digest=d0)
    if parse(mutated).digest == d0:
        return False, "NOT DETECTED — a declaration byte changed and `D` did not"
    return _decides(c, mutated)


# ── the single-operator authorization fixture ───────────────────────────────────────────────
# `PARENT` is a real prior commit of the SAME contract: the head is that blob plus one lifecycle
# append and nothing else, which is exactly what `parent_binds` proves. Every authorization control
# below mutates ONE field of the `merge_approved` row against this fixture.
PARENT = "p" * 40


def _authz(*, digest=None, pr=7, operator="operator", phrase="APPROVE THE MERGE", parent=PARENT,
           drop=()):
    """One `merge_approved` row. `drop` omits keys so a control can probe a single missing value."""
    kv = [("parent_sha", parent), ("digest", digest), ("pr", pr), ("operator", operator),
          ("token", phrase)]
    body = "; ".join(f"{k}={v}" for k, v in kv if k not in drop)
    return f"| 2026-07-18T11:00:00Z | merge_approved | {body} |\n"


def _authorized(*, changed=("src/fanops/example.py",), parent_changed=None, parent_decl=None,
                parent_life="", declare=(), **authz):
    """(head_raw, kwargs) for a contract whose head carries an operator authorization of `PARENT`.

    `parent_life` lets a control give the parent a DIFFERENT lifecycle so the head is a rewrite
    rather than an append; `parent_decl` lets it give the parent a different declaration. `declare`
    widens the declared scope so a control probing authorization does not ALSO trip `ST-1` — a
    control that fails on the wrong rule proves nothing about the rule it names.
    """
    d = parse(build(declare=declare)).digest
    authz.setdefault("digest", d)
    head_raw = build(declare=declare, extra=_authz(**authz))
    parent_raw = build(declare=declare, extra=parent_life) if parent_decl is None else parent_decl
    head = "h" * 40
    return head_raw, {
        "phase": "merge-gate", "pr": 7, "changed": changed,
        "extra_blobs": {(PARENT, CONTRACT_PATH): parent_raw},
        "ancestry": {(PARENT, head)},
        "parent_changed": parent_changed,
    }


def _c_nc_c10(c):
    """An append preserves `D` (content approval survives) but moves the head off the authorized
    parent, so the authorization no longer binds."""
    raw = build(extra=_authz(digest=parse(build()).digest, parent="q" * 40))
    if parse(raw).digest != parse(build()).digest:
        return False, "NOT DETECTED — an append changed `D`"
    return _decides(c, raw, phase="merge-gate", pr=7)


# ── the single-operator authorization boundary ──────────────────────────────────────────────
def _c_nc_so_01(c):
    """CONTROL 1 + 3 + 4: zero reviews, no review API, no principal census — and it AUTHORIZES."""
    raw, kw = _authorized()
    return _decides(c, raw, **kw)


def _c_nc_so_02(c):
    """CONTROL 5: no `merge_approved` at all stays unauthorized."""
    return _decides(c, build(), phase="merge-gate", pr=7)


def _c_nc_so_03(c):
    """CONTROL 6: an authorization naming a different `D` authorized different text."""
    raw, kw = _authorized(digest="sha256:" + "0" * 64)
    return _decides(c, raw, **kw)


def _c_nc_so_04(c):
    """CONTROL 7: an authorization naming a different PR authorized a different change."""
    raw, kw = _authorized(pr=999)
    return _decides(c, raw, **kw)


def _c_nc_so_05(c):
    """CONTROL 8: a parent that is not an ancestor of the head."""
    raw, kw = _authorized(parent="z" * 40)
    return _decides(c, raw, **kw)


def _c_nc_so_06(c):
    """CONTROL 9: a NON-CONTRACT path moved after the authorized parent.

    The path is DECLARED, so `ST-1` cannot fire and `ST-9` is the only rule left to catch it: the
    authorization covered the parent, and code changed underneath it afterwards.
    """
    extra = "tools/contract/decide.py"
    raw, kw = _authorized(changed=("src/fanops/example.py", extra), declare=(extra,))
    kw["parent_changed"] = [extra]
    return _decides(c, raw, **kw)


def _c_nc_so_07(c):
    """CONTROL 10: the declaration changed after the authorized parent — a new contract, not an
    append."""
    raw, kw = _authorized(parent_decl=build(cid="CC-2026-07-18-example", stops="T9: edited"))
    return _decides(c, raw, **kw)


def _c_nc_so_08(c):
    """CONTROL 11: the head lifecycle does not byte-prefix-extend the parent's."""
    raw, kw = _authorized(parent_life="| 2026-07-18T10:30:00Z | binding | pr=91 |\n")
    return _decides(c, raw, **kw)


def _c_nc_so_09(c):
    """CONTROL 12: a LIFECYCLE-ONLY append after the authorized parent still binds."""
    raw, kw = _authorized()
    raw = raw + b"| 2026-07-18T12:00:00Z | binding | pr=7 |\n"
    return _decides(c, raw, **kw)


def _c_nc_so_10(c):
    """CONTROL 13: an authorization with no operator token. The agent may transcribe an operator's
    token; it may never author one, so a row that quotes nothing authorizes nothing."""
    raw, kw = _authorized(drop=("token",))
    return _decides(c, raw, **kw)


def _c_nc_so_11(c):
    """CONTROL 14: NO decision path reads second-person evidence — proven by SOURCE, not behaviour.

    A behavioural probe cannot show absence: it can only show that one input did not change one
    verdict. This asserts the authorization modules contain no review, reviewer-identity or
    principal-census read at all, which is the property the model actually claims.
    """
    import pathlib as _pl
    banned = ("approvals(", "write_principals", "read_reviews", "read_principals", "ReviewPort",
              "reviewDecision", "collaborators", "\"APPROVED\"", "'APPROVED'")
    here = _pl.Path(__file__).parent
    hits = []
    for mod in ("lifecycle.py", "decide.py", "adapters.py", "__main__.py", "report.py", "model.py"):
        text = (here / mod).read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.lstrip().startswith("#") or line.lstrip().startswith('"'):
                continue          # prose may NAME what was removed; only executable code counts
            for b in banned:
                if b in line:
                    hits.append(f"{mod}: {line.strip()[:70]}")
    if hits:
        return False, f"NOT DETECTED — second-person read still present: {hits[:3]}"
    return True, f"no second-person read in {6} authorization modules"


# ── ADR-0105 §4.3a · acceptance is verified, never asserted ─────────────────────────────────
#
# THE DEFECT THESE EXIST FOR: the previous implementation returned state `accepted` whenever an
# `accepted` ROW was present, and set the acceptance gate `satisfied` on the same test. The claim
# being evaluated was the entirety of its own evidence. Worse, NO decision rule read the gate, so
# even the wrong answer was unobservable — which is why `ST-10` had to be added alongside the fix.
#
# Every control below builds a contract that is landed, authorized and merged, then breaks EXACTLY
# ONE external fact. A control that fails on the wrong rule proves nothing about the rule it names.
MERGE_SHA = "m" * 40
MERGED_AT = "2026-07-19T23:19:38Z"


BASE_SHA = "b" * 40
# The pinned registry blob every acceptance fixture reads its required set from — the in-repo file at
# the contract's own base commit, never live branch protection.
REGISTRY_BLOB = (b"current_required_contexts:\n  - \"unit\"\n"
                 b"controls:\n"
                 b"  - id: CI-UNIT\n"
                 b"    name: unit\n"
                 b"    workflow: .github/workflows/ci.yml\n"
                 b"    job: unit\n")
WORKFLOW_PATH = ".github/workflows/ci.yml"
WORKFLOW_BLOB = b"jobs:\n  unit:\n    name: unit\n  other:\n    name: something else\n"


def _acc_rows(*, merge_sha=MERGE_SHA, accepted_sha=None, merged_at=MERGED_AT, runs="101",
              decision=ACCEPTED_DECISION, drop=()):
    """A `merged` + `accepted` pair. `drop` omits keys so a control can probe one missing value.

    The two SHAs are separable BECAUSE THE TWO CHECKS ARE. The `merged` row is read during
    authorization rederivation; the `accepted` row is read during acceptance. A control that broke
    both at once would fire `ST-9` and never reach `ST-10`, proving nothing about acceptance.
    """
    kv = [("merge_sha", merge_sha if accepted_sha is None else accepted_sha),
          ("decision", decision), ("evidence", "the success condition"),
          ("date", "2026-07-20"), ("operator", "operator"), ("check_runs", runs)]
    body = "; ".join(f"{k}={v}" for k, v in kv if k not in drop)
    # `merged_at` is the ROW TIMESTAMP, not a value beside it — the column is the claim being verified.
    return (f"| {merged_at} | merged | merge_sha={merge_sha} |\n"
            f"| 2026-07-20T00:00:00Z | accepted | {body} |\n")


def _landed(*, rows=None, mf=None, trees=None, registry=REGISTRY_BLOB,
            workflow=WORKFLOW_BLOB, head_workflow=None, **authz):
    """(raw, kwargs) for a LANDED, authorized, merged contract carrying `merged` + `accepted`.

    Defaults are all-correct on purpose: each control breaks one fact and inherits the rest, so a
    red result names the fact it broke rather than a fixture that was never coherent.
    """
    head = "h" * 40
    d = parse(build()).digest
    authz.setdefault("digest", d)
    binding = "| 2026-07-18T10:30:00Z | binding | branch=feat/x; pr=7 |\n"
    raw = build(base=BASE_SHA,
                extra=binding + _authz(**authz) + (rows if rows is not None else _acc_rows()))
    parent_raw = build(base=BASE_SHA, extra=binding)
    blobs = {(PARENT, CONTRACT_PATH): parent_raw, (head, CONTRACT_PATH): raw}
    if registry is not None:
        blobs[(BASE_SHA, CI_REGISTRY_PATH)] = registry
    # The governing workflow at BOTH ends. Equal by default, because "did this change edit the
    # workflow that certifies it" must be answerable; a control makes them differ to prove it is.
    if workflow is not None:
        blobs[(BASE_SHA, WORKFLOW_PATH)] = workflow
        at_head = workflow if head_workflow is None else head_workflow
        blobs[(head, WORKFLOW_PATH)] = at_head
        pr_head = (mf.d.get("pr_head") if mf is not None else head) or head
        blobs[(pr_head, WORKFLOW_PATH)] = at_head
    return raw, {
        # NO explicit `pr`. The governed PR comes from the contract's own `binding` row, which is the
        # ordinary command path — passing `--pr` here would hide the very bug `NC-AC-13` exists for.
        "phase": "merge-gate", "main_blob": raw,
        "extra_blobs": blobs,
        "ancestry": {(PARENT, head), (MERGE_SHA, "origin/main")},
        "parent_changed": [CONTRACT_PATH],
        "merge_facts": mf or FakeMergeFacts(pr_head=head),
        "trees": trees,
    }


def _acc_state(raw, kw):
    """The derived STATE for an acceptance fixture. State is the thing these controls are about."""
    trees = kw.pop("trees", None)
    _, ctx = _run(raw, _trees=trees, **kw)
    return ctx["state"], ctx["gates"]


def _c_nc_ac_01(c):
    """REQUIRED CONTROL 1: an `accepted` row ALONE does not yield `accepted`.

    The row is present and complete; the platform simply does not corroborate it. Under the previous
    implementation this returned `accepted` — the row was the whole proof.
    """
    raw, kw = _landed(mf=FakeMergeFacts(pr_head="h" * 40, merged=False, merge_sha=""))
    st, g = _acc_state(raw, kw)
    if st == "accepted":
        return False, "NOT DETECTED — an unsupported `accepted` row still derived state 'accepted'"
    return True, f"state {st!r}, acceptance gate {g.acceptance!r} — the row did not prove itself"


def _c_nc_ac_02(c):
    """REQUIRED CONTROL 2: `accepted` row + STALE authorization ⇒ `acceptance_claimed` via `ST-10`.

    Also the control that keeps `ST-10` non-decorative: it asserts the RULE fires, not merely that
    the state is right. A state nobody's verdict depends on would be the old defect wearing a new
    name.
    """
    raw, kw = _landed(parent="q" * 40)          # a parent that is not an ancestor of the PR head
    st, _ = _acc_state(raw, kw)
    if st != "acceptance_claimed":
        return False, f"NOT DETECTED — expected 'acceptance_claimed', got {st!r}"
    return True, f"state {st!r} — acceptance cannot rest on an unauthorized merge"


def _c_nc_ac_03(c):
    """REQUIRED CONTROL 3: authorization rederived ACROSS THE SQUASH + valid evidence ⇒ `accepted`.

    The positive case, and the one that proves the others are not passing for a trivial reason. The
    authorized parent is NOT an ancestor of the merge commit — that is what a squash does — so this
    only passes because rederivation asks against the pre-merge PR head.
    """
    raw, kw = _landed()
    st, g = _acc_state(raw, kw)
    if st != "accepted" or g.acceptance != "satisfied":
        return False, f"NOT DETECTED — a fully-verified acceptance derived {st!r}/{g.acceptance!r}"
    return True, f"state {st!r}, acceptance {g.acceptance!r} — verified across the squash"


def _c_nc_ac_04(c):
    """REQUIRED CONTROL 4: PR-head/merge-tree MISMATCH ⇒ `merged_unverified`, NOT `ST-7`.

    A completed read that disagrees is a KNOWN NEGATIVE. Reporting it as `ST-7` would say "could not
    check" about a check that ran and failed — the difference between ignorance and a finding.
    """
    raw, kw = _landed(rows="", trees={"h" * 40: "a" * 40, MERGE_SHA: "b" * 40})
    dec, ctx = _run(raw, _trees=kw.pop("trees"), **kw)
    if ctx["state"] != "merged_unverified":
        return False, f"NOT DETECTED — expected 'merged_unverified', got {ctx['state']!r}"
    if dec.rule == "ST-7":
        return False, "NOT DETECTED — a completed, disagreeing read was reported as unavailable"
    return True, f"state 'merged_unverified' via {dec.rule} — a finding, not an unavailability"


def _c_nc_ac_05(c):
    """REQUIRED CONTROL 5: an UNAVAILABLE platform read ⇒ `ST-7`, never a negative finding.

    Fails the PR read itself. The distinction this defends is the whole of `Derived.unverifiable`:
    a network failure must never be able to read as a governance verdict.
    """
    raw, kw = _landed(mf=FakeMergeFacts(fail="pull"))
    dec, ctx = _run(raw, _trees=kw.pop("trees"), **kw)
    if dec.rule != "ST-7":
        return False, f"NOT DETECTED — an unreadable platform gave {dec.rule!r}, not 'ST-7'"
    return True, f"ST-7 → {dec.outcome}; state {ctx['state']!r} — unavailable is never authorized"


def _c_nc_ac_06(c):
    """REQUIRED CONTROL 6: a WRONG merge SHA in the `accepted` row ⇒ `acceptance_claimed` via ST-10.

    Also the control that keeps `ST-10` non-decorative — it asserts the RULE fires, not merely that
    the state is right. Only the ACCEPTED row's SHA is wrong, so authorization still rederives and
    `ST-9` does not pre-empt: this is the narrowest fixture that reaches the acceptance rule at all.
    """
    raw, kw = _landed(rows=_acc_rows(accepted_sha="z" * 40))
    trees = kw.pop("trees", None)
    dec, ctx = _run(raw, _trees=trees, **kw)
    if ctx["state"] != "acceptance_claimed":
        return False, f"NOT DETECTED — expected 'acceptance_claimed', got {ctx['state']!r}"
    if dec.rule != "ST-10":
        return False, f"NOT DETECTED — expected rule 'ST-10', got {dec.rule!r}"
    return True, f"ST-10 → {dec.outcome}; the row named a merge the platform did not perform"


def _c_nc_ac_07(c):
    """REQUIRED CONTROL 7: a WRONG `mergedAt` ⇒ `acceptance_claimed`."""
    raw, kw = _landed(rows=_acc_rows(merged_at="2020-01-01T00:00:00Z"))
    st, _ = _acc_state(raw, kw)
    if st != "acceptance_claimed":
        return False, f"NOT DETECTED — expected 'acceptance_claimed', got {st!r}"
    return True, f"state {st!r} — the recorded merge date is not the platform's"



# ── §4.3a provenance, chronology and the PR-head blob ───────────────────────────────────────
#
# Each of these breaks exactly ONE fact in an otherwise fully-verified world, so a red result names
# the fact it broke rather than a fixture that was never coherent to begin with.
_ACC_AT = "2026-07-20T00:00:00Z"        # the acceptance instant `_acc_rows` writes


def _prov_state(*, _unreadable=(), **landed):
    """Run a landed fixture and return `(state, gates, rule)`.

    `_unreadable` is popped by the SIGNATURE, not forwarded: `_landed` passes every unknown keyword
    on to `_authz`, so a stray one becomes a confusing TypeError instead of a fixture setting.
    """
    raw, kw = _landed(**landed)
    trees = kw.pop("trees", None)
    dec, ctx = _run(raw, _trees=trees, _unreadable=_unreadable, **kw)
    return ctx["state"], ctx["gates"], dec.rule


def _c_nc_ac_18(c):
    """The OTHER temporal direction: a newer run that ALREADY EXISTED at the acceptance instant.

    `NC-AC-14` proves a rerun created AFTER the decision cannot revise it. That alone is satisfied by
    a verifier with no clock at all, which is why it was insufficient on its own. Here the newer run
    started BEFORE the acceptance and is failing: the thing being accepted was not green at the
    moment it was accepted, and recording the older green run must not hide that.
    """
    runs = [_check_run("101"),
            _check_run("999", conclusion="failure", started="2026-07-19T23:30:00Z",
                       completed="2026-07-19T23:40:00Z")]
    st, g, rule = _prov_state(mf=FakeMergeFacts(pr_head="h" * 40, runs=runs))
    if st != "acceptance_claimed":
        return False, f"NOT DETECTED — a newer pre-acceptance failure derived {st!r}"
    return True, f"{rule} → the recorded run was not the latest qualifying one at {_ACC_AT}"


def _c_nc_ac_19(c):
    """`created.base_sha` is agent-written and OUTSIDE `D`; it must not select its own bar.

    The contract names a base the platform does not report, which is how a change could point the
    required-context read at an older, weaker registry commit. Both SHAs were read successfully, so
    this is a FINDING and never `ST-7`.
    """
    st, g, rule = _prov_state(mf=FakeMergeFacts(pr_head="h" * 40, base_sha="9" * 40))
    if rule == "ST-7":
        return False, "NOT DETECTED — a completed disagreement about the base was reported as unavailable"
    if st != "acceptance_claimed":
        return False, f"NOT DETECTED — an unanchored `created.base_sha` derived {st!r}"
    # THE REASON, NOT MERELY THE STATE. With the base unverified the required set is deliberately
    # left unread, so an empty-required-set branch also refuses — and this control would pass with
    # the anchor check deleted, which is precisely how a control becomes decorative.
    if not any("is not the one this change was opened against" in d for d in g.detail):
        return False, (f"NOT DETECTED — refused, but not because the base is unanchored: "
                       f"{[d for d in g.detail if 'accept' in d.lower()][:1]}")
    return True, f"{rule} → the contract cannot choose the commit whose registry sets its bar"


def _c_nc_ac_20(c):
    """MATCHING NAME, WRONG APP. Any App with `checks:write` can publish `unit` and conclude success."""
    runs = [_check_run("101", app_id="99999", app_slug="a-friendly-bot")]
    st, g, rule = _prov_state(mf=FakeMergeFacts(pr_head="h" * 40, runs=runs))
    if st != "acceptance_claimed":
        return False, f"NOT DETECTED — a non-Actions App's green run derived {st!r}"
    return True, f"{rule} → a name is not provenance; the producing App is pinned"


def _c_nc_ac_21(c):
    """MATCHING NAME, WRONG WORKFLOW. The registry pins the context to `ci.yml`; this ran elsewhere."""
    wf = [{"id": "9001", "path": ".github/workflows/somewhere-else.yml", "head_sha": "m" * 40}]
    st, g, rule = _prov_state(mf=FakeMergeFacts(pr_head="h" * 40, wf_runs=wf))
    if st != "acceptance_claimed":
        return False, f"NOT DETECTED — a run from an unpinned workflow derived {st!r}"
    # THE REASON, NOT MERELY THE STATE. An unpinned path is also absent from the blob-stability map,
    # so that guard refuses too — and this control would survive deleting the path check itself.
    if not any("is pinned to" in d for d in g.detail):
        return False, (f"NOT DETECTED — refused, but not because the workflow path is wrong: "
                       f"{[d for d in g.detail if 'workflow' in d.lower()][:1]}")
    return True, f"{rule} → the joined workflow path must be the one the registry pins"


def _c_nc_ac_22(c):
    """MATCHING NAME AND WORKFLOW, CHANGED BLOB. A workflow edited inside the change it certifies."""
    st, g, rule = _prov_state(head_workflow=b"jobs:\n  unit:\n    name: unit\n    if: false\n")
    if st != "acceptance_claimed":
        return False, f"NOT DETECTED — a workflow edited inside its own change derived {st!r}"
    return True, f"{rule} → the governing workflow must be unchanged from the verified base"


def _c_nc_ac_23(c):
    """A check run joining to NO job — nothing connects the tick to a workflow this repo runs."""
    st, g, rule = _prov_state(mf=FakeMergeFacts(pr_head="h" * 40, jobs={"9001": []}))
    if st != "acceptance_claimed":
        return False, f"NOT DETECTED — an unjoined check run derived {st!r}"
    return True, f"{rule} → an unjoinable run is not evidence"


def _c_nc_ac_24(c):
    """An AMBIGUOUS join is unavailability, not a finding — two jobs claiming one check run.

    The read completed; what it returned cannot be resolved to a single provenance. Answering
    "unverified" would state a conclusion about a question the data does not settle.
    """
    jobs = {"9001": [{"name": "unit", "run_id": "9001", "check_run_id": "101",
                      "conclusion": "success", "status": "completed",
                      "started_at": "2026-07-19T23:00:00Z",
                      "completed_at": "2026-07-19T23:05:00Z"},
                     {"name": "unit-again", "run_id": "9001", "check_run_id": "101",
                      "conclusion": "success", "status": "completed",
                      "started_at": "2026-07-19T23:00:00Z",
                      "completed_at": "2026-07-19T23:05:00Z"}]}
    st, g, rule = _prov_state(mf=FakeMergeFacts(pr_head="h" * 40, jobs=jobs))
    if rule != "ST-7":
        return False, f"NOT DETECTED — an ambiguous join gave {rule}, not ST-7"
    return True, "ST-7 → an unresolvable provenance is unavailability, never a verdict"


def _c_nc_ac_25(c):
    """THE POSITIVE CONTROL for the whole chain. Without it every case above passes by breaking it."""
    st, g, rule = _prov_state()
    if st != "accepted" or g.acceptance != "satisfied":
        return False, f"NOT DETECTED — full valid provenance derived {st!r}/{g.acceptance!r}"
    # THE POSITIVE MUST NAME WHAT IT PROVED. Asserting only `accepted` would stay green if a link in
    # the chain were deleted, which is how the job key sat unconsumed while this control passed.
    why = " ".join(g.detail)
    # `unit->unit=unit` is the KEY=DISPLAY pair resolved from the workflow blob and carried out of
    # the predicate that consumed it, so this token cannot appear unless the binding was checked.
    for token in ("check_run_url", "registry-pinned job key", "unit->unit=unit",
                  "unchanged from the verified base"):
        if token not in why:
            return False, f"NOT DETECTED — the verified reason does not evidence {token!r}: {why[-200:]}"
    return True, "App, workflow path, JOB KEY, join, blob stability and chronology all verify"


def _c_nc_ac_26(c):
    """`decision=` is READ. It was recorded and never consulted, so a rejection verified as one."""
    st, g, rule = _prov_state(rows=_acc_rows(decision="rejected"))
    if st != "acceptance_claimed":
        return False, f"NOT DETECTED — `decision=rejected` derived {st!r}"
    return True, f"{rule} → the row's own decision value is enforced, not decorative"


def _c_nc_ac_27(c):
    """Recorded ids must be UNIQUE DECIMAL and DETERMINISTICALLY ORDERED."""
    for label, ids in (("non-decimal", "unit-101"), ("duplicated", "101,101"),
                       ("unordered", "999,101")):
        runs = [_check_run("101"), _check_run("999", name="e2e")]
        st, g, rule = _prov_state(rows=_acc_rows(runs=ids),
                                  mf=FakeMergeFacts(pr_head="h" * 40, runs=runs))
        if st != "acceptance_claimed":
            return False, f"NOT DETECTED — {label} recorded ids derived {st!r}"
    return True, "non-decimal, duplicated and unordered recorded ids each refuse"


def _c_nc_ac_28(c):
    """The contract ABSENT at the pre-merge PR head is a FINDING: the claim was not effective there."""
    # A DIFFERENT PR head, carrying no contract blob. Removing the entry from `extra_blobs` would
    # not do it: `_run` seeds `(head, path)` itself and `extra_blobs` is merged on top, so the blob
    # would come back and the control would silently test nothing.
    raw, kw = _landed(mf=FakeMergeFacts(pr_head="k" * 40))
    trees = kw.pop("trees", None)
    dec, ctx = _run(raw, _trees=trees, **kw)
    if dec.rule == "ST-7":
        return False, "NOT DETECTED — a contract absent at the PR head was reported as unavailable"
    if ctx["state"] == "accepted":
        return False, "NOT DETECTED — a contract absent at the PR head still verified"
    # THE REASON, NOT MERELY THE STATE. This PR head also fails parent-binding, so the fixture
    # refuses either way — and with the old `or raw` substitution reinstated this control still
    # passed, which is exactly the decorative-control shape it exists to prevent.
    if not any("ABSENT at the final pre-merge PR head" in d for d in ctx["gates"].detail):
        return False, (f"NOT DETECTED — refused, but not because the contract is absent at the PR "
                       f"head: {[d for d in ctx['gates'].detail if 'PR head' in d][:1]}")
    return True, f"{dec.rule} → the authorization was not in effect at the merged commit"


def _c_nc_ac_29(c):
    """The contract UNREADABLE at the pre-merge PR head is UNAVAILABILITY: `ST-7`, never a finding."""
    st, g, rule = _prov_state(mf=FakeMergeFacts(pr_head="k" * 40),
                              _unreadable={("k" * 40, CONTRACT_PATH)})
    if rule != "ST-7":
        return False, f"NOT DETECTED — an unreadable PR-head blob gave {rule}, not ST-7"
    return True, "ST-7 → could-not-read never becomes did-not-match"


def _c_nc_ac_30(c):
    """The blob PRESENT at the PR head is the one parent-binding reads — never a substitute.

    The old `repo.blob(...) or raw` fell back to the CURRENT bytes, so a head whose contract differed
    still bound against the document in hand. Here the PR head carries a DIFFERENT lifecycle, and the
    rederivation must read that one: a `merge_approved` present only in the current blob must not
    authorize.
    """
    import inspect
    code = [ln for ln in inspect.getsource(_lc._rederive_post_merge).splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    if not any("mf.pr_head_blob" in ln for ln in code):
        return False, "NOT DETECTED — rederivation does not read the carried PR-head blob"
    if any("or raw" in ln for ln in code):
        return False, "NOT DETECTED — the PR-head blob substitution is still live"
    st, g, rule = _prov_state()
    if g.merge_authorization != "satisfied":
        return False, f"NOT DETECTED — the present PR-head blob failed to bind ({g.merge_authorization})"
    return True, "parent binding reads exactly the bytes that stood at the PR head"



def _c_nc_ac_32(c):
    """EVERY read is an explicit GET. `gh api -f k=v` builds a request BODY and switches to POST.

    The workflow-runs read passes `head_sha` as a parameter, so without `--method GET` this tool was
    issuing a POST to `/actions/runs` — a mutation verb against an endpoint it must never mutate. The
    fake captures the ACTUAL argv rather than reading the source, because the defect is in what gets
    executed, and asserts both halves: the method is pinned AND the parameter still travels.
    """
    from . import adapters
    seen = []

    class _Done:
        returncode, stdout, stderr = 0, '[{"total_count":0,"workflow_runs":[]}]', ""

    def _fake_run(argv, **kw):
        seen.append(list(argv))
        return _Done()

    real = adapters.subprocess.run
    adapters.subprocess.run = _fake_run
    try:
        adapters.MergeFactsPort(slug="o/r").workflow_runs("a" * 40)
    finally:
        adapters.subprocess.run = real
    if not seen:
        return False, "NOT DETECTED — no request was issued at all"
    argv = seen[0]
    if "--method" not in argv or argv[argv.index("--method") + 1] != "GET":
        return False, f"NOT DETECTED — the request does not pin GET: {argv}"
    if not any(a.startswith("head_sha=") for a in argv):
        return False, f"NOT DETECTED — `head_sha` no longer travels with the request: {argv}"
    if "-f" in argv and argv.index("--method") > argv.index("-f"):
        return False, "NOT DETECTED — the method is set after the body flag"
    return True, f"GET pinned and head_sha supplied: {' '.join(argv[:6])}…"


def _c_nc_ac_33(c):
    """The registry's pinned job KEY is VERIFIED against the workflow blob, not unpacked and dropped.

    Three distinct reasons, because they fail differently and a single fixture would prove only one:
    a key the workflow does not declare, a key whose display name is not the required context, and
    two keys rendering one name — which the platform's job payload cannot disambiguate, since it
    reports the display name and never the key.
    """
    cases = [
        ("missing key", b"jobs:\n  somethingelse:\n    name: unit\n", "declares no such job"),
        ("wrong name", b"jobs:\n  unit:\n    name: not-the-context\n", "but that job is named"),
        ("ambiguous", b"jobs:\n  unit:\n    name: unit\n  twin:\n    name: unit\n",
         "cannot be attributed to either"),
    ]
    for label, blob, expect in cases:
        st, g, rule = _prov_state(workflow=blob, head_workflow=blob)
        if st != "acceptance_claimed":
            return False, f"NOT DETECTED — {label} derived {st!r}"
        if not any(expect in d for d in g.detail):
            return False, (f"NOT DETECTED — {label} refused, but not for its own reason: "
                           f"{[d for d in g.detail if 'job' in d][:1]}")
    return True, "missing key, wrong display name and duplicate display names each refuse by name"


def _c_nc_ac_34(c):
    """Pagination completeness is MANDATORY. It was honoured only when `total_count` happened to be
    an int, so a missing, null or string count skipped the check that a truncated read is least
    detectable without.

    A genuine two-page response must succeed; missing, non-integer, inconsistent, short and malformed
    responses must every one raise, which upstream is `ST-7`.
    """
    from . import adapters
    port = adapters.MergeFactsPort(slug="o/r")

    def run_with(payload):
        class _Done:
            returncode, stdout, stderr = 0, _json.dumps(payload), ""
        real = adapters.subprocess.run
        adapters.subprocess.run = lambda argv, **kw: _Done()
        try:
            return port.check_runs("a" * 40), None
        except adapters.PortError as exc:
            return None, str(exc)
        finally:
            adapters.subprocess.run = real

    def page(total, runs):
        return {"total_count": total, "check_runs": runs}
    def r(i):
        return {"id": i, "name": "unit", "conclusion": "success", "status": "completed",
                "started_at": "2026-07-19T23:00:00Z", "completed_at": "2026-07-19T23:05:00Z",
                "app": {"id": 15368, "slug": "github-actions"}}

    ok, err = run_with([page(3, [r("1"), r("2")]), page(3, [r("3")])])
    if err or [x["id"] for x in ok] != ["1", "2", "3"]:
        return False, f"NOT DETECTED — a genuine two-page read failed: {err or ok}"
    bad = {
        "missing": [{"check_runs": [r("1")]}],
        "non-integer": [page("3", [r("1")])],
        "null": [page(None, [r("1")])],
        "inconsistent": [page(3, [r("1"), r("2")]), page(9, [r("3")])],
        "short": [page(3, [r("1"), r("2")])],
        "malformed item": [page(1, ["not-an-object"])],
    }
    for label, payload in bad.items():
        got, err = run_with(payload)
        if err is None:
            return False, f"NOT DETECTED — a {label} response was accepted as complete"
    return True, "two real pages aggregate; missing, non-integer, null, inconsistent, short and malformed all raise"


def _c_nc_ac_31(c):
    """Counts stated in prose ROT. This derives them, so the sentence cannot outlive the code.

    Both numbers had already gone stale: `state()` was documented as twelve states while §4.3a had
    added three, and `RepoPort` was documented as four methods while it had seven. Neither drift was
    visible to anything, because no check read either sentence. This one does.
    """
    import ast, inspect, re as _re, textwrap
    from . import adapters as _ad
    src = inspect.getsource(_lc.state)
    # AST, NOT REGEX. `acceptance_claimed` is returned from a conditional expression, so a
    # `return <NAME>` pattern misses it and the derived count silently comes out one short — the
    # same class of error as the prose number this control exists to replace.
    tree = ast.parse(textwrap.dedent(src))
    states = set(_lc.TERMINAL_EVENTS)
    def results(expr):
        """Only what a `return` can EVALUATE TO. A conditional return's TEST is not a result — and
        walking it swept `SATISFIED` in as a fifteenth state, which is the prose-number error again
        in a different costume."""
        if isinstance(expr, ast.IfExp):
            return results(expr.body) + results(expr.orelse)
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return [expr.value]
        if isinstance(expr, ast.Name):
            v = getattr(_lc, expr.id, None)
            return [v] if isinstance(v, str) else []
        return []

    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            states.update(results(node.value))
    if len(states) != 14:
        return False, f"NOT DETECTED — `state()` derives {len(states)} states, not 14: {sorted(states)}"
    if "FOURTEEN" not in src:
        return False, "NOT DETECTED — the docstring no longer names the count it is checked against"
    repo_surface = sorted(n for n in vars(_ad.RepoPort) if not n.startswith("_"))
    if repo_surface != ["blob", "blob_sha", "contains", "diff_names", "is_ancestor", "resolve",
                        "tree_of"]:
        return False, f"NOT DETECTED — RepoPort's surface changed to {repo_surface}"
    if _re.search(r"\b(Four|four) methods\b", inspect.getsource(_ad.RepoPort)):
        return False, "NOT DETECTED — RepoPort again states a method count in prose"
    return True, f"14 derived states; RepoPort exposes {len(repo_surface)} read-only methods"


def _c_nc_ac_35(c):
    """REQUIRED CONTROL 35: the default base is the contract's OWN, not a ref that moves onto it.

    `--base` defaulted to `origin/main`. That is the right comparison while a change is in flight
    and the WRONG one the moment it lands, because the ref then IS the head: the diff comes back
    empty, no trait derives, and the contract is reported as declaring traits it does not derive.
    Every landed contract answered `CL-2` — a verdict about a default, dressed as a finding about
    the declaration. An empty diff is not evidence that nothing changed.

    Guarded at BOTH halves, because either alone leaves the defect reachable. The CLI must not
    reintroduce a ref default, and `run()` must actually consume `created.base_sha` when given none.
    Proven behaviourally against a repo where the two candidate bases DISAGREE: the declared base
    sees the changed file, `origin/main` sees nothing. Revert either half and this goes red.
    """
    import ast, inspect, textwrap
    from . import __main__ as _m
    from .__main__ import run

    tree = ast.parse(textwrap.dedent(inspect.getsource(_m.main)))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument"
                and node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "--base"):
            dflt = next((k.value for k in node.keywords if k.arg == "default"), None)
            if not (isinstance(dflt, ast.Constant) and dflt.value is None):
                return False, ("NOT DETECTED — `--base` again defaults to a ref rather than to the "
                               "contract's own `created.base_sha`")
            break
    else:
        return False, "NOT DETECTED — no `--base` argument was found to check"

    head = "h" * 40
    raw = build(base=BASE_SHA)
    repo = FakeRepo(blobs={(head, CONTRACT_PATH): raw, (head, ADR_PATH): ADR_BLOB,
                           ("origin/main", ADR_PATH): ADR_BLOB},
                    changed=[], head=head,
                    changed_since={BASE_SHA: ["src/fanops/example.py"], "origin/main": []})
    _dec, ctx = run(_ports(repo=repo), CONTRACT_PATH, base=None, head=head, pr=None,
                    phase="at-head")
    got = list(ctx["derived"].changed_files)
    if got != ["src/fanops/example.py"]:
        return False, (f"NOT DETECTED — the default base derived {got}; it did not read the "
                       f"contract's own `created.base_sha`")
    return True, f"the default base resolved to the declared {BASE_SHA[:12]}, deriving {got}"


def _c_nc_ac_17(c):
    """An `accepted` row recording NO `check_runs` is UNVERIFIED, never MALFORMED.

    The distinction is the whole point. `check_runs` post-dates every acceptance recorded before it
    existed, so requiring it STRUCTURALLY made the correctly-accepted Phase 3B contract MALFORMED and
    routed it to `A5` — "the lifecycle record is invalid, reordered, or the landed declaration was
    edited". A record written in good faith under the then-live rules was accused of being tampered
    with, which is the base-pinned-required-set defect one field over: a present-day bar reaching
    backwards to invalidate a historical acceptance.

    Fail-closed is UNCHANGED and that is what this asserts: the acceptance still does not verify, so
    it stops at `ST-10` with `acceptance_claimed`. Absent evidence is not satisfied; it is also not
    falsified. Both halves are checked here, because dropping either one would let this regress into
    the opposite defect.
    """
    raw, kw = _landed(rows=_acc_rows(drop=("check_runs",)))
    trees = kw.pop("trees", None)
    dec, ctx = _run(raw, _trees=trees, **kw)
    codes = {d.code for d in dec.diagnostics}
    if "ACCEPT-INCOMPLETE" in codes:
        return False, "NOT DETECTED — absent evidence was reported as a MALFORMED record"
    if dec.rule == "A5":
        return False, "NOT DETECTED — absent evidence routed to the lifecycle-tampering rule A5"
    if ctx["state"] != "acceptance_claimed":
        return False, f"NOT DETECTED — expected 'acceptance_claimed', got {ctx['state']!r}"
    if dec.rule != "ST-10":
        return False, f"NOT DETECTED — expected rule 'ST-10', got {dec.rule!r}"
    return True, f"ST-10 → {dec.outcome}; unverified, not malformed"


def _c_nc_ac_08(c):
    """REQUIRED CONTROL 8: a required run that FAILED, was SKIPPED, CANCELLED or is ABSENT.

    All four in one control because they are one predicate — "did every required context succeed" —
    and a required context that is merely present is not a required context that passed. `skipped`
    is the sharp case: at a merge commit unrelated jobs legitimately skip, so a naive "all runs
    succeeded" test would reject a valid acceptance while still admitting a skipped REQUIRED one.
    """
    for concl, runs in (("failure", [_check_run("101", conclusion="failure")]),
                        ("skipped", [_check_run("101", conclusion="skipped")]),
                        ("cancelled", [_check_run("101", conclusion="cancelled")]),
                        ("absent", [_check_run("101", name="something-else")])):
        raw, kw = _landed(mf=FakeMergeFacts(pr_head="h" * 40, runs=runs))
        st, _ = _acc_state(raw, kw)
        if st != "acceptance_claimed":
            return False, f"NOT DETECTED — a {concl} required run derived {st!r}"
    return True, "failed, skipped, cancelled and absent required runs all yield acceptance_claimed"


def _c_nc_ac_09(c):
    """REQUIRED CONTROL 9: ZERO reviews remains fully valid, through acceptance.

    The single-operator guarantee must survive this change. No review exists, none is read, and the
    contract still reaches `accepted` — which is the property #707 established and this must not
    quietly cost.
    """
    raw, kw = _landed()
    st, g = _acc_state(raw, kw)
    if st != "accepted" or g.merge_authorization != "satisfied":
        return False, f"NOT DETECTED — zero-review acceptance derived {st!r}"
    return True, f"state {st!r} with no review read anywhere in the path"


def _c_nc_ac_10(c):
    """REQUIRED CONTROL 10: the new platform port cannot express a review question.

    `MergeFactsPort` reintroduces a GitHub read, which is exactly where the #707 guarantee could
    quietly collapse. Proven by SHAPE: no general path method, and no field on `MergeFacts` that
    could carry a review — so there is no argument to bend toward `/reviews`.
    """
    from . import adapters
    from .model import MergeFacts as _MF
    import dataclasses as _dc
    port = adapters.MergeFactsPort
    escape = [n for n in dir(port)
              if n in ("get", "api", "request", "fetch", "call", "raw", "query")]
    if escape:
        return False, f"NOT DETECTED — the port exposes a general escape hatch: {escape}"
    public = sorted(n for n in vars(port) if not n.startswith("_"))
    if public != ["check_runs", "jobs", "pull", "workflow_runs"]:
        return False, f"NOT DETECTED — the port's surface widened to {public}"
    fields = {f.name for f in _dc.fields(_MF)}
    leaks = {f for f in fields if any(w in f for w in ("review", "approv", "principal", "collab"))}
    if leaks:
        return False, f"NOT DETECTED — MergeFacts can carry {leaks}"
    if hasattr(port, "required_contexts"):
        return False, ("NOT DETECTED — the port reads the required set from live configuration; it "
                       "must be pinned to the contract's base commit instead")
    # COUNT DERIVED, NOT WRITTEN. This message said "two closed reads" while printing four of them,
    # and stayed wrong through every green run — the predicate had already been widened to the real
    # surface and nothing read the sentence describing it. Same failure as the prose counts
    # `NC-AC-31` replaced, and the same failure that let `success_condition` 8 of
    # CC-2026-07-20-acceptance-rederivation ship a stale two-method enumeration.
    return True, f"{len(public)} closed reads {public}, and no MergeFacts field can name a person"


def _c_nc_ac_11(c):
    """REQUIRED CONTROL 11: `ST-4` remains absent, and `ST-10` is not it under a new number.

    `ST-10` asks whether an acceptance CLAIM verifies against the platform. That question has no
    second person in it, and could not be answered by one.
    """
    from . import decide as _d
    ids = [r.id for r in _d.RULES]
    if "ST-4" in ids:
        return False, "NOT DETECTED — ST-4 is registered again"
    st10 = [r for r in _d.RULES if r.id == "ST-10"]
    if not st10:
        return False, "NOT DETECTED — ST-10 is absent, so the acceptance gate has no reader"
    if "review" in st10[0].why.lower() or "person" in st10[0].why.lower():
        return False, f"NOT DETECTED — ST-10 names a second person: {st10[0].why!r}"
    return True, f"ST-4 absent; ST-10 reads the acceptance gate ({len(ids)} rules, no duplicates)"


def _c_nc_ac_13(c):
    """The ORDINARY command path: no `--pr`, and the lifecycle `binding.pr` must be sufficient.

    The platform read used to be guarded on the explicit `pr` argument while the governed PR was not
    resolved until two stages later, so `verify <contract>` — the normal invocation — skipped every
    post-merge check and still printed a confident verdict. Correctness must not depend on how the
    tool was called. Every other acceptance control also runs without `--pr`, so this path is the
    one under test throughout, not a special case.
    """
    raw, kw = _landed()
    assert "pr" not in kw, "the acceptance fixture must not pass an explicit --pr"
    st, g = _acc_state(raw, kw)
    if st != "accepted" or g.acceptance != "satisfied":
        return False, f"NOT DETECTED — the no---pr path derived {st!r}/{g.acceptance!r}"
    return True, f"state {st!r} with the governed PR taken from `binding.pr`, no --pr supplied"


def _c_nc_ac_14(c):
    """A LATER RERUN must not disturb an already-recorded verdict.

    The recorded id stays green while a NEWER run for the same context is added alongside it — and
    the newer run is failing, to make the point sharply. Resolving by name would pick the new one and
    silently decay a recorded acceptance into `acceptance_claimed` with nothing about the change
    having altered. Identity is the anchor.
    """
    # The newer run STARTED AFTER the acceptance instant — that is what makes it "later" as a fact
    # about time rather than about integer size, and it is the half `NC-AC-18` does not cover.
    later = [_check_run("101"),
             _check_run("999", conclusion="failure", started="2026-07-20T02:00:00Z",
                        completed="2026-07-20T02:05:00Z")]
    raw, kw = _landed(mf=FakeMergeFacts(pr_head="h" * 40, runs=later))
    st, g = _acc_state(raw, kw)
    if st != "accepted" or g.acceptance != "satisfied":
        return False, f"NOT DETECTED — a later rerun moved a recorded verdict to {st!r}"
    return True, "the recorded run id still verifies; a later failing rerun is not consulted"


def _c_nc_ac_15(c):
    """The required set is PINNED to the contract's base commit, not read from live configuration.

    The registry blob at the pinned base names a context the check runs do not satisfy. If the set
    were taken from anywhere present-day, this would pass; pinned, it correctly refuses.
    """
    raw, kw = _landed(registry=b"current_required_contexts:\n  - \"a-context-never-run\"\n")
    st, _ = _acc_state(raw, kw)
    if st != "acceptance_claimed":
        return False, f"NOT DETECTED — the pinned required set was not honoured, got {st!r}"
    # And an ABSENT registry at the pinned base is unavailability, never a relaxed bar.
    raw2, kw2 = _landed(registry=None)
    trees = kw2.pop("trees", None)
    dec2, _ = _run(raw2, _trees=trees, **kw2)
    if dec2.rule != "ST-7":
        return False, f"NOT DETECTED — an absent pinned registry gave {dec2.rule!r}, not 'ST-7'"
    return True, "the pinned set decides, and an unreadable pinned set is ST-7 rather than a pass"


def _c_nc_ac_16(c):
    """An INCOMPLETE check-run page is unavailability, never proof that a run is absent."""
    from . import adapters
    import inspect
    src = inspect.getsource(adapters.MergeFactsPort._collect)
    if "total_count" not in src or "incomplete" not in src:
        return False, "NOT DETECTED — the aggregate is not verified against total_count"
    if "--slurp" not in inspect.getsource(adapters.MergeFactsPort._api):
        return False, ("NOT DETECTED — pagination reads a single JSON document, so page two is "
                       "either a parse error or silently unread")
    return True, "every page is slurped, aggregated, and proven against total_count"


def _c_nc_c10b(c):
    landed = build(extra="| 2026-07-18T11:00:00Z | binding | pr=7 |\n")
    rewritten = build(extra="| 2026-07-18T12:00:00Z | binding | pr=9 |\n")
    return _decides(c, rewritten, main_blob=landed)


def _c_nc_c10c(c):
    return _decides(c, build(cid="CC-2026-07-18-mismatch"))


def _c_nc_c11(c):
    """`AC-5`: obligations(T) ⊆ obligations(T ∪ {t}) over EVERY trait subset, not a sampled few."""
    traits = ("cross-system", "governance", "live")
    subsets = [frozenset(s) for i in range(8)
               for s in [[t for j, t in enumerate(traits) if i >> j & 1]]]
    for a in subsets:
        oa, _ = derive.obligations(None, a)
        for t in traits:
            ob, _ = derive.obligations(None, a | {t})
            if not derive.obligations_are_monotone(oa, ob):
                return False, f"NOT DETECTED — obligations({sorted(a)}) ⊄ obligations(+{t})"
    return True, f"monotone over all {len(subsets)} subsets × {len(traits)} additions"


def _c_nc_c12(c):
    """Two proofs: a subtracting trait is caught, and `risk_tier` has exactly one consumer.

    The second is the one that would rot silently. `risk_tier` selects ONLY the breach response;
    the moment a second site reads it, it starts selecting obligations, which is the subtraction
    ADR-0105 §5.1 exists to forbid. Counting the call sites is crude and it is also exactly right.
    """
    full, _ = derive.obligations(None, frozenset({"governance", "live"}))
    gov, _ = derive.obligations(None, frozenset({"governance"}))
    if not derive.obligations_are_monotone(gov, full):
        return False, "NOT DETECTED — adding `live` removed a `governance` obligation"

    poisoned = dict(derive.TRAIT_OBLIGATIONS)
    poisoned["live"] = ()
    saved = derive.TRAIT_OBLIGATIONS
    try:
        derive.TRAIT_OBLIGATIONS = poisoned                     # inject a subtracting trait table
        weak, _ = derive.obligations(None, frozenset({"governance", "live"}))
    finally:
        derive.TRAIT_OBLIGATIONS = saved
    if len(weak) >= len(full):
        return False, "NOT DETECTED — the injected subtraction did not shrink the obligation set"

    # AST, not `grep`: the module's own comments discuss `risk_tier` at length, and a text count
    # would tally the rationale as if it were a read. What must be pinned is the number of places
    # the VALUE is consumed — exactly two, the `refuse`/`stop` halves of ADR-0105 §10's single
    # unauthorized-file row. A third read means it has started selecting obligations.
    tree = ast.parse((Path(__file__).parent / "decide.py").read_text(encoding="utf-8"))
    sites = sum(1 for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and n.attr == "risk_tier")
    if sites != 2:
        return False, (f"NOT DETECTED — `risk_tier` is read at {sites} site(s) in decide.py; it "
                       f"must select ONLY the breach response (RF-2 refuse / ST-1 stop)")
    return True, f"subtraction caught; `risk_tier` read at exactly {sites} sites (RF-2 / ST-1)"


def _c_nc_c13a(c): return _decides(c, build(), phase="pre-implementation")


def _c_nc_c13b(c):
    return _decides(c, build(decl_mutate=lambda d: d.replace("id:", "1bad:", 1)))


def _c_nc_c13c(c):
    raw = build(adr_sha="blob_recorded_at_approval")
    return _decides(c, raw)


def _c_nc_c13d(c): return _decides(c, build(stops='"ES-1: two laws disagree"'))


def _c_nc_c13e(c):
    ev = "| the probe is safe | a run | blob000 | blob:src/fanops/example.py |\n"
    return _decides(c, build(traits="live", evidence=ev, gate="; execution_gate=granted"))


def _c_nc_c13f(c):
    """ADR-0105 §1 T4's DELETION BOUNDARY: deleting a tracked file is not `live`.

    Git retains the content and the change is revertable — which is exactly the property `live`
    exists to flag the ABSENCE of. Such a deletion may still fire `T1`/`T2`/`T3`/`T5` and is judged
    by those; what it must not do is silently acquire the trait whose breach response is `refuse`.
    """
    _, ctx = _run(build(), changed=("src/fanops/example.py",))
    if "live" in ctx["derived"].traits:
        return False, "NOT DETECTED — a tracked-file change acquired the `live` trait"
    fired = {t.id: t.fired for t in ctx["derived"].triggers}
    if fired.get("T4"):
        return False, "NOT DETECTED — T4 fired without a human declaration"
    return True, "a tracked-file deletion is not `live`; T4 stays human-declared"


def _c_nc_c13g(c):
    return _decides(c, build(stops='"EA-1: the fix needs an adjacent module"'),
                    phase="pre-implementation")


def _c_nc_c13h(c):
    return _decides(c, build(extra="| 2026-07-18T11:00:00Z | refused | reason=unsafe |\n"))


def _c_nc_c13i(c): return _decides(c, build(traits="live"))


def _c_nc_c13j(c):
    return _decides(c, build(traits="live", gate="; execution_gate=granted"),
                    changed=("src/fanops/example.py", "src/fanops/undeclared.py"))


def _c_nc_c13k(c): return _decides(c, build(stops='"ES-2: needs a LAW exception"'))


def _c_nc_c13l(c):
    ev = ("| the same claim | run A | blob000 | tool:a |\n"
          "| the same claim | run B | blob000 | tool:b |\n")
    return _decides(c, build(evidence=ev))


def _c_nc_c13m(c):
    return _decides(c, build(stops='"RF-4: cannot be made falsifiable"',
                             decl_mutate=lambda d: d.replace(
                                 "`python -m tools.contract selftest` exits 0 with all controls "
                                 "detected.", "It should feel better.", 1)),
                    phase="pre-implementation")


def _c_nc_c13n(c):
    return _decides(c, build(traits="governance"))


def _c_nc_c13o(c):
    return _decides(c, build(decl_mutate=lambda d: d.replace(
        "`python -m tools.contract selftest` exits 0 with all controls detected.",
        "It should feel better.", 1)))


def _c_nc_c13p(c):
    return _decides(c, build(), artifacts=FakeArtifacts(fail="modules"))


def _c_nc_c13q(c):
    """`blast_radius` is mandatory once `cross-system` holds — the one trait-conditional field."""
    return _decides(c, build(traits="cross-system"),
                    impact=FakeImpact(classification="BREAKING_CHANGE"))


def _c_nc_c13r(c):
    return _decides(c, build(decl_mutate=lambda d: d.replace("| ADR-0105 |", "| not-an-id |", 1)))


def _c_nc_c14(c):
    """No failure path yields `continue`. Every injected failure is driven through and checked."""
    cases = {
        "unparseable": dict(raw=build(decl_mutate=lambda d: d.replace("id:", "1bad:", 1))),
        "unauthorized": dict(raw=build(), changed=("src/fanops/example.py", "src/fanops/other.py")),
        "modules absent": dict(raw=build(), artifacts=FakeArtifacts(fail="modules")),
        "impact absent": dict(raw=build(), impact=FakeImpact(fail=True)),
        "registry absent": dict(raw=build(), registry=FakeRegistry(fail=True)),
        "diff unenumerable": dict(raw=build(), repo_fail="diff"),
        "no approval": dict(raw=build(approve_digest="sha256:wrong")),
        "authority moved": dict(raw=build(adr_sha="recorded-elsewhere")),
    }
    leaked = []
    for name, kw in cases.items():
        raw = kw.pop("raw")
        decision, _ = _run(raw, **kw)
        if decision.outcome == CONTINUE:
            leaked.append(name)
    if leaked:
        return False, f"NOT DETECTED — these failures produced `continue`: {leaked}"
    return True, f"none of the {len(cases)} failure paths produced `continue`"


def _c_nc_c15(c):
    """Exit 2 is reserved, is produced by no decision, and emits NO `decision` field."""
    import json

    from . import report
    from .__main__ import main
    body = report.untrustworthy("gh unavailable", "injected")
    # A KEY check, not a substring check: the payload's prose deliberately says the word "decision"
    # ("no trustworthy decision was reached"), and a naive `"decision" in json` would read that
    # sentence as a field. What must be absent is the FIELD, at any depth.
    if _has_key(json.loads(report.as_json(body)), "decision"):
        return False, "NOT DETECTED — the exit-2 payload carries a `decision` field"
    # The injected failure prints its exit-2 explanation to stderr BY DESIGN; swallowing it here
    # keeps the control's own report readable without weakening what is being proven — the exit
    # code and the absent `decision` field are the assertions, not the message.
    with contextlib.redirect_stderr(io.StringIO()):
        rc = main(["--quiet", "verify", "docs/contracts/__absent__.md", "--base", "base",
                   "--head", "HEAD"])
    if rc != 2:
        return False, f"NOT DETECTED — an absent contract exited {rc}, expected 2"
    from .model import DECISIONS, EXIT_CLASS
    if any(EXIT_CLASS[d] == 2 for d in DECISIONS):
        return False, "NOT DETECTED — a decision maps to exit 2"
    return True, "exit 2 reserved; no decision maps to it; no `decision` field emitted"


def _has_key(obj, key: str) -> bool:
    if isinstance(obj, dict):
        return key in obj or any(_has_key(v, key) for v in obj.values())
    return isinstance(obj, list) and any(_has_key(v, key) for v in obj)


def _c_nc_c07b(c):
    return _decides(c, build(decl_mutate=lambda d: d.replace("supersedes: []",
                                                             "supersedes: []\nwaives_law: C18", 1)))


def _c_nc_c29(c):
    """A REGRESSION CONTROL for a defect this implementation actually shipped and then fixed.

    `git rev-parse <ref>:<absent-path>` exits 128 AND ECHOES ITS ARGUMENT TO STDOUT. Reading stdout
    without checking the exit code returned the literal string `"<ref>:<path>"` — truthy — so
    `contains()` answered True for a file that does not exist and a contract that had never landed
    derived the state `merged`. Every gate downstream of `merged` would then have been reasoning
    about a merge that had not happened.
    """
    from .adapters import RepoPort
    repo = RepoPort()
    absent = "docs/contracts/__definitely_absent__.md"
    got = repo.blob_sha("origin/main", absent)
    if got is not None:
        return False, f"NOT DETECTED — blob_sha returned {got!r} for an absent path"
    if repo.contains("origin/main", absent):
        return False, "NOT DETECTED — contains() is True for an absent path"
    if repo.resolve("definitely-not-a-ref") is not None:
        return False, "NOT DETECTED — resolve() answered for an unresolvable ref"
    real = repo.blob_sha("HEAD", "tools/contract/model.py")
    if real is None or len(real) != 40:
        return False, f"NOT DETECTED — a REAL path did not resolve to a 40-hex blob id ({real!r})"
    return True, f"absent -> None; real -> {real[:12]}…; unresolvable ref -> None"


def _c_nc_c16(c):
    """`AC-11`: the transform must reproduce the CANONICAL module set from the real repository."""
    art = REPO / ".reports" / "architecture" / "derived" / "modules.json"
    if not art.exists():
        return False, "CONTROL CANNOT RUN — derived/modules.json is absent"
    import json
    data = json.loads(art.read_text(encoding="utf-8"))
    if not derive.totality_holds(data):
        return False, "NOT DETECTED — the canonical partition is not total"
    got = {classify.module_of(f"src/fanops/{p}")
           for p in _rel_py()} - {None}
    want = set(data["modules"])
    if got != want:
        miss, extra = sorted(want - got)[:3], sorted(got - want)[:3]
        return False, f"NOT DETECTED — transform ≠ canonical set (missing {miss}, extra {extra})"
    if classify.module_of("src/fanops/post/__init__.py") != "fanops.post":
        return False, "NOT DETECTED — the `__init__` special case is wrong"
    return True, f"{len(got)} modules reproduced exactly; `__init__` maps to the package"


def _rel_py():
    src = REPO / "src" / "fanops"
    return sorted(str(p.relative_to(src)) for p in src.rglob("*.py")
                  if "__pycache__" not in p.parts)


def _c_nc_c17(c):
    """`AC-12`: the closure must equal an INDEPENDENT BFS written here, not the same code twice."""
    import json
    art = REPO / ".reports" / "architecture" / "derived" / "dependencies.json"
    if not art.exists():
        return False, "CONTROL CANNOT RUN — derived/dependencies.json is absent"
    deps = json.loads(art.read_text(encoding="utf-8"))
    edges = deps["edges"]
    seed = "fanops.models"
    if seed not in edges:
        seed = sorted(edges)[0]

    frontier, independent = {seed}, set()          # an independent fixed-point, not the same BFS
    while frontier:
        nxt = {s for s, k in edges.items() if set(k.get("compile", ())) & frontier}
        nxt -= independent | {seed}
        independent |= nxt
        frontier = nxt
    got = set(derive.blast_radius([seed], deps))
    if got != independent:
        return False, (f"NOT DETECTED — closure ≠ independent BFS "
                       f"({len(got)} vs {len(independent)} modules)")
    if any(seed in edges.get(m, {}).get("lazy", ()) for m in got if m not in independent):
        return False, "NOT DETECTED — a lazy edge leaked into the closure"
    return True, f"{len(got)} reverse-reachable modules from {seed}, matching an independent BFS"


def _c_nc_c18(c):
    return _decides(c, build(), changed=("src/fanops/example.py", "src/fanops/undeclared.py"))


def _c_nc_c19(c):
    gen = {"docs/ARCHITECTURE_GOVERNANCE.md"}
    return _decides(c, build(), changed=("src/fanops/example.py", "docs/ARCHITECTURE_GOVERNANCE.md"),
                    artifacts=FakeArtifacts(generated=gen, stale=["ARCHITECTURE_GOVERNANCE.md"]))


def _c_nc_c20a(c):
    """Operator decision D-2: the replacement dimension must ARM on an enum delta, and only then."""
    try:
        from tools.arch import verifymap
    except Exception as exc:
        return False, f"CONTROL CANNOT RUN — {type(exc).__name__}: {exc}"
    triggers = {r.trigger for r in verifymap.REQUIREMENTS}
    if "changed_enums" not in triggers:
        return False, "NOT DETECTED — `changed_enums` is not a requirement"
    if {"changed_state_machines", "changed_rollback"} & triggers:
        return False, "NOT DETECTED — a retired dead predicate is still declared"
    armed = {r.trigger for r in verifymap.required_for(
        {"architecture": {"changed_enums": ["PostState: +holding"]}, "implementation": {}})}
    if "changed_enums" not in armed:
        return False, "NOT DETECTED — an enum delta did not arm the requirement"
    idle = {r.trigger for r in verifymap.required_for(
        {"architecture": {"changed_enums": []}, "implementation": {}})}
    if "changed_enums" in idle:
        return False, "NOT DETECTED — the requirement armed on an unrelated diff"
    return True, "arms on an enum delta, idle otherwise; both dead predicates retired"


def _c_nc_c20b(c):
    return _decides(c, build(decl_mutate=lambda d: d.replace(
        "### rollback\n\n`git revert` the single squash commit; nothing observes the package.\n\n",
        "", 1)))


def _c_nc_c20c(c):
    """The rollback obligation's second home: ADR-0105 §5.1's `live` row → rollback rehearsal."""
    obs, _ = derive.obligations(None, frozenset({"live"}))
    if "OB-ROLLBACK-REHEARSAL" not in {o for o, _ in obs}:
        return False, "NOT DETECTED — a `live` change carries no rollback-rehearsal obligation"
    contained, _ = derive.obligations(None, frozenset())
    if "OB-ROLLBACK-REHEARSAL" in {o for o, _ in contained}:
        return False, "NOT DETECTED — the obligation fires without the trait"
    return True, "`live` ⇒ rollback rehearsal; absent without the trait"


def _c_nc_c21(c):
    """`AC-2` + `AC-16`: no rule without a control, and no `verifymap` predicate that cannot arm."""
    covered = {x.expect_rule for x in CONTROLS if x.expect_rule}
    uncovered = sorted((set(RULE_IDS) | {"OK"}) - covered)
    if uncovered:
        return False, f"NOT DETECTED — rule(s) with no control: {uncovered}"
    try:
        from tools.arch import impact, verifymap
    except Exception as exc:
        return False, f"CONTROL CANNOT RUN — {type(exc).__name__}: {exc}"
    src = (Path(impact.__file__)).read_text(encoding="utf-8")
    dead = [r.trigger for r in verifymap.REQUIREMENTS if src.count(f'"{r.trigger}"') < 2]
    if dead:
        return False, (f"NOT DETECTED — requirement(s) whose dimension impact.py never writes: "
                       f"{dead} (initialized once, never populated — ADR-0105 G4)")
    return True, f"{len(covered)} rule(s) covered; no dead verifymap predicate"


def _c_nc_c22(c):
    _, ctx = _run(build(), changed=("tools/contract/__main__.py",))
    fired = {t.id: t.fired for t in ctx["derived"].triggers}
    if not fired.get("T3"):
        return False, "NOT DETECTED — a `tools/contract/**` change did not fire T3"
    return True, "T3 fires on `tools/contract/**` (the amendment is live)"


def _c_nc_c23(c):
    return _decides(c, build(declare=("tools/newgov/__init__.py",)),
                    changed=("src/fanops/example.py", "tools/newgov/__init__.py"))


def _c_nc_c24(c):
    """THE POSITIVE CONTROL. ADR-0105 §1: the uncontracted path is the default and MUST STAY FREE."""
    _, ctx = _run(build(), changed=("src/fanops/newfeature/__init__.py",))
    fired = {t.id: t.fired for t in ctx["derived"].triggers}
    if fired.get("T3"):
        return False, "NOT DETECTED — an ordinary new package fired T3; the default path is no "\
                      "longer free"
    # WAS: `{d.code for d in ctx["derived"].triggers if False} or set()` — `if False` made the set
    # unconditionally empty, so the guard beneath it was UNREACHABLE. It read as a check and asserted
    # nothing: the `IMPL-007` failure class this package exists to prevent, inside the control file.
    # This calls the detector directly, so it can actually fail.
    findings = classify.governance_surface_findings(["src/fanops/newfeature/__init__.py"],
                                                    base_has=lambda p: False)
    if findings:
        return False, (f"NOT DETECTED — the detector fired on an ordinary package: "
                       f"{[f.code for f in findings]}")
    return True, "an ordinary `src/fanops/` package fires neither T3 nor GS-1"


def _c_nc_c30(c):
    """`GS-2`, which shipped with NO control at all and therefore hid a live false positive.

    The injection is ADR-0105 §1's named false negative in its exact shape: *"a new VALIDATOR added
    under a path not enumerated"* — a single file at `tools/newvalidator.py`, which creates no
    package and so trips no `GS-1` `__init__.py` signal.

    The second half is what keeps the corrected rule honest: a governance contract that declares
    `tests/**` and its own `docs/contracts/` file must NOT be flagged. That is the false positive
    the audit found, and ADR-0105 §3.6 forbids it outright.
    """
    ok, detail = _decides(c, build(traits="governance",
                                   declare=("tools/contract/decide.py", "tools/newvalidator.py")),
                          changed=("src/fanops/example.py", "tools/contract/decide.py"))
    if not ok:
        return ok, detail
    clean = classify.governance_surface_findings(
        [], base_has=lambda p: True,
        declared_governance_paths=("tests/test_contract_compiler.py",
                                   "tests/fixtures/contracts/valid_full.md",
                                   "docs/contracts/CC-2026-07-18-change-contract-compiler.md",
                                   "tools/arch/impact.py", "tools/contract/decide.py"))
    if clean:
        return False, (f"NOT DETECTED — GS-2 fired on paths ADR-0105 §3.6 and the design §19.2 put "
                       f"OUTSIDE T3: {[f.path for f in clean]}")
    return True, f"{detail}; and no false positive on tests/** or docs/contracts/**"


def _c_nc_c31(c):
    """THE METRIC HOLE ITSELF. `GS-2` hid because coverage was measured over RULE IDS only.

    `AC-2` asked "does every rule id have a control?". `ST-8` answered yes, because `NC-C23` names
    it — while `GS-2`, the OTHER half of `ST-8`'s predicate, had none. A rule can read several
    diagnostic codes, so rule-level coverage is strictly weaker than it appears.

    This reads the codes the decision table ACTUALLY CONSUMES straight out of `decide.py`'s AST —
    both the inline set literals and the two named frozensets — so a future rule that reads an
    untested code goes red here instead of shipping green.
    """
    src = (Path(__file__).parent / "decide.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    named: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            if isinstance(node.value, (ast.Set, ast.List, ast.Tuple)):
                named[node.targets[0].id] = {e.value for e in node.value.elts
                                             if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    read: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Lambda):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                read.add(sub.value)
            elif isinstance(sub, ast.Name) and sub.id in named:
                read |= named[sub.id]
    codes = {s for s in read if _CODE_SHAPE.match(s)}
    covered = ({x.expect_code for x in CONTROLS if x.expect_code}
               | {x.expect_rule for x in CONTROLS if x.expect_rule})
    missing = sorted(codes - covered)
    if missing:
        return False, (f"NOT DETECTED — {len(missing)} code(s) read by a decision rule with no "
                       f"control: {missing}")
    return True, f"all {len(codes)} codes consumed by the decision table are covered by a control"


def _c_nc_c25(c):
    """`merged` NEVER implies `accepted` — now across ALL THREE merged states (§4.3a).

    "On main" used to be one state. Splitting it into `merged`, `merged_unverified` and
    `merged_unauthorized` multiplied the ways this implication could be reintroduced, so the control
    checks every one rather than the single case that existed when it was written.
    """
    from . import lifecycle
    from .model import Gates
    d = parse(build())
    cases = {
        "merged_unauthorized": Gates(),                                   # no claim at all
        "merged": Gates(merge_authorization="satisfied"),                 # a claim that verifies
    }
    for expect, g in cases.items():
        st = lifecycle.state(d, d.events, g, merged=True, ci_green=False, proposal_bound=False,
                             pr_open=False, mandatory_ok=True)
        if st != expect:
            return False, f"NOT DETECTED — a merged contract derived {st!r}, expected {expect!r}"
        if st == "accepted":
            return False, "NOT DETECTED — merge implied acceptance"
    accepted = build(extra="| 2026-07-18T12:00:00Z | merged | merge_sha=abc |\n"
                           "| 2026-07-18T13:00:00Z | accepted | merge_sha=abc |\n")
    diags = lifecycle.validate_events(parse(accepted).events, main_blob=None, decl_bytes=b"",
                                      life_bytes=b"")
    if "ACCEPT-INCOMPLETE" not in {x.code for x in diags}:
        return False, "NOT DETECTED — an `accepted` event missing five of six values passed"
    return True, "merged ⇏ accepted in every merged state; an incomplete acceptance is MALFORMED"


def _c_nc_c26(c):
    """Every corrupted variant must fail with ITS OWN rule, not merely fail somehow.

    Failing for the wrong reason is how a checker earns a reputation it has not lived up to: the
    author fixes what the message named, the real defect survives, and the gate reports green.
    """
    wrong = []
    for ctl in CONTROLS:
        if not ctl.expect_rule or ctl.expect_rule == "OK" or ctl.id == c.id:
            continue
        try:
            ok, detail = detect(ctl)
        except Exception as exc:
            wrong.append(f"{ctl.id} errored ({type(exc).__name__})")
            continue
        if not ok:
            wrong.append(f"{ctl.id}: {detail[:48]}")
    if wrong:
        return False, f"NOT DETECTED — {len(wrong)} variant(s) failed with the wrong rule: {wrong[:3]}"
    return True, "every corrupted variant fails with its own named rule"


def _c_nc_c27(c):
    """The pin. `T3_PATTERNS` here must equal the list in the ADR body, or one of them is a lie."""
    adr = REPO / ADR_PATH
    if not adr.exists():
        return False, f"CONTROL CANNOT RUN — {ADR_PATH} is absent"
    declared = classify.adr_t3_patterns(adr.read_text(encoding="utf-8"))
    if not declared:
        return False, "NOT DETECTED — the T3 predicate could not be read out of the ADR"
    if set(declared) != set(classify.T3_PATTERNS):
        miss = sorted(set(declared) - set(classify.T3_PATTERNS))
        extra = sorted(set(classify.T3_PATTERNS) - set(declared))
        return False, f"NOT DETECTED — pin drifted (ADR-only {miss}, code-only {extra})"
    if "tools/contract/**" not in declared:
        return False, "NOT DETECTED — the ADR does not list `tools/contract/**`"
    live = classify.adr_body_digest(adr.read_bytes())
    if live != classify.ADR_0105_DIGEST:
        return False, (f"NOT DETECTED — the ADR body digest moved: pinned "
                       f"{classify.ADR_0105_DIGEST[:26]}…, live {live[:26]}…. The approval binds to "
                       f"the body; a moved digest means it must be re-approved.")
    front = adr.read_text(encoding="utf-8").split("\n---\n", 1)[0]
    if classify.ADR_0105_DIGEST not in front:
        return False, "NOT DETECTED — `approved_digest` in the front matter is not the pinned value"
    return True, (f"{len(declared)} T3 patterns pinned incl. `tools/contract/**`; body digest "
                  f"matches the front matter")


def _c_nc_c28(c):
    """`AC-24`: the dependency direction is one-way. An AST scan, not a convention."""
    offenders = []
    for pkg in ("arch", "ci"):
        root = REPO / "tools" / pkg
        if not root.exists():
            continue
        for f in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                return False, f"CONTROL CANNOT RUN — {f} does not parse: {exc}"
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                if any(m == "tools.contract" or m.startswith("tools.contract.") for m in mods):
                    offenders.append(f"{f.relative_to(REPO)}:{node.lineno}")
    if offenders:
        return False, f"NOT DETECTED — sibling(s) import tools.contract: {offenders}"
    return True, "neither tools/arch nor tools/ci imports tools.contract"


# ── ADR-0105 §1a · the pre-implementation classification correction ─────────────────────────
#
# EVERY CONTROL HERE PAIRS WITH ITS OPPOSITE, because the defect this closes was itself a rule that
# only ever fired one way. `NC-P1` (pre reads intent) is only meaningful beside `NC-P2` (head does
# not); `NC-P10` (pre tolerates over-declaration) is only safe beside `NC-P11` (head still catches
# it, same contract, one phase later). A relaxation proven without its containing control is how a
# gate becomes decorative.
class _TwoSubsystems(FakeArtifacts):
    """`fanops.other` in a SECOND subsystem — otherwise `T1` cannot fire in any control at all."""

    def modules(self):
        m = dict(super().modules())
        m["modules"] = [*m["modules"], "fanops.other"]
        m["subsystem_of"] = {**m["subsystem_of"], "fanops.other": "S16_studio"}
        return m

    def dependencies(self):
        d = dict(super().dependencies())
        d["edges"] = {**d["edges"],
                      "fanops.other": {"compile": ["fanops"], "lazy": [], "optional": [],
                                       "typing": []}}
        return d


def _with_blast(d: str) -> str:
    """`cross-system` makes `blast_radius` mandatory (`CL-1`), which would mask `CL-2` under test."""
    return d.replace("blast_radius: []", "blast_radius: [fanops]", 1)


def _cross_intent(traits="cross-system", extra_path="src/fanops/other.py"):
    """Intent spans two subsystems. The DIFF will carry only the contract — that is the point."""
    return build(traits=traits, declare=(extra_path,), decl_mutate=_with_blast)


def _pre(raw, **kw):
    """`pre`, with a contract-only diff — the state a contract is actually written in."""
    kw.setdefault("artifacts", _TwoSubsystems())
    return _run(raw, changed=(CONTRACT_PATH,), phase="pre-implementation", **kw)


def _fired(ctx) -> dict:
    return {t.id: t.fired for t in ctx["derived"].triggers}


def _c_nc_p1(c):
    decision, ctx = _pre(_cross_intent())
    if not _fired(ctx).get("T1"):
        return False, ("NOT DETECTED — `T1` did not fire; `pre` classified the contract-only diff "
                       "instead of `expected_surfaces`")
    if decision.rule != "OK":
        return False, f"NOT DETECTED — expected OK at `pre`, got {decision.rule}"
    return True, "`T1` fired from `expected_surfaces` on a contract-only diff; `pre` → continue"


def _c_nc_p2(c):
    decision, ctx = _run(_cross_intent(), changed=("src/fanops/example.py",), phase="at-head",
                         artifacts=_TwoSubsystems())
    if _fired(ctx).get("T1"):
        return False, "NOT DETECTED — `T1` fired at `at-head` from intent; the diff rules there"
    if decision.rule != "CL-2":
        return False, f"NOT DETECTED — expected CL-2 at `at-head`, got {decision.rule}"
    return True, "`at-head` classified the diff (1 subsystem) and caught the mismatch"


def _c_nc_p3(c):
    return _decides(c, _cross_intent(traits=""), changed=(CONTRACT_PATH,),
                    phase="pre-implementation", artifacts=_TwoSubsystems())


def _c_nc_p4(c):
    decision, ctx = _pre(_cross_intent(extra_path="src/fanops/data.json"))
    if decision.rule != "ST-7":
        return False, (f"NOT DETECTED — a `src/` path that is no module gave {decision.rule}, not "
                       f"ST-7")
    return True, "a non-module path under `src/` is UNMAPPABLE and fails closed"


def _c_nc_p5(c):
    decision, ctx = _pre(_cross_intent(extra_path="src/fanops/orphan.py"))
    if decision.rule != "ST-7":
        return False, f"NOT DETECTED — an unowned module gave {decision.rule}, not ST-7"
    return True, "a module no subsystem owns is UNMAPPABLE and fails closed"


def _c_nc_p6(c):
    decision, ctx = _pre(build())
    if _fired(ctx).get("T1"):
        return False, "NOT DETECTED — `T1` fired on a single-subsystem intent set; it was BROADENED"
    if ctx["derived"].traits:
        return False, f"NOT DETECTED — one subsystem derived traits {sorted(ctx['derived'].traits)}"
    if decision.rule != "OK":
        return False, f"NOT DETECTED — a contained intent set gave {decision.rule}, not OK"
    return True, "one subsystem → `T1` silent, traits contained; §1's accepted false negative stands"


def _c_nc_p7(c):
    from .__main__ import cmd_preflight, main
    src = inspect.getsource(cmd_preflight)
    # `"open" + "("` is CONCATENATED, not written out. `test_no_verb_writes_into_the_repository`
    # scans every file in this package for the literal, so spelling it here would make this control
    # trip the repo-wide one — a control that reddens the check it agrees with.
    for verb in ("write_text", "write_bytes", "mkdir", "open" + "(", "unlink", "touch"):
        if verb in src:
            return False, f"NOT DETECTED — `preflight` contains a write: {verb}"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["preflight", "src/fanops/framing.py", "--json"])
    data = _json.loads(buf.getvalue())
    if rc != 0 or "verdict" not in data:
        return False, f"NOT DETECTED — contractless preflight exited {rc} / {sorted(data)[:4]}"
    return True, "no contract, no diff, no write verb in the entrypoint; structured JSON returned"


def _c_nc_p8(c):
    t = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    if "tools.contract preflight" not in t:
        return False, ("NOT DETECTED — the front door never names `python -m tools.contract "
                       "preflight`, so a fresh agent cannot reach it")
    return True, "AGENTS.md names the preflight command directly"


def _c_nc_p9(c):
    from .__main__ import main
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main(["preflight", "src/fanops/framing.py", "--json"])
    data = _json.loads(buf.getvalue())
    if data.get("verdict") != "UNDETERMINED":
        return False, (f"NOT DETECTED — a path set that fires nothing returned "
                       f"{data.get('verdict')!r}; only REQUIRED or UNDETERMINED are answerable")
    missing = [t for t in ("T2", "T4", "T6") if t not in (data.get("unevaluable") or {})]
    if missing:
        return False, f"NOT DETECTED — {missing} not reported as unevaluable"
    return True, "path-only silence is UNDETERMINED, never NOT REQUIRED; T2/T4/T6 named unevaluable"


def _c_nc_p10(c):
    decision, ctx = _pre(build(traits="cross-system", decl_mutate=_with_blast))
    if decision.rule == "CL-2":
        return False, ("NOT DETECTED — a CONSERVATIVE declaration was rejected at `pre` for a trait "
                       "`T2` cannot yet evaluate")
    if decision.rule != "OK":
        return False, f"NOT DETECTED — expected OK, got {decision.rule}"
    return True, "declaring more than intent proves is allowed at `pre` while `T2` is unevaluable"


def _c_nc_p11(c):
    # THE INTENT MUST SPAN TWO SUBSYSTEMS WHILE THE DIFF SPANS ONE. With a one-subsystem intent the
    # two evidence sources agree, `CL-2` fires either way, and the control proves nothing about
    # WHICH set `at-head` read — it was written that way first and went DETECTED against a defect
    # that was present.
    decision, ctx = _run(_cross_intent(), changed=("src/fanops/example.py",), phase="at-head",
                         artifacts=_TwoSubsystems())
    if decision.rule != "CL-2":
        return False, (f"NOT DETECTED — the over-declaration `pre` tolerated survived to `at-head` "
                       f"as {decision.rule}")
    return True, "the same contract `NC-P10` allows at `pre` is caught at `at-head` — no leak"


def _c_nc_p12(c):
    for bad, label in (("src/fanops/**", "wildcard"), ("/etc/passwd", "malformed")):
        decision, _ = _pre(_cross_intent(extra_path=bad))
        if decision.rule != "ST-7":
            return False, f"NOT DETECTED — a {label} intended path gave {decision.rule}, not ST-7"
    return True, "a wildcarded and a malformed `expected_surfaces` path each fail closed"


def _c_nc_p13(c):
    decision, ctx = _pre(_cross_intent(traits="", extra_path="src/fanops/orphan.py"))
    if decision.rule == "OK" or not ctx["derived"].unverifiable:
        return False, ("NOT DETECTED — an intended path absent from the ownership map vanished and "
                       "the change read as `contained`")
    if decision.rule != "ST-7":
        return False, f"NOT DETECTED — expected ST-7, got {decision.rule}"
    return True, "an unmapped intended path cannot disappear into `contained`"


def _t2(ctx):
    return next(t for t in ctx["derived"].triggers if t.id == "T2")


def _c_nc_p14(c):
    decision, ctx = _pre(_cross_intent(), impact=FakeImpact(fail=True))
    if decision.rule == "ST-7":
        return False, ("NOT DETECTED — an unavailable impact port halted `pre`, the phase that does "
                       "not use it")
    if decision.rule != "OK":
        return False, f"NOT DETECTED — expected OK at `pre`, got {decision.rule}"
    if "not evaluated" not in _t2(ctx).reason:
        return False, f"NOT DETECTED — `T2` reads {_t2(ctx).reason!r}, not `not evaluated`"
    if any("impact" in u for u in ctx["derived"].unverifiable):
        return False, f"NOT DETECTED — impact reached `unverifiable`: {ctx['derived'].unverifiable}"
    return True, "`pre` survives a raising impact port and records `T2` as not evaluated"


def _c_nc_p15(c):
    decision, ctx = _run(_cross_intent(), changed=("src/fanops/example.py",), phase="at-head",
                         artifacts=_TwoSubsystems(), impact=FakeImpact(fail=True))
    if decision.rule != "ST-7":
        return False, (f"NOT DETECTED — an unavailable impact port at `at-head` gave "
                       f"{decision.rule}, not ST-7; the fail-closed read was weakened")
    return True, "the SAME unavailable input still halts `at-head` — `pre` was narrowed, not weakened"


def _c_nc_p16(c):
    decision, ctx = _run(build(traits="cross-system", decl_mutate=_with_blast),
                         changed=("src/fanops/example.py",), phase="at-head",
                         impact=FakeImpact(classification="MIGRATION_REQUIRED"))
    if not _fired(ctx).get("T2"):
        return False, "NOT DETECTED — `T2` did not fire on a MIGRATION_REQUIRED head diff"
    if "cross-system" not in ctx["derived"].traits:
        return False, f"NOT DETECTED — `T2` fired but traits are {sorted(ctx['derived'].traits)}"
    return True, "`T2` still derives `cross-system` from a real head diff, with `T1` silent"


# ── ADR-0106 · declaration-only contracts ───────────────────────────────────────────────────
def _c_nc_d1(c):
    """The contract PARSES — and is still refused, because nothing has approved it."""
    decision, ctx = _run(build_decl_only(approve=False))
    if any(d.code == "NO-BOUNDARY" for d in decision.diagnostics):
        return False, "NOT DETECTED — the declaration-only shape still emits `NO-BOUNDARY`"
    if not ctx["decl"].value("objective"):
        return False, "NOT DETECTED — the file was admitted but its fields did not parse"
    if decision.rule != "ST-3":
        return False, f"NOT DETECTED — rule {decision.rule} fired, expected ST-3"
    return True, f"parsed as declaration-only, then ST-3 → {decision.outcome}"


def _c_nc_d2(c):
    return _decides(c, build_decl_only(approve_digest="sha256:" + "0" * 64))


def _c_nc_d3(c):
    """Both routes at once. The lifecycle one is read, so the front-matter pair decides nothing."""
    raw = build().replace(b"supersedes: []\n",
                          b"supersedes: []\napproved_digest: sha256:" + b"0" * 64
                          + b"\napproval_token: APPROVE\n", 1)
    return _decides(c, raw)


def _c_nc_d4(c):
    body = build_decl_only(approve=False)
    return _decides(c, body.replace(b"supersedes: []\n",
                                    b"supersedes: []\napproved_digest: sha256:" + b"0" * 64 + b"\n",
                                    1))


def _c_nc_d5(c):
    """THE LOAD-BEARING PROPERTY. If recording the approval moved `D`, `ST-3` could never clear.

    Asserted in the direction that can fail silently: the digest BEFORE the operator answers must
    equal the digest AFTER the agent writes the answer down, including the live-execution gate,
    because an agent writes all three lines into a file whose digest was computed without them.
    """
    unapproved = parse(build_decl_only(approve=False)).digest
    approved = parse(build_decl_only()).digest
    with_gate = parse(build_decl_only(gate="RUN IT")).digest
    if unapproved != approved:
        return False, f"NOT DETECTED — writing the approval moved D: {unapproved[:20]} -> {approved[:20]}"
    if unapproved != with_gate:
        return False, f"NOT DETECTED — writing `execution_gate` moved D: {with_gate[:20]}"
    if parse(build_decl_only(trailer="\nan added byte\n")).digest == unapproved:
        return False, "NOT DETECTED — a change to the declaration ITSELF did not move D"
    return True, f"D is stable across recording the approval ({unapproved[:20]}…) and moves on an edit"


def _c_nc_d6(c):
    landed = build_decl_only()
    return _decides(c, landed.replace(b"Prove the compiler", b"Prove the COMPILER", 1),
                    main_blob=landed)


def _c_nc_d7(c):
    decision, _ = _run(build_decl_only(traits="live", gate="RUN IT"))
    if decision.rule == "RF-1":
        return False, "the control is inverted — RF-1 fired WITH an execution gate recorded"
    return _decides(c, build_decl_only(traits="live"))


def _c_nc_d8(c):
    """Every contract already on `main` must keep the digest its approval names. VERIFICATION ITEM.

    Reads the landed files and recomputes `D` through `digest_range`, then compares against the
    digest each contract's own `approved` event records. A change to the digest rule that reached
    the legacy shape would silently un-approve six landed authorizations.
    """
    from .parse import digest_range
    contracts = sorted((REPO / "docs" / "contracts").glob("CC-*.md"))
    if not contracts:
        return False, "CONTROL ERRORED: no landed contracts found to check"
    broken = []
    for p in contracts:
        raw = p.read_bytes()
        d = parse(raw)
        # THE LAST `approved` EVENT, because that is the one `lifecycle.gates` reads. A contract may
        # be re-approved after an amendment; comparing against the first would check a digest the
        # gate itself abandoned.
        recorded = ([e.get("digest") for e in d.events if e.kind == "approved"] or [""])[-1]
        if not recorded:
            continue
        if digest(digest_range(raw)) != recorded:
            broken.append(f"{p.name}: computes {digest(digest_range(raw))[:20]}…, "
                          f"approval names {recorded[:20]}…")
    if broken:
        return False, "NOT DETECTED — the legacy digest moved:\n             " + \
                      "\n             ".join(broken)
    return True, f"all {len(contracts)} landed contract(s) still compute the digest their approval names"


def _c_nc_d9(c):
    """The scope check is INDEPENDENT of the lifecycle and must survive its removal."""
    return _decides(c, build_decl_only(), changed=("src/fanops/example.py", "src/fanops/rogue.py"))


# ── the harness ─────────────────────────────────────────────────────────────────────────────
def run(verbose: bool = True) -> int:
    results = []
    for c in CONTROLS:
        try:
            ok, detail = detect(c)
        except SystemExit as exc:
            # A control that calls the CLI can hit argparse, which exits the PROCESS. Letting that
            # through would take the whole suite down and report nothing — the run would look like
            # a tooling failure rather than one broken control.
            ok, detail = False, f"CONTROL ERRORED: SystemExit({exc.code})"
        except Exception as exc:                  # a control that cannot run proves nothing
            ok, detail = False, f"CONTROL ERRORED: {type(exc).__name__}: {exc}"
        results.append((c, ok, detail))

    passed = sum(1 for _, ok, _ in results if ok)
    if verbose:
        print("negative controls — does each rule detect the defect it claims to?\n")
        w = max(len(c.defect) for c in CONTROLS)
        for c, ok, detail in results:
            mark = "\x1b[32mDETECTED\x1b[0m" if ok else "\x1b[31mMISSED  \x1b[0m"
            tgt = c.expect_rule or c.expect_code or c.layer
            print(f"  {c.id:<10} {mark}  {c.defect:<{w}}  -> {tgt}")
            print(f"             {detail}")
        print(f"\n  {passed}/{len(results)} injected defects detected.")
        if passed != len(results):
            print("\n  A MISSED control means the rule it names is DECORATIVE: claimed in the "
                  "\n  table but not actually firing. That is worse than having no rule, because "
                  "\n  it manufactures confidence.")
    return 0 if passed == len(results) else 1


_ = (CLARIFICATION, CONTINUE, ESCALATE, EXPANDED, REFUSE, STOP)   # outcome names, for readers
