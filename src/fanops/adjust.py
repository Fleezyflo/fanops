"""Adjust stage: rank ANALYZED posts that have a real lift_score (FIX F22 — failed posts have
none and are excluded). AMPLIFY = re-open a moment request on the winner's SOURCE, injecting
the winning moment's signature as guidance; write_request auto-invalidates the stale response
(Task 10) so ingest_moments answers fresh and reconciles (Task 11) — v1's amplify silently
no-opped. AMPLIFY is capped per source at MAX_AMPLIFY_PER_SOURCE (default 3) so an autonomous loop
can't grow one source endlessly. RETIRE = ledger.retire_clip, which clip/crosspost honor (FIX F55)."""
from __future__ import annotations
from statistics import median
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import LIFT_SCORE, MomentState, PostState
from fanops.moments import request_moments
from fanops.accounts import Accounts

# E1 per-source amplification budget — the single source of truth for the cap, shared by amplify()'s
# default AND variant_amplify.amplify_candidates' pre-check (so the two can never drift apart).
MAX_AMPLIFY_PER_SOURCE = 3

# RETIREMENT SIGNAL floor — mirrors validation_gate._MIN_ATTRIBUTED_N / _MIN_VALUES (and
# variant_learning.variant_min_posts=8), the thresholds every SIBLING actuator already carries and
# this one did not. Retirement is the destructive half of the learn pass, and a rank over a handful
# of scored posts is noise: at n=5 the old code retired a post on a single data point. A pool whose
# scores are all IDENTICAL is worse than thin — it carries no ranking at all, so "the bottom slice"
# degenerates to insertion order. Below either floor: NO losers. WINNERS are deliberately untouched
# by both — amplify only mints work, so acting early there costs an extra moment request, not state.
_MIN_SCORED_N = 8
_MIN_DISTINCT_SCORES = 2

# The floor that actually BINDS, as a fraction of the pool's own median. lift_score is an UNBOUNDED
# weighted sum (track._W — reach alone contributes 0.001 per impression), so a fixed constant can
# only express "low" for one audience size: the shipped lift_floor=20.0 is >6x the highest score this
# system has ever produced, so it excluded nobody and the bottom retire_pct slice retired WHOLE on
# every tick. A ratio is scale-free — it stays true as the account grows — and, unlike a rank, it can
# bind on NOBODY: a pool whose worst posts sit near its middle now retires nothing, which "the bottom
# 20%" could never express. Median, not mean, so one viral outlier cannot drag the floor up under
# everything below it.
_RETIRE_LIFT_RATIO = 0.25

def classify_outcomes(led: Ledger, *, winner_pct: float = 0.3, retire_pct: float = 0.2,
                      lift_floor: float = 20.0, per_surface: bool = False) -> dict:
    # Rank ANALYZED posts that carry a real lift_score (failed posts have none — FIX F22).
    analyzed = [p for p in led.posts.values()
                if p.state is PostState.analyzed and LIFT_SCORE in p.metrics]
    if not analyzed:
        return {"winners": [], "losers": []}
    ranked = sorted(analyzed, key=lambda p: p.metrics.get(LIFT_SCORE, 0.0), reverse=True)
    n = len(ranked)
    # WINNERS. Default (per_surface=False): the global top winner_pct — byte-identical to today. P4(a)
    # (per_surface=True): rank WITHIN each (account, platform) surface so a small account's best can win
    # on its OWN pool instead of being crowded out by a big account; UNION the per-bucket winners. Bucket
    # order is sorted for determinism (R6). The LOSER side below stays GLOBAL in both modes (D1) — only
    # the win_set guard differs, so per_surface never re-scopes (buckets) retirement, it only PROTECTS the
    # per-surface winners from being retired (a small account's best is never amplified AND retired at once).
    if per_surface:
        buckets: dict[tuple, list] = {}
        for p in analyzed:
            buckets.setdefault((p.account, p.platform.value), []).append(p)
        winners: list[str] = []
        for key in sorted(buckets):
            rb = sorted(buckets[key], key=lambda p: p.metrics.get(LIFT_SCORE, 0.0), reverse=True)
            winners.extend(p.id for p in rb[:max(1, round(len(rb) * winner_pct))])
    else:
        winners = [p.id for p in ranked[:max(1, round(n * winner_pct))]]
    # RETIREMENT — GLOBAL in both modes (no per-surface loser bucketing, D1). A winner (whichever
    # mode's set) is NEVER also a loser (stage-6 audit): with operator-raised pcts summing past 1 the
    # slices overlapped — one post amplified AND retired in the same pass; the win_set guard prevents
    # that for the ACTIVE winners.
    scores = [p.metrics.get(LIFT_SCORE, 0.0) for p in ranked]
    if n < _MIN_SCORED_N or len(set(scores)) < _MIN_DISTINCT_SCORES:
        return {"winners": winners, "losers": []}   # too thin / no ranking at all -> retire NOTHING
    # The floor is the TRIGGER; the bottom retire_pct slice is only a CAP on how many one pass may
    # take. That ordering is the whole point: `round(n * retire_pct)` over a bottom slice always names
    # somebody, so as long as rank decided retirement the pass could never retire nothing — it was a
    # ratchet, not a signal. `lift_floor` survives as the operator's ADDITIONAL ceiling (min(), so it
    # can only ever make a pass MORE conservative — an operator raising it can no longer force the
    # whole slice out, which is exactly what the 20.0 default was silently doing every tick).
    floor = min(lift_floor, median(scores) * _RETIRE_LIFT_RATIO)
    lose_n = round(n * retire_pct)
    bottom = ranked[n - lose_n:] if lose_n > 0 else []
    win_set = set(winners)
    losers = [p.id for p in bottom
              if p.id not in win_set and p.metrics.get(LIFT_SCORE, 0.0) < floor
              and not p.metrics.get("lift_degraded")]
    return {"winners": winners, "losers": losers}

