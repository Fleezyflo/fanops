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

from .adapters import GH_ACTIONS_APP_ID, GH_ACTIONS_APP_SLUG
from .model import (ACCEPTANCE_CLAIMED, ACCEPTANCE_EVIDENCE_VALUES, ACCEPTANCE_VALUES, ACCEPTED,
                    ACCEPTED_DECISION, CHECK_RUN_ID, EVENT_KINDS, MAIN_REF, MALFORMED, MERGED,
                    MERGED_UNAUTHORIZED, MERGED_UNVERIFIED, MERGED_VALUES, MISSING,
                    PARENT_BOUND_EVENTS, PARENT_BOUND_VALUES, TERMINAL_EVENTS, Diagnostic, Gates)
from .parse import BOUNDARY, is_utc

SATISFIED, STALE, UNKNOWN_GATE, NOT_SOUGHT = "satisfied", "stale", "unknown", "not_sought"

# `claimed` is a COMPLETED read that disagreed — a definite negative finding, distinct from both
# `satisfied` and from `unknown` (which is a read that did not complete). ADR-0105 §4.3a.
CLAIMED = "claimed"

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
                                                  "acceptance (ADR-0105 §4.2, §4.3a)"))
        # A `merged` row is the date and SHA the acceptance check compares against, so an incomplete
        # one leaves acceptance with nothing external to disagree with — which is how a claim passes.
        if e.kind == "merged":
            missing = [k for k in MERGED_VALUES if not e.get(k)]
            if missing:
                out.append(Diagnostic(MALFORMED, "MERGED-INCOMPLETE",
                                      f"a `merged` event must persist all of "
                                      f"{', '.join(MERGED_VALUES)}; missing {', '.join(missing)}",
                                      line=e.line, got=", ".join(k for k, _ in e.values),
                                      expected=", ".join(MERGED_VALUES),
                                      remediation="the platform merge SHA and `mergedAt` are what "
                                                  "acceptance is checked against (ADR-0105 §4.3a)"))

    # A DECLARATION-ONLY CONTRACT IS ALL DECLARATION, so ANY byte that moves after it lands is a
    # landed-declaration edit — the same §3.6 finding, reached without a boundary to partition on.
    # Omitting this would have left the new shape with no landed-record integrity check at all,
    # which is the one guarantee the append-only model was buying.
    if main_blob is not None and BOUNDARY not in main_blob and main_blob != decl_bytes:
        out.append(Diagnostic(MALFORMED, "DECL-DIVERGED",
                              "the contract differs from the one already on `main` — a "
                              "declaration-only contract is all declaration, so editing a landed "
                              "one is §3.6 governance-sensitive",
                              remediation="a post-approval declaration change is a NEW contract "
                                          "with `supersedes:`, never an edit (ADR-0105 §6)"))

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
          repo=None, path: str = "", raw: bytes = b"", mf=None):
    """The three §4.1 gates. AN UNKNOWN GATE IS NOT A SATISFIED GATE — see `ST-9` and `ST-10`.

    Merge authorization has ONE route: the operator's own `merge_approved` event. No review, reviewer
    identity or principal census is a parameter here, so none can be consulted, defaulted or flagged
    back on. The absence is structural, not configured.

    `mf` is a `MergeFacts` — merge SHA, timestamp, pre-merge PR head and check runs. It is the ONLY
    new input, and its type has no field for a review, so widening this signature did not widen what
    can be consulted. `None` means the read was not attempted; `read_ok=False` means it failed, and
    the caller must already have recorded that in `Derived.unverifiable` (§4.3a).
    """
    detail: list[str] = []
    # ONE GATE, TWO PLACES IT CAN BE RECORDED, SELECTED BY THE CONTRACT'S SHAPE — never by fallback.
    # A declaration-only contract (ADR-0106) has no append chain, so approval is the front-matter
    # `approved_digest`; a lifecycle-bearing one keeps reading its last `approved` event, unchanged.
    # The selector is `boundary_count`, a fact about the bytes, so no contract can be read both ways
    # and no contract can have its approval read from whichever place happens to say yes.
    approved = [e for e in events if e.kind == "approved"]
    content = NOT_SOUGHT
    approved_digest = ""
    if decl.boundary_count == 0:
        approved_digest = str(decl.value("approved_digest") or "")
        if approved_digest:
            content = SATISFIED if approved_digest == decl.digest else STALE
            detail.append(f"content approval names {approved_digest[:20]}…; D is "
                          f"{decl.digest[:20]}… (declaration-only, ADR-0106)")
    elif approved:
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

    # POST-MERGE, THE QUESTION IS ASKED AGAINST THE WRONG COMMIT UNLESS IT IS REDIRECTED.
    #
    # A squash merge creates a NEW commit whose parent is the old `main`, so the authorized parent is
    # not an ancestor of it and `parent_binds` reports False — a false `stale` for an authorization
    # that was, and remains, valid. §4.3a rederives against the final pre-merge PR head, the commit
    # the authorization was always about. This VERIFIES; it cannot create. Every input existed before
    # the merge, so no post-merge append can manufacture an authorization that was never given.
    if main_has_contract and mf is not None and mf.read_ok and mf.pr_head:
        ma = [e for e in events if e.kind == "merge_approved"]
        if ma and repo is not None and path:
            auth, approved_head, why = _rederive_post_merge(ma[-1], decl, pr, events, mf,
                                                            repo=repo, path=path, raw=raw)
            detail.append(why)

    accepted, acc_why = _acceptance(events, mf, auth)
    detail.extend(acc_why)
    if main_has_contract:
        detail.append("the contract exists on `main` — the change has landed")
    return Gates(content, auth, accepted, approved_digest, approved_head, tuple(detail))


