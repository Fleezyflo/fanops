# src/fanops/prompts.py
"""Committed prompt templates for the autonomous LLM responder. Kept in source (not improvised
per-call) so the autonomous creative behavior is reviewable, diff-able, and stable. Each turns a
request payload (MomentRequest/CaptionRequest, already carrying context.md brand guidance) into a
`claude -p` instruction. The CALLER pairs these with the exact pydantic JSON schema via
--json-schema, so these prompts describe INTENT + CONSTRAINTS; the schema enforces SHAPE."""
from __future__ import annotations
import json

def moment_prompt(payload: dict) -> str:
    duration = payload.get("duration", 0.0)
    return (
        "You are the editorial brain of an autonomous fan-account engine for a bilingual (EN/AR) "
        "rapper. From the transcript and signal peaks below, choose the MOMENTS most worth cutting "
        "into short vertical clips. Return picks as JSON matching the provided schema.\n\n"
        f"SOURCE DURATION (seconds): {duration}\n"
        "HARD RULES for every pick:\n"
        f"  - 0 <= start < end <= {duration} (timestamps MUST be real, finite seconds, in-bounds; "
        "never NaN/Infinity).\n"
        "  - (end - start) >= 0.5 seconds.\n"
        "  - `reason` is REQUIRED: one sentence on WHY this moment hits (punchline, beat drop, "
        "quotable bar).\n"
        "  - Prefer moments that align with a transcript line and/or a signal peak.\n"
        "  - Return as many GOOD picks as exist; do not pad. An empty list is valid if nothing is "
        "worth posting.\n\n"
        f"BRAND GUIDANCE:\n{payload.get('guidance', '')}\n\n"
        f"LANGUAGE: {payload.get('language')}\n"
        f"TRANSCRIPT (JSON):\n{json.dumps(payload.get('transcript', []), ensure_ascii=False)}\n"
        f"SIGNAL PEAKS (JSON):\n{json.dumps(payload.get('signal_peaks', []), ensure_ascii=False)}\n"
    )

def caption_prompt(payload: dict) -> str:
    surfaces = payload.get("surfaces", [])
    keys = [s.get("surface") for s in surfaces]
    return (
        "You are the caption writer for an autonomous fan-account engine for a bilingual (EN/AR) "
        "rapper. Write ONE caption per posting surface listed below. Return JSON matching the "
        "provided schema.\n\n"
        "HARD RULES:\n"
        f"  - Write in this language: {payload.get('language')} (match it; do not switch languages).\n"
        "  - One item per surface. Set each item's `surface` to the EXACT key given (copy verbatim — "
        "do not reformat, abbreviate, or fix it).\n"
        f"  - Surfaces to caption (use these exact keys): {json.dumps(keys, ensure_ascii=False)}\n"
        "  - Keep it on-brand and platform-appropriate; no slurs, no off-brand claims.\n\n"
        f"BRAND GUIDANCE:\n{payload.get('guidance', '')}\n\n"
        f"CLIP TRANSCRIPT EXCERPT: {payload.get('transcript_excerpt', '')}\n"
        f"SURFACES (JSON):\n{json.dumps(surfaces, ensure_ascii=False)}\n"
    )
