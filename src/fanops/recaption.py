# src/fanops/recaption.py
"""Backlog re-caption THROUGH the original caption pipeline (operator order 2026-07-25).

`fanops posts recaption` drives the EXISTING stages over every awaiting_approval / non-imminent
queued post — it implements NO caption logic of its own: caption.request_captions (fresh payload =
current persona/corpus/genre by construction) -> responder.answer_pending(kinds=captions, parallel)
-> caption.ingest_captions (brand-risk, vet_hashtags_traced, tag_sources,
seed-fallback — the one true vet) with ONE shared pass_recent in schedule order (mirrors
pipeline._stage_ingest_captions) -> a short transaction syncing each linked post's caption/hashtags
from the seed clip's meta_captions.

Apply is batched: open all caption gates, answer captions ONLY in a parallel pool (never drain
moments/hooks), then ingest+sync serially for pass_recent. Clip states cycle captions_requested ->
captioned (the pipeline's own vocabulary) and are then
RESTORED to their prior state: crosspost dedups per (clip, surface) by content-addressed post id
(crosspost.py `existing = led.posts.get(pid)`), but it RE-MINTS rejected/failed surfaces of any
`captioned` clip — restoring the prior state keeps "published/rejected/failed untouched" true even
if the daemon runs mid-backlog. A clip HELD by ingest (brand-risk / language) stays held with its
posts untouched — a hold is the pipeline speaking, not a verb failure.

Resumable: 00_control/.recaption_progress.json journals done clips + each in-flight clip's prior
state; a re-run skips done clips and finishes in-flight ones. Ledger.snapshot(cfg) runs once before
the first write. The approval lifecycle is untouched: post STATES never change here."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from fanops.accounts import Accounts
from fanops.caption import ingest_captions, request_captions
from fanops.config import Config
from fanops.controlio import write_json_atomic
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.models import ClipState, PostState
from fanops.responder import get_responder
from fanops.timeutil import iso_z

_TARGET_STATES = (PostState.awaiting_approval, PostState.queued)


def _journal_path(cfg: Config):
    return cfg.control / ".recaption_progress.json"


def _load_journal(cfg: Config) -> dict:
    p = _journal_path(cfg)
    if not p.exists():
        return {"done": [], "prior": {}, "notes": {}}
    try:
        j = json.loads(p.read_text())
        return {"done": list(j.get("done") or []), "prior": dict(j.get("prior") or {}),
                "notes": dict(j.get("notes") or {})}
    except (ValueError, TypeError) as e:                 # torn/corrupt journal: start fresh, loudly (never crash the run)
        get_logger(cfg)("recaption", "-", "journal_unreadable", err=str(e)[:120])
        return {"done": [], "prior": {}, "notes": {}}


def _save_journal(cfg: Config, j: dict) -> None:
    write_json_atomic(_journal_path(cfg), {**j, "ts": iso_z(datetime.now(timezone.utc))})


def _seed_clip_id(led: Ledger, post) -> str | None:
    """The clip the pipeline captions for this post's moment: posts hang off per-aspect render clips
    (crosspost mints post.parent_id = target_clip.id) while meta_captions live on the SEED clip the
    caption cycle ran for. Prefer the sibling clip with the richest meta_captions; fall back to the
    post's own parent."""
    clip = led.clips.get(post.parent_id)
    if clip is None:
        return None
    best = None
    for c in led.clips.values():
        if c.parent_id == clip.parent_id and c.meta_captions:
            if best is None or len(c.meta_captions) > len(best.meta_captions):
                best = c
    return (best or clip).id


def _targets(led: Ledger) -> list[tuple[str, list[str]]]:
    """[(seed_clip_id, [post_id, ...])] in schedule order (earliest linked post first); posts within
    a clip likewise schedule-ordered. Targets = awaiting_approval + queued only."""
    groups: dict[str, list] = {}
    for p in led.posts.values():
        if p.state in _TARGET_STATES:
            cid = _seed_clip_id(led, p)
            if cid is not None:
                groups.setdefault(cid, []).append(p)
    def _pkey(p): return p.scheduled_time or p.created_at or "~"
    out = [(cid, [p.id for p in sorted(ps, key=_pkey)]) for cid, ps in groups.items()]
    out.sort(key=lambda t: _pkey(min((led.posts[i] for i in t[1]), key=_pkey)))
    return out


