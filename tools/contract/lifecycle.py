"""R13 — the three gates (§4.1), the nine derived states (§4.3), invalidation (§4.4), acceptance (D-3).

ADR-0105 §4 is self-contained and this is the only module that needs `ReviewPort`, which is why it
is its own file rather than part of `validate.py`.

THE CIRCULARITY, AND WHY THE SPLIT RESOLVES IT. Lifecycle state has to live somewhere. Putting it in
an approval-bound artifact makes recording state void the approval that recording it is evidence of.
ADR-0105 answers with a byte split: `D` covers the declaration only, so an append CANNOT change it —
not by convention, by construction. Exact-head approval is then the one gate that must not move the
head, and it is the one gate recorded OUTSIDE the tree, as a GitHub PR review, which already binds
natively to a `commit_id`.

`review.commit_id` is compared explicitly and GitHub's `reviewDecision` badge is never read. §Risks
directs Phase 3 to compare rather than trust the badge, whether or not `dismiss_stale_reviews` is
enabled — the badge answers "is this PR approved", the contract asks "is THIS COMMIT approved", and
those diverge exactly when it matters.
"""
from __future__ import annotations

from .adapters import PortError
from .model import (ACCEPTANCE_VALUES, EVENT_KINDS, MALFORMED, MISSING, TERMINAL_EVENTS, Diagnostic,
                    Gates)
from .parse import BOUNDARY, is_utc

SATISFIED, STALE, UNKNOWN_GATE, NOT_SOUGHT = "satisfied", "stale", "unknown", "not_sought"


def validate_events(events, *, main_blob: bytes | None, decl_bytes: bytes, life_bytes: bytes):
    """V-lifecycle. Shape, order, monotone time, and append-only against `main`'s copy.

    The append-only check is a BYTE PREFIX comparison, not a semantic one. A lifecycle that merely
    "means the same thing" as the landed one is not append-only: §3.6 makes rewriting or reordering
    history governance-sensitive precisely because a reordered record can make an earlier
    authorization say something it did not say at the time it was given.
    """
    out: list[Diagnostic] = []
    last_ts = ""
    for i, e in enumerate(events):
        if e.kind not in EVENT_KINDS:
            out.append(Diagnostic(MALFORMED, "EVENT-KIND", f"{e.kind!r} is not a lifecycle event",
                                  line=e.line, got=e.kind, expected=", ".join(EVENT_KINDS)))
        if not is_utc(e.timestamp):
            out.append(Diagnostic(MALFORMED, "EVENT-TIME",
                                  f"{e.timestamp!r} is not a UTC ISO-8601 instant", line=e.line,
                                  got=e.timestamp, expected="YYYY-MM-DDTHH:MM:SSZ"))
        elif last_ts and e.timestamp < last_ts:
            out.append(Diagnostic(MALFORMED, "EVENT-ORDER",
                                  f"event {i + 1} goes backwards in time ({e.timestamp} after "
                                  f"{last_ts}) — the record is not append-only", line=e.line,
                                  got=e.timestamp, expected=f">= {last_ts}"))
        if is_utc(e.timestamp):
            last_ts = max(last_ts, e.timestamp)
        if e.kind in TERMINAL_EVENTS and i != len(events) - 1:
            out.append(Diagnostic(MALFORMED, "EVENT-AFTER-TERMINAL",
                                  f"{e.kind!r} is terminal but {len(events) - i - 1} event(s) "
                                  f"follow it", line=e.line))
        if e.kind == "accepted":
            missing = [k for k in ACCEPTANCE_VALUES if not e.get(k)]
            if missing:
                out.append(Diagnostic(MALFORMED, "ACCEPT-INCOMPLETE",
                                      f"an `accepted` event must persist all of "
                                      f"{', '.join(ACCEPTANCE_VALUES)}; missing "
                                      f"{', '.join(missing)}", line=e.line,
                                      got=", ".join(k for k, _ in e.values),
                                      expected=", ".join(ACCEPTANCE_VALUES),
                                      remediation="an acceptance nobody can audit is not an "
                                                  "acceptance (operator decision D-3)"))

    if main_blob is not None and BOUNDARY in main_blob:
        m_decl, _, m_life = main_blob.partition(BOUNDARY)
        if m_decl != decl_bytes:
            out.append(Diagnostic(MALFORMED, "DECL-DIVERGED",
                                  "the declaration differs from the one already on `main` — "
                                  "editing a landed declaration is §3.6 governance-sensitive, not "
                                  "a formatting change",
                                  remediation="a post-approval declaration change is a NEW contract "
                                              "with `supersedes:`, never an edit (ADR-0105 §6)"))
        elif m_life and not life_bytes.startswith(m_life):
            out.append(Diagnostic(MALFORMED, "LIFECYCLE-REWRITTEN",
                                  "the lifecycle is not a byte prefix-extension of the one on "
                                  "`main` — history was rewritten, reordered or truncated",
                                  remediation="append only; never edit an event already recorded"))
    return out


