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


def is_weak_hook(text: str | None, used: set[str] = frozenset()) -> bool:
    """True if `text` is a hook to REJECT (-> clean clip). `used` is the set of hooks already taken
    this run; a case/space-insensitive repeat is rejected to kill cross-feed repetition."""
    if not text or not text.strip():
        return True                                   # nothing to show
    low = text.strip().lower()
    if low in {u.strip().lower() for u in used}:
        return True                                   # duplicate of another clip's hook
    if _SUPERLATIVE.search(low):
        return True                                   # 'his hardest/coldest/most ...' generic template
    if _EDITING.search(low):
        return True                                   # hooks on the editing, not the content
    if any(c in low for c in _CLICHES):
        return True                                   # tired filler cliche
    return False
