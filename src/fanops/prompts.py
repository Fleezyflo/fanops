# src/fanops/prompts.py
"""Committed prompt templates for the autonomous LLM responder. Kept in source (not improvised
per-call) so the autonomous creative behavior is reviewable, diff-able, and stable. Each turns a
request payload (MomentRequest/CaptionRequest, already carrying context.md brand guidance) into a
`claude -p` instruction. The CALLER pairs these with the exact pydantic JSON schema via
--json-schema, so these prompts describe INTENT + CONSTRAINTS; the schema enforces SHAPE."""
from __future__ import annotations
import json

# Clip-length band (mirrors clip._MIN_CLIP_S/_MAX_CLIP_S). A source below the floor becomes one
# whole-source clip; the band midpoint sets how many clips a long source should yield.
_SHORT_SOURCE_S = 12.0
_BAND_SPAN_S = 17.0     # ~midpoint of the 12-22 band: aim for one clip per ~17s of source
_MAX_TARGET_PICKS = 6

def _target_pick_count(duration: float) -> int:
    """How many non-overlapping clips to AIM for, by source length. <=0 (unprobed) -> 0 (no target,
    let the model decide); a short source -> 1 (one whole-source clip); else ~one per band-span,
    floored at 1 (NO dead band) and capped so a long source can't request an unbounded list."""
    if duration <= 0: return 0
    if duration < _SHORT_SOURCE_S: return 1
    return max(1, min(_MAX_TARGET_PICKS, round(duration / _BAND_SPAN_S)))

def moment_prompt(payload: dict) -> str:
    duration = payload.get("duration", 0.0)
    target = _target_pick_count(duration)
    aim = (f"  - AIM FOR about {target} non-overlapping clip(s) for this ~{duration:.0f}s source. "
           "Spread them across the timeline; picks MUST NOT overlap each other. Return fewer ONLY if "
           "the material genuinely lacks that many distinct moments.\n") if target else ""
    short = (f"  - SHORT SOURCE: this source is under {_SHORT_SOURCE_S:.0f}s, so return EXACTLY ONE "
             "pick covering the whole source (start=0, end=SOURCE DURATION). NEVER return an empty "
             "list for a short source — a short clip is still worth posting.\n"
             ) if 0 < duration < _SHORT_SOURCE_S else ""
    return (
        "You are the editorial brain of an autonomous fan-account engine for a bilingual (EN/AR) "
        "rapper. From the transcript and signal peaks below, choose the MOMENTS most worth cutting "
        "into 12-22 second vertical clips. Return picks as JSON matching the provided schema.\n\n"
        f"SOURCE DURATION (seconds): {duration}\n"
        "HARD RULES for every pick:\n"
        f"  - 0 <= start < end <= {duration} (timestamps MUST be real, finite seconds, in-bounds; "
        "never NaN/Infinity).\n"
        "  - TARGET 12-22 seconds per clip: set start/end so (end - start) is about 12-22 seconds. "
        "Widen around the key line to include its lead-in and payoff; NEVER a 2-6 second fragment.\n"
        f"{short}"
        f"{aim}"
        "  - `reason` is REQUIRED: one sentence on WHY this moment hits (punchline, beat drop, "
        "quotable bar). Never use em-dashes (—) or en-dashes (–); use a comma or period.\n"
        "  - Prefer moments that align with a transcript line and/or a signal peak.\n"
        "  - A long source almost always has several distinct moments; an empty list is valid ONLY "
        "when nothing is genuinely worth posting.\n\n"
        f"BRAND GUIDANCE:\n{payload.get('guidance', '')}\n\n"
        f"LANGUAGE: {payload.get('language')}\n"
        f"TRANSCRIPT (JSON):\n{json.dumps(payload.get('transcript', []), ensure_ascii=False)}\n"
        f"SIGNAL PEAKS (JSON):\n{json.dumps(payload.get('signal_peaks', []), ensure_ascii=False)}\n"
    )