def amplify(led: Ledger, cfg: Config, winner_post_ids: list[str], *,
            max_amplify_per_source: int = MAX_AMPLIFY_PER_SOURCE, extra_guidance: str = "") -> Ledger:
    for pid in winner_post_ids:
        post = led.posts.get(pid)
        if post is None:
            continue
        clip = led.clips.get(post.parent_id)
        moment = led.moments.get(clip.parent_id) if clip else None
        src = led.sources.get(moment.parent_id) if moment else None
        if not src:
            continue
        # E1: per-source amplification budget. A MISSING key defaults to 0 (sources without the
        # count keep amplifying until they hit the cap). At/over the cap, skip the source entirely
        # — no write_request, no state flip — so an autonomous LLM can't grow one source endlessly.
        used = int(src.meta.get("amplify_count", 0))
        if used >= max_amplify_per_source:
            continue
        guidance = (f"AMPLIFY: a moment like '{moment.transcript_excerpt}' ({moment.reason}) "
                    f"hit hard (lift={post.metrics.get(LIFT_SCORE)}). Find MORE moments in that "
                    f"vein in this source — do not repeat the same timestamps.")
        if extra_guidance:
            guidance += f" {extra_guidance}"
        accts = Accounts.load(cfg)
        led = request_moments(led, cfg, src.id, accounts=accts, guidance=guidance)
        src.meta["amplify_count"] = used + 1
    return led

def retire(led: Ledger, loser_post_ids: list[str]) -> Ledger:
    for pid in loser_post_ids:
        post = led.posts.get(pid)
        if post is None:
            continue
        led.retire_clip(post.parent_id)                 # suppress this clip (FIX F55)
        clip = led.clips.get(post.parent_id)
        if clip is not None:
            # If no sibling clip of this moment is still live, retire the MOMENT too — else
            # clip.py's render guard (which checks moment state) would re-render it into a
            # fresh live clip on a later pass, silently undoing the retirement.
            # Deliberately the INTRINSIC predicate, not the derived `is_suppressed`: this asks whether a SIBLING
            # is retired IN ITSELF, to decide whether to retire the moment. Deriving here is circular.
            live_sibs = [c for c in led.clips_of(clip.parent_id) if not led.is_retired_clip(c.id)]
            if not live_sibs:
                led.set_moment_state(clip.parent_id, MomentState.retired)
            # ...and carry that retirement DOWN to the never-shipped posts — the same rule
            # _delete_moment_cascade applies to a DROPPED moment (ledger.py), for the same reason.
            # retire_clip / set_moment_state are plain no-cascade state flips (canary depends on that,
            # canary.py:942), so this path suppressed the lineage and left its awaiting_approval/queued
            # posts behind: invisible to review_buckets, refused by every actionable reader, and still
            # counted in every raw state census — a phantom backlog that never drains. Anything that has
            # TOUCHED a platform keeps its state; that record is why preserve exists. When the MOMENT
            # went too, sweep all of its clips so a sibling retired by an older pass (before this cascade
            # existed) is healed as well. Idempotent: re-running retires nothing new.
            for c in (led.clips_of(clip.parent_id) if not live_sibs else [clip]):
                for p in led.posts_of(c.id):
                    if p.state in Ledger._UNSHIPPED_POST_STATES:
                        led.posts[p.id] = p.model_copy(update={"state": PostState.retired})
    return led
