# src/fanops/stranded_posts.py
"""One-time reconcile for posts stranded under a RETIRED lineage (content-lifecycle).

Before the cascade carried preserve-and-retire DOWN to never-shipped posts, a re-decided moment was
retired while its `awaiting_approval` / `queued` posts kept their state. `review_buckets` hides such a
post from the Review worklist, but `approve_account` / `approve_batch` / `publish_due` read
`led.posts` directly — so it stayed one bulk-approve click away from publishing lineage the pipeline
had already dropped, and it silently inflated every raw awaiting tally.

`Ledger._delete_moment_cascade` now stops NEW ones, `actions_approve` refuses to promote one, and
`post.run.publish_due` refuses to SHIP one (the last of those was the real gap: it is the only reader that
reaches a platform, so until it guarded, this verb was the safety net rather than the tidy-up it should be).
With all three closed, a stranded row is inert wherever it is read; this verb reconciles the LABELS of the
rows already on disk, so the tallies and the state machine agree. READ-ONLY by default; `--apply` snapshots
first, then
applies exactly the cascade's rule: never-shipped (`Ledger._UNSHIPPED_POST_STATES`) -> `retired`;
anything that has touched a platform keeps its state, because that record is why preserve exists.
"""
from __future__ import annotations

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState


def stranded_posts(led: Ledger) -> list:
    """Every post whose clip OR parent moment is retired. Pure, lock-free read. Same predicate the
    approve guard applies in `actions_approve._approve_ids_with_render` — one rule, one meaning. The
    MOMENT check is the load-bearing one: a stranded post's clip is usually still `ClipState.queued`."""
    out = []
    for p in led.posts.values():
        clip = led.clips.get(p.parent_id)
        if clip is None:
            continue
        if led.is_retired_clip(clip.id) or led.is_retired_moment(clip.parent_id):
            out.append(p)
    return out


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
    for state, n in _tally(posts).items():
        verdict = "-> retired" if state in {s.value for s in unshipped} else "   kept (has touched a platform)"
        print(f"  {state:<18} {n:>5}   {verdict}")
    by_account: dict = {}
    for p in targets:
        by_account[p.account] = by_account.get(p.account, 0) + 1
    if by_account:
        print("would retire, by account:")
        for handle, n in sorted(by_account.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {handle:<24} {n:>5}")

    if not getattr(args, "apply", False):
        print(f"\ndry-run: {len(targets)} post(s) would be retired. Re-run with --apply to write.")
        return 0
    if not targets:
        print("\nnothing to reconcile.")
        return 0

    # Runs SAFELY against a live daemon — no stop, no refusal. The publisher claims a post by flipping
    # `queued`->`submitting` INSIDE a transaction and persisting it BEFORE any network call
    # (post/run.py, the F11 crash-safety fix), so both interleavings are already closed: if we win the
    # lock the post is `retired` and publish_due — which iterates `queued` only — never sees it; if the
    # publisher wins, the post is `submitting` and the re-read below skips it, because `submitting` is
    # not in _UNSHIPPED_POST_STATES. A heartbeat refusal here would only have made a human perform the
    # coordination the state machine already performs.
    # The S18 import is LAZY (apply-path only), so this module adds no compile-time edge to S18.
    from fanops.audit import write_audit

    snap = Ledger.snapshot(cfg)
    print(f"\nsnapshot: {snap}")
    done: list[str] = []
    with Ledger.transaction(cfg) as led2:
        for p0 in targets:
            p = led2.posts.get(p0.id)
            if p is None or p.state not in unshipped:
                continue                     # re-read under the lock: it moved between plan and apply
            led2.posts[p.id] = p.model_copy(update={"state": PostState.retired})
            done.append(p.id)
    write_audit(cfg, "reconcile_retired", done, reason="stranded_under_retired_lineage", retired=len(done))
    print(f"retired {len(done)} post(s). Rollback snapshot: {snap}")
    return 0
