# src/fanops/studio/actions.py — CREATE
"""Lock-safe Studio mutations (no Flask). Each public action opens ONE Ledger.transaction and does
its existence + state(queued) + not-imminent guard + mutation INSIDE the lock, on the in-lock
freshly-loaded ledger — mirroring the CLI recovery verbs (cli.py:285,298) so it cannot lose-update
against a concurrent cron `fanops run`. Reads/normalization that can fail happen OUTSIDE the lock."""
from __future__ import annotations
import copy
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
from fanops.models import CaptionSet, ClipState, MomentDecision, MomentHookDecision, Post, PostState
from fanops.ids import child_id, surface_key, _hash
from fanops import overlay
from fanops.timeutil import parse_iso, iso_z
from fanops.studio.views import _imminent, suggest_time

SNOOZE_DAYS = 365
_GATE_MODELS = {"moments": MomentDecision, "moment_hooks": MomentHookDecision, "captions": CaptionSet}
_VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}   # the has_video_stream subset of MEDIA_EXT
if not (_VIDEO_EXT <= MEDIA_EXT): raise ValueError("_VIDEO_EXT drifted out of ingest.MEDIA_EXT")  # import-time drift guard (not assert — survives -O)

def _inherit_captions(meta: dict | None) -> dict:
    """DEEP-copy a sibling clip's meta_captions for an inheriting clip (release_stitches / approve_with_hook).
    A shallow dict()/model_copy shares the inner {caption,hashtags} dicts, so a later in-place edit to one
    clip's caption would silently corrupt the other — defended here (latent today; captions are replaced, not
    mutated in place — but this makes it structural)."""
    return copy.deepcopy(meta or {})


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
    """Return (post, None) if the post is editable: an awaiting_approval post (the Review worklist — gated,
    so never imminent) OR a queued (approved) post that is not imminent (the Schedule cockpit). Else
    (None, error). post-approval-lifecycle: the operator edits/regenerates/reschedules BEFORE approving."""
    if post_id not in led.posts:
        return None, f"no such post: {post_id}"
    p = led.posts[post_id]
    if p.state is PostState.awaiting_approval:
        return p, None                                 # awaiting -> always editable (it cannot ship yet)
    if p.state is not PostState.queued:
        return None, f"post {post_id} is {p.state.value}; only awaiting-approval or queued posts are editable"
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


