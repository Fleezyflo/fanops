# src/fanops/variant_learning.py
"""Creative-variation v2: the SAFE half of the A/B loop. Pure, read-only scoring of which
per-account hook variant has earned a trustworthy win, so request_captions can bias the next
caption toward it. Touches NONE of amplify/classify_outcomes/_delete_moment_cascade (C1) — this
module must NEVER be imported by track.py/pipeline.py (the amplify/delete-cascade path stays
blind to the learner; enforced by an isolation grep test)."""
from __future__ import annotations
from statistics import mean
from fanops.models import Platform, PostState


def best_hooks(led, cfg, account: str, platform: Platform) -> list[str]:
    """Return the winning hook(s) for this (account, platform) surface IFF the leading variant has
    >= cfg.variant_min_posts analyzed posts carrying a lift_score AND its mean lift beats the
    runner-up's mean by >= cfg.variant_min_gap. Below either threshold -> [] (the noise guard: with
    a handful of posts, lift_score is statistically meaningless and acting on it amplifies noise).
    Pure function of ledger state: no I/O, no mutation, no random/hash/wall-clock -> a re-run yields
    the identical result (the content-addressed/determinism invariant)."""
    min_posts = cfg.variant_min_posts
    min_gap = cfg.variant_min_gap
    by_hook: dict[str, list[float]] = {}
    for p in led.posts.values():
        if (p.variant_key and p.variant_hook and p.account == account and p.platform is platform
                and p.state is PostState.analyzed and "lift_score" in p.metrics):
            by_hook.setdefault(p.variant_hook, []).append(float(p.metrics["lift_score"]))
    if not by_hook:
        return []
    ranked = sorted(by_hook.items(), key=lambda kv: mean(kv[1]), reverse=True)
    leader_hook, leader_lifts = ranked[0]
    if len(leader_lifts) < min_posts:
        return []
    # A winner must be COMPARATIVE: with no runner-up there is nothing to beat by min_gap, so a lone
    # high-performing variant is "still exploring", not a proven A/B winner. Returning it would bias
    # creative against an implicit zero AND collapse the per-account exploration that variation
    # exists to create. No runner-up -> [] (stricter than an implicit-zero baseline, on purpose).
    if len(ranked) < 2:
        return []
    runner_mean = mean(ranked[1][1])
    if mean(leader_lifts) - runner_mean < min_gap:
        return []
    return [leader_hook]
