"""Run / Make / Gates / Library read-models for the Studio: pipeline status, asset catalog,
stitch queues, discover candidates, manual publish queue, and agent gate rows. Pure (no HTTP/Flask).
Lazy-imports led_for_request and _publish_mode_label from the views facade to avoid circular imports."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fanops.accounts import Accounts
from fanops.config import Config
from fanops.errors import fail_open
from fanops.ledger import Ledger
from fanops.models import ClipState, PostState, StitchState, SourceState
from fanops.studio.views_home import awaiting_moment_count
from fanops.timeutil import parse_iso


def review_candidates(cfg: Config) -> list[dict]:
    """Track C: discover candidates awaiting approval — the top-level thumbnails `fanops discover`
    wrote into 00_review/ (the approved/ subdir is excluded; glob('*.jpg') matches top-level only).
    Lets the operator approve in the browser instead of dragging files in Finder; approving moves the
    thumbnail to 00_review/approved/ (actions.approve_candidate), then `fanops intake` copies the
    original into the inbox."""
    d = cfg.review
    if not d.exists():
        return []
    return [{"eid": p.stem} for p in sorted(d.glob("*.jpg"))]


# States the manual Publish tab surfaces — the by-hand-postable subset of actions._POSTABLE
# (queued is the norm; failed/error/needs_reconcile are recoverable posts the operator posts by hand).
# submitting/submitted are in-flight on a live backend, not a manual worklist item.
_MANUAL_QUEUE = {PostState.queued, PostState.needs_reconcile, PostState.failed, PostState.error}


def publish_queue(cfg: Config, *, now: Optional[datetime] = None,
                  account: Optional[str] = None) -> list[dict]:
    """Track B (manual / zero-dependency publishing): the worklist of `queued` posts the operator
    posts BY HAND. Each row carries the surface, caption, and the post id (Studio serves the clip at
    /media/<post_id>, marks it posted at /publish/posted/<post_id>). `due` = scheduled_time has
    passed. Due-first, then by schedule. Lock-free read; mutation is actions.mark_published. P5: an
    optional `account` filters the dict rows after the due-first sort (None default unchanged)."""
    now = now or datetime.now(timezone.utc)
    led = Ledger.load(cfg)
    rows = []
    for p in led.posts.values():
        if p.state not in _MANUAL_QUEUE:                 # every state mark_published accepts by hand
            continue
        due = False
        if p.scheduled_time:
            with fail_open("studio.views_run.publish_queue"):
                due = parse_iso(p.scheduled_time) <= now
        rows.append({"post_id": p.id, "clip_id": p.parent_id, "account": p.account,
                     "platform": p.platform.value, "caption": p.caption, "state": p.state.value,
                     "scheduled_time": p.scheduled_time, "due": due})
    # due-first; within a bucket by schedule. "9999" sentinel (not "") so a None/unscheduled post
    # sorts LAST, not as if it were the most urgent (ecc:python-review).
    rows.sort(key=lambda r: (not r["due"], r["scheduled_time"] or "9999"))
    if account is not None:        # P5: per-account filter on the dict rows
        rows = [r for r in rows if r["account"] == account]
    return rows


def pipeline_status(cfg: Config) -> dict:
    """Lock-free counts for the Run tab's status line: where the unit chain stands + how many gates
    are waiting + the active poster backend. Lets the operator see, in one glance, whether the next
    move is 'ingest', 'run a pass', or 'answer a gate'."""
    from collections import Counter
    from fanops.pipeline_status import PendingIndex, status_control_lines, source_backlog
    from fanops.pipeline_run import run_stage_snapshot
    from fanops.studio import views as _views
    led = _views.led_for_request(cfg)
    idx = PendingIndex.build(cfg, led)
    run_line, wait_line = status_control_lines(cfg, led, idx)
    bl = source_backlog(led, cfg, idx)
    snap = run_stage_snapshot(cfg)
    by_kind = Counter(kind for _, kind, _ in idx.ordered)
    run_chip = f"{snap['stage']}:{snap['unit']}" if snap else None
    pending_unbound, queue_lines, held_pending = [], [], 0
    if cfg.queue_gate:
        for sid, s in sorted(led.sources.items()):
            if s.origin_kind != "native" or s.state is not SourceState.pending:
                continue
            if s.batch_id:
                held_pending += 1
            else:
                pending_unbound.append({"id": sid, "name": Path(s.source_path).name if s.source_path else sid,
                                        "duration": s.duration, "thumb_url": f"/source-thumb/{sid}"})
        by_batch: dict[str, list] = {}
        for sid, s in led.sources.items():
            if s.origin_kind == "native" and s.state is SourceState.pending and s.batch_id:
                by_batch.setdefault(s.batch_id, []).append(sid)
        for bid, sids in sorted(by_batch.items()):
            b = led.get_batch(bid)
            if not b:
                continue
            queue_lines.append({"batch_id": bid, "name": b.name, "sources": sorted(sids),
                                "target_accounts": b.target_accounts})
    # T2.3: machine-origin moment re-opens that request_moments parked instead of serving. Listed
    # unconditionally — the gate can be switched off AFTER a park, and a row nobody can release is
    # exactly the invisible backlog this ticket exists to end.
    pending_reopens = []
    for sid, s in sorted(led.sources.items()):
        parked = s.meta.get("pending_reopen")
        if isinstance(parked, dict):
            pending_reopens.append({"id": sid, "origin": parked.get("origin") or "",
                                    "requested_at": parked.get("requested_at") or ""})
    backlog_rows = []
    for r in bl.rows:
        row = {"id": r.id, "state": r.state, "bucket": r.bucket, "wait_line": r.wait_line,
               "block_reason": r.block_reason, "artifacts": r.artifacts}
        if snap and snap.get("unit") == r.id:
            row["active_stage"] = snap["stage"]
            row["stage_age"] = snap["stage_age"]
        backlog_rows.append(row)
    return {
        "sources": bl.actionable,   # in-progress pipeline work (NOT raw inventory — see source_backlog)
        "sources_blocked": bl.blocked_on_gates,
        "sources_recoverable": bl.recoverable,
        "sources_inventory": bl.inventory,
        "sources_held": bl.held,
        "native_total": bl.actionable + bl.blocked_on_gates + bl.recoverable + bl.inventory + bl.held,
        "pending_unbound": pending_unbound,
        "pending_unbound_count": len(pending_unbound),
        "queue_lines": queue_lines,
        "held_pending": held_pending,
        "pending_reopens": pending_reopens,
        "queue_gate": cfg.queue_gate,
        "backlog_rows": backlog_rows,
        "run_chip": run_chip,
        "third_party": sum(1 for s in led.sources.values() if s.origin_kind == "third_party"),
        "clips": len(led.clips), "posts": len(led.posts),
        "awaiting": awaiting_moment_count(led),   # S3: ACTIONABLE — MOMENTS (== Home/Review worklist), not raw posts
        "published": len(led.posts_in_state(PostState.published)),
        "holds": sum(1 for c in led.clips.values() if c.held),
        "pending_moments": by_kind.get("moments", 0),
        "pending_moment_hooks": by_kind.get("moment_hooks", 0),
        "pending_captions": by_kind.get("captions", 0),
        "run_line": run_line,
        "wait_line": wait_line,
        # R3-followup: the UI mode label MUST be the per-channel truth, not the legacy global. On a live
        # deployment with per-channel routing, cfg.poster_backend still reads 'dryrun' (the legacy
        # FANOPS_POSTER is the fallback bridge, not the per-channel source of truth) — surfacing it
        # printed 'dryrun' on a system that was actually publishing live, the UI lie that triggered this fix.
        # _publish_mode_label resolves to the distinct providers actually publishing (e.g. 'postiz, zernio'),
        # or 'dryrun' when cfg.is_live is False. ONE source for every status surface — no more divergence
        # between Home (which already used _publish_mode_label) and Make/Schedule/Publish (which used the
        # legacy global). hx-confirm gates that read `backend != 'dryrun'` still trigger when ANY channel
        # publishes live, which is the correct behavior (a live publish_now needs a confirm).
        "backend": _views._publish_mode_label(cfg),
        "accounts": [a.handle for a in Accounts.load(cfg).active()],   # Account-First: Run-form batch-target options
        "upload_max_bytes": cfg.upload_max_bytes,   # S02: chunked-upload JS intercept threshold (legacy single-shot uses the same cap)
        "errored": errored_sources(led),   # MOL-123: recoverable sources (error / moments_empty) for the Run-tab list
    }


_RECOVERABLE_SOURCE_STATES = (SourceState.error, SourceState.moments_empty)


def errored_sources(led: Ledger) -> list[dict]:
    """MOL-123: the recoverable-source rows for the Run tab — every source in error / moments_empty, with the
    FULL error_reason (the operator needs the exact failure, not a truncation) + filename + batch. Pure read;
    fail-open to [] on a torn row so it never 500s the panel."""
    out: list[dict] = []
    for s in led.sources.values():
        if s.state not in _RECOVERABLE_SOURCE_STATES:
            continue
        with fail_open("studio.views_run.errored_sources"):
            out.append({"id": s.id, "state": s.state.value, "error_reason": s.error_reason or "",
                        "batch_id": s.batch_id, "created_at": s.created_at,
                        "name": Path(s.source_path).name if s.source_path else s.id})
    return out


def run_next_step(status: dict) -> dict:
    """S3: the Make tab's ONE 'do this next' affordance, derived PURELY from pipeline_status counts (no ledger
    read; fail-open via .get so a torn/partial dict never raises). Gate ON: add → queue → make. Gate OFF:
    add → gate → prepare → review (gates PRECEDE review because a pending decision is BLOCKING mid-pipeline
    clips). Returns {key, label, hint}."""
    s = status if isinstance(status, dict) else {}
    def _n(k):
        try: return int(s.get(k, 0) or 0)
        except (TypeError, ValueError): return 0
    footage = _n("native_total") + _n("third_party")
    gates = _n("pending_moments") + _n("pending_moment_hooks") + _n("pending_captions")
    awaiting = _n("awaiting")
    if footage == 0:
        return {"key": "add", "label": "Add a video to begin",
                "hint": "Choose a file above, or paste a link under More — then tick accounts and Make clips."}
    recoverable = _n("sources_recoverable")
    if recoverable:
        label = f"{recoverable} source{'s' if recoverable != 1 else ''} need attention"
        return {"key": "recover", "label": label,
                "hint": "Open the source in Library to read the failure and resume or reset from there."}
    if s.get("queue_gate"):
        if _n("pending_unbound_count"):
            return {"key": "queue", "label": f"Make clips for {_n('pending_unbound_count')} pending file(s)",
                    "hint": "Tick the accounts you want, then Make clips."}
        if _n("held_pending"):
            return {"key": "make", "label": f"Make clips for {_n('held_pending')} queued file(s)",
                    "hint": "Make clips releases the queued file(s) and cuts until Review."}
        if _n("sources"):
            return {"key": "prepare", "label": "Run a pass",
                    "hint": "Cut clips and write captions for every released source — they'll land in Review."}
        return {"key": "add", "label": "Add more footage",
                "hint": "Upload or drop another file when you're ready."}
    if gates:
        hint = "Some clips are paused waiting on a decision. Answer them, then run Prepare again to finish those clips."
        if awaiting: hint += f" ({awaiting} clip(s) are also waiting in Review.)"
        return {"key": "gate", "label": f"Answer {gates} processing decision(s)", "hint": hint}
    if awaiting:
        return {"key": "review", "label": f"{awaiting} clip(s) ready",
                "hint": "Review and approve them in the Review tab — nothing ships until you do."}
    return {"key": "prepare", "label": "Run a pass",
            "hint": "Cut clips and write captions for every account — they'll land in Review."}


def asset_catalog(cfg: Config) -> dict:
    """Lock-free read-model for the Library tab (M1): every remembered Source split by origin_kind, with
    just-enough metadata to recognize it. Fail-open — a torn/absent ledger yields empty lists, never a
    500 (the Studio invariant)."""
    try:                                             # whole body guarded: a torn row must not 500 either
        led = Ledger.load(cfg)
        from fanops.pipeline_status import source_backlog
        bl = source_backlog(led, cfg)
        from fanops.pipeline_run import run_stage_snapshot
        run_active = run_stage_snapshot(cfg) is not None
        by_id = {r.id: r for r in bl.rows}
        from fanops.models import SourceState
        rows = [{"id": s.id, "origin_kind": s.origin_kind, "state": s.state.value,
                 "bucket": by_id[s.id].bucket if s.id in by_id else "inventory",
                 "wait_line": by_id[s.id].wait_line if s.id in by_id else None,
                 "block_reason": by_id[s.id].block_reason if s.id in by_id else None,
                 "artifacts": by_id[s.id].artifacts if s.id in by_id else None,
                 "retire_preview": (led.preview_retire_cascade(s.id) if s.origin_kind == "native"
                                    and s.state is not SourceState.retired else None),
                 "name": Path(s.source_path).name if s.source_path else s.id,   # P6: human filename, not the opaque id
                 "duration": s.duration, "width": s.width, "height": s.height,
                 "degraded_reason": s.degraded_reason} for s in led.sources.values()]   # RF1: the VISIBLE-degradation channel (probe_failed) -> a Library marker, else a 0×0 source silently renders a mangled clip
        return {"native": [r for r in rows if r["origin_kind"] == "native"],
                "third_party": [r for r in rows if r["origin_kind"] == "third_party"],
                "backlog": {"actionable": bl.actionable, "blocked_on_gates": bl.blocked_on_gates,
                            "recoverable": bl.recoverable, "inventory": bl.inventory},
                "run_active": run_active}
    except Exception as exc:                          # invariant: the Library tab must never 500 — but
        from fanops.log import get_logger             # a read-fail is RECORDED, never silently shown as "empty"
        get_logger(cfg)("library", "-", "error", err=str(exc)[:160])
        return {"native": [], "third_party": [], "backlog": {"actionable": 0, "blocked_on_gates": 0,
                                                            "recoverable": 0, "inventory": 0}, "run_active": False}


def pending_stitches(cfg: Config) -> list:
    """Lock-free read-model for the Stitches tab (M3): the SUGGESTED stitch_plans awaiting operator
    approval. Fail-open — a torn/absent ledger yields [] (and logs), never a 500 (the Studio invariant)."""
    try:
        led = Ledger.load(cfg)
        rows = [{"id": p.id, "clip_id": p.clip_id, "strategy_key": p.strategy_key,
                 "asset_ids": p.asset_ids, "state": p.state.value,
                 "rank_score": p.rank_score, "rationale": p.rationale}      # M5: the routine-loop's WHY + fit
                for p in led.stitch_plans.values() if p.state is StitchState.suggested]
        # best-fit first (highest rank_score); a None rank sinks to the bottom; tie -> stable by id
        rows.sort(key=lambda r: (-(r["rank_score"] or 0.0), r["id"]))
        return rows
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("stitches", "-", "error", err=str(exc)[:160])
        return []


def pending_stitch_drafts(cfg: Config) -> list:
    """Lock-free read-model for the Stitches tab (M4): rendered `stitch_draft` clips awaiting the operator's
    RELEASE (the second gate — approved plans render into these unpostable drafts; releasing one makes it
    crosspost-eligible). Fail-open — a torn/absent ledger yields [], never a 500 (the Studio invariant)."""
    try:
        led = Ledger.load(cfg)
        return [{"id": c.id, "parent_id": c.parent_id, "aspect": c.aspect.value}
                for c in led.clips.values() if c.state is ClipState.stitch_draft]
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("stitches", "-", "error", err=str(exc)[:160])
        return []


def gate_rows(cfg: Config) -> list[dict]:
    """Lock-free read-model for the Gates tab (Phase 3a): every PENDING moment/caption agent gate
    with the request context the operator needs to answer it (transcript/signals for moments, the
    surface list for captions). Corrupt request files surface as dismiss-only rows (corrupt=True).
    Same enumeration `fanops respond` uses, surfaced for the browser."""
    from fanops.agentstep import pending, request_path
    from fanops.pipeline_status import _gate_is_corrupt
    from fanops.transcribe import _trust_tier
    rows: list[dict] = []
    for kind in ("moments", "moment_hooks", "captions"):
        for key in pending(cfg, kind=kind):
            if _gate_is_corrupt(cfg, kind, key):
                rows.append({"kind": kind, "key": key, "corrupt": True})
                continue
            try:
                payload = json.loads(request_path(cfg, kind, key).read_text())
            except Exception as exc:
                from fanops.log import get_logger
                get_logger(cfg)("gates", key, "request_read_failed", kind=kind, err=str(exc)[:160])
                continue                               # torn/unreadable request file: SKIP it (match the
                                                       # docstring) rather than render an empty, unanswerable
                                                       # gate form whose blank submit could write a bad answer
                                                       # (ecc audit). The corruption is also logged by
                                                       # latest_request_id during pending().
            if kind == "moments" and payload.get("transcript"):
                lang = payload.get("language")
                tr = []
                for seg in payload["transcript"]:
                    if isinstance(seg, dict):
                        s = dict(seg)
                        s["trust_tier"] = _trust_tier(s, src_lang=lang)
                        tr.append(s)
                    else:
                        tr.append(seg)
                payload = {**payload, "transcript": tr}
            rows.append({"kind": kind, "key": key, **payload})
    return rows