def clear_time(cfg: Config, post_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    """P1: deliberately DROP a post's scheduled_time. On an awaiting post just clears it. On a QUEUED post,
    FIRST sends it back to awaiting_approval (unapprove) THEN clears — both in ONE transaction, in that order,
    so the post is NEVER persisted as queued-and-timeless (which publish_due would publish-now). Reuses
    _guard_editable_post (rejects unknown/imminent/wrong-state), mirroring reschedule_post's shape. The
    unapprove uses the immutable model_copy (ledger layer); the scheduled_time=None is the in-place actions-
    layer edit (like reschedule_post line 89) — consistent with both conventions."""
    now = _now(now)
    with Ledger.transaction(cfg) as led:
        p, err = _guard_editable_post(led, post_id, now)
        if err:
            return ActionResult(ok=False, error=err)
        if p.state is PostState.queued:
            led.unapprove_post(post_id)        # queued -> awaiting FIRST (model_copy), so it's never queued+None
        led.posts[post_id].scheduled_time = None
    return ActionResult(ok=True, detail={"post_id": post_id})


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


def reburn_hook(cfg: Config, post_id: str, hook: str, *, now: Optional[datetime] = None) -> ActionResult:
    """Face 4 — re-burn ONE editable surface's on-screen HOOK (NO LLM). The operator edits the literal
    per-account hook text; this re-burns it via ffmpeg (overlay.burn_hook_only) onto the SAME deterministic
    variant path /media serves, then a SHORT transaction flips post.variant_hook + post.media_urls ONLY.
    Both survive repost_post (the real 'Post again' reuse path). It NEVER writes clip.meta_captions['hook']
    — that key is dead (the on-screen-hook source of truth is Moment.hooks_by_persona, read at crosspost).
    Gated on cfg.creative_variation (per-surface variant burns only exist then). The 600s ffmpeg runs
    LOCK-FREE (the 60s flock guard forbids holding the lock across it — mirror regenerate_caption); the
    field flip is re-guarded inside a short transaction. hook_burn_failed (burn returns False — no libass /
    nothing burnable) -> ok=True, detail.hook_burned=False (WARN, surfaced; an EDIT, so NO rollback, unlike
    approve_with_hook). Does NOT publish — safe on any backend, no confirm gate."""
    if not cfg.creative_variation:
        return ActionResult(ok=False, error="re-burn needs per-account hooks ON (FANOPS_CREATIVE_VARIATION)")
    from fanops.models import Fmt, PLATFORM_ASPECT, Render, RenderState
    now = _now(now)
    led = Ledger.load(cfg)                              # lock-free read: reject early, then burn OUTSIDE the lock
    p, err = _guard_editable_post(led, post_id, now)
    if err:
        return ActionResult(ok=False, error=err)
    clip = led.clips.get(p.parent_id)
    if clip is None:
        return ActionResult(ok=False, error=f"no clip for post {post_id}")
    # The on-screen hook is owned by the per-account RENDER (the single source of truth). A hook EDIT changes
    # the content -> a NEW content-addressed render id (child_id of clip+hook); burn it (atomic, LOCK-FREE)
    # and point the post at it. The render's hook_text ALWAYS matches the burned pixels — the old reburn
    # mutated post.variant_hook alone and drifted from the file. Lineage for filing: clip->moment->source.
    aspect = PLATFORM_ASPECT.get(p.platform, Fmt.r9x16)
    tw, th = {Fmt.r9x16: (1080, 1920), Fmt.r1x1: (1080, 1080), Fmt.r16x9: (1920, 1080)}.get(aspect, (1080, 1920))
    # AUDIT H1: the render IDENTITY + cut decision come from the SAME source the crosspost mint uses
    # (account_render_spec), so a re-burn of an OVERRIDE account (its own length/framing) PRESERVES the
    # per-account CUT instead of silently reverting it to a bare-hook, global-length, centred shared clip.
    from fanops.crosspost import account_render_spec
    from fanops.clip import render_account_cut
    from fanops.accounts import Accounts
    acct = next((a for a in Accounts.load(cfg).accounts if a.handle == p.account), None)   # None -> global defaults
    rid, wants_cut, acct_profile, acct_top_bias = account_render_spec(cfg, clip=clip, hook=hook, acct=acct)
    mom = led.moments.get(clip.parent_id)
    src = led.sources.get(mom.parent_id) if mom is not None else None
    batch_id = src.batch_id if src is not None else None
    source_id = src.id if src is not None else None
    skey = surface_key(p.account, p.platform.value)
    vpath = cfg.render_path(batch_id, source_id, rid, aspect)   # filed under clips/{batch}/{src}/; mkdirs
    produced = False
    if wants_cut:                                       # override account: re-cut the SOURCE at its own band+crop (LOCK-FREE)
        produced = render_account_cut(led, cfg, clip.parent_id, aspect=aspect, profile=acct_profile,
                                      hook=hook, out_path=vpath, top_bias=acct_top_bias)
    burned = produced
    if not produced:                                    # default band/frame OR a failed cut -> shared-clip burn
        burned = overlay.burn_hook_only(clip.path, vpath, hook, width=tw, height=th,
                                        font=cfg.subtitle_font)   # LOCK-FREE; atomic + fail-open: vpath always exists
    with Ledger.transaction(cfg) as led2:               # re-guard + write INSIDE a short transaction
        p2, err2 = _guard_editable_post(led2, post_id, _now(None))   # fresh now: the burn may have made it imminent
        if err2:
            return ActionResult(ok=False, error=err2)
        # add_render is content-addressed first-write-wins: a re-burn of an EXISTING hook reuses the same
        # render; a NEW hook adds a fresh one (the prior render, if now unreferenced, is GC-swept by state).
        # is_account_cut mirrors the crosspost mint: truthful when an override account got its own cut.
        led2.add_render(Render(id=rid, clip_id=p2.parent_id, account=p2.account, surface_key=skey,
                               hook_text=hook, path=vpath, state=RenderState.rendered,
                               batch_id=batch_id, source_id=source_id, is_account_cut=produced))
        p2.render_id = rid                              # the authoritative pointer
        p2.variant_hook = hook                          # read-only mirror of Render.hook_text (carried by repost_post)
        p2.media_urls = [f"file://{vpath}"]
    return ActionResult(ok=True, detail={"post_id": post_id, "hook": hook, "hook_burned": bool(burned),
                                         "render_id": rid, "media_url": f"file://{vpath}"})


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


def run_ingest(cfg: Config, *, batch_name: str = "", target_accounts=()) -> ActionResult:
    """Drive `fanops ingest` from the browser: catalogue 01_inbox under one transaction (the exact
    cmd_ingest path). When batch_name is non-blank, mint a named, account-targeted Batch in the SAME
    transaction and catalogue the inbox under its id (blank name => today's ungrouped ingest, byte-
    identical). A toolchain-absent / control-file error is surfaced as a clean ActionResult, never a 500."""
    from fanops.ingest import ingest_drops
    from fanops.digest import write_digest
    from fanops.batches import create_batch
    from fanops.accounts import Accounts
    n = 0; batch = None
    try:
        with Ledger.transaction(cfg) as led:
            if batch_name.strip():
                # Account-First (T1/T4): feed the active-handle set so a batch targeting a dead/typo'd
                # handle is FLAGGED at creation (else crosspost silently skips every surface -> 0 posts).
                active = {a.handle for a in Accounts.load(cfg).active()}   # loaded only on the batched path (byte-identical otherwise)
                batch = create_batch(led, name=batch_name, target_accounts=list(target_accounts),
                                     now_iso=iso_z(_now(None)), active_handles=active)
            led = ingest_drops(led, cfg, batch_id=(batch.id if batch else None))
            n = len(led.sources)
        write_digest(Ledger.load(cfg), cfg)
    except Exception as exc:
        return ActionResult(ok=False, error=f"ingest failed: {str(exc)[:160]}")
    detail = {"sources": n}
    if batch is not None:
        detail.update(batch=batch.name, batch_id=batch.id)
        if batch.error_reason: detail["warnings"] = [batch.error_reason]   # zero-target advisory -> Studio Run panel
    return ActionResult(ok=True, detail=detail)


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
                 allowed_ext: Optional[set[str]] = None, dest_dir: Optional[Path] = None) -> ActionResult:
    """Stream operator-uploaded raw video into cfg.inbox so `run_ingest` catalogues it — the browser
    replacement for a Finder drag. Each file is validated (video ext, traversal-safe name), streamed to
    a `.uploadpart` temp in the inbox, then os.replace'd into place (so a half-upload never appears to
    ingest). Untrusted input crossing a boundary: the raw-name traversal triad + secure_filename + an
    inbox-bound resolve are the path-safety gates; Flask's MAX_CONTENT_LENGTH (set in create_app) refuses
    an oversize body BEFORE this runs. Never 500s — every fallible step yields a skip reason. ok is True
    iff at least one file landed (all-rejected is a failure, not a green no-op)."""
    if allowed_ext is None: allowed_ext = _VIDEO_EXT   # ECC fix #12: no shared mutable default (module set)
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


