"""R13 — the three gates (§4.1), the nine derived states (§4.3), invalidation (§4.4), acceptance (D-3).

ADR-0105 §4 is self-contained and this is the only module that needs `ReviewPort`, which is why it
is its own file rather than part of `validate.py`.

THE CIRCULARITY, AND WHY THE SPLIT RESOLVES IT. Lifecycle state has to live somewhere. Putting it in
an approval-bound artifact makes recording state void the approval that recording it is evidence of.
ADR-0105 answers with a byte split: `D` covers the declaration only, so an append CANNOT change it —
not by convention, by construction.

THE SECOND CIRCULARITY, AND WHY RELOCATION DID NOT RESOLVE IT (ADR-0105 §4.1, amended). Two events
must bind to a *commit*, and a record written into the tree cannot name the commit that contains it:
the hash is computed over the record. The original ADR escaped this for `merge_approved` by moving
that one record out of the tree into a GitHub PR review — which binds natively to a `commit_id`, and
which also, unstated, requires a second account to exist. It left `head_proposed` with no escape at
all, so the `implemented` state was unreachable by construction.

The amendment binds to the PARENT instead of relocating the record. An event names `parent_sha`, the
commit it is appended onto, and `parent_binds` proves from git that the head differs from it by
lifecycle appends to this contract and nothing else. That delta is, by the §3 byte split above,
incapable of changing the declaration, the code, or any authority — so approving the parent and
merging the head approve the same change. Same guarantee, stated in the only direction writable.

`review.commit_id` is compared explicitly and GitHub's `reviewDecision` badge is never read. §Risks
directs Phase 3 to compare rather than trust the badge, whether or not `dismiss_stale_reviews` is
enabled — the badge answers "is this PR approved", the contract asks "is THIS COMMIT approved", and
those diverge exactly when it matters.
"""
from __future__ import annotations

from .adapters import PortError
from .model import (ACCEPTANCE_VALUES, EVENT_KINDS, MALFORMED, MISSING, PARENT_BOUND_EVENTS,
                    PARENT_BOUND_VALUES, TERMINAL_EVENTS, Diagnostic, Gates)
from .parse import BOUNDARY, is_utc

SATISFIED, STALE, UNKNOWN_GATE, NOT_SOUGHT = "satisfied", "stale", "unknown", "not_sought"

# The two §4.1 evidence routes for the exact-head gate. `witnessed` is a second principal's review;
# `unwitnessed` is the operator's own record, admissible ONLY where the platform proves no second
# principal exists. The name travels into the report and the payload: governance that degrades
# silently is a bypass, governance that degrades loudly is a disclosure.
WITNESSED, UNWITNESSED = "witnessed", "unwitnessed"


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
        if e.kind in PARENT_BOUND_EVENTS:
            absent = [k for k in PARENT_BOUND_VALUES if not e.get(k)]
            if absent:
                out.append(Diagnostic(MALFORMED, "PARENT-BIND-INCOMPLETE",
                                      f"a `{e.kind}` event must persist "
                                      f"{', '.join(PARENT_BOUND_VALUES)}; missing "
                                      f"{', '.join(absent)}", line=e.line,
                                      got=", ".join(k for k, _ in e.values),
                                      expected=", ".join(PARENT_BOUND_VALUES),
                                      remediation="an approval that names no commit approves "
                                                  "nothing in particular (ADR-0105 §4.1)"))
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


def parent_binds(event, *, repo, path: str, head_sha: str, raw: bytes) -> tuple[bool, str]:
    """Does a parent-bound event still bind to `head_sha`? `(bool, why)`, git-computed throughout.

    FOUR CHECKS, AND THE RECORD IS TAKEN AT ITS WORD FOR NONE OF THEM. The event supplies one datum,
    `parent_sha`; everything else is read out of git. Together they establish that the head and the
    approved parent differ by lifecycle appends to THIS contract and by nothing else:

    1. `parent_sha` is an ancestor of the head — so the head really descends from what was approved.
    2. No path other than this contract moved between them — so no code rode in behind the approval.
    3. The declaration bytes are identical at both — check 2 is path-level and would not notice a
       declaration edit inside the one file it permits to move.
    4. The head's lifecycle byte-prefix-extends the parent's — appended to, never rewritten.

    2 and 3 are the pair that matters. Dropping either leaves a hole big enough to merge through.
    """
    parent = event.get("parent_sha")
    if not parent: return False, "the event names no `parent_sha`"
    if not head_sha: return False, "the head could not be resolved"
    if not repo.is_ancestor(parent, head_sha):
        return False, f"{parent[:12]} is not an ancestor of the head {head_sha[:12]}"
    moved = [f for f in repo.diff_names(parent, head_sha) if f != path]
    if moved:
        return False, (f"{len(moved)} path(s) other than the contract moved since {parent[:12]}: "
                       f"{', '.join(moved[:3])}")
    p_raw = repo.blob(parent, path)
    if p_raw is None or BOUNDARY not in p_raw:
        return False, f"the contract is absent or has no lifecycle boundary at {parent[:12]}"
    p_decl, _, p_life = p_raw.partition(BOUNDARY)
    h_raw = repo.blob(head_sha, path) or raw
    h_decl, _, h_life = h_raw.partition(BOUNDARY)
    if p_decl != h_decl:
        return False, f"the declaration changed after {parent[:12]} — that is a new contract, not an append"
    if not h_life.startswith(p_life):
        return False, f"the lifecycle was rewritten rather than appended to after {parent[:12]}"
    return True, f"the head is {parent[:12]} plus lifecycle appends to this contract and nothing else"


