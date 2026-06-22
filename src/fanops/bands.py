# src/fanops/bands.py
"""Clip-length BANDS by content type. A song's hook/verse is a longer watchable unit than a spoken
beat, so songs get a wider band cut on a fuller section; talk keeps the tight default. ONE home so
the render enforcement (clip.fit_window) and the moment prompt (prompts) agree on the same band —
they used to share bare module constants and could drift. band_for resolves an operator profile
name (Config.clip_profile / FANOPS_CLIP_PROFILE) to a Band, falling back to TALK for anything
unknown (the validate-or-default posture — never crash an autonomous run over a bad profile)."""
from __future__ import annotations
from typing import NamedTuple

class Band(NamedTuple):
    lo: float       # render floor + prompt short-source threshold: a source below `lo` -> one whole clip
    hi: float       # render ceiling (seconds)
    @property
    def span(self) -> float: return (self.lo + self.hi) / 2.0   # midpoint: aim ~one clip per `span` s

TALK = Band(12.0, 22.0)     # spoken beats: tight, punchy (today's default)
SONG = Band(18.0, 35.0)     # music: a full hook/verse is longer and more watchable

# M2 (LOCKED 2026-06-22): three operator-facing LENGTH tiers, ADDED alongside the legacy content-type
# bands — NOT aliases of talk/song. Keeping talk/song at their own values means every existing .env /
# ledger profile renders byte-identically (no re-band, no normalize, no learning-cohort split); the
# operator picks short/medium/long as a deliberate new choice.
SHORT = Band(8.0, 15.0)     # quick punch
MEDIUM = Band(16.0, 26.0)   # default-ish watchable unit
LONG = Band(28.0, 45.0)     # a full section / longer watch

_PROFILES = {"talk": TALK, "song": SONG, "short": SHORT, "medium": MEDIUM, "long": LONG}

def band_for(profile: str | None) -> Band:
    """Resolve a content-type profile name to its Band. Unknown/empty/None -> TALK (the safe default,
    today's behavior). Case-insensitive; surrounding whitespace tolerated (a .env value may carry it)."""
    return _PROFILES.get((profile or "").strip().lower(), TALK)
