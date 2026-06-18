# src/fanops/hookscore.py
"""Hook-quality measurement (Task 5 + Task 9). Two jobs, BOTH non-gating:
  - narration_signature(): a high-precision detector for third-person scene-narration with no viewer
    address. Used as (a) a critic-INDEPENDENT viewer-POV METER on the scoreboard (so a loosened/biased
    critic can't inflate the number) and (b) a SIGNAL fed to the critic ('this reads as narration, look
    here'). It REJECTS NOTHING — quality decisions belong to the reasoning critic. High precision on
    purpose: it flags only CLEAR third-person-pronoun recaps, accepting misses (a regex can decide the
    obvious 'he stopped answering' but not the borderline call — that is the critic's job).
The narration shape the operator's failures share: a third-person pronoun subject (he/she/they/his/...)
with NO second-person/viewer address and no question — it recounts the clip to no one. A viewer marker
(you/your/pov/imagine/?) or an imperative-to-the-viewer opener (wait/watch/listen/...) means the line
addresses the scroller, so it is NOT narration regardless of any third-person pronoun elsewhere."""
from __future__ import annotations
import re

# the scroller is addressed: 2nd person, POV, an invitation, or a question
_VIEWER = re.compile(r"\b(you|your|youre|u|ur|pov|imagine)\b|'(re|ll)\b|\?", re.IGNORECASE)
# an imperative opener that directs the VIEWER's watch action ('wait for...', 'watch...', "don't...")
_IMPERATIVE_OPEN = re.compile(r"^\s*(wait|watch|listen|stop|don'?t|play|tell me|name|find)\b", re.IGNORECASE)
# a third-person pronoun subject/object — the narration tell
_THIRD_PERSON = re.compile(r"\b(he|him|his|she|her|hers|they|them|their|theirs)\b", re.IGNORECASE)

def narration_signature(text: str | None) -> bool:
    """True if `text` reads as third-person scene-narration (a recap with no viewer address). Never a
    gate — only a meter + a signal. High precision: flags a clear third-person-pronoun line that does
    NOT address the viewer; everything that addresses the scroller (2nd person / POV / question /
    imperative opener) is NOT narration, even if it also names 'he'/'she'."""
    if not text or not text.strip():
        return False                                  # nothing to flag
    low = text.strip().lower()
    if _VIEWER.search(low) or _IMPERATIVE_OPEN.search(low):
        return False                                  # addresses the viewer -> not narration
    return bool(_THIRD_PERSON.search(low))            # third-person subject + no viewer address -> recap