def gates(decl, events, *, head_sha: str, pr: int | None, reviews, main_has_contract: bool,
          repo=None, path: str = "", raw: bytes = b"", principals=None):
    """The three §4.1 gates. AN UNKNOWN GATE IS NOT A SATISFIED GATE — see `ST-4`.

    `principals` is the platform's write-access set, or `None` for unreadable. It decides ADMISSIBILITY
    of the unwitnessed route and nothing else — no field, flag or declaration can reach it, which is
    what keeps the route from being a choice anyone makes.
    """
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
    evidence = ""
    if pr is not None:
        if reviews is None:
            exact = UNKNOWN_GATE
            detail.append("PR reviews could not be read; the gate is UNKNOWN, which is not satisfied")
        else:
            hits = [cid for cid, state in reviews if state == "APPROVED" and cid == head_sha]
            approved_head = hits[0] if hits else ""
            exact = SATISFIED if hits else (STALE if reviews else NOT_SOUGHT)
            if hits: evidence = WITNESSED
            detail.append(f"{len(reviews)} review(s); {len(hits)} approving the exact head "
                          f"{head_sha[:12] or '<none>'}")

        # ── the unwitnessed route (§4.1, amended) — reached only when no review witnessed the head
        if exact != SATISFIED:
            ma = [e for e in events if e.kind == "merge_approved"]
            if not ma:
                detail.append("no in-file `merge_approved` event; the witnessed route is the only "
                              "one in play")
            elif principals is None:
                exact = UNKNOWN_GATE
                detail.append("the write-principal set is unreadable, so the unwitnessed route "
                              "cannot be shown ADMISSIBLE — UNKNOWN, which is not satisfied")
            elif len(principals) != 1:
                detail.append(f"the unwitnessed route is INADMISSIBLE: {len(principals)} principals "
                              f"can push ({', '.join(principals[:4])}), so a witnessed review is "
                              f"obtainable and therefore required")
            elif repo is None or not path:
                exact = UNKNOWN_GATE
                detail.append("the repository is unreadable, so parent-binding cannot be proven — "
                              "UNKNOWN, which is not satisfied")
            else:
                ok, why = parent_binds(ma[-1], repo=repo, path=path, head_sha=head_sha, raw=raw)
                if ok:
                    exact, approved_head, evidence = SATISFIED, ma[-1].get("parent_sha"), UNWITNESSED
                    detail.append(f"UNWITNESSED exact-head approval accepted: {why}. "
                                  f"`{principals[0]}` is the only principal with push access, so no "
                                  f"second reviewer exists to witness it — this approval carries the "
                                  f"operator's judgement alone")
                else:
                    exact = STALE
                    detail.append(f"the in-file `merge_approved` does not bind to the head: {why}")

    accepted = SATISFIED if any(e.kind == "accepted" for e in events) else NOT_SOUGHT
    if main_has_contract:
        detail.append("the contract exists on `main` — the change has landed")
    return Gates(content, exact, accepted, approved_digest, approved_head, tuple(detail), evidence)


def state(decl, events, g: Gates, *, merged: bool, ci_green: bool, proposal_bound: bool,
          pr_open: bool, mandatory_ok: bool) -> str:
    """The nine §4.3 states, FIRST MATCH WINS. State is computed, never declared.

    `merged` is derived from the change having landed, independently of any written event, so a
    delayed acceptance leaves no gap in the record. And `merged` NEVER implies `accepted`: merge is
    an event, acceptance is a separate operator decision demonstrating the success condition, and
    collapsing the two is exactly the shortcut this ordering forbids (`NC-C25`).

    `proposal_bound` replaces the original `head_proposed.head_sha == head_sha` test, which no commit
    could ever satisfy: appending the event IS the commit, so the event would have to name a hash
    computed over itself. `implemented` was unreachable by construction until `parent_binds` gave the
    claim a writable direction (`NC-C57`).
    """
    for kind in TERMINAL_EVENTS:
        if any(e.kind == kind for e in events):
            return kind
    if any(e.kind == "accepted" for e in events): return "accepted"
    if merged: return "merged"
    if g.exact_head_approval == SATISFIED: return "approved_for_merge"
    if ci_green and proposal_bound: return "implemented"
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
#
# ROW 2 IS AMENDED. It used to read "VOID if it predates the append", because the head moved. Under
# parent-binding the head moving is no longer the question — what the append CONTAINED is. A
# lifecycle-only append leaves the declaration and every other path byte-identical, so the approval
# still covers the change; `parent_binds` proves that from git rather than assuming it. Any append
# carrying anything else fails check 2 or 3 and voids the approval exactly as before. Without this
# amendment the unwitnessed route would void itself the instant it was written down, which is the
# original circularity wearing a different hat.
INVALIDATION = (
    ("declaration_edited", "VOID", "VOID", "stops; renewed approval required"),
    ("lifecycle_appended", "survives", "survives if the append is lifecycle-only", "continues"),
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


def read_principals(port, pr: int | None):
    """(principals, problem). `None` means UNKNOWN, and unknown makes the unwitnessed route
    inadmissible — the fail-closed direction, matching `read_reviews` above.

    Skipped entirely when no PR is in play: pre-implementation verification asks nothing of the
    exact-head gate, and paying a network round trip to learn that would be pure cost.
    """
    if pr is None:
        return [], None
    try:
        return port.write_principals(), None
    except PortError as exc:
        return None, f"repository write-principals unreadable: {exc}"


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
