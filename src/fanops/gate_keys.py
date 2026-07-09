# src/fanops/gate_keys.py
"""Shared gate-key → source-id resolution. ONE implementation imported by responder + status — no copy."""
from __future__ import annotations


def gate_source_id(led, kind: str, key: str) -> str | None:
    """Resolve the owning source id for a gate key — moments/moment_hooks key on source id directly;
    captions keys on a clip id -> clip.parent=moment, moment.parent=source."""
    if kind in ("moments", "moment_hooks"):
        return key.split(".", 1)[0]
    clip = led.clips.get(key)
    mom = led.moments.get(clip.parent_id) if clip is not None else None
    return mom.parent_id if mom is not None else None
