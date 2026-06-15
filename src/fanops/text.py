"""Deterministic AI-tell sanitizer applied to ALL LLM / transcript-derived text before it is
stored or burned on-screen. The HARD guarantee behind the prompt's 'no em-dash' instruction:
no em/en-dash, curly quote, or invisible character survives, regardless of model compliance.
Idempotent; None-safe; leaves Arabic and hashtag text untouched. Codepoints as \\u escapes."""
from __future__ import annotations
import re

_DASHES = re.compile(r"\s*[—–‒―]\s*")   # em / en / figure / horizontal-bar -> ", "
_SQUO = re.compile(r"[‘’‛]")                 # curly single -> straight '
_DQUO = re.compile(r"[“”‟]")                 # curly double -> straight "
_ZEROWIDTH = re.compile(r"[​‌‍﻿]")      # zero-width / joiners / BOM -> dropped

def sanitize_generated_text(text: str | None, *, max_words: int | None = None) -> str | None:
    """Strip AI-tell punctuation + invisibles from LLM / transcript-derived text. None -> None.
    Dashes become a comma-space (preserving the clause break naturally); curly quotes straighten;
    NBSP -> space; zero-width chars drop. Optional max_words trims AFTER cleanup so the kept words
    are real. Idempotent — safe to re-apply (e.g. a one-shot migration over already-stored text)."""
    if text is None: return None
    text = _DASHES.sub(", ", text)
    text = _SQUO.sub("'", text)
    text = _DQUO.sub('"', text)
    text = text.replace(" ", " ")                     # non-breaking space -> regular space
    text = _ZEROWIDTH.sub("", text)
    text = re.sub(r"\s+", " ", text).strip().strip(" ,")   # collapse runs, drop dash-artifact edge commas
    if max_words is not None:
        text = " ".join(text.split()[:max_words])
    return text