def _sync_posts(led: Ledger, cfg: Config, clip, post_ids: list[str], now: datetime) -> tuple[int, int]:
    """Write each linked target post's caption/hashtags from the seed clip's fresh meta_captions.
    Same key contract as crosspost (`account/platform`, legacy `@`-prefixed fallback). Queued posts
    inside the imminent window are SKIPPED (shipping — same fail-safe as the Studio edit guard).
    Returns (synced, skipped_imminent). Post states are never touched."""
    from fanops.studio.views import _imminent            # Flask-free read-model (cli.cmd_publish_queue precedent)
    synced = skipped = 0
    for pid in post_ids:
        p = led.posts.get(pid)
        if p is None or p.state not in _TARGET_STATES:
            continue                                     # state moved (published/rejected) since enumeration -> untouched
        if p.state is PostState.queued and _imminent(p.scheduled_time, now):
            skipped += 1
            get_logger(cfg)("recaption", clip.id, "skipped_imminent", post=pid, at=p.scheduled_time)
            continue
        entry = clip.meta_captions.get(f"{p.account}/{p.platform.value}") \
            or clip.meta_captions.get(f"@{p.account}/{p.platform.value}")
        if not entry:
            get_logger(cfg)("recaption", clip.id, "no_caption_for_surface", post=pid,
                            surface=f"{p.account}/{p.platform.value}")
            continue
        p.caption = entry["caption"]
        p.hashtags = list(entry.get("hashtags") or [])
        p.edited_at = iso_z(now)
        synced += 1
    return synced, skipped