def _rederive_post_merge(ev, decl, pr, events, mf, *, repo, path: str, raw: bytes):
    """`(gate, authorized_parent, why)` for a MERGED PR. Five checks, all against pre-merge facts.

    THIS VERIFIES AN AUTHORIZATION THAT ALREADY EXISTED. Every input — the PR head, the event, the
    trees — was fixed before the merge, so no post-merge append can manufacture authorization that
    was never given. What the merge changed is only WHICH COMMIT the question must be asked against.
    """
    # THE CONTRACT BLOB AT THE PR HEAD, READ IN S5 — never substituted.
    #
    # This was `repo.blob(mf.pr_head, path) or raw`. That `or` silently swapped in the CURRENT blob
    # whenever the read returned nothing, so two different failures both became "verify against
    # whatever we have here": a contract ABSENT at the PR head (a known negative — the claim was not
    # effective at that commit) and an UNREADABLE ref (unavailability). Both then parent-bound
    # against a document that was never at that head. `None` is unavailability and cannot reach this
    # function; `b""` is the contract genuinely absent.
    if mf.pr_head_blob is None:
        return STALE, "", (f"the contract blob at the PR head {mf.pr_head[:12]} was not read, so "
                           f"authorization cannot be rederived against it")
    if mf.pr_head_blob == b"":
        return STALE, "", (f"this contract is ABSENT at the final pre-merge PR head "
                           f"{mf.pr_head[:12]} — the authorization it records was not in effect at "
                           f"the commit that was merged")
    ok, approved_head, why = _merge_authorization(ev, decl, pr, repo=repo, path=path,
                                                  head_sha=mf.pr_head, raw=mf.pr_head_blob)
    if ok != SATISFIED:
        return STALE, "", f"post-merge rederivation at PR head {mf.pr_head[:12]} failed: {why}"
    if not mf.merge_sha or not repo.is_ancestor(mf.merge_sha, MAIN_REF):
        return STALE, "", (f"the platform reports merge {mf.merge_sha[:12] or '<none>'} but it is "
                           f"not present on `main` — the landed change is not the authorized one")
    recorded = [e for e in events if e.kind == "merged"]
    if recorded and recorded[-1].get("merge_sha") != mf.merge_sha:
        return STALE, "", (f"the `merged` row records {recorded[-1].get('merge_sha')[:12]} but the "
                           f"platform merged {mf.merge_sha[:12]}")
    # Content identity, not commit identity: a squash is SUPPOSED to be a different commit.
    #
    # The trees were resolved in S5, where a failed read reaches `Derived.unverifiable` and `ST-7`.
    # They are NOT re-read here, because an unresolvable ref is unavailability and this function can
    # only return findings — resolving them at this point would convert "could not read" into "did
    # not match", asserting a comparison that never happened. ONLY TWO SUCCESSFULLY READ, UNEQUAL
    # TREES REACH THE MISMATCH BELOW.
    pr_tree, merge_tree = mf.pr_tree, mf.merge_tree
    if pr_tree != merge_tree:
        return STALE, "", (f"the landed tree {merge_tree[:12]} differs from the authorized PR-head "
                           f"tree {pr_tree[:12]} — something changed in the merge")
    return SATISFIED, approved_head, (f"post-merge rederivation at PR head {mf.pr_head[:12]}: {why}; "
                                      f"merge {mf.merge_sha[:12]} is on `main` and its tree "
                                      f"{merge_tree[:12]} equals the authorized PR-head tree")


