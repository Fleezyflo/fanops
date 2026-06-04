# src/fanops/variant_amplify.py
"""Creative-variation v3: variant-gated AMPLIFICATION — the first feature to touch the amplify path
(audit C1). When a per-account hook variant has earned a SUSTAINED, well-evidenced win, it authorizes
an extra amplify of that win's source (the existing adjust.amplify), carrying the winning hook into
the moment-request guidance. Gated FAR harder than v2: variant_learning.best_hooks as a FLOOR, plus
more posts + a bigger gap + a sustained lead across >= cfg.variant_amplify_min_streak DISTINCT
evidence windows. Default OFF (FANOPS_VARIANT_AMPLIFY).

SAFETY (the whole point): this module is AMPLIFY-ONLY. It must NEVER import or call retire /
_delete_moment_cascade / retire_clip / set_moment_state / set_clip_state. A candidate failing the
gate is simply not amplified (it is NOT retired). On ANY doubt the actuator does nothing and leaves
the ledger byte-identical (fail-SAFE). Deterministic: no random/hash()/wall-clock; the streak
fingerprint is content-addressed via ids._hash, so a re-run on the same ledger is idempotent.
Enforced by the retire-isolation AST test + the mutation-proof + wrong-signal no-op tests in
tests/test_variant_amplify.py."""
from __future__ import annotations
from fanops.ids import _hash
from fanops.models import Platform, PostState
from fanops.variant_learning import best_hooks


def _surfaces(led) -> set[tuple[str, Platform]]:
    """Distinct (account, platform) surfaces that have at least one analyzed variant post — derived
    purely from the ledger (no Accounts dependency), matching how best_hooks scopes per surface."""
    return {(p.account, p.platform) for p in led.posts.values()
            if p.variant_key and p.variant_hook and p.state is PostState.analyzed
            and "lift_score" in p.metrics}


def _evidence_fingerprint(led, account: str, platform: Platform) -> str:
    """A content-addressed digest of the SORTED analyzed post-ids for this surface. A NEW analyzed
    post changes it -> a new 'window'. Deterministic (ids._hash, no wall-clock/random)."""
    pids = sorted(p.id for p in led.posts.values()
                  if p.account == account and p.platform is platform
                  and p.state is PostState.analyzed and "lift_score" in p.metrics)
    return _hash("variant_streak", account, platform.value, *pids)


def update_streaks(led, cfg):
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
    return led
