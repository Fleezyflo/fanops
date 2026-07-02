# src/fanops/moment_hook_learning.py
"""P4(c) — proven hook STYLES into the moment (vision hook AUTHOR) prompt. The caption loop already
biases captions toward the gated winning hooks (caption._learned_hooks -> caption_prompt); this is the
missing read that carries the SAME cross-surface union of gated winners UP to moment_prompt, so the
vision author (not only the caption writer) leans toward what has worked.

SAFE READ side, exactly like variant_learning / caption._learned_hooks: pure, no mutation, NO amplify /
retire / cascade. Reuses the SAME scorer selection as caption.py:145 (ucb_rank if cfg.variant_ucb else
best_hooks) so the moment-side and caption-side never disagree on what won. Master-gated on
FANOPS_VARIANT_LEARNING (the learning master switch) AND FANOPS_MOMENT_HOOK_LEARNING; default OFF.
FAIL-OPEN: any error -> [] (logged once) so a learning hiccup can never block a moment request.

NB (C1): like caption.py, this module invokes the scorer via the `scorer = ucb_rank if ... else best_hooks`
indirection, so it is NOT a literal `best_hooks(`/`ucb_rank(` caller (the AST isolation locks detect literal
calls). It is nonetheless listed in BOTH allowed-sets (test_variant_learning.py) as a documented, safe,
request-side invocation site — the same treatment caption.py gets. moments.py/pipeline.py call THIS wrapper,
never the scorers, so they stay out of the danger sets. Safety here is by construction: a pure read that
returns [] on any doubt, never reaching adjust/ledger's amplify/retire/cascade path."""
from __future__ import annotations
from fanops.log import get_logger
from fanops.variant_learning import best_hooks, ucb_rank
from fanops.hookscore import narration_signature


def proven_hook_styles(led, cfg, accounts) -> list[str]:
    """Read-only cross-surface union of each ACTIVE (account, platform) surface's gated winning hook
    style(s). Ordered, de-duplicated (insertion order -> deterministic). [] when the master flag or the
    moment-hook flag is off, when accounts is None, or on any scorer error (fail-open).

    RF5 (viewer-POV at the source): each winner is filtered through the read-only viewer-POV METER
    (narration_signature) BEFORE it can prime the hook author — a historically-winning-but-third-person
    hook is a poisoned example that would re-teach the generator the exact anti-pattern we starve
    everywhere else, so it is DROPPED here (not injected). The meter still gates NOTHING downstream."""
    if not cfg.variant_learning or not cfg.moment_hook_learning or accounts is None:
        return []
    try:
        scorer = ucb_rank if cfg.variant_ucb else best_hooks   # match caption.py:145 (v3 bandit vs v2 greedy)
        seen: set[str] = set()
        out: list[str] = []
        for a in accounts.active():
            for plat in a.platforms:
                for h in scorer(led, cfg, a.handle, plat):
                    if h not in seen and not narration_signature(h):   # drop a third-person winner (poisoned prime)
                        seen.add(h)
                        out.append(h)
        return out
    except Exception:
        get_logger(cfg)("moment_hook_learning", "-", "error")   # fail-OPEN, not silent
        return []
