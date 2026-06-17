# src/fanops/studio/actions.py — CREATE
"""Lock-safe Studio mutations (no Flask). Each public action opens ONE Ledger.transaction and does
its existence + state(queued) + not-imminent guard + mutation INSIDE the lock, on the in-lock
freshly-loaded ledger — mirroring the CLI recovery verbs (cli.py:285,298) so it cannot lose-update
against a concurrent cron `fanops run`. Reads/normalization that can fail happen OUTSIDE the lock."""
from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence

from pydantic import ValidationError
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from fanops.config import Config
from fanops.errors import AuthError, ToolchainMissingError, reason
from fanops.ingest import MEDIA_EXT
from fanops.ledger import Ledger
from fanops.models import CaptionSet, ClipState, MomentDecision, Post, PostState
from fanops.timeutil import parse_iso, iso_z
from fanops.studio.views import _imminent

SNOOZE_DAYS = 365
_GATE_MODELS = {"moments": MomentDecision, "captions": CaptionSet}
_VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}   # the has_video_stream subset of MEDIA_EXT
if not (_VIDEO_EXT <= MEDIA_EXT): raise ValueError("_VIDEO_EXT drifted out of ingest.MEDIA_EXT")  # import-time drift guard (not assert — survives -O)


@dataclass(frozen=True)
class ActionResult:
    """The outcome of one Studio action — frozen so a result can't be mutated after construction (every
    action returns a fresh one; no call site reassigns ok/error/detail). Construct directly or via the
    success()/failure() factories."""
    ok: bool
    error: Optional[str] = None
    detail: Optional[dict] = None

    @classmethod
    def success(cls, detail: Optional[dict] = None) -> "ActionResult":
        return cls(ok=True, detail=detail)

    @classmethod
    def failure(cls, error: str) -> "ActionResult":
        return cls(ok=False, error=error)


def _now(now: Optional[datetime]) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _normalize_z(new_time: str) -> str:
    """Parse an ISO time, COERCE naive -> UTC (iso_z would otherwise treat naive as LOCAL time),
    and re-emit the canonical ...Z aware form. Raises ValueError on unparseable input."""
    dt = parse_iso(new_time)                       # raises ValueError on garbage
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)       # explicit UTC coercion (never local-tz guess)
    return iso_z(dt)


def _guard_editable_post(led: Ledger, post_id: str, now: datetime) -> tuple[Optional[Post], Optional[str]]:
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


def save_uploads(cfg: Config, files: Sequence[FileStorage], *, probe: bool = True,
                 allowed_ext: set[str] = _VIDEO_EXT, dest_dir: Optional[Path] = None) -> ActionResult:
    """Stream operator-uploaded raw video into cfg.inbox so `run_ingest` catalogues it — the browser
    replacement for a Finder drag. Each file is validated (video ext, traversal-safe name), streamed to
    a `.uploadpart` temp in the inbox, then os.replace'd into place (so a half-upload never appears to
    ingest). Untrusted input crossing a boundary: the raw-name traversal triad + secure_filename + an
    inbox-bound resolve are the path-safety gates; Flask's MAX_CONTENT_LENGTH (set in create_app) refuses
    an oversize body BEFORE this runs. Never 500s — every fallible step yields a skip reason. ok is True
    iff at least one file landed (all-rejected is a failure, not a green no-op)."""
    files = [f for f in (files or []) if getattr(f, "filename", "")]   # drop empty (no-file-chosen) parts
    if not files:
        return ActionResult(ok=False, error="no files selected — choose a video to upload")
    saved, skipped = [], []
    dest_root = dest_dir or cfg.inbox                              # M1: third-party intake lands in a peer staging dir
    dest_root.mkdir(parents=True, exist_ok=True)
    inbox = dest_root.resolve()                                    # the bound everything below is kept within
    for f in files:
        raw = f.filename or ""
        name = secure_filename(raw)                                    # strips path, .., unsafe chars; "" if hostile
        if not name or "/" in raw or "\\" in raw or ".." in raw:       # reject traversal on the RAW name (mirror approve_candidate)
            skipped.append((raw, "unsafe name")); continue
        if Path(name).suffix.lower() not in allowed_ext:
            skipped.append((raw, "unsupported type")); continue
        suffix = Path(name).suffix
        name = Path(name).stem[:255 - len(suffix) - len(".uploadpart")] + suffix   # keep name + temp-suffix ≤ NAME_MAX so an overlong name never trips an OSError that embeds the fs path in a skip reason
        dest = (inbox / name).resolve()
        if not dest.is_relative_to(inbox):                             # belt-and-braces: final path MUST stay in the inbox
            skipped.append((raw, "escapes inbox")); continue
        tmp = inbox / f"{name}.uploadpart"                            # same-dir temp → os.replace is atomic; suffix ∉ MEDIA_EXT so a leaked temp is never ingested
        try:
            f.save(str(tmp))                                          # FileStorage.save streams in chunks (no full-buffer)
            os.replace(tmp, dest)                                     # atomic swap-in; a crash mid-stream leaves only the .uploadpart temp
        except OSError as exc:
            try: tmp.unlink()                                         # best-effort cleanup of the partial temp
            except OSError: pass
            skipped.append((raw, exc.strerror or "write failed")); continue   # strerror omits the fs path (no path disclosure in the reason)
        if probe:
            from fanops.ingest import has_video_stream                # local import so a test's mocker.patch is seen
            try:
                if not has_video_stream(dest):
                    dest.unlink(missing_ok=True); skipped.append((raw, "no video stream")); continue
            except ToolchainMissingError:
                pass                                                  # ffprobe absent → don't reject; ingest re-checks later
        saved.append(name)
    if not saved:                                                     # every file was rejected → a real failure, not a green "0 saved"
        return ActionResult(ok=False, error=f"nothing saved — {len(skipped)} file(s) rejected (wrong type, unsafe name, or unreadable)", detail={"saved": saved, "skipped": skipped})
    return ActionResult(ok=True, detail={"saved": saved, "skipped": skipped})


