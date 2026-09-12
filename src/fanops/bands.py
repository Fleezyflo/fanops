# src/fanops/bands.py
"""Clip-length BAND names leftover on Account.clip_profile / FANOPS_CLIP_PROFILE rows.
The pick is the cut — ingest and render do not apply these numbers. band_for still
resolves a stored profile name so old rows load; unknown/empty refuse (never TALK)."""
from __future__ import annotations
from typing import NamedTuple

class Band(NamedTuple):
    lo: float       # pick-gate floor (validate_pick) + prompt short-source threshold: a source below lo -> one whole clip
    hi: float       # pick-gate TARGET ceiling (seconds); render does not apply this
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
PROFILE_NAMES = frozenset(_PROFILES)    # the validatable set: accounts.set_clip_profile / add_account
                                        # and band_for refuse any name not in here. WRITE is strict
                                        # (never persist junk); LOAD via band_for is also refuse —
                                        # unknown/empty must not silently become TALK.

def band_for(profile: str | None) -> Band:
    """Resolve a content-type profile name to its Band. Unknown/empty/None raise ValueError
    (never TALK). Case-insensitive; surrounding whitespace tolerated (a .env value may carry it)."""
    key = (profile or "").strip().lower()
    got = _PROFILES.get(key)
    if got is None:
        raise ValueError(f"unknown clip_profile: {profile!r} (valid: {', '.join(sorted(PROFILE_NAMES))})")
    return got
