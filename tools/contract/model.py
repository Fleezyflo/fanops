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

import re

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

# The values an `accepted` event must STRUCTURALLY persist (ADR-0105 §4.2, §4.3a). Acceptance is a
# separate decision from merge; recording it with any of these missing would produce an acceptance
# nobody can audit, which is the same as no acceptance at all. `evidence` remains — it is rationale
# for a human and is NEVER read as proof (§4.3a): a row cannot prove itself by describing itself.
ACCEPTANCE_VALUES = ("merge_sha", "decision", "evidence", "date", "operator")

# `check_runs` is what makes an acceptance CHECKABLE, and it is required for the acceptance GATE to
# reach `satisfied` — `_acceptance` refuses a row that records none. It is deliberately NOT in
# `ACCEPTANCE_VALUES`, because those two requirements differ in kind and a past record must not be
# judged by a bar invented after it.
#
# Putting it there made every acceptance recorded before this field existed MALFORMED, and malformed
# lifecycle rows route to `A5` — "the lifecycle record is invalid, reordered, or the landed
# declaration was edited", a §3.6 tampering finding. The Phase 3B contract, correctly accepted under
# the then-live rules, was accused of being edited. That is the same defect the base-pinned required
# set exists to prevent (a present-day bar invalidating a historical acceptance), reproduced one
# field over. Absent evidence is UNVERIFIED, never FALSIFIED.
ACCEPTANCE_EVIDENCE_VALUES = ("check_runs",)

# The accepted row's own value semantics, enforced rather than assumed (§4.3a).
#
# `decision=` must be exactly this. A row reading `decision=rejected` alongside otherwise-valid
# evidence used to verify as an acceptance, because nothing ever read the field it recorded.
ACCEPTED_DECISION = "accepted"
# Recorded check-run ids must be unique DECIMAL strings. Non-decimal text cannot name a platform
# object, and a duplicate id lets one run stand in for two required contexts.
CHECK_RUN_ID = re.compile(r"^[0-9]+$")

# The values a `merged` event must persist.
#
# `merged_at` is DELIBERATELY NOT HERE. Chronology is verified against the event's own TIMESTAMP
# COLUMN, which is the claim the row actually makes about when the merge happened. Accepting a
# separate self-written `merged_at=` while ignoring the timestamp would let a row satisfy chronology
# with a field it authored and contradict itself in the column that dates it.
MERGED_VALUES = ("merge_sha",)

# Where the required-context set is pinned from, at the contract's own `created.base_sha`.
CI_REGISTRY_PATH = ".github/ci-control-registry.yml"
REQUIRED_CONTEXTS_KEY = "current_required_contexts"

# ── derived state names (ADR-0105 §4.3, amended by §4.3a) ───────────────────────────────────
#
# Three states were added because "on main" is not one situation but four, and collapsing them let
# the weakest read as the strongest. A merge whose authorization rederives is `merged`; one whose
# claim cannot be verified is `merged_unverified`; one with no claim at all is `merged_unauthorized`;
# and an `accepted` row whose proof does not complete is `acceptance_claimed`, NOT `accepted`.
# The ref that means "landed". Defined HERE, in the module that imports nothing, because both the
# CLI and the lifecycle rules need it and neither may import the other.
MAIN_REF = "origin/main"

ACCEPTED = "accepted"
ACCEPTANCE_CLAIMED = "acceptance_claimed"
MERGED = "merged"
MERGED_UNVERIFIED = "merged_unverified"
MERGED_UNAUTHORIZED = "merged_unauthorized"

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
class CheckRun:
    """One check run, with the facts provenance and chronology need — never just a name.

    `app_id`/`app_slug` answer WHO PRODUCED IT. `status`, `started_at` and `completed_at` answer WHEN,
    which is what makes "the latest qualifying run at the moment of acceptance" a decidable question
    instead of an ordering guessed from how large an integer is.
    """
    id: str = ""
    name: str = ""
    conclusion: str = ""
    status: str = ""
    started_at: str = ""
    completed_at: str = ""
    app_id: str = ""
    app_slug: str = ""


