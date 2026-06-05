# src/fanops/variant_transfer.py
"""Cross-account / cross-surface learning transfer (the v2 follow-up). transferred_hooks() proposes
a SAME-PLATFORM hook STYLE proven on multiple OTHER surfaces as a weak prior for a COLD recipient
surface (one with no trustworthy winner of its own). It reuses variant_learning.best_hooks (v2's
gate) on every donor and on the recipient — never re-implementing or loosening it — and adds a
STRICTER cross-donor gate on top. Pure, read-only, deterministic (no random/hash/wall-clock).

SAFETY (C1): like variant_learning, this module must NEVER be imported/called by the amplify/
delete-cascade path (track.py/pipeline.py/adjust.py/ledger.py). It biases the caption REQUEST only;
the amplify path stays blind to it (enforced by the isolation tests in tests/test_variant_learning.py)."""
from __future__ import annotations
from fanops.models import Platform
from fanops.variant_learning import best_hooks


def _persona_tokens(persona: str | None) -> set[str]:
    """Lowercased word-set for deterministic persona overlap. None/empty -> empty set."""
    return set((persona or "").lower().split())


def transferred_hooks(led, cfg, accounts, account: str, platform: Platform) -> list[str]:
    """Borrowed hook STYLE(s) for a COLD (account, platform) recipient, or [] when transfer should
    not fire. Rules (all from the spec, in order):
      0. accounts is None  -> [] (no sibling registry -> nothing to borrow; keeps the new caption
         signature backward-compatible).
      1. own-wins: if best_hooks(led,cfg,account,platform) is non-empty, the recipient already has
         its OWN trustworthy winner -> [] (transfer only fills the cold-start gap; never overrides
         a surface's own proven style — the anti-homogenization core).
      2. donors: every OTHER active surface on the SAME platform (recipient excluded). Each donor's
         winner comes from best_hooks (v2 gate reused verbatim). Tally, per winning hook, the SET of
         distinct donor handles that won it.
      3. cross-donor gate (stricter than v2): keep only hooks won on >= cfg.variant_transfer_min_donors
         distinct donor surfaces.
      4. defensive dedupe: drop any kept hook the recipient itself already won (it can't, given rule
         1 returned [] for it, but this stays correct if rule 1 ever changes).
      5. persona rank: order survivors by (persona token-overlap with the recipient DESC, donor
         count DESC, hook string ASC) — fully deterministic — then cap at cfg.variant_transfer_max_hooks.
    """
    if accounts is None:
        return []
    # rule 1 — own winner wins.
    if best_hooks(led, cfg, account, platform):
        return []

    recipient_persona = None
    donor_personas: dict[str, set[str]] = {}        # handle -> persona tokens
    donor_handles: list[str] = []
    for acct in accounts.active():
        if platform not in acct.platforms:
            continue                                # rule 2 — SAME platform only
        if acct.handle == account:
            recipient_persona = _persona_tokens(acct.persona)
            continue                                # rule 2 — recipient is not its own donor
        donor_handles.append(acct.handle)
        donor_personas[acct.handle] = _persona_tokens(acct.persona)
    if recipient_persona is None:
        recipient_persona = set()

    # rule 2/3 — per winning hook, the set of distinct donor surfaces that won it.
    winners_by_hook: dict[str, set[str]] = {}
    for handle in donor_handles:
        for hook in best_hooks(led, cfg, handle, platform):     # v2 gate on each donor
            winners_by_hook.setdefault(hook, set()).add(handle)

    min_donors = cfg.variant_transfer_min_donors
    qualified = {h: donors for h, donors in winners_by_hook.items() if len(donors) >= min_donors}
    if not qualified:
        return []

    # rule 5 — deterministic persona-aware ranking. For each qualifying hook, its persona score is
    # the BEST token-overlap among the donors that won it (a hook is "close" if any close donor won
    # it). Ties broken by donor count desc, then hook string asc.
    def _score(item: tuple[str, set[str]]) -> tuple[int, int, str]:
        hook, donors = item
        best_overlap = max((len(recipient_persona & donor_personas[d]) for d in donors), default=0)
        return (-best_overlap, -len(donors), hook)

    ordered = sorted(qualified.items(), key=_score)
    max_hooks = cfg.variant_transfer_max_hooks
    return [hook for hook, _ in ordered[:max_hooks]]
