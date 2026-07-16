# src/fanops/hookcheck.py
"""Deterministic MECHANICAL hook-hygiene floor (v2). It does not judge hook QUALITY — that is the
vision author's job (the author writes the hook seeing the footage). This floor is the ONLY gate after
the author; it rejects only the three things a regex can decide without reading meaning:
  - an EMPTY hook (nothing to show)
  - an EXACT cross-clip duplicate (the same line burned twice)
  - an opening-TEMPLATE cluster (the 'before he was X' x6 / 'wait for the Y' x6 'reads like a bot' tell)
The semantic slop-regexes (superlative templates, 'cuts'/editing, shot-description, cliches) were
DELETED in v2: as regexes they both over-fire (kill a legible 'he names the day it changed') and
under-fire (miss third-person narration entirely), which is exactly why prompt-only quality capped at
~22%. Quality is the AUTHOR's alone: the editor+critic cascade this line once named was DELETED (#72) —
no judge survives it. A rejected hook becomes None -> a clean clip (clean beats slop). Length is NOT gated; the prompt owns brevity."""
from __future__ import annotations
import re

# Opening-template clustering (the 'before he was X' x6 / 'wait for the Y' x6 tell): EXACT-string
# dedup misses it because the strings differ. We key on the first three WORD tokens; once this many
# accepted hooks already share that opening, the next one reads like a bot and is rejected.
# v2.1 TUNE (forensic: 6/51 corpus hooks were blanked exactly here): 2 tokens / max 2 OVER-fired — it
# nuked good distinct hooks that merely shared a 2-word opener ('you ever win…' killed because 'you ever'
# was already taken twice). THREE tokens / max 3 keeps real ×6 templates caught (they share 3+ opening
# words and recur >>3) while letting 'you ever X' diverge on the 3rd word. This is mechanical feed-
# HYGIENE (deterministic anti-repetition), not a quality judgment — the vision author owns quality.
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
    NOT — feed-wide opener MONOTONY is a quality/diversity concern the vision author (the prompt) owns,
    not this mechanical floor. cluster_scope=None defaults to `used` (byte-identical to the single-set callers).
    Hook QUALITY (generic, narration, hype) is owned by the vision author (the prompt), not here."""
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


