"""Types, the closed field set, and the per-field type table. THIS MODULE IMPORTS NOTHING.

That is not tidiness — it is what makes `decide.py` provably pure. Every value the decision reads is
a frozen dataclass defined here, built upstream by code that may do I/O. If this module could import
an adapter, a port, or `tools.arch`, a decision could acquire a hidden input and the determinism
proof (`AC-3`) would be asserting something weaker than it claims.

The type table is the ONLY place a string becomes an int, a date or an enum (ADR-0105 §6.3 as
resolved by operator decision D-1). The parser performs no coercion whatsoever, so implicit typing —
`true` becoming a bool, `2026-07-18` becoming a date, `no` becoming False — is impossible by
construction rather than by discipline. `NC-C06` injects twelve such values and asserts each parses
to its literal text.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── the closed field set (ADR-0105 §3.1: 18 fields, plus `supersedes` from §6) ──────────────
#
# Three location kinds, because the fields genuinely differ in shape and forcing one shape on all
# three would distort them. Prose fields are rationale — `objective`, `success_condition` and
# `rollback` are paragraphs, and a one-line scalar would truncate the thing being approved. Table
# fields are records with fixed columns. Front-matter fields are scalars and flat lists.

FRONTMATTER_FIELDS = ("id", "traits", "authorized_actions", "incidental_allowlist",
                      "blast_radius", "invariants", "stop_conditions", "supersedes")
PROSE_FIELDS = ("objective", "success_condition", "rollback")
TABLE_FIELDS = ("authority", "owners", "allowed_scope", "prohibited_scope", "expected_surfaces",
                "coupling", "reusable_evidence", "verification")

ALL_FIELDS = FRONTMATTER_FIELDS + PROSE_FIELDS + TABLE_FIELDS      # 8 + 3 + 8 = 19 slots

# Unconditionally mandatory (ADR-0105 §3.1 "Required" column == mandatory).
MANDATORY_FIELDS = ("id", "objective", "success_condition", "traits", "authority", "owners",
                    "allowed_scope", "prohibited_scope", "authorized_actions", "expected_surfaces",
                    "invariants", "verification", "rollback")

# Mandatory only when a trait is present. `blast_radius` is the ONE field for which ADR-0105 states
# the condition ("if `cross-system`"). `coupling` is marked "conditional" with NO stated condition,
# so it is treated as optional: inventing a condition would add a requirement the approved model
# does not contain, and a contract would then be rejected for violating a rule nobody approved.
TRAIT_CONDITIONAL_FIELDS = {"blast_radius": "cross-system"}

OPTIONAL_FIELDS = ("incidental_allowlist", "reusable_evidence", "stop_conditions", "coupling",
                   "supersedes")

# Mandatory AND legitimately empty. Exactly one field qualifies, and the ADR says so outright:
# §5.1 — *"`contained` is not a trait. It is the derived label for the empty trait set … A contract
# may therefore exist with an empty trait set."* Treating `traits: []` as a missing value would make
# the contained case — the one the ADR most wants to stay cheap — impossible to express.
EMPTY_ALLOWED_FIELDS = ("traits",)

# Fixed columns per table field (ADR-0105 §3.1, §7, §8, §9). A missing, extra or reordered column is
# MALFORMED with the expected header echoed — never silently positional.
TABLE_COLUMNS = {
    "authority": ("id", "source_file", "blob_sha"),
    "owners": ("subsystem_id", "why_touched"),
    "allowed_scope": ("glob", "why", "basis"),
    "prohibited_scope": ("glob", "why"),
    "expected_surfaces": ("path", "kind", "why"),
    "coupling": ("what", "must_move_with", "why"),
    "reusable_evidence": ("claim", "proven_by", "proven_at", "binding"),
    "verification": ("obligation_id", "control_or_requirement", "distinct_boundary"),
}

# Per-field types. `str` = a scalar; `list` = a list of scalars; `table` = a list of row dicts;
# `prose` = free text. NOTHING converts to bool, int or date, because no field is one of those.
FIELD_TYPES = {
    **{f: "list" for f in ("traits", "authorized_actions", "incidental_allowlist", "blast_radius",
                           "invariants", "stop_conditions", "supersedes")},
    "id": "str",
    **{f: "prose" for f in PROSE_FIELDS},
    **{f: "table" for f in TABLE_FIELDS},
}

TRAITS = ("cross-system", "governance", "live")
ACTIONS = ("design", "implement", "merge", "apply-live", "accept")
SURFACE_KINDS = ("NEW", "MODIFIED", "DELETED")
SCOPE_BASES = ("declared", "inferred")

# ── lifecycle (ADR-0105 §4.2 — eleven event kinds) ──────────────────────────────────────────
EVENT_KINDS = ("created", "approved", "binding", "implementation_started", "head_proposed",
               "merge_approved", "merged", "accepted", "refused", "superseded", "abandoned")
TERMINAL_EVENTS = ("refused", "superseded", "abandoned")

# The five values an `accepted` event must persist (operator decision D-3). Acceptance is a separate
# decision from merge; recording it with any of these missing would produce an acceptance nobody can
# audit, which is the same as no acceptance at all.
ACCEPTANCE_VALUES = ("merge_sha", "decision", "evidence", "date", "operator")

# The values a PARENT-BOUND event must persist (ADR-0105 §4.1, amended). `parent_sha` is the commit
# the event is appended ONTO, never the commit that contains it: a record cannot name the commit
# whose hash is computed over the record. Naming the parent is the same fact, stated in the only
# direction that can be written down.
PARENT_BOUND_EVENTS = ("merge_approved", "head_proposed")
PARENT_BOUND_VALUES = ("parent_sha",)

# ── decisions (ADR-0105 §10) ────────────────────────────────────────────────────────────────
CONTINUE = "continue"
CLARIFICATION = "clarification_required"
EXPANDED = "expanded_authorization_required"
STOP = "stop"
ESCALATE = "escalate"
REFUSE = "refuse"

DECISIONS = (CONTINUE, CLARIFICATION, EXPANDED, STOP, ESCALATE, REFUSE)

# Exit classes (operator decision D-8). Three-valued and decisive. The exact decision travels in the
# structured payload's `decision` field; the exit class never replaces it, and exit 2 is reserved for
# "no trustworthy decision was reached" and is used by no decision at all.
EXIT_CONTINUE, EXIT_DECIDED_NO, EXIT_UNTRUSTWORTHY = 0, 1, 2

EXIT_CLASS = {CONTINUE: EXIT_CONTINUE, **{d: EXIT_DECIDED_NO for d in DECISIONS if d != CONTINUE}}

# ── diagnostics ─────────────────────────────────────────────────────────────────────────────
MISSING, MALFORMED, UNSUPPORTED, UNKNOWN, SEMANTIC = ("MISSING", "MALFORMED", "UNSUPPORTED",
                                                      "UNKNOWN", "SEMANTIC")


@dataclass(frozen=True)
class Diagnostic:
    """One located, named problem. Emitted in source order, so output is deterministic."""
    kind: str                    # MISSING | MALFORMED | UNSUPPORTED | UNKNOWN | SEMANTIC
    code: str                    # UNSUP-ANCHOR, DUP-KEY, SCOPE-UNAUTHORIZED, GS-1, …
    detail: str
    line: int = 0
    column: int = 0
    got: str = ""
    expected: str = ""
    path: str = ""               # the repository path a per-file diagnostic is about
    remediation: str = ""
    evidence: tuple[str, ...] = ()

    def located(self) -> str:
        return f"{self.line}:{self.column}" if self.line else "-"


@dataclass(frozen=True)
class Field:
    """A parsed field, with the byte range it occupied in the ORIGINAL declaration bytes.

    The range is retained so nothing downstream is ever tempted to recompute `D` from a
    re-serialization. Approval binds to bytes; a round-trip that "preserves meaning" does not
    preserve bytes, and the digest would silently change (`NC-C08`).
    """
    name: str
    value: object
    line: int
    start: int
    end: int


@dataclass(frozen=True)
class LifecycleEvent:
    kind: str
    timestamp: str               # UTC, ISO-8601, compared lexically (which is why it must be UTC)
    line: int
    values: tuple[tuple[str, str], ...] = ()

    def get(self, key: str, default: str = "") -> str:
        return dict(self.values).get(key, default)


@dataclass(frozen=True)
class Declaration:
    """The parsed declaration + the byte facts approval binds to."""
    path: str
    digest: str                                    # `D` — sha256 over the declaration byte range
    raw: bytes                                     # the whole file, verbatim
    decl_bytes: bytes                              # exactly the range `D` covers
    fields: tuple[Field, ...] = ()
    events: tuple[LifecycleEvent, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    boundary_count: int = 0

    def value(self, name: str, default: object = None) -> object:
        for f in self.fields:
            if f.name == name:
                return f.value
        return default

    def present(self, name: str) -> bool:
        return any(f.name == name for f in self.fields)

    @property
    def id(self) -> str:
        v = self.value("id")
        return v if isinstance(v, str) else ""

    @property
    def traits(self) -> frozenset[str]:
        v = self.value("traits") or []
        return frozenset(v) if isinstance(v, list) else frozenset()


@dataclass(frozen=True)
class Trigger:
    """One of T1–T6, with the evidence that decided it. `fired=False` still carries its reason."""
    id: str
    fired: bool
    reason: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class Derived:
    """Every repository fact the decision reads, frozen. Each carries where it came from.

    `unverifiable` is the load-bearing field. A governance tool's worst failure is reporting
    `continue` because a check silently did not run, so an input that could not be resolved is
    recorded by name here and `ST-7` reads it. Absence of evidence never becomes evidence of absence.
    """
    triggers: tuple[Trigger, ...] = ()
    traits: frozenset[str] = frozenset()
    risk_tier: str = "none"
    owners: tuple[str, ...] = ()
    path_owner: tuple[tuple[str, str], ...] = ()   # (path, subsystem_id)
    blast_radius: tuple[str, ...] = ()
    obligations: tuple[tuple[str, str], ...] = ()  # (obligation_id, what it requires)
    labels: tuple[tuple[str, str], ...] = ()       # (path, declared|generated-consequence|…)
    unauthorized: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    impact_classification: str = ""
    authority_blobs: tuple[tuple[str, str, bool], ...] = ()   # (id, blob_sha_now, exists)
    unverifiable: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class Gates:
    """The three ADR-0105 §4.1 gates. `unknown` is NOT `satisfied` — see `decide.py` ST-3/ST-9.

    `merge_authorization` has exactly ONE route: the operator's parent-bound `merge_approved` event.
    There is no second evidence class to name, so the field recording WHICH route satisfied the gate
    is gone with the route it existed to disclose.
    """
    content_approval: str = "not_sought"           # satisfied | stale | unknown | not_sought
    merge_authorization: str = "not_sought"
    acceptance: str = "not_sought"
    approved_digest: str = ""
    approved_head: str = ""                        # the parent the operator authorized
    detail: tuple[str, ...] = ()


@dataclass(frozen=True)
class DecisionInput:
    """The complete, frozen input to `decide()`. No I/O, no clock, no network reachable from here."""
    declaration: Declaration
    derived: Derived
    gates: Gates
    state: str
    diagnostics: tuple[Diagnostic, ...] = ()
    phase: str = "at-head"                         # pre-implementation | at-head | merge-gate


@dataclass(frozen=True)
class Decision:
    outcome: str
    rule: str
    why: str
    next_actor: str
    phase: str = ""
    diagnostics: tuple[Diagnostic, ...] = field(default_factory=tuple)

    @property
    def exit_class(self) -> int:
        return EXIT_CLASS[self.outcome]
