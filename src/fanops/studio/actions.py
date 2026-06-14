# src/fanops/studio/actions.py — CREATE
"""Lock-safe Studio mutations (no Flask). Each public action opens ONE Ledger.transaction and does
its existence + state(queued) + not-imminent guard + mutation INSIDE the lock, on the in-lock
freshly-loaded ledger — mirroring the CLI recovery verbs (cli.py:285,298) so it cannot lose-update
against a concurrent cron `fanops run`. Reads/normalization that can fail happen OUTSIDE the lock."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import ValidationError

from fanops.config import Config
from fanops.errors import AuthError, ToolchainMissingError, reason
from fanops.ledger import Ledger
from fanops.models import CaptionSet, MomentDecision, PostState
from fanops.timeutil import parse_iso, iso_z
from fanops.studio.views import _imminent

SNOOZE_DAYS = 365
_GATE_MODELS = {"moments": MomentDecision, "captions": CaptionSet}


@dataclass
class ActionResult:
    ok: bool
    error: Optional[str] = None
    detail: Optional[dict] = None


def _now(now: Optional[datetime]) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _normalize_z(new_time: str) -> str:
    """Parse an ISO time, COERCE naive -> UTC (iso_z would otherwise treat naive as LOCAL time),
    and re-emit the canonical ...Z aware form. Raises ValueError on unparseable input."""
    dt = parse_iso(new_time)                       # raises ValueError on garbage
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)       # explicit UTC coercion (never local-tz guess)
    return iso_z(dt)


def _guard_editable_post(led: Ledger, post_id: str, now: datetime):
    """Return (post, None) if post exists, is queued, and is not imminent; else (None, error)."""
    if post_id not in led.posts:
        return None, f"no such post: {post_id}"
    p = led.posts[post_id]
    if p.state is not PostState.queued:
        return None, f"post {post_id} is not queued (state={p.state.value}); only queued posts are editable"
    if _imminent(p.scheduled_time, now):
        return None, f"post {post_id} is imminent/already due — shipping now, cannot edit"
    return p, None


def reschedule_post(cfg: Config, post_id: str, new_time: str, *, now: Optional[datetime] = None) -> ActionResult:
    now = _now(now)
    try:
        z = _normalize_z(new_time)                 # OUTSIDE the lock: reject bad input early
    except (ValueError, TypeError) as exc:
        return ActionResult(ok=False, error=f"bad time {new_time!r}: {str(exc)[:120]}")
    with Ledger.transaction(cfg) as led:
        p, err = _guard_editable_post(led, post_id, now)
        if err:
            return ActionResult(ok=False, error=err)
        p.scheduled_time = z
    return ActionResult(ok=True, detail={"post_id": post_id, "scheduled_time": z})


def edit_caption(cfg: Config, post_id: str, caption: str, *, now: Optional[datetime] = None) -> ActionResult:
    now = _now(now)
    with Ledger.transaction(cfg) as led:
        p, err = _guard_editable_post(led, post_id, now)
        if err:
            return ActionResult(ok=False, error=err)
        p.caption = caption
    return ActionResult(ok=True, detail={"post_id": post_id, "caption": caption})


def regenerate_caption(cfg: Config, post_id: str, guidance: str = "", *,
                       model=None, now: Optional[datetime] = None) -> ActionResult:
    """Review-first milestone 3 — re-run the caption model for ONE queued post and write the new
    caption back, so the operator changes a hint and 'gets it again' without hand-writing a caption
    or touching the CLI. Reuses the PRODUCTION caption prompt (prompts.caption_prompt) for the post's
    single surface, plus the operator's typed `guidance` as a highest-priority instruction. The SAME
    off-brand guard the pipeline applies (caption.brand_risk_flag) re-runs on the result — a
    regenerated off-brand caption is REJECTED, never written (no guardrail bypass). The slow model
    call runs OUTSIDE the ledger flock (it can be a ~180s `claude -p`, and holding the lock that long
    would deadlock a concurrent run — the 60s pytest timeout guards exactly that); the post is
    re-guarded INSIDE a short transaction before the write, so a run that publishes the post mid-call
    can't be clobbered. `model(prompt, schema)->dict` is injectable for tests; the default is the same
    `claude -p` the llm responder uses. Bounded to ONE model call per click (PRD cost mitigation).
    Does NOT publish — safe on any backend, so no confirm gate."""
    from fanops.prompts import caption_prompt
    from fanops.caption import brand_risk_flag
    now = _now(now)
    led = Ledger.load(cfg)                              # lock-free read: reject early, build context
    p, err = _guard_editable_post(led, post_id, now)
    if err:
        return ActionResult(ok=False, error=err)
    surface = f"{p.account}/{p.platform.value}"         # the documented caption lookup contract
    clip = led.clips.get(p.parent_id)
    moment = led.moments.get(clip.parent_id) if clip else None
    src = led.sources.get(moment.parent_id) if moment else None
    base = cfg.context_path.read_text() if cfg.context_path.exists() else ""
    full_guidance = base
    if (guidance or "").strip():                        # operator hint is highest priority for this re-roll
        full_guidance = (base + "\n\nOPERATOR INSTRUCTION FOR THIS REGENERATION (highest priority): "
                         + guidance.strip())
    payload = {"clip_id": p.parent_id, "language": src.language if src else None,
               "transcript_excerpt": moment.transcript_excerpt if moment else "",
               "guidance": full_guidance,
               "surfaces": [{"surface": surface, "platform": p.platform.value}]}
    if model is None:
        from fanops.llm import claude_json
        model = claude_json
    try:                                                # the slow generation, OUTSIDE any lock
        out = model(caption_prompt(payload), CaptionSet.model_json_schema())
    except ToolchainMissingError as exc:
        return ActionResult(ok=False, error="Regenerate needs the `claude` CLI on PATH (run "
                            f"`fanops autopilot` once to enable auto mode): {str(exc)[:160]}")
    except Exception as exc:
        return ActionResult(ok=False, error=f"regenerate failed: {str(exc)[:160]}")
    try:
        cs = CaptionSet(**{**out, "request_id": "regen"})
    except (ValidationError, TypeError) as exc:
        return ActionResult(ok=False, error=f"regenerated caption was malformed: {reason(exc) if isinstance(exc, ValidationError) else exc}")
    item = next((it for it in cs.items if it.surface == surface), None)
    if item is None and len(cs.items) == 1:
        item = cs.items[0]                              # single-surface regen: accept a lone item
    if item is None:
        return ActionResult(ok=False, error=f"model returned no caption for {surface}")
    flag = brand_risk_flag(item.caption, cfg)           # SAME guard as ingest_captions — no bypass
    if flag:
        return ActionResult(ok=False, error=f"regenerated caption rejected — {flag}. "
                            "Edit it by hand or regenerate again.")
    new_caption, new_tags = item.caption, list(item.hashtags or [])
    with Ledger.transaction(cfg) as led2:               # re-guard + write INSIDE a short transaction
        # fresh now: the model call may have taken ~180s, during which the post could have become
        # imminent/due — re-check against real wall-clock (fail-safe), not the stale entry-time now.
        p2, err2 = _guard_editable_post(led2, post_id, _now(None))
        if err2:
            return ActionResult(ok=False, error=err2)
        p2.caption = new_caption
        p2.hashtags = new_tags
    return ActionResult(ok=True, detail={"post_id": post_id, "caption": new_caption, "hashtags": new_tags})


def approve_candidate(cfg: Config, eid: str) -> ActionResult:
    """Track C: approve a discover candidate from the browser — move 00_review/<eid>.jpg into
    00_review/approved/ (what the operator used to do by hand in Finder). eid must be a bare stem
    (no path separators / ..) so a Studio POST can't move an arbitrary file. No ledger touch — this
    is a review-folder move; `fanops intake` then copies the original into the inbox."""
    if not eid or "/" in eid or "\\" in eid or ".." in eid:
        return ActionResult(ok=False, error=f"bad candidate id: {eid!r}")
    src = cfg.review / f"{eid}.jpg"
    if not src.exists():
        return ActionResult(ok=False, error=f"no such candidate: {eid}")
    dst = cfg.review / "approved" / f"{eid}.jpg"
    try:                                               # read-only mount / disk full / rename race
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
    except OSError as exc:
        return ActionResult(ok=False, error=f"approve failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"eid": eid})


def run_ingest(cfg: Config) -> ActionResult:
    """Drive `fanops ingest` from the browser: catalogue 01_inbox under one transaction (the exact
    cmd_ingest path). A toolchain-absent / control-file error is surfaced as a clean ActionResult,
    never a 500."""
    from fanops.ingest import ingest_drops
    from fanops.digest import write_digest
    n = 0
    try:
        with Ledger.transaction(cfg) as led:
            led = ingest_drops(led, cfg)
            n = len(led.sources)
        write_digest(Ledger.load(cfg), cfg)
    except Exception as exc:
        return ActionResult(ok=False, error=f"ingest failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"sources": n})


def run_pull(cfg: Config, url: str) -> ActionResult:
    """Drive `fanops pull <url>`: yt-dlp the URL (network, NO lock) then ingest under a transaction.
    Rejects a non-http(s) URL up front (mirrors the CLI's _http_url validator)."""
    from fanops.ingest import download_url, ingest_drops
    from fanops.digest import write_digest
    if not (url or "").strip().startswith(("http://", "https://")):
        return ActionResult(ok=False, error=f"url must be http(s):// — got {url!r}")
    n = 0
    try:
        download_url(cfg, url.strip())
        with Ledger.transaction(cfg) as led:
            led = ingest_drops(led, cfg, origin="url")
            n = len(led.sources)
        write_digest(Ledger.load(cfg), cfg)
    except Exception as exc:
        return ActionResult(ok=False, error=f"pull failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"sources": n})


def run_advance(cfg: Config, base_time: Optional[str] = None, *, confirmed: bool = True) -> ActionResult:
    """Drive one `fanops advance` pass (transcribe -> moments gate -> render -> captions gate ->
    crosspost -> publish due). Blocks on an unusable accounts config first (mirrors cmd_advance's
    _check_accounts: an empty account_id must never reach Blotato). base_time defaults to now, so a
    Studio-triggered pass schedules across today; any advance error (incl. a live auth failure) is
    surfaced cleanly, never a 500. On a LIVE backend a pass PUBLISHES to real accounts, so the Studio
    button must pass confirmed=True (the route derives it from a confirm checkbox); dryrun publishes
    nothing and needs no confirm."""
    from fanops.pipeline import advance
    from fanops.accounts import Accounts
    if cfg.poster_backend != "dryrun" and not confirmed:
        return ActionResult(ok=False, error=f"LIVE backend ({cfg.poster_backend}): a pass PUBLISHES "
                            "due posts to real accounts — tick the confirm box, then run again.")
    try:
        problems = Accounts.load(cfg).validate()       # malformed accounts.json -> clean error, not 500
    except Exception as exc:
        return ActionResult(ok=False, error=f"accounts.json: {str(exc)[:160]}")
    if problems:
        return ActionResult(ok=False, error="accounts.json: " + "; ".join(problems))
    bt = base_time or iso_z(_now(None))
    try:
        summary = advance(cfg, base_time=bt)
    except AuthError as exc:
        # F52 parity: a bad/missing key fails EVERY post — advance's own transaction already rolled
        # back (it saves only on clean exit), but surface the FATAL severity, not a soft "failed".
        # Name the right key per backend (ecc holistic audit GAP 2 — was Blotato-only).
        key = "POSTIZ_API_KEY" if cfg.poster_backend == "postiz" else "BLOTATO_API_KEY"
        return ActionResult(ok=False, error=f"FATAL auth failure — check {key}: {str(exc)[:160]}")
    except Exception as exc:
        return ActionResult(ok=False, error=f"advance failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail=summary)


def run_prepare(cfg: Config, base_time: Optional[str] = None, *, confirmed: bool = True) -> ActionResult:
    """Auto-prepare (review-first, milestone 1): answer every pending moment/caption gate via the
    configured responder, then advance — looped until no gate remains — so finished clips land in
    Review WITHOUT the operator hand-writing a caption. With FANOPS_RESPONDER=llm the gates answer
    themselves (the one-click/autopilot path); in manual mode the responder writes nothing and the
    gates stay for the Gates tab. Same live-publish confirm + accounts guards as run_advance — a
    prepare pass still crossposts/publishes due posts on a live backend. Mirrors cmd_run's loop."""
    from fanops.pipeline import advance
    from fanops.accounts import Accounts
    from fanops.responder import get_responder
    if cfg.poster_backend != "dryrun" and not confirmed:
        return ActionResult(ok=False, error=f"LIVE backend ({cfg.poster_backend}): a prepare pass "
                            "PUBLISHES due posts to real accounts — tick the confirm box, then run again.")
    try:
        problems = Accounts.load(cfg).validate()       # malformed/empty-id accounts -> clean error, not 500
    except Exception as exc:
        return ActionResult(ok=False, error=f"accounts.json: {str(exc)[:160]}")
    if problems:
        return ActionResult(ok=False, error="accounts.json: " + "; ".join(problems))
    bt = base_time or iso_z(_now(None))
    responder = get_responder(cfg)
    summary = None
    for _ in range(10):                                # respond -> advance until stable (no gate left)
        try:
            responder.answer_pending(cfg)              # llm answers the gates; manual writes nothing
            summary = advance(cfg, base_time=bt)
        except AuthError as exc:
            key = "POSTIZ_API_KEY" if cfg.poster_backend == "postiz" else "BLOTATO_API_KEY"
            return ActionResult(ok=False, error=f"FATAL auth failure — check {key}: {str(exc)[:160]}")
        except Exception as exc:
            return ActionResult(ok=False, error=f"prepare failed: {str(exc)[:160]}")
        if summary["awaiting"]["moments"] == 0 and summary["awaiting"]["captions"] == 0:
            break
    return ActionResult(ok=True, detail=summary)


# Non-terminal states an operator may mark "posted by hand". `error` is included (ecc:python-review):
# it is semantically a recoverable failure like `failed` (digest.py treats them alike), so the UI
# must not strand an error-state post. Excludes the terminal published/analyzed/retired.
_POSTABLE = {PostState.queued, PostState.needs_reconcile, PostState.submitting,
             PostState.submitted, PostState.failed, PostState.error}

def mark_published(cfg: Config, post_id: str, url: Optional[str] = None) -> ActionResult:
    """Track B: the operator posted this clip by hand — force the post to `published` (+ optional
    live URL). Like `fanops resolve <id> published` but STRICTER (ecc:python-review): resolve is the
    unguarded force-anything escape hatch, whereas this rejects an already-terminal
    (published/analyzed/retired) post so a double-click can't churn terminal state. Tight local
    transaction, no network."""
    with Ledger.transaction(cfg) as led:
        if post_id not in led.posts:
            return ActionResult(ok=False, error=f"no such post: {post_id}")
        p = led.posts[post_id]
        if p.state not in _POSTABLE:
            return ActionResult(ok=False, error=f"post {post_id} is {p.state.value} — only an unpublished post can be marked posted")
        p.state = PostState.published
        if url:
            p.public_url = url
    return ActionResult(ok=True, detail={"post_id": post_id, "url": url})


def answer_gate(cfg: Config, kind: str, key: str, data: dict) -> ActionResult:
    """Answer a moment/caption agent gate from the browser through the SAME validated contract the
    responder uses (Phase 3a): echo the latest request_id, validate the FULL response against its
    Pydantic model, and write response.json ONLY if valid — a bad answer never lands, so the gate
    stays pending (the operator can retry). No Ledger lock: gate files live under 04_agent_io, not
    the ledger; read_response's request_id staleness check is the safety net, not a lock."""
    from fanops.agentstep import latest_request_id, response_path
    model = _GATE_MODELS.get(kind)
    if model is None:
        return ActionResult(ok=False, error=f"unknown gate kind: {kind!r}")
    rid = latest_request_id(cfg, kind, key)
    if rid is None:
        return ActionResult(ok=False, error=f"no pending {kind} gate for {key!r}")
    full = {"request_id": rid, **data}
    if kind == "moments":
        full["source_id"] = key                    # MomentDecision echoes the source it decides
    try:
        validated = model(**full)
    except ValidationError as exc:
        return ActionResult(ok=False, error=reason(exc))
    response_path(cfg, kind, key).write_text(validated.model_dump_json(indent=2))
    return ActionResult(ok=True, detail={"kind": kind, "key": key})


def snooze_clip(cfg: Config, clip_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    """Push every non-imminent queued post of a clip ~SNOOZE_DAYS into the future, in ONE
    transaction (atomic — never a partial snooze). Inherits the same guard + normalization."""
    now = _now(now)
    z = iso_z(now + timedelta(days=SNOOZE_DAYS))
    with Ledger.transaction(cfg) as led:
        if clip_id not in led.clips:
            return ActionResult(ok=False, error=f"no such clip: {clip_id}")
        count = 0
        for p in led.posts.values():
            if p.parent_id == clip_id and p.state is PostState.queued and not _imminent(p.scheduled_time, now):
                p.scheduled_time = z
                count += 1
    return ActionResult(ok=True, detail={"clip_id": clip_id, "count": count, "scheduled_time": z})
