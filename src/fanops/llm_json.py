"""JSON extraction helpers shared by Claude and Cursor LLM transports."""
from __future__ import annotations

import json
import re

_JSON_FENCE_RE = re.compile(r'```json\s*\n(.*?)\n\s*```', re.DOTALL)


def _json_candidates(text: str) -> list[str]:
    """Return candidate JSON-object strings extracted from `text`, in priority order:
    1. Content of ```json … ``` fenced blocks (the lang tag must be present).
    2. Balanced-brace substrings found by scanning (outermost `{…}` objects only).
    Only object-shaped (brace-delimited) substrings are returned — arrays/scalars
    are not included. String literals inside braces are handled so `{` / `}` inside
    a JSON string value don't confuse the depth counter."""
    if not text:
        return []
    candidates: list[str] = []
    for m in _JSON_FENCE_RE.finditer(text):
        candidates.append(m.group(1))
    # Balanced-brace scan for outermost objects, respecting string escaping.
    i, n = 0, len(text)
    while i < n:
        if text[i] != '{':
            i += 1; continue
        depth = in_str = esc = 0
        for j in range(i, n):
            c = text[j]
            if esc:
                esc = 0
            elif c == '\\' and in_str:
                esc = 1
            elif c == '"':
                in_str ^= 1
            elif not in_str:
                if c == '{': depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        candidates.append(text[i:j + 1]); i = j + 1; break
        else:
            i += 1
    return candidates


def _extract_json_object(text: str) -> dict | None:
    """Return the first valid JSON object parsed from `text`, or None.
    Iterates `_json_candidates` in order; returns the first that parses as a
    JSON dict. Arrays, scalars, and parse errors are skipped."""
    for cand in _json_candidates(text):
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None