def save_uploads_and_ingest(cfg: Config, files: Sequence[FileStorage], *, batch_name: str = "",
                            target_accounts=()) -> ActionResult:
    """One-click upload->catalogue (M5 fast-follow): stream the uploads (save_uploads) and, IF any landed,
    immediately run the ingest pass so the operator doesn't need a second 'Ingest inbox' click. A save
    failure short-circuits (nothing landed -> nothing to ingest). An ingest failure is surfaced but the
    files are SAFELY in 01_inbox — a manual 'Ingest inbox' still catalogues them — so it's a recoverable
    not-fully-done, never a lost upload. Returns the merged detail (saved/skipped + sources). batch_name/
    target_accounts thread through to run_ingest so an upload can mint its named batch in one click."""
    up = save_uploads(cfg, files)
    if not up.ok:
        return up                                          # nothing landed -> nothing to ingest
    ing = run_ingest(cfg, batch_name=batch_name, target_accounts=target_accounts)
    detail = {**(up.detail or {}), **(ing.detail or {})}
    if not ing.ok:
        n = len((up.detail or {}).get("saved", []))
        return ActionResult(ok=False, detail=detail,
                            error=f"uploaded {n} file(s), but auto-ingest failed (they're in 01_inbox — "
                                  f"click 'Ingest inbox' to retry): {ing.error}")
    return ActionResult(ok=True, detail=detail)


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
    if cfg.is_live and not confirmed:
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
    if cfg.is_live and not confirmed:
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
    if cfg.is_live and not confirmed:
        return ActionResult(ok=False, error=f"LIVE backend ({cfg.poster_backend}): this PUBLISHES the "
                            "post to a real account — tick the confirm box, then click again.")
    # Short lock-free guard read for a friendly message; publish_post's own CLAIM transaction is the
    # authoritative queued-only gate (a state change in the gap is re-validated there -> a clean no-op).
    led = Ledger.load(cfg)
    if post_id not in led.posts:
        return ActionResult(ok=False, error=f"no such post: {post_id}")
    st = led.posts[post_id].state
    if st is not PostState.queued:
        return ActionResult(ok=False, error=f"post {post_id} is {st.value} — only a queued post can be published")
    try:
        # network runs OUTSIDE the ledger lock (per-post claim->network->finalize) — the Studio no longer
        # holds the flock across the publish round-trip, so a concurrent daemon pass isn't starved.
        state = publish_post(cfg, post_id)
    except AuthError as exc:
        # bad/missing key fails every post — publish_post re-raises (halt); name the right key per backend.
        key = "POSTIZ_API_KEY" if cfg.poster_backend == "postiz" else "BLOTATO_API_KEY"
        return ActionResult(ok=False, error=f"FATAL auth failure — check {key}: {str(exc)[:160]}")
    except Exception as exc:
        # A non-auth failure (media upload RuntimeError, corrupt clip.path, etc.) must NOT escape to
        # Flask as a 500 — the cockpit surfaces it cleanly (mirrors run_advance's broad catch).
        return ActionResult(ok=False, error=f"publish failed: {str(exc)[:160]}")
    # ONLY 'published' is success: _publish_one advances submitted -> published on a clean poster return,
    # so any other terminal state means the post did NOT fully ship. A None return means the CLAIM gate
    # found it no longer queued (e.g. a concurrent daemon pass just claimed it between the guard read and
    # the claim) — tell the operator to retry rather than print a confusing "post is None".
    if state == "published":
        return ActionResult(ok=True, detail={"post_id": post_id, "state": state})
    if state is None:
        return ActionResult(ok=False, error="post was not claimable (it may be publishing already) — refresh and try again")
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
            # bump both approved (queued) and pre-approval (awaiting_approval) posts — Review shows the
            # latter, so a Review-card snooze must actually move something (not a silent 0-count no-op).
            if (p.parent_id == clip_id and p.state in (PostState.queued, PostState.awaiting_approval)
                    and not _imminent(p.scheduled_time, now)):
                p.scheduled_time = z
                count += 1
    return ActionResult(ok=True, detail={"clip_id": clip_id, "count": count, "scheduled_time": z})


