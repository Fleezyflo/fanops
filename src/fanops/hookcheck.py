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
# dedup misses it because the strings differ. We key on the first two WORD tokens; once this many
# accepted hooks already share that opening, the next one reads like a bot and is rejected. Two
# tokens (not one) keeps precision high — many hooks may legitimately start 'the', few share 'the bar'.
# KEPT in v2: this is mechanical feed-HYGIENE (deterministic anti-repetition), not a quality judgment,
# and it is the ONLY code enforcing opening diversity (the editor's diversity mandate is prompt-prose).
_TEMPLATE_PREFIX_TOKENS = 2
_TEMPLATE_CLUSTER_MAX = 2                             # the (MAX+1)th hook sharing the opening is rejected

def _prefix_key(text: str) -> tuple:
    return tuple(re.findall(r"\w+", text.lower())[:_TEMPLATE_PREFIX_TOKENS])

def is_weak_hook(text: str | None, used: set[str] = frozenset()) -> bool:
    """True if `text` is a hook to REJECT on MECHANICAL grounds only (empty / exact-dup / opening
    cluster). `used` is the set of hooks already taken this run; a case/space-insensitive repeat OR an
    opening-template cluster is rejected to kill cross-feed repetition (the 'reads like a bot' tell).
    Hook QUALITY (generic, narration, hype) is judged by the reasoning critic, not here."""
    if not text or not text.strip():
        return True                                   # nothing to show
    low = text.strip().lower()
    if low in {u.strip().lower() for u in used}:
        return True                                   # duplicate of another clip's hook
    key = _prefix_key(low)
    if key and sum(1 for u in used if _prefix_key(u) == key) >= _TEMPLATE_CLUSTER_MAX:
        return True                                   # >=2 accepted hooks share this opening -> a template cluster
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
