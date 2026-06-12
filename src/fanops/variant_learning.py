# src/fanops/variant_learning.py
"""Creative-variation v2: the SAFE half of the A/B loop. Pure, read-only scoring of which
per-account hook variant has earned a trustworthy win, so request_captions can bias the next
caption toward it. Touches NONE of amplify/classify_outcomes/_delete_moment_cascade (C1) — this
module must NEVER be imported by track.py/pipeline.py (the amplify/delete-cascade path stays
blind to the learner; enforced by an isolation grep test)."""
from __future__ import annotations
from math import log, sqrt
from statistics import mean
from fanops.models import LIFT_SCORE, Platform, PostState


def _collect_lifts(led, account: str, platform: Platform) -> dict[str, list[float]]:
    """Group this (account, platform) surface's ANALYZED variant posts by hook -> their lift_scores.
    The single gather predicate both scorers share (so v2/v3 can never disagree on what data exists).
    An 'arm' only appears here once it has >= 1 analyzed post carrying a lift_score."""
    by_hook: dict[str, list[float]] = {}
    for p in led.posts.values():
        if (p.variant_key and p.variant_hook and p.account == account and p.platform is platform
                and p.state is PostState.analyzed and LIFT_SCORE in p.metrics):
            by_hook.setdefault(p.variant_hook, []).append(float(p.metrics[LIFT_SCORE]))
    return by_hook


def best_hooks(led, cfg, account: str, platform: Platform) -> list[str]:
    """Return the winning hook(s) for this (account, platform) surface IFF the leading variant has
    >= cfg.variant_min_posts analyzed posts carrying a lift_score AND its mean lift beats the
    runner-up's mean by >= cfg.variant_min_gap. Below either threshold -> [] (the noise guard: with
    a handful of posts, lift_score is statistically meaningless and acting on it amplifies noise).
    Pure function of ledger state: no I/O, no mutation, no random/hash/wall-clock -> a re-run yields
    the identical result (the content-addressed/determinism invariant)."""
    min_posts = cfg.variant_min_posts
    min_gap = cfg.variant_min_gap
    by_hook = _collect_lifts(led, account, platform)
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


def ucb_rank(led, cfg, account: str, platform: Platform) -> list[str]:
    """v3 deterministic UCB1 bandit over this surface's OWN hook arms. For each arm: score =
    mean_lift + c*sqrt(ln N / n), where n = that arm's analyzed-post count, N = total across arms,
    c = cfg.variant_ucb_c (default sqrt 2). Returns [argmax score]; ties on equal score broken by the
    sorted-lower hook string (deterministic — never insertion order). N == 0 -> []. Every arm has
    n >= 1 by construction (_collect_lifts only yields hooks with an analyzed post), so ln N / n is
    always defined (N == 1 -> ln 1 = 0 -> bonus 0 -> bare mean). No random/hash/clock — a re-run is
    byte-identical. Balances exploiting proven hooks against exploring under-sampled ones; never
    silent once any variant data exists (the v2 weakness this replaces). Does NOT touch amplify (C1):
    only caption.py/digest.py may call this."""
    by_hook = _collect_lifts(led, account, platform)
    if not by_hook:
        return []
    total = sum(len(lifts) for lifts in by_hook.values())   # N >= 1 here
    c = cfg.variant_ucb_c
    ln_n = log(total)                                       # total >= 1 -> ln defined (ln 1 = 0)
    scored = sorted(
        ((hook, mean(lifts) + c * sqrt(ln_n / len(lifts))) for hook, lifts in by_hook.items()),
        key=lambda hs: hs[0],                              # stable, sorted by hook string
    )
    best_hook = max(scored, key=lambda hs: hs[1])[0]
    return [best_hook]
