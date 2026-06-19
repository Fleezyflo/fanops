# src/fanops/hookcheck.py
"""Deterministic MECHANICAL hook-hygiene floor (v2). It no longer judges hook QUALITY — that is the
reasoning critic's job (hookjudge.py). It rejects only the three things a regex can decide without
reading meaning:
  - an EMPTY hook (nothing to show)
  - an EXACT cross-clip duplicate (the same line burned twice)
  - an opening-TEMPLATE cluster (the 'before he was X' x6 / 'wait for the Y' x6 'reads like a bot' tell)
The semantic slop-regexes (superlative templates, 'cuts'/editing, shot-description, cliches) were
DELETED in v2: as regexes they both over-fire (kill a legible 'he names the day it changed') and
under-fire (miss third-person narration entirely), which is exactly why prompt-only quality capped at
~22%. Quality calls now belong to the always-on strict critic. A rejected hook becomes None -> a clean
clip (clean beats slop). Length is NOT gated; the prompt owns brevity."""
from __future__ import annotations
import re

# Opening-template clustering (the 'before he was X' x6 / 'wait for the Y' x6 tell): EXACT-string
# dedup misses it because the strings differ. We key on the first three WORD tokens; once this many
# accepted hooks already share that opening, the next one reads like a bot and is rejected.
# v2.1 TUNE (forensic: 6/51 corpus hooks were blanked exactly here): 2 tokens / max 2 OVER-fired — it
# nuked good distinct hooks that merely shared a 2-word opener ('you ever win…' killed because 'you ever'
# was already taken twice). THREE tokens / max 3 keeps real ×6 templates caught (they share 3+ opening
# words and recur >>3) while letting 'you ever X' diverge on the 3rd word. This is mechanical feed-
# HYGIENE (deterministic anti-repetition), not a quality judgment — the reasoning critic owns quality.
_TEMPLATE_PREFIX_TOKENS = 3
_TEMPLATE_CLUSTER_MAX = 3                             # the (MAX+1)th hook sharing the 3-word opening is rejected

def _prefix_key(text: str) -> tuple:
    return tuple(re.findall(r"\w+", text.lower())[:_TEMPLATE_PREFIX_TOKENS])

def is_weak_hook(text: str | None, used: set[str] = frozenset(), *, cluster_scope: set[str] | None = None) -> bool:
    """True if `text` is a hook to REJECT on MECHANICAL grounds only (empty / exact-dup / opening
    cluster). `used` is the FEED-WIDE set of hooks already taken (a case/space-insensitive exact repeat
    is rejected anywhere — burning the same line twice reads like a bot). `cluster_scope` is the
    opening-template scope: the caller's CURRENT decision batch (one source's picks / one edit run), so a
    'before he was X' x6 lazy batch is caught while the same opener recurring across DIFFERENT videos is
    NOT — feed-wide opener MONOTONY is a quality/diversity concern the prompt + critic own, not this
    mechanical floor. cluster_scope=None defaults to `used` (byte-identical to the single-set callers).
    Hook QUALITY (generic, narration, hype) is judged by the reasoning critic, not here."""
    if not text or not text.strip():
        return True                                   # nothing to show
    low = text.strip().lower()
    if low in {u.strip().lower() for u in used}:
        return True                                   # exact duplicate of another clip's hook (feed-wide)
    scope = used if cluster_scope is None else cluster_scope   # default: today's behavior; callers narrow to one batch
    key = _prefix_key(low)
    if key and sum(1 for u in scope if _prefix_key(u) == key) >= _TEMPLATE_CLUSTER_MAX:
        return True                                   # >=3 hooks in this DECISION share the opening -> a template cluster
    return False


# P1 hook-pattern provenance. The 6 proven retention formulas defined in prompts._hook_spec, as stable
# snake_case KEYS the responder/editor declare and P3/P4 group by. normalize_hook_pattern maps an LLM's
# label (any case/spacing/synonym) to a canonical key, or None when it is absent/unknown — so a bad
# label degrades to "unknown pattern" (validate-or-default) and never crashes an ingest.
HOOK_PATTERNS = ("open_loop", "curiosity", "comment_bait", "contrarian", "pov", "proof")
_PATTERN_ALIASES = {
    "open_loop": "open_loop", "openloop": "open_loop", "open": "open_loop", "loop": "open_loop",
    "payoff": "open_loop", "payoff_tease": "open_loop",
    "curiosity": "curiosity", "curiosity_gap": "curiosity", "gap": "curiosity",
    "comment_bait": "comment_bait", "comment": "comment_bait", "opinion": "comment_bait",
    "opinion_bait": "comment_bait", "comment_opinion": "comment_bait", "comment_opinion_bait": "comment_bait",
    "contrarian": "contrarian", "bold": "contrarian", "bold_claim": "contrarian", "contrarian_bold": "contrarian",
    "pov": "pov", "relatable": "pov", "pov_relatable": "pov",
    "proof": "proof", "stakes": "proof", "proof_stakes": "proof",
}

def normalize_hook_pattern(value) -> str | None:
    """Map an LLM-declared hook-pattern label to a canonical HOOK_PATTERNS key, or None if it is
    empty/non-string/unknown. Case-, space-, slash- and dash-insensitive (e.g. 'Open Loop',
    'curiosity-gap', 'POV / relatable' all resolve). Unknown labels -> None (never raises)."""
    if not isinstance(value, str) or not value.strip():
        return None
    key = re.sub(r"[\s/\-]+", "_", value.strip().lower())
    return _PATTERN_ALIASES.get(key)
