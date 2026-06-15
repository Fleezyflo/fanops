# src/fanops/variant_amplify.py
"""Creative-variation v3: variant-gated AMPLIFICATION — the first feature to touch the amplify path
(audit C1). When a per-account hook variant has earned a SUSTAINED, well-evidenced win, it authorizes
an extra amplify of that win's source (the existing adjust.amplify), carrying the winning hook into
the moment-request guidance. Gated FAR harder than v2: variant_learning.best_hooks as a FLOOR, plus
more posts + a bigger gap + a sustained lead across >= cfg.variant_amplify_min_streak DISTINCT
evidence windows. Default OFF (FANOPS_VARIANT_AMPLIFY).

SAFETY (the whole point): this module is AMPLIFY-ONLY. It must NEVER import or call retire /
_delete_moment_cascade / retire_clip / set_moment_state / set_clip_state. A candidate failing the
gate is simply not amplified (it is not retired as a consequence). On ANY doubt the actuator does
nothing and leaves the ledger CONTENT byte-identical (fail-SAFE; the streak counters do advance —
that is the feature). Deterministic: no random/hash()/wall-clock; the streak fingerprint is
content-addressed via ids._hash, so a re-run on the same ledger is idempotent.

PRECISE guarantee (do NOT overclaim — the false-safety-contract lesson, audit I1): the GUARANTEE
that holds STRUCTURALLY is "no wrong signal can ever delete or unpublish a LIVE (published/analyzed/
submitted/submitting/needs_reconcile) post or clip" — enforced by ledger._delete_moment_cascade's
_LIVE_*_STATES preservation (the C1 fix), which v3 does not change, PLUS this module never itself
retiring/deleting (AST-proven). What v3 does NOT promise: the amplify it TRIGGERS reuses the existing
adjust.amplify -> reconcile cascade, so once the agent re-decides the source's moments, the already-
posted winning MOMENT is set to `retired` (its live post/clip are preserved) and any NON-LIVE sibling
lineage (e.g. a merely-`rendered` clip / `queued` post) on that source is reconciled away — EXACTLY
as the v1/v2 classify->amplify learn-loop already does on weaker (single-snapshot) evidence. v3 adds
a HARDER-gated trigger to that pre-existing path, not a new deletion path. Enforced by the
retire-isolation AST test (mutation-proven against helper/getattr/alias evasions) + the mutation-
proven streak gate + wrong-signal no-op tests + the live-preservation assertion, all in
tests/test_variant_amplify.py."""
from __future__ import annotations
from statistics import mean
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.adjust import amplify, MAX_AMPLIFY_PER_SOURCE   # AMPLIFY-ONLY: import amplify (+ the shared
#                                          E1 budget constant), NEVER retire (C1 / G1)
from fanops.ids import _hash
from fanops.log import get_logger
from fanops.models import LIFT_SCORE, Platform, PostState
from fanops.validation_gate import learning_validated
from fanops.variant_learning import best_hooks


def _surfaces(led) -> set[tuple[str, Platform]]:
    """Distinct (account, platform) surfaces that have at least one analyzed variant post — derived
    purely from the ledger (no Accounts dependency), matching how best_hooks scopes per surface."""
    return {(p.account, p.platform) for p in led.posts.values()
            if p.variant_key and p.variant_hook and p.state is PostState.analyzed
            and LIFT_SCORE in p.metrics}


def _evidence_fingerprint(led, account: str, platform: Platform) -> str:
    """A content-addressed digest of the SORTED analyzed post-ids for this surface. A NEW analyzed
    post changes it -> a new 'window'. Deterministic (ids._hash, no wall-clock/random)."""
    pids = sorted(p.id for p in led.posts.values()
                  if p.account == account and p.platform is platform
                  and p.state is PostState.analyzed and LIFT_SCORE in p.metrics)
    return _hash("variant_streak", account, platform.value, *pids)


def update_streaks(led: Ledger, cfg: Config) -> None:
    """Advance/reset the per-surface sustained-win streak. Deterministic + idempotent on unchanged
    evidence. This is the ONLY state-mutating helper in this module, and it mutates ONLY
    led.variant_streaks (never a unit's state, never the amplify/retire path)."""
    for account, platform in _surfaces(led):
        key = f"{account}|{platform.value}"
        winners = best_hooks(led, cfg, account, platform)   # v2 gate (the FLOOR)
        prior = led.variant_streaks.get(key)
        if not winners:
            # No trustworthy winner now -> doubt resets the streak (fail-SAFE).
            if prior is None or prior.get("streak", 0) != 0:
                led.variant_streaks[key] = {"hook": None, "fingerprint": "", "streak": 0}
            continue
        winner = winners[0]
        fp = _evidence_fingerprint(led, account, platform)
        if prior is None or prior.get("hook") != winner:
            led.variant_streaks[key] = {"hook": winner, "fingerprint": fp, "streak": 1}
        elif prior.get("fingerprint") != fp:
            # Same winner, NEW evidence batch (a real new window) -> advance.
            led.variant_streaks[key] = {"hook": winner, "fingerprint": fp,
                                        "streak": int(prior.get("streak", 0)) + 1}
        # else: same winner, SAME evidence -> no change (idempotent re-run).
    # NB: returns None (the `-> None` annotation) — the mutation propagates IN PLACE on led.variant_
    # streaks; the sole caller (apply_variant_amplify) ignores the return, so the old `return led`
    # was dead. Behavior is identical.


