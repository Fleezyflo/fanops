# src/fanops/studio/actions.py — CREATE
"""Lock-safe Studio mutations (no Flask). Each public action opens ONE Ledger.transaction and does
its existence + state(queued) + not-imminent guard + mutation INSIDE the lock, on the in-lock
freshly-loaded ledger — mirroring the cmd_reconcile/cmd_resolve recovery verbs in cli.py so it cannot
lose-update against a concurrent cron `fanops run`. Reads/normalization that can fail happen OUTSIDE the lock."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from fanops.config import Config
from fanops.errors import AuthError, reason
from fanops.ledger import Ledger
from fanops.models import (CaptionSet, ClipState, MomentDecision, MomentHookDecision, PostState, _REVIEW_REVERT_BLOCKED)  # noqa: F401
from fanops.audit import write_audit
from fanops.log import get_logger
from fanops.studio.actions_common import ActionResult, _now, _inherit_captions  # noqa: F401
from fanops.studio.actions_run import (run_ingest, run_pull, save_uploads, save_uploads_and_ingest, save_thirdparty_uploads, run_ingest_thirdparty, run_advance, run_prepare, upload_init, upload_chunk, upload_finalize, catalogue_inbox, bind_queue, release_batch, release_all_held, release_reopens)  # noqa: F401
from fanops.studio.actions_approve import (approve_posts, reject_posts, unapprove_post, approve_with_hook, approve_clip, approve_batch, approve_account, approve_moment, approve_as_is, approve_with_edits, approve_stitches, dismiss_stitches, release_stitches)  # noqa: F401
from fanops.studio.actions_casting import cast_add, cast_remove  # noqa: F401
from fanops.studio.actions_segments import set_segments, clear_segments  # noqa: F401
from fanops.studio.actions_edit import edit_caption, reburn_hook, regenerate_caption, _guard_editable_post  # noqa: F401
from fanops.studio.actions_schedule import (SNOOZE_DAYS, _normalize_z, reschedule_post, clear_time, accept_suggested_account, snooze_clip, _seconds_away, reschedule_bucket, shift_account_schedule, reschedule_account, randomize_account_schedule, publish_due_bucket)  # noqa: F401
from fanops.studio.actions_publish import (mark_published, _studio_publish_guard, preflight_publish_media, reconcile_inflight, publish_now)  # noqa: F401
from fanops.studio.actions_crosspost import (repost_post, repost_to_other_accounts, crosspost_to_account, crosspost_all_to_account, _warm_target_aspect)  # noqa: F401
from fanops.studio.actions_recover import (retry_rate_limited_failures, retry_oversize_failures, retry_transient_failures, recover_posts, bulk_send_to_review, resolve_post, _refuse_retired, _rearm_to_queued)  # noqa: F401

_GATE_MODELS = {"moments": MomentDecision, "moment_hooks": MomentHookDecision, "captions": CaptionSet}


def approve_candidate(cfg: Config, eid: str) -> ActionResult:
    """Track C: approve a discover candidate from the browser — admit the original into the
    catalogue (same path as inbox ingest: discover.intake copies to 01_inbox, then catalogue_inbox
    stage+ingests a Source). Moves 00_review/<eid>.jpg into approved/ as the operator signal. eid
    must be a bare stem (no path separators / ..). Fails honestly when the manifest original is
    missing or the content matches a retired Source (retired_dedup dead-end)."""
    from fanops.discover import _load_json, intake as discover_intake
    from fanops.ids import make_id
    if not eid or "/" in eid or "\\" in eid or ".." in eid:
        return ActionResult(ok=False, error=f"bad candidate id: {eid!r}")
    thumb = cfg.review / f"{eid}.jpg"
    if not thumb.exists():
        return ActionResult(ok=False, error=f"no such candidate: {eid}")
    info = _load_json(cfg.review / "manifest.json", {}).get(eid)
    sp = info.get("source_path") if info else None
    original = Path(sp) if sp else None
    if original is None or not original.exists() or original.is_symlink():
        return ActionResult(ok=False, error=f"original missing for candidate {eid} — cannot admit")
    digest = (info or {}).get("sha256")
    sid = make_id("src", digest) if digest else None
    dst = cfg.review / "approved" / f"{eid}.jpg"

    def _restore_thumb() -> None:
        """Put the review thumbnail back so a failed approve is retryable from the Candidates tab."""
        try:
            if dst.exists() and not thumb.exists():
                dst.rename(thumb)
        except OSError as exc:
            get_logger(cfg)("candidates", eid, "approve_restore_failed", err=str(exc)[:160])

    try:                                               # read-only mount / disk full / rename race
        dst.parent.mkdir(parents=True, exist_ok=True)
        thumb.rename(dst)
    except OSError as exc:
        return ActionResult(ok=False, error=f"approve failed: {str(exc)[:160]}")
    try:
        discover_intake(cfg)                           # copy approved original(s) into 01_inbox/
    except Exception as exc:
        _restore_thumb()
        get_logger(cfg)("candidates", eid, "approve_intake_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"intake failed: {str(exc)[:160]}")
    ingest = catalogue_inbox(cfg)                      # existing stage+ingest txn site (actions_run)
    if not ingest.ok:
        _restore_thumb()
        return ActionResult(ok=False, error=ingest.error)
    ing = ingest.detail or {}
    if ing.get("retired_dedup"):
        _restore_thumb()
        rid = ing["retired_dedup"][0]
        return ActionResult(ok=False, error=f"content matches retired source {rid} — re-upload blocked",
                            detail={"eid": eid, "retired_dedup": ing["retired_dedup"]})
    if ing.get("added", 0) >= 1 and not cfg.queue_gate:
        from fanops.studio.actions_run import kick_prepare
        kick_prepare(cfg)
    detail: dict = {"eid": eid}
    if sid:
        detail["source_id"] = sid
    detail["added"] = ing.get("added", 0)
    if ing.get("retired_dedup"):
        detail["retired_dedup"] = ing["retired_dedup"]
    return ActionResult(ok=True, detail=detail)


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
        full["source_id"] = key.split(".", 1)[0]   # bare or per-account key -> source id
    try:
        validated = model(**full)
    except ValidationError as exc:
        return ActionResult(ok=False, error=reason(exc))
    response_path(cfg, kind, key).write_text(validated.model_dump_json(indent=2))
    return ActionResult(ok=True, detail={"kind": kind, "key": key})


def pull_metrics_studio(cfg: Config, *, window: str = "30d") -> ActionResult:
    """Pull analytics for live posts — closes the Posted→Learn loop from Studio."""
    if not cfg.is_live:
        return ActionResult(ok=False, error="Publishing is off — turn on Go Live before pulling metrics.")
    from fanops.track import pull_metrics, _default_list_posts
    from fanops.digest import write_digest
    from fanops.errors import fail_open
    try:
        led0 = Ledger.load(cfg)
        pollable = [p for p in led0.posts.values()
                    if p.submission_id and p.state in (PostState.published, PostState.analyzed)]
        if not pollable:
            return ActionResult(ok=True, detail={"outcome": "metrics_pulled", "analyzed": 0, "series_rows": 0,
                                                    "degraded": 0, "pollable": 0})
        rows = list(_default_list_posts(cfg, posts=pollable)(window))
    except (RuntimeError, AuthError) as exc:
        return ActionResult(ok=False, error=str(exc)[:160])
    except Exception as exc:
        get_logger(cfg)("metrics", "-", "metrics_pull_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"metrics pull failed: {str(exc)[:160]}")
    try:
        with Ledger.transaction(cfg) as led:
            before = {pid: len(p.metrics_series) for pid, p in led.posts.items()}
            led = pull_metrics(led, cfg, list_posts=lambda _w: rows, window=window)
            analyzed = len(led.posts_in_state(PostState.analyzed))
            added = deg = 0
            for pid, p in led.posts.items():
                new_rows = p.metrics_series[before.get(pid, 0):]
                added += len(new_rows)
                deg += sum(1 for r in new_rows if r.get("lift_degraded"))
    except Exception as exc:
        get_logger(cfg)("metrics", "-", "metrics_apply_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"metrics apply failed: {str(exc)[:160]}")
    with fail_open("studio.actions.pull_metrics_studio.digest"):
        write_digest(Ledger.load(cfg), cfg)
    write_audit(cfg, "pull_metrics", [], reason="studio_pull_metrics", analyzed=analyzed, series_rows=added)
    return ActionResult(ok=True, detail={"outcome": "metrics_pulled", "analyzed": analyzed,
                                          "series_rows": added, "degraded": deg, "pollable": len(pollable)})


# dryrun-boundary M3: revert_phantom_published (+ its CLI verb) is DELETED. It was the operator's
# recovery path for reconcile-laundered phantom `published` rows — a class the boundary makes
# unconstructable (a dryrun post never reaches `published`; nothing writes a terminal-without-url row).
# The 29 legacy rows it once cleaned were pruned outright (M4). No detector, no undo — the bad row
# can't be built in the first place.


def restore_persona_hook(cfg: Config, post_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    """Restore a guard-stripped hook onto the owner moment and re-render."""
    led = Ledger.load(cfg)
    p = led.posts.get(post_id)
    if p is None:
        return ActionResult(ok=False, error=f"no such post: {post_id}")
    clip = led.clips.get(p.parent_id)
    mom = led.moments.get(clip.parent_id) if clip is not None else None
    if mom is None:
        return ActionResult(ok=False, error="no moment for post")
    removed = mom.hook_removed
    if not removed:
        return ActionResult(ok=False, error="no stripped hook to restore")
    return reburn_hook(cfg, post_id, removed, now=now)


def resume_source_studio(cfg: Config, source_id: str, *, from_stage: str = "auto", force: bool = False) -> ActionResult:
    """MOL-123: the Studio Resume button for an errored / moments_empty source. Delegates to the SAME
    stage-aware helper the CLI verb uses (pipeline.resume_source, MOL-121) — no parallel implementation —
    so an errored source with a good transcript resumes at `transcribed` (re-enters at signals) instead of
    a full re-transcribe. MOL-471: `from_stage` + `force` thread through for the T0 reset recipe.
    Rejects an unknown / non-recoverable source (resume_source returns False)."""
    from fanops.pipeline import resume_source
    with Ledger.transaction(cfg) as led:
        if source_id not in led.sources:
            return ActionResult(ok=False, error=f"no such source: {source_id}")
        if not resume_source(led, source_id, from_stage=from_stage, force=force, cfg=cfg):
            return ActionResult(ok=False, error=f"source {source_id} is not recoverable (state={led.sources[source_id].state.value})")
    write_audit(cfg, "resume_source", [source_id], reason="studio_resume", from_stage=from_stage, force=force)
    return ActionResult(ok=True, detail={"source_id": source_id})


def retire_source_studio(cfg: Config, source_id: str) -> ActionResult:
    """Retire a source from the Studio (cascade-safe; file kept on disk)."""
    with Ledger.transaction(cfg) as led:
        if source_id not in led.sources:
            return ActionResult(ok=False, error=f"no such source: {source_id}")
        led.retire_source(source_id)
    write_audit(cfg, "retire_source", [source_id], reason="studio_retire")
    return ActionResult(ok=True, detail={"source_id": source_id})


def promote_source_studio(cfg: Config, source_id: str) -> ActionResult:
    """Promote a discovered orphan to catalogued (starts the normal pipeline)."""
    from fanops.pipeline import promote_source
    with Ledger.transaction(cfg) as led:
        if source_id not in led.sources:
            return ActionResult(ok=False, error=f"no such source: {source_id}")
        if not promote_source(led, source_id):
            return ActionResult(ok=False, error=f"source {source_id} is not promotable (state={led.sources[source_id].state.value})")
    write_audit(cfg, "promote_source", [source_id], reason="studio_promote")
    return ActionResult(ok=True, detail={"source_id": source_id})


def dismiss_gate_studio(cfg: Config, kind: str, key: str) -> ActionResult:
    """Discard a stuck gate request (operator-confirmed). Does NOT delete the source."""
    from fanops.agentstep import discard_gates_for
    from fanops.gate_keys import gate_source_id
    if kind not in ("moments", "moment_hooks", "captions"):
        return ActionResult(ok=False, error=f"unknown gate kind: {kind!r}")
    n = discard_gates_for(cfg, kind, key)
    sid = gate_source_id(Ledger.load(cfg), kind, key) or key
    write_audit(cfg, "dismiss_gate", [sid], reason="studio_dismiss", kind=kind, key=key, discarded=n)
    return ActionResult(ok=True, detail={"kind": kind, "key": key, "discarded": n})


def release_held_clip(cfg: Config, clip_id: str) -> ActionResult:
    """Clear a brand-risk hold from the browser — the UI twin of `fanops unhold`. Reuses the canonical
    transition (cli.py unhold): held->captions_requested so the next advance re-runs the caption gate.
    Tight local transaction, no network. Rejects a non-held clip so a stray click can't churn a live
    clip's state (stricter than the operator-trusted CLI verb)."""
    with Ledger.transaction(cfg) as led:
        if clip_id not in led.clips: return ActionResult(ok=False, error=f"no such clip: {clip_id}")
        c = led.clips[clip_id]
        if not c.held: return ActionResult(ok=False, error=f"clip {clip_id} is not held (state={c.state.value})")
        c.held = False; c.held_reason = None; led.set_clip_state(clip_id, ClipState.captions_requested)
    return ActionResult(ok=True, detail={"clip_id": clip_id, "state": ClipState.captions_requested.value})