def caption_prompt(payload: dict) -> str:
    surfaces = payload.get("surfaces", [])
    keys = [s.get("surface") for s in surfaces]
    # Creative-variation v2: when the gated scorer has earned a trustworthy winner it feeds the
    # winning hook(s) in via `learned_hooks`. Surface them as a STYLE cue the model leans toward
    # (tone/length/angle) — explicitly NOT to copy verbatim, so the win generalizes across clips.
    # Absent/empty/None → no block at all, so the prompt stays byte-identical to today.
    learned = payload.get("learned_hooks")
    learned_block = (
        "  - What worked recently for these accounts — lean toward this STYLE (tone, length, "
        "angle), do NOT copy verbatim: "
        f"{json.dumps(learned, ensure_ascii=False)}\n"
        if learned else ""
    )
    # Cross-surface transfer (the v2 follow-up): a hook STYLE proven on OTHER same-platform surfaces,
    # offered to a COLD recipient as a LIGHTER nudge than its own proven style above. Separate key
    # (learned_hooks_transferred) so own-signal always reads as primary. Absent -> no block (prompt
    # stays byte-identical to v2).
    transferred = payload.get("learned_hooks_transferred")
    transferred_block = (
        "  - Also working elsewhere on this platform (a LIGHTER nudge than your own style above, "
        "if any) — lean toward this STYLE, do NOT copy verbatim: "
        f"{json.dumps(transferred, ensure_ascii=False)}\n"
        if transferred else ""
    )
    return (
        "You write captions for FAN ACCOUNTS that repost and celebrate a bilingual (EN/AR) rapper. "
        "You are a FAN hyping the artist to other fans — NEVER the artist, never an official account. "
        "Write ABOUT the artist in the THIRD PERSON; never first person as the artist (no 'I', 'me', "
        "'my' as if you made the music). Write ONE caption per posting surface listed below. Return "
        "JSON matching the provided schema.\n\n"
        "HARD RULES:\n"
        f"  - Write in this language: {payload.get('language')} (match it; do not switch languages).\n"
        f"  - Set each item's `language` field to {json.dumps(payload.get('language'))} (declare the "
        "language you actually wrote in — it is validated against the source language; a missing or "
        "mismatched value holds the clip).\n"
        "  - One item per surface. Set each item's `surface` to the EXACT key given (copy verbatim — "
        "do not reformat, abbreviate, or fix it).\n"
        f"  - Surfaces to caption (use these exact keys): {json.dumps(keys, ensure_ascii=False)}\n"
        "  - Each `caption` is HASHTAGS ONLY: a single line of relevant, platform-appropriate "
        "hashtags (roughly 5-15) separated by spaces and NOTHING ELSE — no sentences, no prose, no "
        "@mentions, no emoji. Put the SAME tags in the `hashtags` array. Example shape: "
        "'#rapper #موسيقى #hiphop #fyp'. Hashtags MAY mix languages (English tags like #fyp are fine "
        "even for an Arabic clip — reach beats language purity), so the language rule above does NOT "
        "constrain the tags; still set `language` to the SOURCE language. Stay on-brand: no slurs, "
        "no off-brand claims.\n"
        "  - Honor each surface's `persona` when present — it sets the fan angle/voice for that "
        "account (e.g. which tags or sub-scene to lean into).\n"
        "  - ALSO return a short on-screen `hook` per item: a punchy <=7-word HYPE line ABOUT the "
        "artist (THIRD-PERSON fan voice, never first person), in the source language, that grabs "
        "attention in the first 2 seconds. Make each surface's hook GENUINELY DIFFERENT (different "
        "angle/words); these are A/B creative variants per account. NEVER use em-dashes (—), "
        "en-dashes (–), or curly/smart quotes in the hook — use a comma, period, or straight "
        "apostrophe. If you cannot, omit `hook` and a default will be used.\n"
        f"{learned_block}"
        f"{transferred_block}"
        "\n"
        f"BRAND GUIDANCE:\n{payload.get('guidance', '')}\n\n"
        f"CLIP TRANSCRIPT EXCERPT: {json.dumps(payload.get('transcript_excerpt', ''), ensure_ascii=False)}\n"
        f"SURFACES (JSON):\n{json.dumps(surfaces, ensure_ascii=False)}\n"
    )
