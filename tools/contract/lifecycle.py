"""R13 — the three gates (§4.1), the derived states (§4.3), invalidation (§4.4), acceptance (D-3).

THE CIRCULARITY, AND WHY THE SPLIT RESOLVES IT. Lifecycle state has to live somewhere. Putting it in
an approval-bound artifact makes recording state void the approval that recording it is evidence of.
ADR-0105 answers with a byte split: `D` covers the declaration only, so an append CANNOT change it —
not by convention, by construction.

THE SECOND CIRCULARITY, AND WHY RELOCATION DID NOT RESOLVE IT (ADR-0105 §4.1a). Two events must bind
to a *commit*, and a record written into the tree cannot name the commit that contains it: the hash
is computed over the record. The original ADR escaped this for `merge_approved` by moving that one
record out of the tree into a GitHub PR review — which binds natively to a `commit_id`, and which
also, unstated, requires a SECOND ACCOUNT TO EXIST. It left `head_proposed` with no escape at all, so
the `implemented` state was unreachable by construction.

The correction binds to the PARENT instead of relocating the record. An event names `parent_sha`, the
commit it is appended onto, and `parent_binds` proves from git that the head differs from it by
lifecycle appends to this contract and nothing else. That delta is, by the §3 byte split above,
incapable of changing the declaration, the code, or any authority — so authorizing the parent and
merging the head authorize the same change. Same guarantee, stated in the only direction writable.

SINGLE-OPERATOR AUTHORIZATION, AND WHY NO REVIEW IS READ HERE. This repository has exactly one human
operator. A rule that requires a second person is not strict, it is UNSATISFIABLE: it can be waited
on forever but never cleared, and ADR-0105 §4.1a already names that outcome — a governance system
that can never authorize a merge in the repository it governs "does not fail safe, it fails
INOPERATIVE, and inoperative controls are removed wholesale rather than satisfied."

So merge authorization has ONE route: an operator-issued `merge_approved` event, parent-bound. This
module reads NO review, NO reviewer identity, and NO principal census — not as a default that some
flag can reverse, but because the code to read them no longer exists. A GitHub review cannot grant,
strengthen, weaken or block authorization, and a dead review API changes no verdict.

What that does NOT relax: the event must name the current `D`, the governed PR, an operator and a
token, and it must parent-bind. Every one of those is checked against git or the declaration, never
taken at its word. The operator can authorize; the operator cannot authorize vaguely.
"""
from __future__ import annotations

from .model import (ACCEPTANCE_VALUES, EVENT_KINDS, MALFORMED, MISSING, PARENT_BOUND_EVENTS,
                    PARENT_BOUND_VALUES, TERMINAL_EVENTS, Diagnostic, Gates)
from .parse import BOUNDARY, is_utc

SATISFIED, STALE, UNKNOWN_GATE, NOT_SOUGHT = "satisfied", "stale", "unknown", "not_sought"

# Every value a `merge_approved` event must carry. `parent_sha` binds the commit, `digest` binds the
# declaration, `pr` binds the change, `operator` and `token` bind the human act. An authorization
# missing any one of them authorizes something less specific than a merge.
MERGE_AUTH_VALUES = ("parent_sha", "digest", "pr", "operator", "token")


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


def gates(decl, events, *, head_sha: str, pr: int | None, main_has_contract: bool,
          repo=None, path: str = "", raw: bytes = b""):
    """The three §4.1 gates. AN UNKNOWN GATE IS NOT A SATISFIED GATE — see `ST-9`.

    Merge authorization has ONE route: the operator's own `merge_approved` event. No review, reviewer
    identity or principal census is a parameter here, so none can be consulted, defaulted or flagged
    back on. The absence is structural, not configured.
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

    auth = NOT_SOUGHT
    approved_head = ""
    if pr is not None:
        ma = [e for e in events if e.kind == "merge_approved"]
        if not ma:
            detail.append("no operator `merge_approved` event — the merge is unauthorized")
        elif repo is None or not path:
            auth = UNKNOWN_GATE
            detail.append("the repository is unreadable, so parent-binding cannot be proven — "
                          "UNKNOWN, which is not satisfied")
        else:
            auth, approved_head, why = _merge_authorization(ma[-1], decl, pr, repo=repo, path=path,
                                                            head_sha=head_sha, raw=raw)
            detail.append(why)

    accepted = SATISFIED if any(e.kind == "accepted" for e in events) else NOT_SOUGHT
    if main_has_contract:
        detail.append("the contract exists on `main` — the change has landed")
    return Gates(content, auth, accepted, approved_digest, approved_head, tuple(detail))


def _merge_authorization(ev, decl, pr: int, *, repo, path: str, head_sha: str, raw: bytes):
    """`(gate, authorized_parent, why)` for ONE operator `merge_approved` event.

    Five checks, and the event is taken at its word for none of them. It supplies `parent_sha`; git
    supplies the ancestry and the delta, the declaration supplies `D`, and the caller supplies the
    governed PR. `operator` and `token` must be non-empty because an authorization that names no
    human and quotes no instruction records that something was authorized without recording WHAT —
    and the agent may transcribe an operator's token but may never author one.
    """
    absent = [k for k in MERGE_AUTH_VALUES if not ev.get(k)]
    if absent:
        return STALE, "", (f"the `merge_approved` event omits {', '.join(absent)} — an authorization "
                           f"missing any of {', '.join(MERGE_AUTH_VALUES)} is not specific enough to "
                           f"authorize a merge")
    if ev.get("digest") != decl.digest:
        return STALE, "", (f"the authorization names D {ev.get('digest')[:20]}… but the declaration "
                           f"is {decl.digest[:20]}… — it authorized a different contract text")
    if str(ev.get("pr")) != str(pr):
        return STALE, "", (f"the authorization names PR #{ev.get('pr')} but the governed PR is "
                           f"#{pr} — it authorized a different change")
    ok, why = parent_binds(ev, repo=repo, path=path, head_sha=head_sha, raw=raw)
    if not ok:
        return STALE, "", f"the `merge_approved` does not bind to the head: {why}"
    return SATISFIED, ev.get("parent_sha"), (
        f"OPERATOR merge authorization accepted: {why}. It names D {decl.digest[:20]}…, PR #{pr}, "
        f"operator `{ev.get('operator')}` and token `{ev.get('token')}`")


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
    if g.merge_authorization == SATISFIED: return "approved_for_merge"
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
# amendment an in-file authorization would void itself the instant it was written down, which is the
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