def repost_post(cfg: Config, post_id: str) -> ActionResult:
    """'Post again' (post-approval-lifecycle): spawn a NEW awaiting_approval post from the SAME clip+surface
    as a shipped post, re-entering the approval gate. The source post stays immutable history. Honors
    fan-accounts-repost-freely — reposting is allowed; this is NOT a supersede. The new id is content-
    addressed with a repost epoch (count of existing posts for this clip+surface) so it never collides with
    the original or a prior repost, and `add_post`'s setdefault therefore does not silently drop it. The
    operator schedules it on approval (scheduled_time=None). One transaction, never a 500."""
    try:
        with Ledger.transaction(cfg) as led:
            src = led.posts.get(post_id)
            if src is None: return ActionResult(ok=False, error=f"no such post: {post_id}")
            skey = surface_key(src.account, src.platform.value)
            epoch = sum(1 for p in led.posts.values()                       # originals + prior reposts for this surface
                        if p.parent_id == src.parent_id and p.account == src.account and p.platform is src.platform)
            new_id = child_id("post", src.parent_id, f"{skey}#r{epoch}")
            led.add_post(Post(id=new_id, parent_id=src.parent_id, state=PostState.awaiting_approval,
                              account=src.account, account_id=src.account_id, platform=src.platform,
                              caption=src.caption, hashtags=list(src.hashtags or []), aspect=src.aspect,
                              media_urls=list(src.media_urls or []), scheduled_time=None,
                              created_at=iso_z(_now(None)),   # content-lifecycle: fresh birth day (aware)
                              submission_id=f"fanops_{_hash('idemp', new_id)}",
                              first_frame_kind=src.first_frame_kind,
                              cut_seconds=src.cut_seconds, clip_profile=src.clip_profile,
                              batch_id=src.batch_id,   # Account-First Studio: a repost keeps its source batch grouping
                              variant_key=src.variant_key, variant_hook=src.variant_hook,
                              variation_axis=src.variation_axis))   # carry P2 axis so a repost's attribution isn't lost
    except Exception as exc:
        return ActionResult(ok=False, error=f"repost failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"post_id": new_id, "source_id": post_id})

def _warm_target_aspect(cfg: Config, moment_id: str, aspect) -> None:
    # #4 lock-free pre-render (mirror pipeline._prewarm): _clip_for_aspect on a THROWAWAY Ledger.load snapshot
    # reuses an existing render OR runs render_moment, which writes cid.mp4 + its fingerprint sidecar with NO
    # flock held. The in-transaction _clip_for_aspect below then hits the fingerprint-skip and mints
    # microseconds-fast instead of running ffmpeg (600s-bound) UNDER the lock — N bulk clips no longer
    # serialize N renders behind the write lock. FAIL-OPEN: any error here just means the in-lock path renders
    # as today (never a crash); the snapshot state is discarded — only the on-disk mp4+fp persist, and the
    # transaction re-resolves authoritatively.
    from fanops.crosspost import _clip_for_aspect
    try: _clip_for_aspect(Ledger.load(cfg), cfg, moment_id, aspect)
    except Exception: pass

def crosspost_to_account(cfg: Config, clip_id: str, target_account: str, platform: str, *,
                         now: Optional[datetime] = None) -> ActionResult:
    """Cross-account reuse (content-lifecycle Phase 4): mint a fresh awaiting_approval post of an EXISTING clip
    on a NEW (target_account, platform) surface — how a later-onboarded account gets posts for clips that
    already left ClipState.captioned. Honors fan-accounts-repost-freely: NO supersede/dedup beyond the per-
    (clip,surface) content-addressed setdefault; NO one-version-per-moment guard. Does NOT reset clip state and
    does NOT re-run moments. Aspect-correct (renders/reuses the target aspect via _clip_for_aspect) and
    duration-capped (PLATFORM_MAX_SECONDS, mirroring crosspost_clips). Caption: the clip's per-surface caption
    if present, else an EMPTY caption + empty hashtags (the operator edits in Review before approving — a
    deliberate softening of the seed-tag fallback, which lives upstream in the caption pipeline, not at mint).
    created_at is wall-clock birth (NOT part of the pid). Enters the standard approval gate, scheduled_time=None.
    One transaction, never a 500."""
    from fanops.accounts import Accounts
    from fanops.models import Platform, PLATFORM_ASPECT, PLATFORM_MAX_SECONDS, Fmt
    from fanops.crosspost import _clip_for_aspect
    now = _now(now)
    try: plat = Platform(platform)
    except ValueError: return ActionResult(ok=False, error=f"unknown platform: {platform!r}")
    try: accts = Accounts.load(cfg)
    except Exception as exc: return ActionResult(ok=False, error=f"accounts.json: {str(exc)[:160]}")
    surf = next((s for s in accts.surfaces() if s.account == target_account and s.platform is plat), None)
    if surf is None:
        return ActionResult(ok=False, error=f"no active surface {target_account}/{platform} — onboard it in Go Live first")
    skey = surface_key(surf.account, surf.platform.value)
    aspect = PLATFORM_ASPECT.get(plat, Fmt.r9x16)
    pre = Ledger.load(cfg).clips.get(clip_id)                                  # #4: lock-free read of the moment id...
    if pre is not None: _warm_target_aspect(cfg, pre.parent_id, aspect)        # ...so the target aspect renders OUTSIDE the flock
    try:
        with Ledger.transaction(cfg) as led:
            clip = led.clips.get(clip_id)
            if clip is None: return ActionResult(ok=False, error=f"no such clip: {clip_id}")
            if clip.held or led.is_retired_clip(clip.id) or led.is_retired_moment(clip.parent_id):
                return ActionResult(ok=False, error=f"clip {clip_id} is held/retired — not eligible for cross-post")
            m = led.moments.get(clip.parent_id)
            clip_dur = (m.end - m.start) if m is not None else None
            max_secs = PLATFORM_MAX_SECONDS.get(plat)
            if max_secs is not None and clip_dur is not None and clip_dur > 0 and clip_dur > max_secs:
                return ActionResult(ok=False, error=f"clip duration {clip_dur:.0f}s exceeds {platform} cap {max_secs}s")
            target_clip = _clip_for_aspect(led, cfg, clip.parent_id, aspect)   # the RIGHT-aspect render (H7); warm -> fingerprint-skip
            pid = child_id("post", target_clip.id, skey)
            if pid in led.posts:                                               # honest report (H9)
                return ActionResult(ok=True, detail={"post_id": pid, "clip_id": clip_id, "already_exists": True,
                                                     "surface": f"{surf.account}/{surf.platform.value}"})
            if not (target_clip.path and os.path.exists(target_clip.path)):    # #10: a gc-swept render -> refuse at mint,
                return ActionResult(ok=False, error=f"clip {clip_id} render missing on disk — re-run the clip before cross-posting")  # not silently at publish
            cap = clip.meta_captions.get(f"{surf.account}/{surf.platform.value}")
            caption = cap["caption"] if isinstance(cap, dict) and cap.get("caption") else ""
            hashtags = list(cap.get("hashtags", [])) if isinstance(cap, dict) else []
            led.add_post(Post(id=pid, parent_id=target_clip.id, state=PostState.awaiting_approval,
                              account=surf.account, account_id=surf.account_id, platform=surf.platform,
                              caption=caption, hashtags=hashtags, aspect=aspect, scheduled_time=None,
                              created_at=iso_z(now), submission_id=f"fanops_{_hash('idemp', pid)}",
                              clip_profile=cfg.clip_profile))
    except Exception as exc:
        return ActionResult(ok=False, error=f"cross-post failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"post_id": pid, "clip_id": clip_id, "already_exists": False,
                                         "surface": f"{surf.account}/{surf.platform.value}"})