def save_thirdparty_uploads(cfg: Config, files: Sequence[FileStorage]) -> ActionResult:
    """Land operator-uploaded THIRD-PARTY assets (video OR photo) through the EXACT same validated
    contract as save_uploads (traversal triad + secure_filename + dir-bound resolve + atomic replace +
    probe), but into the PEER staging dir cfg.thirdparty_inbox — never the native 01_inbox, so a native
    ingest pass can't reach and mislabel them — with the photo-inclusive MEDIA_EXT gate."""
    from fanops.ingest import MEDIA_EXT
    return save_uploads(cfg, files, allowed_ext=MEDIA_EXT, dest_dir=cfg.thirdparty_inbox)


def run_ingest_thirdparty(cfg: Config) -> ActionResult:
    """Catalogue the third-party staging dir as third_party Sources (inert to clip-production). Mirrors
    run_ingest (one transaction + write_digest, never a 500). Surfaces the PII-excluded COUNT so a
    deliberately-uploaded file the ingest name-filter drops is visible to the operator, not silently lost."""
    from fanops.ingest import ingest_drops, is_excluded, MEDIA_EXT
    from fanops.digest import write_digest
    staged = ([f for f in cfg.thirdparty_inbox.rglob("*") if f.is_file() and f.suffix.lower() in MEDIA_EXT]
              if cfg.thirdparty_inbox.exists() else [])
    excluded = sum(1 for f in staged if is_excluded(f.name))      # deliberate uploads the name-filter drops
    n = added = 0
    try:
        with Ledger.transaction(cfg) as led:
            before = sum(1 for s in led.sources.values() if s.origin_kind == "third_party")
            led = ingest_drops(led, cfg, origin="upload", origin_kind="third_party", inbox=cfg.thirdparty_inbox)
            n = sum(1 for s in led.sources.values() if s.origin_kind == "third_party")
            added = n - before                                    # THIS call's delta (sha256 dedup → 0 on a repeat)
        write_digest(Ledger.load(cfg), cfg)
    except Exception as exc:
        return ActionResult(ok=False, error=f"third-party ingest failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"sources": n, "added": added, "excluded": excluded})


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
    done = False
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
            done = True; break
    # In llm mode the responder is SUPPOSED to drain the gates; hitting the 10-pass cap with gates
    # still pending means it isn't converging (malformed answers / gates regenerating) — surface that
    # instead of a green "prepared" the operator would wrongly trust (ecc audit: code+python MEDIUM).
    # In manual mode the responder writes nothing, so remaining gates are EXPECTED (they wait in the
    # Gates tab) — that stays ok=True.
    if not done and cfg.responder_mode == "llm":
        return ActionResult(ok=False, detail=summary,
                            error="auto-prepare did not finish — gates still pending after 10 passes "
                            "(is `claude` working?); run Prepare again or answer them in the Gates tab")
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


def publish_now(cfg: Config, post_id: str, *, confirmed: bool = True) -> ActionResult:
    """Ship ONE reviewed post IMMEDIATELY from the Studio (milestone 5: publish in the UI) via the
    SAME poster path the pipeline uses (post.run.publish_post) — a real post on a live backend, a
    dryrun no-op->published locally — IGNORING the post's (future) schedule, so the occasional-batch
    operator can review then ship without waiting for the schedule or touching the CLI. Same
    live-publish confirm + fatal-auth surfacing as run_advance; queued-only; scoped to THIS post
    (other scheduled posts are untouched). Distinct from mark_published (Track B: 'I posted by hand')
    — this actually drives the poster."""
    from fanops.post.run import publish_post
    if cfg.poster_backend != "dryrun" and not confirmed:
        return ActionResult(ok=False, error=f"LIVE backend ({cfg.poster_backend}): this PUBLISHES the "
                            "post to a real account — tick the confirm box, then click again.")
    try:
        with Ledger.transaction(cfg) as led:
            if post_id not in led.posts:
                return ActionResult(ok=False, error=f"no such post: {post_id}")
            st = led.posts[post_id].state
            if st is not PostState.queued:
                return ActionResult(ok=False, error=f"post {post_id} is {st.value} — only a queued post can be published")
            led = publish_post(led, cfg, post_id, in_transaction=True)
            state = led.posts[post_id].state.value
    except AuthError as exc:
        # bad/missing key fails every post — publish_post re-raises (halt); name the right key per backend.
        key = "POSTIZ_API_KEY" if cfg.poster_backend == "postiz" else "BLOTATO_API_KEY"
        return ActionResult(ok=False, error=f"FATAL auth failure — check {key}: {str(exc)[:160]}")
    except Exception as exc:
        # A non-auth failure (media upload RuntimeError, corrupt clip.path, etc.) must NOT escape to
        # Flask as a 500 — the cockpit surfaces it cleanly (mirrors run_advance's broad catch).
        return ActionResult(ok=False, error=f"publish failed: {str(exc)[:160]}")
    # ONLY 'published' is success: _submit_one advances submitted -> published on a clean poster
    # return, so any other terminal state (failed, or a poster that stalled at submitting/submitted)
    # means the post did NOT fully ship — report it incomplete rather than a false success.
    if state == "published":
        return ActionResult(ok=True, detail={"post_id": post_id, "state": state})
    return ActionResult(ok=False, error=f"publish did not complete (post is {state}) — see the run log")


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


def release_held_clip(cfg: Config, clip_id: str) -> ActionResult:
    """Clear a brand-risk hold from the browser — the UI twin of `fanops unhold`. Reuses the canonical
    transition (cli.py unhold): held->captions_requested so the next advance re-runs the caption gate.
    Tight local transaction, no network. Rejects a non-held clip so a stray click can't churn a live
    clip's state (stricter than the operator-trusted CLI verb)."""
    with Ledger.transaction(cfg) as led:
        if clip_id not in led.clips: return ActionResult(ok=False, error=f"no such clip: {clip_id}")
        c = led.clips[clip_id]
        if not c.held: return ActionResult(ok=False, error=f"clip {clip_id} is not held (state={c.state.value})")
        c.held = False; c.held_reason = None; c.state = ClipState.captions_requested
    return ActionResult(ok=True, detail={"clip_id": clip_id, "state": ClipState.captions_requested.value})


def approve_stitches(cfg: Config, ids: Sequence[str]) -> ActionResult:
    """M3 operator approval (multi-select): suggested -> approved for each selected stitch_plan in ONE
    transaction, idempotent (a non-suggested plan is a no-op). Never a 500."""
    sel = [i for i in (ids or []) if i]
    try:
        with Ledger.transaction(cfg) as led:
            for pid in sel: led.approve_stitch_plan(pid)
    except Exception as exc:
        return ActionResult(ok=False, error=f"approve failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"approved": len(sel)})

def dismiss_stitches(cfg: Config, ids: Sequence[str]) -> ActionResult:
    """M3 operator dismiss (multi-select): suggested|approved -> dismissed (terminal) for each selected
    stitch_plan in ONE transaction, idempotent. Never a 500."""
    sel = [i for i in (ids or []) if i]
    try:
        with Ledger.transaction(cfg) as led:
            for pid in sel: led.dismiss_stitch_plan(pid)
    except Exception as exc:
        return ActionResult(ok=False, error=f"dismiss failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"dismissed": len(sel)})