@dataclass(frozen=True)
class MergeFacts:
    """Platform facts about a merged PR, read once in S5 and frozen. NONE OF THESE IS A REVIEW.

    This type is the whole interface between the GitHub read and the decision. It names merge facts
    and check runs and NOTHING else — there is no field here for a review, a reviewer, an approval
    count or a collaborator, so no amount of downstream code can consult one. The guarantee is the
    same one `gates()` makes by having no `reviews` parameter: absence enforced by shape.

    `read_ok=False` means the read did not complete. That is UNAVAILABLE, not a negative finding, and
    the caller must have already recorded it in `Derived.unverifiable` so it stops at `ST-7` (§4.3a).
    """
    read_ok: bool = False
    pr_head: str = ""                              # the FINAL pre-merge PR head — what §4.1a is about
    merge_sha: str = ""                            # the platform's own merge commit
    merged_at: str = ""                            # platform `mergedAt`, UTC ISO-8601
    merged: bool = False
    # The required set is PINNED to the contract's own `created.base_sha`, read from the in-repo
    # control registry's `current_required_contexts`. NOT live branch protection: a live setting is
    # present-day configuration, and letting it decide a historical acceptance means relaxing
    # protection tomorrow could retroactively invalidate — or manufacture — an acceptance recorded
    # today. A verdict about the past must be computed from evidence that is itself fixed in the past.
    required_contexts: tuple[str, ...] = ()
    # context -> (workflow_path, job_key) from the SAME pinned registry. A required context is
    # otherwise only a NAME, and a name is author-controlled: without the mapping there is nothing
    # to check the joined workflow run against.
    context_provenance: tuple[tuple[str, tuple[str, str]], ...] = ()
    # Every check run bound to `merge_sha`, as `CheckRun` records. Rich, not `(id, name, conclusion)`:
    # the producing App identity is what separates a run GitHub Actions made from one any App with
    # `checks:write` published under the same name, and the server timestamps are what make "later"
    # a fact about time rather than about integer size.
    check_runs: tuple[CheckRun, ...] = ()
    # The documented check-run -> job -> workflow-run join, resolved in S5.
    # check_run_id -> (job_name, workflow_run_id, workflow_path)
    run_provenance: tuple[tuple[str, tuple[str, str, str]], ...] = ()
    # context -> (status, detail) from `adapters.workflow_job_binding` against the workflow blob AT
    # THE VERIFIED BASE. Binds the registry's job KEY to the display name the platform reports, which
    # is the only field a check run actually carries; `ambiguous` means two keys render one name and
    # no deterministic attribution exists.
    job_binding: tuple[tuple[str, tuple[str, str]], ...] = ()
    # workflow path -> True when its blob at the PR head is byte-identical to its blob at the
    # externally-verified base. A workflow edited inside the change it is certifying is not evidence
    # about that change.
    workflow_stable: tuple[tuple[str, bool], ...] = ()
    # The platform's own PR base SHA. The external anchor for `created.base_sha`, which the agent
    # writes OUTSIDE `D` and which selects the registry commit the required set is read from.
    base_sha: str = ""
    # The contract blob AT the final pre-merge PR head, read in S5. `None` = the read did not
    # complete (unavailability -> ST-7); `b""` = read fine and the contract was ABSENT at that head
    # (a known negative: the claim was not effective there). Never a substituted current blob.
    pr_head_blob: bytes | None = None
    # Trees resolved in S5, where a failed read can still reach `Derived.unverifiable`. Empty means
    # UNRESOLVED, which is unavailability — never a completed mismatch (ADR-0105 §4.3a).
    pr_tree: str = ""
    merge_tree: str = ""


@dataclass(frozen=True)
class Gates:
    """The three ADR-0105 §4.1 gates. `unknown` is NOT `satisfied` — see `decide.py` ST-3/ST-9/ST-10.

    `merge_authorization` has exactly ONE route: the operator's parent-bound `merge_approved` event.
    There is no second evidence class to name, so the field recording WHICH route satisfied the gate
    is gone with the route it existed to disclose.

    `acceptance` gained the value `claimed`, which is the entire point of §4.3a: a row that asserts
    acceptance without external proof is a CLAIM, and a claim is not a gate. It used to be
    `satisfied` on row presence alone — and because no rule read the field, nothing could observe
    that it was wrong.
    """
    content_approval: str = "not_sought"           # satisfied | stale | unknown | not_sought
    merge_authorization: str = "not_sought"
    acceptance: str = "not_sought"                 # satisfied | claimed | unknown | not_sought
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