def _acceptance(events, mf, auth: str) -> tuple[str, list[str]]:
    """`(gate, why)`. AN `accepted` ROW IS A CLAIM. This is what turns it into a finding, or not.

    The row supplies what it asserts; every check below reads the PLATFORM. `evidence=` is rationale
    for a human and is deliberately never consulted — a record cannot prove itself by describing
    itself, and the previous implementation's entire acceptance test was that the row existed.

    Returns `claimed` for a COMPLETED read that disagrees — a definite negative finding. It never
    returns `claimed` for a read that did not complete: that is UNAVAILABLE, recorded in
    `Derived.unverifiable` upstream so it stops at `ST-7`. Collapsing the two would let a network
    failure read as a governance verdict.
    """
    acc = [e for e in events if e.kind == "accepted"]
    if not acc:
        return NOT_SOUGHT, []
    ev = acc[-1]
    if mf is None or not mf.read_ok:
        return UNKNOWN_GATE, ["an `accepted` row is present but the platform could not be read — "
                              "UNKNOWN, which is not satisfied"]
    if auth != SATISFIED:
        return CLAIMED, ["an `accepted` row is present but merge authorization does not verify — "
                         "acceptance cannot rest on an unauthorized merge"]
    if not mf.merged or not mf.merge_sha:
        return CLAIMED, ["an `accepted` row is present but the platform does not report this PR "
                         "as merged"]
    if ev.get("merge_sha") != mf.merge_sha:
        return CLAIMED, [f"the `accepted` row names merge SHA {ev.get('merge_sha')[:12] or '<none>'} "
                         f"but the platform merged {mf.merge_sha[:12]}"]

    merged_rows = [e for e in events if e.kind == "merged"]
    if not merged_rows:
        return CLAIMED, ["an `accepted` row is present with no `merged` row to date it against"]
    # CHRONOLOGY IS THE ROW'S OWN TIMESTAMP COLUMN, not a self-written value beside it. The column is
    # the claim the event makes about when the merge happened; letting a separate `merged_at=` field
    # satisfy this would let a row pass on a value it authored while its dating column said otherwise.
    if merged_rows[-1].timestamp != mf.merged_at:
        return CLAIMED, [f"the `merged` row is timestamped {merged_rows[-1].timestamp} but the "
                         f"platform dates the merge {mf.merged_at}"]
    if merged_rows[-1].get("merge_sha") != mf.merge_sha:
        return CLAIMED, ["the `merged` row names a different SHA than the platform merged"]

    # THE ANCHOR FOR THE BAR ITSELF. `created.base_sha` is agent-written and lives OUTSIDE `D`, yet it
    # chooses which commit's registry supplies the required set — so an unchecked one lets a contract
    # point at an older, weaker registry and be judged against a bar it selected for itself. The
    # platform's own PR base is the external answer. Both were read successfully, so a disagreement
    # is a FINDING; this is checked before the required set because with the base in doubt the set
    # was deliberately never read.
    claimed_base = next((e.get("base_sha") for e in events
                         if e.kind == "created" and e.get("base_sha")), "")
    if not claimed_base:
        return CLAIMED, ["the contract records no `created.base_sha`, so the required-context set "
                         "has no commit to be pinned to"]
    if not mf.base_sha:
        return CLAIMED, ["the platform reports no base SHA for this PR, so `created.base_sha` has "
                         "nothing external to be checked against"]
    if claimed_base != mf.base_sha:
        return CLAIMED, [f"the contract records `created.base_sha={claimed_base[:12]}` but the "
                         f"platform reports this PR based on {mf.base_sha[:12]} — the commit whose "
                         f"registry sets the required bar is not the one this change was opened "
                         f"against, and the anchor for that bar is not the contract's to choose"]

    # Only the REQUIRED set may be judged. Unrelated runs are legitimately `skipped` at a merge
    # commit (`impact report` and `scheduled reconciliation` both are), so "every run succeeded"
    # would reject a valid acceptance for jobs that were never the bar.
    if not mf.required_contexts:
        return CLAIMED, ["the pinned registry names no required context, so a green merge proves "
                         "nothing about required CI"]

    # THE ROW'S OWN VALUES ARE READ, NOT ASSUMED. `decision=` used to be recorded and never
    # consulted, so a row saying `decision=rejected` beside otherwise-valid evidence verified as an
    # acceptance. A field nothing reads is not a record, it is decoration.
    if ev.get("decision") != ACCEPTED_DECISION:
        return CLAIMED, [f"the `accepted` row records decision={ev.get('decision') or '<none>'!s}, "
                         f"not {ACCEPTED_DECISION!r}"]

    # RESOLVE THE RECORDED IDS THEMSELVES — a rerun must not move an already-recorded verdict.
    #
    # The earlier version rebuilt a name→run map on every evaluation and compared the recorded ids to
    # whatever was newest. A rerun of a required job mints a NEW id, so a verdict recorded yesterday
    # silently became `acceptance_claimed` today with nothing about the change having altered. That
    # is an acceptance decaying on its own, which is not a verification. Identity is the anchor: the
    # ids that were recorded are the ids that get looked up, and later runs are simply not consulted.
    by_id = {r.id: r for r in mf.check_runs}
    prov = dict(mf.run_provenance)
    ctx_map = dict(mf.context_provenance)
    stable = dict(mf.workflow_stable)
    at = ev.timestamp
    recorded = [x.strip() for k in ACCEPTANCE_EVIDENCE_VALUES
                for x in ev.get(k, "").split(",") if x.strip()]
    if not recorded:
        # NOT malformed — see `ACCEPTANCE_EVIDENCE_VALUES`. A row predating this requirement is
        # UNVERIFIED, which is not satisfied and stops here; it is not a tampered record.
        return CLAIMED, [f"the `accepted` row records no {'/'.join(ACCEPTANCE_EVIDENCE_VALUES)}, so "
                         f"nothing binds the claim to a run the platform actually performed"]
    bad = [r for r in recorded if not CHECK_RUN_ID.match(r)]
    if bad:
        return CLAIMED, [f"recorded check-run id(s) {', '.join(bad)} are not decimal platform ids"]
    if len(set(recorded)) != len(recorded):
        return CLAIMED, ["the `accepted` row records the same check-run id twice, so one run would "
                         "stand as evidence for two required contexts"]
    if recorded != sorted(recorded, key=int):
        return CLAIMED, ["the recorded check-run ids are not in ascending numeric order — the row's "
                         "evidence must be deterministic so two correct agents write the same bytes"]

    covered: dict[str, str] = {}
    bound: dict[str, str] = {}
    for rid in recorded:
        run = by_id.get(rid)
        if run is None:
            return CLAIMED, [f"recorded check run {rid} is not bound to the merge SHA "
                             f"{mf.merge_sha[:12]}"]
        ctx = run.name
        if ctx not in mf.required_contexts:
            return CLAIMED, [f"recorded check run {rid} is {ctx!r}, which is not a required context"]
        # PROVENANCE, not a name. Any App with `checks:write` can publish a green run under a
        # required context's exact name; pinning the producing App is what makes the tick mean CI ran.
        if (run.app_id, run.app_slug) != (GH_ACTIONS_APP_ID, GH_ACTIONS_APP_SLUG):
            return CLAIMED, [f"recorded check run {rid} ({ctx}) was produced by App "
                             f"{run.app_slug or '<none>'}/{run.app_id or '<none>'}, not GitHub "
                             f"Actions ({GH_ACTIONS_APP_SLUG}/{GH_ACTIONS_APP_ID})"]
        if run.status != "completed" or run.conclusion != "success":
            return CLAIMED, [f"recorded check run {rid} ({ctx}) is {run.status or '<none>'}/"
                             f"{run.conclusion or '<none>'}, not completed/success"]
        if not run.completed_at or run.completed_at > at:
            return CLAIMED, [f"recorded check run {rid} ({ctx}) completed "
                             f"{run.completed_at or '<never>'}, which is not on or before the "
                             f"acceptance at {at} — evidence cannot post-date the decision it proves"]
        joined = prov.get(rid)
        if joined is None:
            return CLAIMED, [f"recorded check run {rid} ({ctx}) joins to no workflow job, so nothing "
                             f"connects it to a workflow this repository actually runs"]
        job_name, _run_id, wf_path = joined
        want_path, want_job = ctx_map.get(ctx, ("", ""))
        if wf_path != want_path:
            return CLAIMED, [f"required context {ctx!r} is pinned to {want_path} but run {rid} came "
                             f"from {wf_path or '<unknown>'}"]
        # THE PINNED JOB KEY IS CONSUMED HERE. It was previously unpacked and discarded, so the chain
        # ended at the joined job's DISPLAY NAME — a value the workflow author controls and the
        # registry never checked. Binding the key means the registry's claim about which job produces
        # a required context is verified against the workflow blob at the base, not assumed.
        status, detail = dict(mf.job_binding).get(ctx, ("missing", ""))
        if status == "missing":
            return CLAIMED, [f"the registry pins required context {ctx!r} to job key {want_job!r} in "
                             f"{want_path}, but that workflow declares no such job at the verified "
                             f"base"]
        if status == "ambiguous":
            return CLAIMED, [f"job keys {want_job!r} and {detail!r} in {want_path} both render the "
                             f"display name the platform reports, so a job carrying it cannot be "
                             f"attributed to either — the join is not deterministic"]
        if detail != ctx:
            return CLAIMED, [f"the registry pins {ctx!r} to job key {want_job!r}, but that job is "
                             f"named {detail!r} in {want_path} at the verified base"]
        if job_name != detail:
            return CLAIMED, [f"run {rid} joins to platform job {job_name!r}, which is not the pinned "
                             f"job {want_job!r} ({detail!r})"]
        if stable.get(wf_path) is not True:
            return CLAIMED, [f"the governing workflow {wf_path} differs between the verified base and "
                             f"the PR head — a workflow edited inside the change it certifies is not "
                             f"evidence about that change"]
        bound[ctx] = f"{want_job}={detail}"
        covered[ctx] = rid
    missing = [c for c in mf.required_contexts if c not in covered]
    if missing:
        return CLAIMED, [f"no recorded check run covers required context(s) {', '.join(missing)}"]

    # TEMPORAL PINNING, BOTH DIRECTIONS. Relative to the acceptance instant: a run created AFTER it
    # is not consulted (a rerun cannot revise a verdict already made), and a run that already existed
    # THEN and is newer than the recorded one must be the evidence instead — whatever it concluded.
    # A newer failed, cancelled, skipped or still-pending run present at that moment means the thing
    # being accepted was not green at the moment it was accepted.
    for ctx, rid in covered.items():
        mine = by_id[rid]
        contemporaneous = [r for r in mf.check_runs
                           if r.name == ctx and r.started_at and r.started_at <= at]
        newer = [r for r in contemporaneous if (r.started_at, r.id) > (mine.started_at, mine.id)]
        if newer:
            n = max(newer, key=lambda r: (r.started_at, r.id))
            return CLAIMED, [f"required context {ctx!r} records run {rid} (started {mine.started_at}) "
                             f"but run {n.id} had already started {n.started_at} by the acceptance at "
                             f"{at} and is {n.status or '<none>'}/{n.conclusion or '<none>'} — the "
                             f"recorded run was not the latest qualifying one at that instant"]
    return SATISFIED, [f"acceptance verified against the platform: merge {mf.merge_sha[:12]} at "
                       f"{mf.merged_at}, base {mf.base_sha[:12]} externally confirmed, required "
                       f"contexts {', '.join(mf.required_contexts)} each satisfied by the RECORDED "
                       f"GitHub-Actions run that was latest at {at} "
                       f"({', '.join(f'{c}={covered[c]}' for c in sorted(covered))}), each joined "
                       f"through `check_run_url` to the registry-pinned job key "
                       f"({', '.join(f'{c}->{bound[c]}' for c in sorted(bound))}) in its pinned "
                       f"workflow, whose blob is unchanged from the verified base"]