def run_recaption(cfg: Config, *, apply: bool = False, responder=None, now: datetime | None = None) -> dict:
    """Drive the original caption pipeline over the backlog. Dry-run (default) is a pure read.

    Apply is THREE phases (not one-clip-at-a-time LLM):
      1) request_captions for every pending seed clip (ledger only — fast)
      2) ONE captions-only parallel answer_pending (never moments/hooks; forced pool)
      3) ingest+sync+restore in schedule order (pass_recent / vet stay serial)

    `responder` is injectable for tests (default: configured llm responder)."""
    now = now or datetime.now(timezone.utc)
    led = Ledger.load(cfg)
    targets = _targets(led)
    summary = {"clips": len(targets), "posts": sum(len(ps) for _, ps in targets),
               "done": 0, "synced": 0, "skipped_imminent": 0, "held": 0, "pending": 0, "rows": []}
    for cid, pids in targets:
        c = led.clips.get(cid)
        summary["rows"].append({"clip": cid, "state": c.state.value if c else "?", "posts": len(pids),
                                "earliest": min((led.posts[i].scheduled_time or led.posts[i].created_at or "") for i in pids)})
    if not apply:
        return summary
    if cfg.responder_mode != "llm" and responder is None:
        summary["error"] = "responder_off"               # the same single AI switch every gate obeys
        return summary
    journal = _load_journal(cfg)
    pending_clips = [(cid, pids) for cid, pids in targets if cid not in journal["done"]]
    if not pending_clips:
        return summary
    snap = Ledger.snapshot(cfg)                          # the existing backup mechanism — once, before any write
    summary["snapshot"] = str(snap)
    accts = Accounts.load(cfg)
    resp = responder if responder is not None else get_responder(cfg)
    pass_recent: dict[str, list[str]] = {}               # ONE shared dict, schedule order — mirrors _stage_ingest_captions
    from fanops.pipeline import _owner_caption_surfaces  # the same owner gate the pipeline requests with (P10)

    # --- phase 1: open EVERY caption gate (no LLM) ---
    work: list[tuple[str, list[str]]] = []
    for cid, pids in pending_clips:
        with Ledger.transaction(cfg) as led2:
            clip = led2.clips.get(cid)
            if clip is None:
                journal["notes"][cid] = "missing_clip"; journal["done"].append(cid); _save_journal(cfg, journal)
                continue
            if clip.state is ClipState.held:
                journal["notes"][cid] = f"skipped_held:{clip.held_reason or ''}"[:160]
                _save_journal(cfg, journal)              # NOT done: operator unhold + rerun picks it up
                continue
            if clip.state is not ClipState.captions_requested:       # crash-resume: an in-flight request stands
                m = led2.moments.get(clip.parent_id)
                want = _owner_caption_surfaces(cfg, m, accts) if m is not None else []
                if not want:
                    journal["notes"][cid] = "no_owner_surfaces"; journal["done"].append(cid); _save_journal(cfg, journal)
                    continue
                journal["prior"][cid] = clip.state.value
                _save_journal(cfg, journal)
                led2 = request_captions(led2, cfg, cid, want, accounts=accts)
            work.append((cid, pids))

    if not work:
        return summary

    # --- phase 2: captions ONLY, parallel LLM fan-out (never drain moments/hooks) ---
    resp.answer_pending(cfg, kinds=("captions",), parallel=True)

    # --- phase 3: ingest + sync in schedule order (pass_recent needs serial reduce) ---
    for cid, pids in work:
        with Ledger.transaction(cfg) as led3:
            led3 = ingest_captions(led3, cfg, cid, pass_recent=pass_recent)
            clip3 = led3.clips.get(cid)
            if clip3 is None:
                journal["notes"][cid] = "missing_clip"; journal["done"].append(cid); _save_journal(cfg, journal)
                continue
            if clip3.state is ClipState.captions_requested:          # no/stale response: retry on the next run
                summary["pending"] += 1
                journal["notes"][cid] = "response_pending"
                _save_journal(cfg, journal)
                continue
            if clip3.state is ClipState.held:                        # the pipeline held it — posts stay untouched
                summary["held"] += 1
                journal["notes"][cid] = f"held:{clip3.held_reason or ''}"[:160]
                journal["done"].append(cid); journal["prior"].pop(cid, None)
                _save_journal(cfg, journal)
                get_logger(cfg)("recaption", cid, "held_by_pipeline", reason=(clip3.held_reason or "")[:120])
                continue
            synced, skipped = _sync_posts(led3, cfg, clip3, pids, now)
            prior = journal["prior"].pop(cid, None) or "queued"      # unknown prior (posts exist) -> queued, never captioned:
            led3.set_clip_state(cid, ClipState(prior))               # a `captioned` leftover lets crosspost re-mint rejected surfaces
            summary["synced"] += synced; summary["skipped_imminent"] += skipped; summary["done"] += 1
            journal["done"].append(cid); journal["notes"].pop(cid, None)
            _save_journal(cfg, journal)
            get_logger(cfg)("recaption", cid, "recaptioned", posts=synced, skipped_imminent=skipped)
    return summary


def cmd_posts_recaption(cfg: Config, args) -> int:
    """`fanops posts recaption [--apply]` — default is the read-only dry-run listing."""
    apply = bool(getattr(args, "apply", False))
    if apply and getattr(args, "dry_run", False):
        print("--apply and --dry-run are mutually exclusive"); return 2
    if apply and cfg.responder_mode != "llm":
        print("AI responder is off — recaption re-runs the caption model, so it obeys the same "
              "single switch as every other gate (Go-Live -> AI Responder / FANOPS_RESPONDER=llm).")
        return 2
    s = run_recaption(cfg, apply=apply)
    for r in s["rows"]:
        print(f"[{r['state']:>10}] clip {r['clip']}  posts={r['posts']}  earliest={r['earliest']}")
    if not apply:
        print(f"-- dry-run: {s['clips']} clip(s), {s['posts']} post(s) would re-caption. Run with --apply.")
        return 0
    print(f"-- recaptioned {s['done']}/{s['clips']} clip(s): {s['synced']} post(s) synced, "
          f"{s['skipped_imminent']} imminent skipped, {s['held']} held, {s['pending']} pending "
          f"(snapshot: {s.get('snapshot', '-')}; journal: {_journal_path(cfg)})")
    return 0 if not s.get("pending") else 3              # 3 = partial (pending responses) -> rerun resumes