def crosspost_all_to_account(cfg: Config, source_account: str, target_account: str, platform: str, *,
                             now: Optional[datetime] = None) -> ActionResult:
    """Bulk cross-account backfill (content-lifecycle Phase 4): mint an awaiting_approval post on
    (target_account, platform) for EVERY clip already posted to source_account. Each enters the approval gate.
    Honors repost-freely (per-(clip,surface) setdefault is the only dedup, so a re-run is a clean no-op).
    clip_ids is a SET — a multi-platform source_account yields one source post per platform per clip, the set
    collapses them to ONE crosspost_to_account call per clip (correct: fan out once per clip). Reports
    minted / already_exists / skipped honestly. LATENCY (ECC review): a FIRST fan-out to an aspect that has
    no existing render makes each clip pay an ffmpeg render (600s-bound) under its own short lock — N clips
    serialize N renders. Not a deadlock (per-clip lock, released between clips) and mirrors crosspost_clips;
    the common same-aspect reuse returns instantly. Operator-gated, single-operator Studio."""
    if source_account == target_account:                 # bulk backfill is CROSS-account; same->same is a no-op
        return ActionResult(ok=False, error=f"source and target are the same account ({source_account}) — pick a different target")
    led = Ledger.load(cfg)
    clip_ids = sorted({p.parent_id for p in led.posts.values() if p.account == source_account})
    if not clip_ids:
        return ActionResult(ok=False, error=f"no clips posted to {source_account} — nothing to backfill")
    minted, existed, skipped = [], [], []
    for cid in clip_ids:
        r = crosspost_to_account(cfg, cid, target_account, platform, now=now)
        if not r.ok: skipped.append(cid)
        elif r.detail and r.detail.get("already_exists"): existed.append(cid)
        else: minted.append(cid)
    if not minted and not existed:
        return ActionResult(ok=False, error=f"nothing minted ({len(skipped)} skipped) — held/retired or bad surface",
                            detail={"minted": 0, "already_exists": 0, "skipped": len(skipped)})
    return ActionResult(ok=True, detail={"minted": len(minted), "already_exists": len(existed),
                                         "skipped": len(skipped), "target": f"{target_account}/{platform}"})