def select_run_ids(mf) -> tuple[list[str], list[str]]:
    """`(ids, problems)` — the ids an operator should RECORD, chosen once, before first acceptance.

    LATEST BY SERVER TIME, not by integer size. Ids are opaque platform identifiers; "the bigger
    number is the newer run" is an assumption about how GitHub mints them, and a verdict that rests
    on it rests on something the platform never promised. The check runs carry `started_at`, so the
    latest attempt is decidable from the data rather than inferred from it.

    After the row exists this function is never consulted again — `_acceptance` resolves the recorded
    ids themselves and pins them to the acceptance instant, so a later rerun cannot displace a verdict
    that has already been made.
    """
    chosen, problems = [], []
    for ctx in mf.required_contexts:
        runs = [r for r in mf.check_runs if r.name == ctx]
        if not runs:
            problems.append(f"required context {ctx!r} has no check run bound to {mf.merge_sha[:12]}")
            continue
        undated = [r for r in runs if not r.started_at]
        if undated:
            problems.append(f"required context {ctx!r} has a check run with no `started_at`, so "
                            f"which attempt is latest cannot be established from the platform data")
            continue
        r = max(runs, key=lambda x: (x.started_at, x.id))
        if (r.app_id, r.app_slug) != (GH_ACTIONS_APP_ID, GH_ACTIONS_APP_SLUG):
            problems.append(f"the latest run for {ctx!r} was produced by App "
                            f"{r.app_slug or '<none>'}, not GitHub Actions")
            continue
        if r.status != "completed" or r.conclusion != "success":
            problems.append(f"required context {ctx!r} concluded "
                            f"{r.conclusion or r.status or '<none>'}, not success")
            continue
        chosen.append(r.id)
    return sorted(chosen, key=lambda x: int(x) if x.isdigit() else -1), problems


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
    """The FOURTEEN derived states, FIRST MATCH WINS. State is computed, never declared.

    Eleven reachable through the ladder below plus the three terminal event kinds returned above it.
    The docstring said "twelve" while §4.3a had already added `acceptance_claimed`,
    `merged_unverified` and `merged_unauthorized` — a count in prose does not move when the code
    does, so `NC-AC-31` derives it from `state()` itself rather than trusting this sentence.

    `merged` is derived from the change having landed, independently of any written event, so a
    delayed acceptance leaves no gap in the record. And `merged` NEVER implies `accepted`: merge is
    an event, acceptance is a separate operator decision demonstrating the success condition, and
    collapsing the two is exactly the shortcut this ordering forbids (`NC-C25`).

    §4.3a closed the SAME shortcut in the other direction. The guard above was one-way — it stopped
    `merged` implying `accepted`, while an `accepted` ROW produced `accepted` outright. Both halves
    are now closed: neither the merge nor the claim decides, only the verified finding does.

    `proposal_bound` replaces the original `head_proposed.head_sha == head_sha` test, which no commit
    could ever satisfy: appending the event IS the commit, so the event would have to name a hash
    computed over itself. `implemented` was unreachable by construction until `parent_binds` gave the
    claim a writable direction (`NC-C57`).
    """
    for kind in TERMINAL_EVENTS:
        if any(e.kind == kind for e in events):
            return kind
    # ROW PRESENCE NEVER DECIDES. This row used to read `if any(e.kind == "accepted"): return
    # "accepted"` — the claim being evaluated was the whole of its own evidence. Now the gate has
    # already been checked against the platform, so what lands here is a FINDING about the row, not
    # the row itself. `claimed` and `unknown` both fall through to `acceptance_claimed`: a claim that
    # did not verify and a claim that could not be checked are equally not acceptance.
    if any(e.kind == "accepted" for e in events):
        return ACCEPTED if g.acceptance == SATISFIED else ACCEPTANCE_CLAIMED
    # "On main" is four situations, not one. Collapsing them let the weakest read as the strongest.
    if merged:
        if g.merge_authorization == SATISFIED: return MERGED
        if any(e.kind == "merge_approved" for e in events): return MERGED_UNVERIFIED
        return MERGED_UNAUTHORIZED
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
