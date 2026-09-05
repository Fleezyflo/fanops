"""Shared metrics-read helpers: redaction, JSON parsing, poster fail reasons."""
from __future__ import annotations

from fanops.errors import redact


def _safe(cfg, text, limit: int = 200) -> str:
    # Scrub EVERY provider key from an external body before it lands in error_reason/stderr/run.log
    # (stage-5 audit follow-up: the 401 paths withhold the body, but the non-401 echoes still embed it,
    # and a 5xx/proxy/WAF page can reflect the presented key). cfg may be None (legacy callers) -> no-op.
    if cfg is None:
        return (text or "")[:limit]
    return redact(text, cfg.postiz_api_key, cfg.zernio_api_key, limit=limit)


def poster_fail_reason(*sources) -> str | None:
    """Short human reason from a Postiz/Zernio row. Never a stack dump. Never a secret."""
    import json as _json

    def coerce(v):
        if v is None or isinstance(v, bool) or isinstance(v, (int, float)):
            return None
        if isinstance(v, str):
            t = v.strip()
            if not t:
                return None
            if t[:1] in "{[":
                try:
                    return coerce(_json.loads(t))
                except ValueError:
                    pass
            if "    at " in t and len(t) > 200:
                t = t.split("\n", 1)[0].strip() or t
            return t[:200]
        if isinstance(v, dict):
            for k in ("message", "errorMessage", "error", "reason", "type"):
                if k in v:
                    s = coerce(v[k])
                    if s:
                        return s
            cause = v.get("cause")
            if isinstance(cause, dict):
                s = coerce(cause.get("failure") or cause)
                if s:
                    return s
            info = v.get("applicationFailureInfo")
            if isinstance(info, dict):
                s = coerce(info.get("type"))
                if s:
                    return s
            return None
        if isinstance(v, list):
            for item in v:
                s = coerce(item)
                if s:
                    return s
        return None

    for src in sources:
        s = coerce(src)
        if s:
            return s
    return None


def _json_or_raise(resp, label: str, cfg=None):
    # ECC fix #4: a 200 with a non-JSON body (HTML error page from a misconfigured proxy) made
    # resp.json() raise a raw JSONDecodeError that propagated out of pull_metrics and aborted the
    # WHOLE pass — every post lost its metrics. Convert it to a diagnosable RuntimeError the callers
    # already handle as a per-step failure. requests' JSONDecodeError subclasses ValueError.
    try:
        return resp.json()
    except ValueError as err:
        raise RuntimeError(f"{label}: non-JSON {resp.status_code} response: {_safe(cfg, resp.text)}") from err
