# src/fanops/origin_backfill.py
"""One-shot provenance RECONSTRUCTION for the moments minted before `Moment.origin` existed (MOL-756).

`MomentOrigin` (MOL-747) gives a moment a place to say who asked for it, but nothing writes it: every
row already on disk loads `unknown`. That is the honest value and it is also useless to the thing that
needs it — an origin-keyed review or purge keyed on `unknown` selects the whole corpus. This module
reconstructs the label for the one class the ledger CAN still identify, and it says so in the label it
writes.

THE INFERENCE, stated plainly so it can be argued with. `Moment` carries no timestamp; `Post.created_at`
is the only birth day in the lineage. A post born on one of the operator-named days is walked UP its
lineage (post -> clip -> moment) and its moment is labelled. That is a GUESS about provenance, licensed
only while the lineage is strictly 1:1:1 and self-contained: one post per selected clip, one clip per
selected moment, and no post from outside the window hanging off a selected clip. If any of those fails,
a labelled moment could be one that ALSO produced work from another era, and the label would be a lie.
So the survey measures each of them and the apply path REFUSES rather than adapting — see `_stops`.

WHY THE LABEL IS NOT A PARAMETER. Every row this module touches was reconstructed after the fact, never
observed at mint. `MomentOrigin.machine_inferred` is the enum member that says exactly that, and it is
what travels with the row into every later reader — so the destructive consumer (the purge) can weigh an
inferred label differently from an authored one. A caller-supplied origin would let an inference be
written as `machine` and pass for an authored fact; that is the one thing this module must not permit.
An already-authored row (`operator` / `machine`) is never overwritten, for the same reason.

DAYS ARE ARGUMENTS. The calendar days, and the expected size of the selection, are operator input on
every call. Nothing about the 2026-07/08 amplify burst is a literal in here: a number baked into a module
is a number that rots, and the next operator would have no way to see that it had.

Read-only by default. `apply` snapshots FIRST, then does the whole write in ONE `Ledger.transaction`,
re-measuring under the lock so a ledger that moved between the plan and the confirm refuses instead of
labelling something the operator never saw. Idempotent: an already-labelled row is counted, not rewritten.
"""
from __future__ import annotations

import hashlib
from collections import Counter

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import MomentOrigin

# The label this module writes. Fixed, never a parameter — see the module docstring.
BACKFILL_ORIGIN = MomentOrigin.machine_inferred
# Authored provenance: observed, not inferred. An inference may never overwrite one of these.
_AUTHORED = (MomentOrigin.operator, MomentOrigin.machine)


def survey_amplify_descended(led: Ledger, *, days: frozenset[str], expect_moments: int | None = None) -> dict:
    """Measure the day-selected lineage and every invariant that licenses labelling it. Pure, lock-free.

    Returns the whole measurement, never a verdict alone: a refusal that does not SHOW the numbers it
    refused on just sends the operator to a shell, which is the gap this ticket exists to close. The
    survey is the same call the plan renders and the apply path re-runs under the lock, so the operator
    can only ever confirm a plan that was measured the same way it is enforced.
    """
    hist: Counter = Counter((p.created_at or "")[:10] for p in led.posts.values())
    target_posts = [p for p in led.posts.values() if (p.created_at or "")[:10] in days]
    clip_ids: set[str] = set(); posts_per_clip: Counter = Counter(); skipped_orphan = 0
    for p in target_posts:
        if p.parent_id not in led.clips:
            skipped_orphan += 1; continue                  # a post whose clip is gone: counted, never guessed at
        clip_ids.add(p.parent_id); posts_per_clip[p.parent_id] += 1
    moment_ids: set[str] = set(); clips_per_moment: Counter = Counter()
    for cid in clip_ids:
        mid = led.clips[cid].parent_id
        if mid not in led.moments:
            skipped_orphan += 1; continue                  # a clip whose moment is gone: same
        moment_ids.add(mid); clips_per_moment[mid] += 1
    cross_linked = sum(1 for p in led.posts.values()
                       if p.parent_id in clip_ids and (p.created_at or "")[:10] not in days)
    unlabelled = sorted(m for m in moment_ids if led.moments[m].origin is MomentOrigin.unknown)
    already = sorted(m for m in moment_ids if led.moments[m].origin is BACKFILL_ORIGIN)
    authored = sorted(m for m in moment_ids if led.moments[m].origin in _AUTHORED)
    survey = {
        "days": sorted(days),
        "day_histogram": dict(sorted(hist.items())),       # invariant 1 is SHOWN, not asserted against a rotting literal
        "posts": len(target_posts), "clips": len(clip_ids), "moments": len(moment_ids),
        "by_source": dict(sorted(Counter(led.moments[m].parent_id for m in moment_ids).items())),
        "unlabelled": len(unlabelled), "already_labelled": len(already), "skipped_authored": len(authored),
        "skipped_orphan": skipped_orphan,
        "max_posts_per_clip": max(posts_per_clip.values(), default=0),
        "max_clips_per_moment": max(clips_per_moment.values(), default=0),
        "cross_linked_posts": cross_linked,
        "unlabelled_ids": unlabelled,
        "label": BACKFILL_ORIGIN.value,
        "corpus_unlabelled": sum(1 for m in led.moments.values() if m.origin is MomentOrigin.unknown),
        "corpus_moments": len(led.moments),
    }
    survey["plan_token"] = _plan_token(unlabelled)
    survey["stops"] = _stops(survey, expect_moments=expect_moments)
    return survey