def reschedule_bucket(cfg: Config, *, now: Optional[datetime] = None) -> ActionResult:
    """Routine re-spread of the APPROVED bucket: re-stagger every queued (approved) post onto a fresh
    cadence starting from `now`, reusing crosspost's proven deterministic stagger (surface_time). Skips
    imminent posts (about to fire — don't disturb them) and never touches awaiting/published/etc. One
    transaction, idempotent-by-`now`, never a 500. The Schedule-tab 'reschedule all' control."""
    from fanops.crosspost import surface_time
    now = _now(now); date_str = now.date().isoformat()
    due: list = []
    try:
        with Ledger.transaction(cfg) as led:
            due = [p for p in led.posts.values() if p.state is PostState.queued and not _imminent(p.scheduled_time, now)]
            due.sort(key=lambda p: (p.scheduled_time or "", p.account, p.platform.value, p.id))  # stable order in
            for i, p in enumerate(due):
                p.scheduled_time = surface_time(now, p.account, p.platform.value, date_str, i,
                                                clip_id=p.parent_id, lead_minutes=cfg.publish_lead_minutes)
    except Exception as exc:
        return ActionResult(ok=False, error=f"reschedule failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"rescheduled": len(due)})

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


def approve_posts(cfg: Config, ids: Sequence[str], *, now: Optional[datetime] = None) -> ActionResult:
    """Post-approval gate (multi-select, the Review-tab batch): awaiting_approval -> queued for each
    selected post in ONE transaction, idempotent (a non-awaiting post is a no-op). One `now` stamp for
    the whole batch so approve_post's stale-schedule bump is consistent. Never a 500."""
    sel = [i for i in (ids or []) if i]
    now = _now(now); now_iso = iso_z(now)
    try:
        with Ledger.transaction(cfg) as led:
            for pid in sel:                                  # P1: untimed/stale post -> a strictly-future suggestion (not now)
                post = led.posts.get(pid)
                sugg = suggest_time(cfg, post, now=now) if post is not None else None
                led.approve_post(pid, now_iso=now_iso, suggested_iso=sugg)
    except Exception as exc:
        return ActionResult(ok=False, error=f"approve failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"approved": len(sel)})

def reject_posts(cfg: Config, ids: Sequence[str]) -> ActionResult:
    """Operator discard (multi-select): awaiting_approval -> rejected (terminal) for each selected post
    in ONE transaction, idempotent. Never a 500."""
    sel = [i for i in (ids or []) if i]
    try:
        with Ledger.transaction(cfg) as led:
            for pid in sel: led.reject_post(pid)
    except Exception as exc:
        return ActionResult(ok=False, error=f"reject failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"rejected": len(sel)})

def unapprove_post(cfg: Config, post_id: str) -> ActionResult:
    """Send an approved-but-unsent post back to Review (the Schedule-tab 'send back' control): queued ->
    awaiting_approval. Idempotent; a non-queued post is a clean no-op. Tight transaction, no network."""
    try:
        with Ledger.transaction(cfg) as led:
            if post_id not in led.posts: return ActionResult(ok=False, error=f"no such post: {post_id}")
            led.unapprove_post(post_id)
    except Exception as exc:
        return ActionResult(ok=False, error=f"unapprove failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"post_id": post_id})

def _warm_hooked_render(cfg: Config, moment_id: str, aspect, hook: str) -> None:
    """Lock-free pre-render of the HOOKED clip (mirror _warm_target_aspect, but FORCE the burn): set the
    restored hook on a THROWAWAY Ledger.load snapshot's moment and call render_moment, which writes cid.mp4
    + its fingerprint sidecar with the hook burned and NO flock held. The in-lock render_moment in
    approve_with_hook then hits the fingerprint-skip and adopts it WITHOUT running ffmpeg under the lock —
    _clip_for_aspect would have REUSED the old clean render, so the warm must drive render_moment directly.
    FAIL-OPEN: any error just means the in-lock path renders (bounded 600s); the snapshot is discarded."""
    from fanops.clip import render_moment
    try:
        snap = Ledger.load(cfg)
        mom = snap.moments.get(moment_id)
        if mom is None: return
        snap.moments[moment_id] = mom.model_copy(update={"hook": hook, "hook_removed": None})
        render_moment(snap, cfg, moment_id, aspect=aspect)
    except Exception: pass

def approve_with_hook(cfg: Config, clip_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    """The 'restore the auto-removed hook, then approve' half of the removed-hook choice (the operator's
    core ask, slice 2). RESTORES moment.hook from moment.hook_removed, RE-RENDERS the clip so the hook BURNS
    into the mp4 (lock-free pre-warm -> in-lock fingerprint-skip; mirrors crosspost's #4 warm), PRESERVES the
    clip's captioned state + per-surface captions across the re-render, then approves EVERY awaiting_approval
    post of the clip. A render failure rolls the whole thing back (atomic) and surfaces the error — the
    operator asked for the hook, so we never silently ship clean. No awaiting posts -> a clean no-op that
    does NOT touch a possibly-shipped render. One transaction for the commit; the heavy ffmpeg ran outside it."""
    from fanops.clip import render_moment
    if cfg.creative_variation:
        return ActionResult(ok=False, error="creative variation is ON — per-surface hooks own the on-screen "
                            "burn, so the moment hook can't be restored this way (turn off FANOPS_CREATIVE_VARIATION).")
    now = _now(now); now_iso = iso_z(now)
    snap = Ledger.load(cfg)                               # lock-free: resolve the removed hook + PRE-WARM the render
    c0 = snap.clips.get(clip_id)
    if c0 is None: return ActionResult(ok=False, error=f"no such clip: {clip_id}")
    m0 = snap.moments.get(c0.parent_id)
    removed = (m0.hook_removed if m0 is not None else None)
    if removed: _warm_hooked_render(cfg, c0.parent_id, c0.aspect, removed)   # ffmpeg OUTSIDE the flock
    approved = 0
    try:
        with Ledger.transaction(cfg) as led:
            clip = led.clips.get(clip_id)
            if clip is None: return ActionResult(ok=False, error=f"no such clip: {clip_id}")
            ids = [p.id for p in led.posts.values()
                   if p.parent_id == clip_id and p.state is PostState.awaiting_approval]
            mom = led.moments.get(clip.parent_id)
            restored = (mom.hook_removed if mom is not None else None)
            if ids and restored:                          # only re-render when there's actually a post to ship with it
                led.moments[clip.parent_id] = mom.model_copy(update={"hook": restored, "hook_removed": None})
                orig = led.clips[clip_id]
                led, rc = render_moment(led, cfg, clip.parent_id, aspect=clip.aspect)   # fp-skip adopts the warm mp4
                if rc.state is ClipState.error:
                    raise RuntimeError(rc.error_reason or "clip re-render failed")
                if rc.hook_burn_failed:                        # CRITICAL (ecc review): a SUCCESSFUL render that
                    # couldn't burn the hook (ffmpeg lacks the text filter, or the hook made no burnable text)
                    # would ship the post CLEAN. The operator asked for the hook -> roll back, never silent-clean.
                    raise RuntimeError("hook burn failed — ffmpeg can't render on-screen text (no libass), "
                                       "or the hook produced nothing burnable; not shipping clean")
                led.clips[clip_id] = led.clips[clip_id].model_copy(
                    update={"state": orig.state, "meta_captions": _inherit_captions(orig.meta_captions)})   # keep captioned state + DEEP-copied captions
            for pid in ids:                                  # P1: untimed/stale post -> a strictly-future suggestion (not now)
                post = led.posts.get(pid)
                sugg = suggest_time(cfg, post, now=now) if post is not None else None
                led.approve_post(pid, now_iso=now_iso, suggested_iso=sugg)
            approved = len(ids)
    except Exception as exc:
        return ActionResult(ok=False, error=f"approve-with-hook failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"approved": approved, "clip_id": clip_id, "hook": bool(removed)})

def _approve_matching(cfg: Config, pred, *, now: Optional[datetime] = None, detail: Optional[dict] = None) -> ActionResult:
    """Approve EVERY awaiting_approval post matching `pred` in ONE transaction (the shared spine for the
    scoped bulk-approve actions). One `now` stamp for the whole batch so approve_post's stale-schedule bump
    is consistent; P1 strictly-future suggestion per post (never machine-guns to now). Idempotent, never a
    500. `detail` is merged into the result (e.g. {"clip_id": ...} / {"account": ...})."""
    now = _now(now); now_iso = iso_z(now); approved = 0
    try:
        with Ledger.transaction(cfg) as led:
            ids = [p.id for p in led.posts.values() if p.state is PostState.awaiting_approval and pred(p)]
            for pid in ids:                                  # P1: untimed/stale post -> a strictly-future suggestion (not now)
                post = led.posts.get(pid)
                sugg = suggest_time(cfg, post, now=now) if post is not None else None
                led.approve_post(pid, now_iso=now_iso, suggested_iso=sugg)
            approved = len(ids)
    except Exception as exc:
        return ActionResult(ok=False, error=f"approve failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={**(detail or {}), "approved": approved})

def approve_clip(cfg: Config, clip_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    """M3b 'all accounts of this moment': one-click approve EVERY awaiting_approval surface of ONE clip, so
    the operator approves a whole moment's per-account set without ticking each box. Idempotent, never a 500."""
    return _approve_matching(cfg, lambda p: p.parent_id == clip_id, now=now, detail={"clip_id": clip_id})

def approve_account(cfg: Config, handle: str, *, batch: Optional[str] = None,
                    now: Optional[datetime] = None) -> ActionResult:
    """M3b 'this account across the whole video': one-click approve EVERY awaiting_approval post of ONE account
    (optionally scoped to the active batch — Post.batch_id), so the operator clears a persona's whole run at
    once. A blank handle -> clean no-op (the button only shows under an active account filter). Idempotent,
    never a 500."""
    handle = (handle or "").strip()
    if not handle:
        return ActionResult(ok=True, detail={"account": None, "approved": 0})
    return _approve_matching(cfg, lambda p: p.account == handle and (batch is None or p.batch_id == batch),
                             now=now, detail={"account": handle, "batch": batch})

def approve_as_is(cfg: Config, clip_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    """The 'ship it clean' half of the removed-hook choice: one-click approve EVERY awaiting_approval post of
    a clip WITHOUT restoring the auto-removed hook. Functionally identical to approve_clip (a clip with no
    hook_removed has nothing to restore) — delegates to it and records the no-hook choice. hook_removed stays
    on the moment as a record (the choice re-applies to any future repost). Idempotent, never a 500."""
    r = approve_clip(cfg, clip_id, now=now)
    if not r.ok:
        return r
    return ActionResult(ok=True, detail={**r.detail, "hook": False})

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

def release_stitches(cfg: Config, ids: Sequence[str]) -> ActionResult:
    """M4 operator RELEASE (multi-select): the second gate — a rendered `stitch_draft` clip the operator
    reviewed is promoted to `captioned` (now crosspost-eligible), inheriting the base clip's per-surface
    captions (an impact-cut keeps the same subject/caption as the bare clip the operator already saw). The
    ONLY transition out of stitch_draft is this explicit operator action — re-checked in-lock so a
    non-stitch_draft id is a clean no-op. Captions come from the best captioned sibling (same moment +
    aspect); none found -> released with whatever captions the base carries (crosspost skips empty surfaces).
    One transaction, idempotent, never a 500."""
    sel = [i for i in (ids or []) if i]
    released = 0
    try:
        with Ledger.transaction(cfg) as led:
            for cid in sel:
                c = led.clips.get(cid)
                if c is None or c.state is not ClipState.stitch_draft:
                    continue                                  # only a rendered stitch_draft releases
                base = _best_caption_sibling(led, c)
                if base is not None:
                    c.meta_captions = _inherit_captions(base.meta_captions)
                c.state = ClipState.captioned
                released += 1
    except Exception as exc:
        return ActionResult(ok=False, error=f"release failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"released": released})

def _best_caption_sibling(led, stitch):
    """The clip whose captions the stitch inherits: a non-stitch sibling (same moment + aspect) that
    carries meta_captions, preferring a captioned one. None if no caption-bearing sibling exists."""
    sibs = [c for c in led.clips.values() if c.parent_id == stitch.parent_id and c.aspect is stitch.aspect
            and c.id != stitch.id and c.state is not ClipState.stitch_draft and c.meta_captions]
    if not sibs:
        return None
    sibs.sort(key=lambda c: (c.state is not ClipState.captioned, c.id))   # captioned first, then deterministic
    return sibs[0]