def gates(decl, events, *, head_sha: str, pr: int | None, reviews, main_has_contract: bool):
    """The three §4.1 gates. AN UNKNOWN GATE IS NOT A SATISFIED GATE — see `ST-4`."""
    detail: list[str] = []
    approved = [e for e in events if e.kind == "approved"]
    content = NOT_SOUGHT
    approved_digest = ""
    if approved:
        approved_digest = approved[-1].get("digest")
        content = SATISFIED if approved_digest == decl.digest else STALE
        detail.append(f"content approval names {approved_digest[:20] or '<no digest>'}…; "
                      f"D is {decl.digest[:20]}…")

    exact = NOT_SOUGHT
    approved_head = ""
    if pr is not None:
        if reviews is None:
            exact = UNKNOWN_GATE
            detail.append("PR reviews could not be read; the gate is UNKNOWN, which is not satisfied")
        else:
            hits = [cid for cid, state in reviews if state == "APPROVED" and cid == head_sha]
            approved_head = hits[0] if hits else ""
            exact = SATISFIED if hits else (STALE if reviews else NOT_SOUGHT)
            detail.append(f"{len(reviews)} review(s); {len(hits)} approving the exact head "
                          f"{head_sha[:12] or '<none>'}")

    accepted = SATISFIED if any(e.kind == "accepted" for e in events) else NOT_SOUGHT
    if main_has_contract:
        detail.append("the contract exists on `main` — the change has landed")
    return Gates(content, exact, accepted, approved_digest, approved_head, tuple(detail))


def state(decl, events, g: Gates, *, merged: bool, ci_green: bool, head_sha: str,
          pr_open: bool, mandatory_ok: bool) -> str:
    """The nine §4.3 states, FIRST MATCH WINS. State is computed, never declared.

    `merged` is derived from the change having landed, independently of any written event, so a
    delayed acceptance leaves no gap in the record. And `merged` NEVER implies `accepted`: merge is
    an event, acceptance is a separate operator decision demonstrating the success condition, and
    collapsing the two is exactly the shortcut this ordering forbids (`NC-C25`).
    """
    for kind in TERMINAL_EVENTS:
        if any(e.kind == kind for e in events):
            return kind
    if any(e.kind == "accepted" for e in events): return "accepted"
    if merged: return "merged"
    if g.exact_head_approval == SATISFIED: return "approved_for_merge"
    if ci_green and any(e.kind == "head_proposed" and e.get("head_sha") == head_sha
                        for e in events): return "implemented"
    if g.content_approval == SATISFIED and any(e.kind == "implementation_started"
                                               for e in events): return "in_implementation"
    if g.content_approval == SATISFIED: return "approved"
    if pr_open and mandatory_ok: return "in_review"
    return "draft"


# ── §4.4 invalidation, stated so rows 4 and 6 do not contradict each other (operator D-5) ────
#
# The tension in the ADR's table dissolves once VOIDING A RECORD is separated from HALTING WORK.
# Row 4 ("base moves") says the base advancing does not itself void content approval. Row 6 ("a
# cited authority's blob changed") says that case FLAGS. Both hold at once: the authority change
# does not destroy the approval record — it can be re-affirmed without re-approving the design from
# scratch — but it DOES halt work under §10's "a cited authority changed after approval → stop".
# Only this reading is implemented, and `ST-2` is where the halt lands.
INVALIDATION = (
    ("declaration_edited", "VOID", "VOID", "stops; renewed approval required"),
    ("lifecycle_appended", "survives", "VOID if it predates the append", "continues"),
    ("head_moved", "survives", "VOID", "continues; re-approve at the new head"),
    ("base_moved_diff_same", "survives", "survives; required CI re-runs", "continues"),
    ("base_moved_diff_changed", "survives", "VOID", "continues; re-approve"),
    ("authority_blob_changed", "survives — FLAGGED", "FLAGGED", "stops until re-confirmed"),
    ("id_reused", "VOID", "VOID", "stops"),
)


def read_reviews(port, pr: int | None):
    """(reviews, problem). `None` reviews means UNKNOWN — never an empty list, which reads as 'no
    approval exists' and would let a `gh` outage look like a deliberate absence of approval."""
    if pr is None:
        return [], None
    try:
        return port.approvals(pr), None
    except PortError as exc:
        return None, f"PR #{pr} reviews unreadable: {exc}"


def binding_of(events, key: str, default: str = "") -> str:
    """The latest value of a lifecycle binding. Later appends supersede earlier ones (§3.3)."""
    for e in reversed(events):
        v = e.get(key)
        if v:
            return v
    return default


def missing_terminal_note(events) -> Diagnostic | None:
    if not events:
        return Diagnostic(MISSING, "NO-EVENTS", "the lifecycle section records no events",
                          expected="at least a `created` event")
    return None
