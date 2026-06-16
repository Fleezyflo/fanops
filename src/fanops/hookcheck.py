# src/fanops/hookcheck.py
"""Deterministic hook-quality guard: reject KNOWN on-screen-hook slop so it never ships. A rejected
hook becomes None -> a clean clip (clean beats slop). HIGH-PRECISION on purpose — it catches the
clearly-bad patterns diagnosed from real round-1 output, and leaves nuanced calls (a vague 'wait for
the switch up' vs a concrete 'wait for the frequency line') to the prompt's generate-and-select and
a later LLM critic. The regression-locked failure modes:
  - generic superlative templates that fit any clip ('his hardest bar', 'his most slept-on hook')
  - tired cliches ('hits different', 'everyone replayed')
  - hooking on the EDITING instead of the content ('watch how he cuts')
  - cross-clip REPEATS (the 'reads like a bot' tell)
Length is NOT gated here: a strong hook can run long ('indie artists live or die in week one'); the
prompt owns brevity. is_weak_hook is also the shared predicate a future hook critic reuses."""
from __future__ import annotations
import re

# 'his' + a superlative (-est, or 'most ...') -> the lazy generic template that fits any clip
_SUPERLATIVE = re.compile(r"\bhis\s+(\w+est|most)\b", re.IGNORECASE)
# editing/scene-cut hooks: the plural 'cuts' is the tell ('watch how he cuts', 'the cuts speed up')
_EDITING = re.compile(r"\bcuts\b", re.IGNORECASE)
# tired filler cliches that read as generic regardless of the clip
_CLICHES = ("hits different", "everyone replayed", "everybody replayed")

# Opening-template clustering (the 'before he was X' x6 / 'wait for the Y' x6 tell): EXACT-string
# dedup misses it because the strings differ. We key on the first two WORD tokens; once this many
# accepted hooks already share that opening, the next one reads like a bot and is rejected. Two
# tokens (not one) keeps precision high — many hooks may legitimately start 'the', few share 'the bar'.
_TEMPLATE_PREFIX_TOKENS = 2
_TEMPLATE_CLUSTER_MAX = 2                             # the (MAX+1)th hook sharing the opening is rejected

def _prefix_key(text: str) -> tuple:
    return tuple(re.findall(r"\w+", text.lower())[:_TEMPLATE_PREFIX_TOKENS])

def is_weak_hook(text: str | None, used: set[str] = frozenset()) -> bool:
    """True if `text` is a hook to REJECT (-> clean clip). `used` is the set of hooks already taken
    this run; a case/space-insensitive repeat OR an opening-template cluster is rejected to kill
    cross-feed repetition (the 'reads like a bot' tell)."""
    if not text or not text.strip():
        return True                                   # nothing to show
    low = text.strip().lower()
    if low in {u.strip().lower() for u in used}:
        return True                                   # duplicate of another clip's hook
    key = _prefix_key(low)
    if key and sum(1 for u in used if _prefix_key(u) == key) >= _TEMPLATE_CLUSTER_MAX:
        return True                                   # >=2 accepted hooks share this opening -> a template cluster
    if _SUPERLATIVE.search(low):
        return True                                   # 'his hardest/coldest/most ...' generic template
    if _EDITING.search(low):
        return True                                   # hooks on the editing, not the content
    if any(c in low for c in _CLICHES):
        return True                                   # tired filler cliche
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