def _plan_token(unlabelled_ids: list[str]) -> str:
    """Fingerprint of EXACTLY the rows a confirm would write, so "confirm" means "confirm what I was
    shown" and not merely "click again". The daemon keeps minting while an operator reads a plan; if the
    set moved, the confirm refuses and the operator re-plans."""
    h = hashlib.sha256(BACKFILL_ORIGIN.value.encode())
    for mid in unlabelled_ids:
        h.update(b"\n"); h.update(mid.encode())
    return h.hexdigest()[:16]


def _stops(s: dict, *, expect_moments: int | None) -> list[str]:
    """Every reason this ledger may NOT be labelled, each carrying the value it was measured at. Empty
    means the inference holds. These never adapt to what they find — that is the whole contract."""
    out: list[str] = []
    empty = [d for d in s["days"] if not s["day_histogram"].get(d)]
    if empty:
        out.append(f"day(s) with no post on this ledger: {empty} — check them against day_histogram {s['day_histogram']}")
    if not (s["posts"] == s["clips"] == s["moments"]):
        out.append(f"lineage is not 1:1:1: {s['posts']} post(s) -> {s['clips']} clip(s) -> {s['moments']} moment(s)")
    if s["max_posts_per_clip"] > 1:
        out.append(f"a selected clip carries {s['max_posts_per_clip']} posts (max must be 1)")
    if s["max_clips_per_moment"] > 1:
        out.append(f"a selected moment carries {s['max_clips_per_moment']} clips (max must be 1)")
    if s["cross_linked_posts"]:
        out.append(f"{s['cross_linked_posts']} post(s) from OUTSIDE the selected days hang off a selected clip")
    if s["skipped_orphan"]:
        out.append(f"{s['skipped_orphan']} selected row(s) have a missing ancestor — the lineage cannot be walked")
    if expect_moments is not None and s["moments"] != expect_moments:
        out.append(f"selection is {s['moments']} moment(s), operator expected {expect_moments}")
    return out


def backfill_origin(cfg: Config, *, days, apply: bool = False, expect_moments: int | None = None,
                    plan_token: str | None = None) -> dict:
    """Plan (default) or apply the reconstruction. THE one entry point — the CLI verb and the Studio
    button are both thin callers, so headless and cockpit can never diverge on what they mean.

    Plan: loads the ledger, surveys, writes nothing. Apply: refuses on any stop, then `Ledger.snapshot`
    FIRST (the rollback exists before the mutation does), then ONE `Ledger.transaction` that re-surveys
    under the lock and refuses again if the plan moved. `plan_token` is optional for headless parity and
    REQUIRED in spirit for an operator confirm: pass back the token the plan returned.
    """
    dayset = frozenset(days)
    survey = survey_amplify_descended(Ledger.load(cfg), days=dayset, expect_moments=expect_moments)
    report = {**survey, "applied": False, "labelled": 0, "snapshot": None, "refused": None}
    if not apply:
        return report
    if survey["stops"]:
        report["refused"] = "invariant"; return report                    # refuse BEFORE paying for a snapshot
    if plan_token is not None and plan_token != survey["plan_token"]:
        report["refused"] = "plan_moved"; return report
    snap = Ledger.snapshot(cfg)                                           # BEFORE the mutation — the ordering contract
    labelled: list[str] = []; refused: str | None = None; live = survey
    with Ledger.transaction(cfg) as led:
        live = survey_amplify_descended(led, days=dayset, expect_moments=expect_moments)   # authority: measured UNDER the lock
        if live["stops"]:
            refused = "invariant"
        elif plan_token is not None and plan_token != live["plan_token"]:
            refused = "plan_moved"
        else:
            for mid in live["unlabelled_ids"]:
                led.moments[mid] = led.moments[mid].model_copy(update={"origin": BACKFILL_ORIGIN})
                labelled.append(mid)
    from fanops.audit import write_audit                                  # lazy: the apply path only
    write_audit(cfg, "origin_backfill", [], reason="one_time_amplify_labelling", days=sorted(dayset),
                moments=live["moments"], labelled=len(labelled), refused=refused, snapshot=str(snap))
    return {**live, "applied": refused is None, "labelled": len(labelled), "snapshot": str(snap), "refused": refused}


_REPORT_KEYS = ("days", "day_histogram", "moments", "posts", "clips", "by_source", "unlabelled",
                "already_labelled", "skipped_authored", "skipped_orphan", "max_posts_per_clip",
                "max_clips_per_moment", "cross_linked_posts", "label", "plan_token", "stops",
                "applied", "labelled", "snapshot", "refused")


def cmd_origin_backfill(cfg: Config, args) -> int:
    """`fanops origin-backfill` — the headless twin of the Studio button. Kept so the same engine is
    reachable without a browser; the cockpit is the primary trigger (an operator must be able to SEE
    missing provenance, not have to know a verb exists). Every line routes through `get_logger`, so
    cli.py gains no `print(` and its exact-equality budget does not move."""
    from fanops.log import get_logger
    rep = backfill_origin(cfg, days=args.day, apply=bool(getattr(args, "apply", False)),
                          expect_moments=getattr(args, "expect_moments", None))
    log = get_logger(cfg)
    log("origin_backfill", "-", "report", level="error" if rep["stops"] else "info",
        **{k: rep[k] for k in _REPORT_KEYS})
    return 2 if (rep["stops"] or rep["refused"]) else 0