def _source_for_surface(led, account: str, platform: Platform, hook: str):
    """Map a (surface, winning hook) to ONE source + a representative post (spec's deterministic
    source-mapping rule). The winning hook's analyzed posts may trace to several sources; pick the
    source with the MOST such posts (best-evidenced), ties broken by the lexicographically-lowest
    source_id (source ids are content-addressed `source_<hash>` strings, so lexicographic order is
    stable + deterministic — there is no numeric ordering to honor); the representative post_id is the
    lexicographically-lowest post_id among that source's winning-hook posts. Returns (source_id,
    post_id) or (None, None) if the lineage can't be resolved."""
    by_source: dict[str, list[str]] = {}
    for p in led.posts.values():
        if not (p.account == account and p.platform is platform and p.variant_hook == hook
                and p.state is PostState.analyzed and LIFT_SCORE in p.metrics):
            continue
        clip = led.clips.get(p.parent_id)
        moment = led.moments.get(clip.parent_id) if clip else None
        src = led.sources.get(moment.parent_id) if moment else None
        if src is None:
            continue
        by_source.setdefault(src.id, []).append(p.id)
    if not by_source:
        return None, None
    # most posts, then lowest source_id (lexicographic) — fully deterministic.
    source_id = min(by_source, key=lambda sid: (-len(by_source[sid]), sid))
    post_id = min(by_source[source_id])
    return source_id, post_id


def amplify_candidates(led, cfg) -> list[dict]:
    """Pure, read-only. Return the list of {source_id, winning_hook, post_id, evidence} to amplify —
    one per surface that clears the FULL gate (best_hooks floor + min_posts + min_gap + min_streak +
    E1 budget). [] on any doubt. No I/O, no mutation."""
    out: list[dict] = []
    for account, platform in sorted(_surfaces(led), key=lambda s: (s[0], s[1].value)):
        winners = best_hooks(led, cfg, account, platform)        # FLOOR (v2 gate)
        if not winners:
            continue
        hook = winners[0]
        # Re-derive the winner's posts/lifts on this surface for the v3 stronger thresholds.
        lifts = [float(p.metrics[LIFT_SCORE]) for p in led.posts.values()
                 if p.account == account and p.platform is platform and p.variant_hook == hook
                 and p.state is PostState.analyzed and LIFT_SCORE in p.metrics]
        if len(lifts) < cfg.variant_amplify_min_posts:
            continue
        # runner-up mean among OTHER hooks on this surface (best_hooks already guaranteed >= 2 hooks).
        others: dict[str, list[float]] = {}
        for p in led.posts.values():
            if (p.account == account and p.platform is platform and p.variant_hook
                    and p.variant_hook != hook and p.state is PostState.analyzed
                    and LIFT_SCORE in p.metrics):
                others.setdefault(p.variant_hook, []).append(float(p.metrics[LIFT_SCORE]))
        runner_mean = max((mean(v) for v in others.values()), default=0.0)
        if mean(lifts) - runner_mean < cfg.variant_amplify_min_gap:
            continue
        entry = led.variant_streaks.get(f"{account}|{platform.value}", {})
        if entry.get("hook") != hook or int(entry.get("streak", 0)) < cfg.variant_amplify_min_streak:
            continue                                             # single-window guard
        source_id, post_id = _source_for_surface(led, account, platform, hook)
        if source_id is None:
            continue
        if int(led.sources[source_id].meta.get("amplify_count", 0)) >= MAX_AMPLIFY_PER_SOURCE:
            continue                                              # E1 budget (shared constant; no drift)
        out.append({"source_id": source_id, "winning_hook": hook, "post_id": post_id,
                    "evidence": {"posts": len(lifts), "streak": int(entry.get("streak", 0))}})
    return out


def apply_variant_amplify(led: Ledger, cfg: Config) -> Ledger:
    """Actuator. Update streaks, then amplify each fully-gated candidate's source — injecting the
    winning hook as extra guidance. AMPLIFY-ONLY: never calls retire/_delete_moment_cascade. FAIL-SAFE:
    any exception -> log once, NO partial mutation beyond what already committed, return led. The
    caller (cli.run / cmd_amplify_variants) holds the transaction; an uncaught raise there would roll
    back, but we swallow here so an autonomous run never even sees it. Inert when the kill switch
    (FANOPS_VARIANT_AMPLIFY) is off — the default."""
    if not cfg.variant_amplify:
        return led                                  # kill switch / default OFF -> inert
    if not learning_validated(cfg):
        # OFF-until-proven (Phase 2): the kill switch is ON but no real metrics row has confirmed
        # lift_score's field shape (run `fanops cutover metrics`). Amplifying would re-mine sources
        # off a possibly-meaningless lift — stay inert, but LOG the why (not silent) so the operator
        # knows it's gated on validation, not on a weak signal.
        get_logger(cfg)("variant_amplify", "-", "skipped_unvalidated",
                        hint="run `fanops cutover metrics` to confirm lift fields")
        return led
    try:
        update_streaks(led, cfg)
        for cand in amplify_candidates(led, cfg):
            hint = (f"Recent on-screen hooks that performed best here: '{cand['winning_hook']}'. "
                    f"Lean toward this STYLE (tone, length, angle) — do not copy verbatim.")
            amplify(led, cfg, [cand["post_id"]], extra_guidance=hint)   # the existing C1-fixed path
    except Exception as e:
        # FAIL-SAFE, not fail-silent: record WHY so a run that quietly stops amplifying is
        # distinguishable from one with nothing to amplify (FIX F51's whole point).
        get_logger(cfg)("variant_amplify", "-", "error", err=str(e)[:120])
    return led
