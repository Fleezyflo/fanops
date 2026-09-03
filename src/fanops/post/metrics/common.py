"""Shared metrics-read helpers: redaction, JSON parsing, poster fail reasons, IG Graph insights."""
from __future__ import annotations

from typing import Optional

from fanops.config import Config
from fanops.errors import redact
from fanops.log import get_logger


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


def _retention_fraction(avg_watch_ms, duration_s) -> Optional[float]:
    """Watch-through as a [0,1] rate (what _W['retention']=3.0 expects) = avg_watch_ms / (duration_s*1000),
    CLAMPED to [0,1] (a loop/measurement can exceed the clip length). None when either input is missing/
    non-positive -> retention is honestly ABSENT (degraded), NEVER fabricated. Raw ms would swamp the lift
    scale (LOCKED #1), so this is the only shape that reaches record_metrics."""
    if not isinstance(avg_watch_ms, (int, float)) or isinstance(avg_watch_ms, bool):
        return None
    if not isinstance(duration_s, (int, float)) or isinstance(duration_s, bool) or duration_s <= 0:
        return None
    return max(0.0, min(1.0, float(avg_watch_ms) / (float(duration_s) * 1000.0)))


class GraphInsightsClient:
    """The IG metrics reader (Leg 2): Meta Graph media-insights is the SOLE source of an IG post's real
    performance (reach/views/saves/shares/likes/comments + retention). Emits the SAME {postSubmissionId,
    metrics} row contract as PostizMetricsClient so track.record_metrics is UNCHANGED. Per-post isolation
    mirrors the Postiz/Zernio readers: a post with no resolved media_id or a transient insights failure (None)
    is SKIPPED (no row -> keeps its prior snapshot, re-polled next pass), never wholesale-zeroed. A scope
    refusal (MetaInsightsScopeError) fails the pass CLOSED + LOUD: sets `insights_blocked` and STOPS (no rows
    -> no wrong numbers), so doctor + Home can surface the one external gate. retention is the [0,1] watch-
    through fraction derived from avg_watch_ms + the post's cut_seconds; absent when the duration is unknown."""
    def __init__(self, cfg: Config, *, posts: Optional[list] = None, insights_fn=None):
        self.cfg = cfg
        self.posts = posts or []
        # each post -> (media_id, post_type). The insights request is DERIVED from the declared type
        # (Post.post_type at mint, meta_graph.insights_metrics_for) — a feed video is never
        # asked for a reels-only metric. The client forwards p.post_type verbatim (no default, no guess);
        # when it is still unresolved (None — a legacy row stamped before the type was carried),
        # media_insights refuses the empty-metric request PRE-FLIGHT and returns None, so this post
        # transient-skips (below) and re-resolves its type next reconcile pass — never a malformed request,
        # never a false scope-block. The injected insights_fn (the test seam) is a 2-arg (media_id,
        # post_type) callable and is used verbatim (byte-identical). The DEFAULT path resolves PER-ACCOUNT
        # creds from each post's handle (the per-handle-creds gap) so an authored IG post is measured with
        # ITS handle's token, not the single global one — a handle with no per-account creds resolves the
        # global (byte-identical single-account).
        self._insights = insights_fn
        self.insights_blocked = False

    def _default_insights(self, media_id, product_type, handle):
        from fanops import meta_graph
        creds = meta_graph.resolve_meta_creds(self.cfg, handle=handle)
        return meta_graph.media_insights(self.cfg, media_id, product_type, creds=creds)

    def list_posts(self, window: str = "30d") -> list[dict]:
        from fanops.errors import MetaInsightsScopeError
        rows: list[dict] = []
        for p in self.posts:
            media_id = getattr(p, "media_id", None)
            sid = getattr(p, "submission_id", None)
            if not (media_id and sid):
                continue                                        # unresolved -> skip (keeps prior snapshot)
            try:
                # No getattr default: a missed rename must raise, not silent-None into transient_skip (MOL-824).
                pt = getattr(p, "post_type")
                raw = (self._insights(media_id, pt) if self._insights is not None
                       else self._default_insights(media_id, pt, getattr(p, "account", None)))
            except MetaInsightsScopeError:
                # the one external gate: fail CLOSED + LOUD, write NOTHING, stop the pass.
                self.insights_blocked = True
                from fanops import meta_graph as _mg
                _mg._set_insights_blocked(self.cfg)             # persist LOUD so doctor/Home surface the gate
                get_logger(self.cfg)("graph_insights", str(sid), "insights_blocked_scope")
                break
            if raw is None:
                get_logger(self.cfg)("graph_insights", str(sid), "transient_skip")
                continue                                        # transient -> skip this id, keep going
            metrics = {k: v for k, v in raw.items() if k != "avg_watch_ms"}
            ret = _retention_fraction(raw.get("avg_watch_ms"), getattr(p, "cut_seconds", None))
            if ret is not None:
                metrics["retention"] = ret                      # [0,1] fraction; absent (degraded) if no duration
            rows.append({"postSubmissionId": sid, "metrics": metrics})
            from fanops import meta_graph as _mg
            _mg._clear_insights_blocked(self.cfg)               # insights flowed -> self-heal the blocked signal
        return rows
