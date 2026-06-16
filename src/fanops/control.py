# src/fanops/control.py
"""The control-file contract: ONE validated reader for the editorial inputs that steer runtime
output. context.md (the brand brief) is injected verbatim into every moment + caption prompt — the
single biggest output lever — yet the old readers (_guidance in moments.py + caption.py) returned ""
SILENTLY when it was absent, so a missing brief produced ungrounded clips with zero signal. This is
the fix: load_guidance is fail-OPEN but LOUD (missing/empty/oversize each log a WARNING and degrade
to a bounded/empty string; an autonomous run never crashes on a bad brief). The hard "you actually
need a brief" signal lives in doctor (a preflight), not here. Mirrors config.tuning()'s contract."""
from __future__ import annotations
import logging
from fanops.config import Config

logger = logging.getLogger("fanops.control")

# context.md is injected verbatim into every agent prompt. Bound it so a truncated/corrupted/giant
# edit can't blow up the prompt (or the token bill) — 32 KiB is ample for a creative brief.
_MAX_GUIDANCE_BYTES = 32_768

def load_guidance(cfg: Config) -> str:
    """The brand brief from context.md, validated. Returns the file's text when present, non-empty,
    and within the byte bound. Missing / empty-or-whitespace / oversize / unreadable each log a
    WARNING and return a safe value ("" or a bounded prefix) — fail-open so a bad brief degrades the
    run visibly instead of crashing it. The single reader for context.md (moments.py + caption.py
    both call this; do not re-add a local _guidance)."""
    p = cfg.context_path
    if not p.exists():
        logger.warning("no brand brief: %s is missing — clips/captions will be UNGROUNDED "
                       "(run `fanops doctor`)", p)
        return ""
    try:
        text = p.read_text()
    except OSError as e:                                  # unreadable (perms / vanished mid-read)
        logger.warning("could not read %s (%s) — proceeding UNGROUNDED", p, e)
        return ""
    if not text.strip():
        logger.warning("empty brand brief: %s has no content — clips/captions will be UNGROUNDED", p)
        return ""
    raw = text.encode("utf-8")
    if len(raw) > _MAX_GUIDANCE_BYTES:
        # truncate on a UTF-8 boundary (ignore a trailing partial multibyte char), warn loudly.
        text = raw[:_MAX_GUIDANCE_BYTES].decode("utf-8", "ignore")
        logger.warning("brand brief %s exceeds %d bytes — truncating the injected guidance",
                       p, _MAX_GUIDANCE_BYTES)
    return text
