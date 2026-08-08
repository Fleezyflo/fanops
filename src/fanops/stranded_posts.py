# src/fanops/stranded_posts.py
"""READ-ONLY census of the posts sitting under a RETIRED lineage (content-lifecycle).

Before the cascade carried preserve-and-retire DOWN to never-shipped posts, a re-decided moment was
retired while its `awaiting_approval` / `queued` posts kept their state. `review_buckets` hid such a
post from the Review worklist, but `approve_account` / `approve_batch` / `publish_due` read
`led.posts` directly — so it stayed one bulk-approve click away from publishing lineage the pipeline
had already dropped. This module was the data patch for that missing check: it rewrote the stored
labels of the rows already on disk so the tallies and the state machine agreed.

That patch has no subject any more. Suppression is DERIVED at every read by `Ledger.is_suppressed`,
which walks post -> clip -> moment and fails CLOSED on a missing ancestor; `can_promote` refuses to
advance a suppressed post and `can_seed` refuses to mint from a suppressed clip. A stored label under a
dead lineage is inert wherever it is read, and writing `retired` over it would only copy the derivation
into a second source of truth free to drift from it — the exact defect the derived layer removed.

So what remains is a census and nothing else: it COUNTS the posts a retired lineage suppresses, by state
and by account, so an operator can see the shape of the corpus. `--apply` is GONE, and with it the
rollback snapshot, the write lock it guarded and the audit record of what it changed. Nothing here
mutates the ledger, and the mechanical acceptance for that is a zero-hit grep for the writer symbols —
so do not name them again in this file, not even in prose.
"""
from __future__ import annotations

from fanops.config import Config
from fanops.ledger import Ledger


def stranded_posts(led: Ledger) -> list:
    """Every post whose own state OR whose lineage is suppressed, per `Ledger.is_suppressed` — one rule,
    one owner, the same answer `can_promote` and the Review worklist get. Pure, lock-free read. Strictly
    WIDER than the hand-rolled walk it replaced: the owner fails CLOSED, so a post whose clip row is
    MISSING now counts as suppressed where this function used to skip it, and a post already labelled
    `retired` counts as itself rather than only through its ancestors."""
    return [p for p in led.posts.values() if led.is_suppressed(p)]


def _tally(posts) -> dict:
    t: dict = {}
    for p in posts:
        t[p.state.value] = t.get(p.state.value, 0) + 1
    return dict(sorted(t.items(), key=lambda kv: -kv[1]))


def cmd_posts_reconcile_retired(cfg: Config, args) -> int:
    unshipped = set(Ledger._UNSHIPPED_POST_STATES) - set(Ledger._LIVE_POST_STATES)
    led = Ledger.load(cfg)
    posts = stranded_posts(led)
    targets = [p for p in posts if p.state in unshipped]

    print(f"posts under a retired lineage: {len(posts)}")
    unshipped_vals = {s.value for s in unshipped}
    for state, n in _tally(posts).items():
        # Own-state `retired` short-circuits is_suppressed; every other counted label arrived via the
        # lineage walk. Never assert platform contact — this census does not check it.
        if state == "retired":
            verdict = "own state"
        elif state in unshipped_vals:
            verdict = "suppressed by lineage"
        else:
            verdict = "suppressed by lineage (own state kept)"
        print(f"  {state:<18} {n:>5}   {verdict}")
    by_account: dict = {}
    for p in targets:
        by_account[p.account] = by_account.get(p.account, 0) + 1
    if by_account:
        print("by account:")
        for handle, n in sorted(by_account.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {handle:<24} {n:>5}")

    print(f"\n{len(targets)} post(s) under a retired lineage carry a non-terminal label — read-only census; suppression is DERIVED, nothing to write.")
    return 0
