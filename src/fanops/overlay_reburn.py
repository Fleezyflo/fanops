# src/fanops/overlay_reburn.py
"""`fanops overlay-reburn` — in-place ass-only recut of awaiting Review `{cid}.mp4` files.

Existing Review files were burned with top-third hooks. Overlay helper changes only affect new
renders. This verb recuts awaiting-only files IN PLACE using the PROVED crop — never a fresh
`_resolve` as the render source, never `render_reframed`'s center fail-open.

Reuses `MigrationLock` (`reframe.lock`). A stale lock is an operator unlink; `fanops reframe
--status` will not understand `or_` run dirs under `07_reports/overlay_reburn/`.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from fanops import clip as clipmod
from fanops import framing
from fanops import overlay
from fanops import reframe_apply as ra
from fanops.clip import _build_ass_text, fingerprint_of_payload
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.models import PostState, _LIVE_POST_STATES
from fanops.pipeline_run import set_paused
from fanops.reframe import (
    Candidate, Reconstruction, ReframePaths, _delta_keys, _is_bare_clip,
    _seed_scratch_vstart, _top_bias_candidates, _window_candidates, snapshot_ledger,
)

OVERLAY_KEYS = {"ass"}

UNTOUCHED = ra.UNTOUCHED
BACKED_UP = ra.BACKED_UP
COMMITTED = ra.COMMITTED
TORN = ra.TORN
RESTORED = ra.RESTORED
AMBIGUOUS = ra.AMBIGUOUS


def _is_http(url) -> bool:
    return isinstance(url, str) and url.startswith(("http://", "https://"))


def _posts_of(led: Ledger, clip_id: str) -> list:
    return [p for p in led.posts.values() if getattr(p, "parent_id", None) == clip_id]


def _live_or_queued(posts) -> bool:
    return any(p.state in _LIVE_POST_STATES or p.state is PostState.queued for p in posts)


def _hosted_http(c, posts) -> bool:
    """Veto http(s) on clip.media_url or any post.media_urls. file:// does NOT veto."""
    if _is_http(getattr(c, "media_url", None)):
        return True
    for p in posts:
        for u in (getattr(p, "media_urls", None) or []):
            if _is_http(u):
                return True
    return False


def _has_render_id(posts) -> bool:
    return any(getattr(p, "render_id", None) for p in posts)


def _has_awaiting(posts) -> bool:
    return any(p.state is PostState.awaiting_approval for p in posts)


def _seed_scratch_detect_track(paths: ReframePaths, cfg: Config, src) -> None:
    """Copy production detect/track sidecars into scratch so `_resolve` hits the cache (no YuNet replay).

    `reframe._seed_scratch_vstart` copies vstart ONLY. Detection is cached per (source, window) at
    `framing.py` `{source_id}.detect.json` / `{source_id}.track.json`. Without this copy, scratch
    `_resolve` replays YuNet and smart-framed Review clips go UNRECONSTRUCTABLE.
    """
    sid = getattr(src, "id", None)
    if not sid:
        return
    prod_fr = paths.production_cfg.agent_io / "framing"
    scratch_fr = cfg.agent_io / "framing"
    for name in (f"{sid}.detect.json", f"{sid}.track.json"):
        src_p = prod_fr / name
        if not src_p.exists():
            continue
        dest = scratch_fr / name
        paths.assert_write_target(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_p, dest)


def prove_payload(paths: ReframePaths, cfg: Config, led: Ledger, c, fp_stored: str) -> Reconstruction:
    """Prove the stored fingerprint by enumerating windows × top_bias × crop × ass. Not reconstruct()."""
    m = led.moments[c.parent_id]
    src = led.sources[m.parent_id]
    _seed_scratch_vstart(paths, cfg, m, src)
    _seed_scratch_detect_track(paths, cfg, src)

    ass_disk = paths.read_ass_text(c.id)
    ass_cands = []
    if ass_disk is not None:
        ass_cands.append((ass_disk, "ass:disk"))
    ass_cands.append(("", "ass:empty"))

    by_bytes: dict = {}
    for win, wlab in _window_candidates(paths, cfg, m, src):
        cs, ce = win
        try:
            res = framing._resolve(cfg, src, cs, ce, capture_failures=True)
            crops = [((res.focus, res.track, res.content_type), "crop:resolve"),
                     ((None, None, None), "crop:centered")]
        except Exception as exc:
            get_logger(cfg)("overlay_reburn", c.id, "resolve_failed", reason=type(exc).__name__)
            crops = [((None, None, None), "crop:centered")]
        for ass, alab in ass_cands:
            for tb, tlab in _top_bias_candidates(m, cfg):
                for (focus, track, ct), clab in crops:
                    payload = clipmod._render_fingerprint_payload(
                        src.source_path, cs, ce, c.aspect.value, src.width or 0, src.height or 0,
                        ass, top_bias=tb, focus=focus, track=track, content_type=ct)
                    key = clipmod.fingerprint_payload_bytes(payload)
                    lab = f"{wlab}|{alab}|{tlab}|{clab}"
                    if key in by_bytes:
                        prev = by_bytes[key]
                        by_bytes[key] = Candidate(prev.payload, prev.labels + (lab,))
                    else:
                        by_bytes[key] = Candidate(payload, (lab,))

    hits = [cand for blob, cand in by_bytes.items()
            if hashlib.sha256(blob).hexdigest() == fp_stored]
    if len(hits) == 1:
        return Reconstruction(True, hits[0].payload, hits[0].labels, len(by_bytes), 1)
    return Reconstruction(False, None, (), len(by_bytes), len(hits))


def _payload_with_new_ass(old: dict, ass_new: str) -> dict:
    new = dict(old)
    new["ass"] = ass_new
    return new


def _delta_class(delta: list) -> str | None:
    if not delta:
        return "already_current"
    if set(delta) - OVERLAY_KEYS or list(delta) != ["ass"]:
        return "extra_delta_keys"
    return None


def _row(c, **extra) -> dict:
    return {"clip_id": c.id, "moment_id": c.parent_id, "aspect": c.aspect.value, **extra}


def classify_clip(paths: ReframePaths, cfg: Config, led: Ledger, c) -> dict:
    """Classify ONE clip. Guards first — each can only DECLINE. One throw must not abort the corpus."""
    try:
        return _classify_clip(paths, cfg, led, c)
    except Exception as exc:
        get_logger(cfg)("overlay_reburn", getattr(c, "id", "-"), "error", reason=type(exc).__name__)
        return _row(c, classification="error", reason=type(exc).__name__)


def _classify_clip(paths: ReframePaths, cfg: Config, led: Ledger, c) -> dict:
    m = led.moments.get(c.parent_id)
    if m is None:
        return _row(c, classification="missing_input", reason="moment absent from ledger")
    posts = _posts_of(led, c.id)
    if not _has_awaiting(posts):
        return _row(c, classification="no_awaiting_post", reason="no awaiting_approval post on this clip")
    if _live_or_queued(posts):
        return _row(c, classification="live_or_queued_sibling", reason="sibling in _LIVE_POST_STATES or queued")
    if _has_render_id(posts):
        return _row(c, classification="render_id", reason="post.render_id set (per-account file)")
    if _hosted_http(c, posts):
        return _row(c, classification="hosted_http", reason="http(s) on clip.media_url or post.media_urls")
    if m.segments:
        return _row(c, classification="supercut", reason="moment has segments")
    if not _is_bare_clip(c):
        return _row(c, classification="stitch", reason="clip id is not its content address")
    hook = ((m.hook or "").strip())
    if not hook:
        return _row(c, classification="empty_hook", reason="moment hook is empty")
    src = led.sources.get(m.parent_id)
    if src is None or not src.source_path:
        return _row(c, classification="missing_input", reason="source absent")
    if not Path(src.source_path).exists():
        return _row(c, classification="missing_input", reason="source media absent on disk")
    fp_stored = paths.read_stored_fingerprint(c.id)
    if fp_stored is None:
        return _row(c, classification="missing_input", reason="no {cid}.render.json")

    rec = prove_payload(paths, cfg, led, c, fp_stored)
    base = _row(c, fp_stored=fp_stored, reconstruction_proved=rec.proved,
                candidate_payloads_tried=rec.tried, winning_provenance_labels=list(rec.labels))
    if rec.matches == 0 or not rec.proved:
        return {**base, "classification": "unreconstructable", "reason": "could not reproduce fp_stored"}
    if rec.matches >= 2:
        return {**base, "classification": "reconstruction_ambiguous", "reason": "two or more distinct payloads hash to fp"}

    proved = rec.payload
    ass_new, _hbf = _build_ass_text(led, cfg, c.parent_id, c.id, c.aspect,
                                    clip_start=proved["cs"], clip_end=proved["ce"])
    if not ass_new or not str(ass_new).strip():
        return {**base, "classification": "empty_hook", "reason": "today's _build_ass_text is empty"}
    payload_new = _payload_with_new_ass(proved, ass_new)
    delta = _delta_keys(proved, payload_new)
    skip = _delta_class(delta)
    if skip:
        return {**base, "classification": skip, "reason": f"delta {delta}",
                "payload_old": proved, "payload_new": payload_new, "payload_delta": delta}
    fp_new = fingerprint_of_payload(payload_new)
    return {**base, "classification": "eligible", "reason": "awaiting+ass-only",
            "fp_new": fp_new, "payload_old": proved, "payload_new": payload_new,
            "payload_delta": delta}


def _render_args(payload: dict) -> dict:
    return {
        "src_path": payload["src"], "cs": payload["cs"], "ce": payload["ce"],
        "aspect": payload["aspect"], "src_w": payload["w"], "src_h": payload["h"],
        "top_bias": bool(payload.get("top_bias")),
        "focus": tuple(payload["focus"]) if payload.get("focus") else None,
        "track": [tuple(s) for s in payload["track"]] if payload.get("track") else None,
        "content_type": payload.get("ct"),
    }


@dataclass
class RunDirs:
    root: Path
    backups: Path
    staging: Path
    journal: Path
    plan: Path
    summary: Path

    @classmethod
    def build(cls, cfg: Config, run_id: str) -> "RunDirs":
        root = cfg.reports / "overlay_reburn" / run_id
        return cls(root=root, backups=root / "backups", staging=root / "staging",
                   journal=root / "journal.jsonl", plan=root / "plan.json",
                   summary=root / "summary.json")

    def mkdirs(self) -> None:
        for d in (self.root, self.backups, self.staging):
            d.mkdir(parents=True, exist_ok=True)


def new_run_id(stamp: float | None = None) -> str:
    return "or_" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(stamp or time.time()))


def inspect_clip(dirs: RunDirs, row: dict) -> str:
    """Mirror reframe inspect_clip: torn = new pixels, stale fp (healable)."""
    mp4, side = Path(row["media_path"]), Path(row["sidecar_path"])
    pre = row["preimage"]
    cur_mp4 = ra.sha256_file(mp4) if mp4.exists() else None
    cur_fp = ra._stored_fp(side)
    bk_mp4 = dirs.backups / f"{row['clip_id']}.mp4"
    bk_ok = bk_mp4.exists() and ra.sha256_file(bk_mp4) == pre["media_sha256"]
    if cur_mp4 is None:
        return AMBIGUOUS
    if cur_mp4 == pre["media_sha256"] and cur_fp == row["fp_old"]:
        return RESTORED if bk_ok else (BACKED_UP if bk_mp4.exists() else UNTOUCHED)
    if cur_mp4 != pre["media_sha256"] and cur_fp == row["fp_new"]:
        return COMMITTED
    if cur_mp4 != pre["media_sha256"] and cur_fp == row["fp_old"]:
        return TORN
    return AMBIGUOUS


def backup_clip(dirs: RunDirs, row: dict) -> dict:
    """Byte-exact backup of mp4 + render.json + .ass. Never overwrite a divergent backup."""
    cid = row["clip_id"]
    items = [(Path(row["media_path"]), f"{cid}.mp4", row["preimage"]["media_sha256"]),
             (Path(row["sidecar_path"]), f"{cid}.render.json", row["preimage"]["sidecar_sha256"])]
    ass_sha = row["preimage"].get("ass_file_sha256")
    ass_p = Path(row["ass_path"])
    if ass_sha and ass_p.exists():
        items.append((ass_p, f"{cid}.ass", ass_sha))
    out: dict = {}
    for src, name, want in items:
        dst = dirs.backups / name
        if dst.exists():
            got = ra.sha256_file(dst)
            if got != want:
                raise ra.PlanStale(f"{cid}: existing backup {name} sha {got} != planned preimage {want}")
            out[name] = got
            continue
        if not src.exists():
            continue
        shutil.copy2(src, dst)
        got = ra.sha256_file(dst)
        if got != want:
            raise ra.PlanStale(f"{cid}: backup of {name} verified {got} != {want}")
        out[name] = got
    return out


def rollback_clip(dirs: RunDirs, row: dict) -> dict:
    """Restore mp4 + sidecar + .ass from backup."""
    cid = row["clip_id"]
    bk_mp4 = dirs.backups / f"{cid}.mp4"
    bk_side = dirs.backups / f"{cid}.render.json"
    bk_ass = dirs.backups / f"{cid}.ass"
    pre = row["preimage"]
    if not bk_mp4.exists() or not bk_side.exists():
        return {"clip_id": cid, "status": "ROLLBACK_NO_BACKUP"}
    if ra.sha256_file(bk_mp4) != pre["media_sha256"] or ra.sha256_file(bk_side) != pre["sidecar_sha256"]:
        return {"clip_id": cid, "status": "ROLLBACK_BACKUP_CORRUPT"}
    if (ra.sha256_file(row["media_path"]) == pre["media_sha256"]
            and ra._stored_fp(Path(row["sidecar_path"])) == row["fp_old"]):
        ass_ok = (not pre.get("ass_file_sha256")
                  or (Path(row["ass_path"]).exists() and ra.sha256_file(row["ass_path"]) == pre["ass_file_sha256"]))
        if ass_ok:
            return {"clip_id": cid, "status": "ROLLBACK_NOOP"}
    pairs = [(bk_mp4, row["media_path"]), (bk_side, row["sidecar_path"])]
    if bk_ass.exists() and pre.get("ass_file_sha256"):
        pairs.append((bk_ass, row["ass_path"]))
    for bk, dst in pairs:
        tmp = Path(str(dst) + ".rbpart")
        shutil.copy2(bk, tmp)
        os.replace(str(tmp), dst)
    if ra.sha256_file(row["media_path"]) != pre["media_sha256"] or ra.sha256_file(row["sidecar_path"]) != pre["sidecar_sha256"]:
        return {"clip_id": cid, "status": "ROLLBACK_VERIFY_FAILED"}
    if pre.get("ass_file_sha256") and ra.sha256_file(row["ass_path"]) != pre["ass_file_sha256"]:
        return {"clip_id": cid, "status": "ROLLBACK_VERIFY_FAILED"}
    return {"clip_id": cid, "status": "ROLLED_BACK", "media_sha256": pre["media_sha256"]}


def _clear_file_media_url(prod_cfg: Config, cid: str) -> None:
    """Clear file:// clip.media_url only. ledger_changed is NOT a run failure."""
    with Ledger.transaction(prod_cfg) as led:
        c = led.clips.get(cid)
        if c is None:
            return
        url = getattr(c, "media_url", None) or ""
        if url.startswith("file://"):
            led.clips[cid] = c.model_copy(update={"media_url": None})


def _veto_live(led, c) -> str | None:
    posts = _posts_of(led, c.id) if hasattr(led, "posts") else []
    if _live_or_queued(posts):
        return "live_or_queued_sibling"
    if _has_render_id(posts):
        return "render_id"
    if _hosted_http(c, posts):
        return "hosted_http"
    return None


def apply_clip(paths: ReframePaths, dirs: RunDirs, led, row: dict, *, run_id: str,
               prod_cfg: Config | None = None) -> dict:
    """BACK UP -> RENDER TO STAGING -> VALIDATE -> COMMIT mp4, then .ass, then sidecar."""
    cid = row["clip_id"]
    rec: dict = {"run_id": run_id, "clip_id": cid, "fp_old": row["fp_old"], "fp_new": row["fp_new"],
                 "payload_delta": row.get("payload_delta") or ["ass"]}
    state = inspect_clip(dirs, row)
    if state == COMMITTED:
        return {**rec, "phase": "skip", "status": "already_committed"}
    if state == AMBIGUOUS:
        return {**rec, "phase": "stop", "status": "AMBIGUOUS",
                "error": "disk state matches no known phase — explicit repair required"}
    if state == TORN:
        ass_new = row.get("ass_new") or (row.get("payload_new") or {}).get("ass") or ""
        if ass_new:
            overlay.write_ass(ass_new, row["ass_path"])
        ra._write_sidecar_atomic(row["sidecar_path"], row["fp_new"])
        return {**rec, "phase": "heal", "status": "healed_sidecar",
                "final": {"media_sha256": ra.sha256_file(row["media_path"]),
                          "fp": ra._stored_fp(Path(row["sidecar_path"]))}}

    c = led.clips.get(cid) if hasattr(led, "clips") else None
    if c is not None:
        veto = _veto_live(led, c)
        if veto:
            return {**rec, "phase": "preimage", "status": "PREIMAGE_MISMATCH", "error": veto}

    proved = row["payload_old"]
    rargs = _render_args(proved)
    if rargs["content_type"] == framing.RENDER_STACK_PAIR:
        return {**rec, "phase": "refuse", "status": "STACK_PAIR_REFUSED",
                "error": "refusing stack-pair — render_reframed fail-open would center"}

    backups = backup_clip(dirs, row)
    rec["backup"] = backups

    ass_new = row.get("ass_new") or (row.get("payload_new") or {}).get("ass") or ""
    focus, track, ct = rargs["focus"], rargs["track"], rargs["content_type"]
    payload_actual = clipmod._render_fingerprint_payload(
        rargs["src_path"], rargs["cs"], rargs["ce"], rargs["aspect"],
        rargs["src_w"], rargs["src_h"], ass_new, top_bias=rargs["top_bias"],
        focus=focus, track=track, content_type=ct)
    fp_actual = fingerprint_of_payload(payload_actual)
    if fp_actual != row["fp_new"]:
        return {**rec, "phase": "refuse", "status": "FINGERPRINT_DIVERGED",
                "error": f"inputs hash to {fp_actual[:16]} but plan proved {row['fp_new'][:16]}"}
    drift = _delta_keys(proved, payload_actual)
    if set(drift) - OVERLAY_KEYS:
        return {**rec, "phase": "refuse", "status": "EXTRA_DELTA",
                "error": f"a re-render would also change {sorted(set(drift) - OVERLAY_KEYS)}"}

    staged_mp4 = dirs.staging / f"{cid}.mp4"
    staged_ass = dirs.staging / f"{cid}.ass"
    if ass_new:
        overlay.write_ass(ass_new, staged_ass)
        extra_vf = overlay.subtitles_vf(staged_ass)
    else:
        extra_vf = None
    try:
        res = clipmod.render_reframed(
            rargs["src_path"], str(staged_mp4), rargs["cs"], rargs["ce"], rargs["aspect"],
            src_w=rargs["src_w"], src_h=rargs["src_h"], extra_vf=extra_vf,
            top_bias=rargs["top_bias"], focus=focus, track=track, content_type=ct)
    except (OSError, TimeoutError) as exc:
        return {**rec, "phase": "render", "status": "RENDER_FAILED", "error": type(exc).__name__}
    if getattr(res, "returncode", 1) != 0 or not staged_mp4.exists() or staged_mp4.stat().st_size == 0:
        return {**rec, "phase": "render", "status": "RENDER_FAILED",
                "error": f"rc={getattr(res, 'returncode', '?')}"}

    ok, fails = ra.validate_output(str(staged_mp4), row)
    if not ok:
        staged_mp4.unlink(missing_ok=True)
        return {**rec, "phase": "validate", "status": "VALIDATION_FAILED", "error": fails}
    if not ra.decodes(str(staged_mp4)):
        staged_mp4.unlink(missing_ok=True)
        return {**rec, "phase": "validate", "status": "VALIDATION_FAILED", "error": ["staged output does not decode"]}

    staged_sha = ra.sha256_file(staged_mp4)
    if staged_sha == row["preimage"]["media_sha256"]:
        staged_mp4.unlink(missing_ok=True)
        return {**rec, "phase": "commit", "status": "UNCHANGED_PIXELS", "final": {"media_sha256": staged_sha}}

    os.replace(str(staged_mp4), row["media_path"])
    if ass_new:
        if staged_ass.exists():
            os.replace(str(staged_ass), row["ass_path"])
        else:
            overlay.write_ass(ass_new, row["ass_path"])
    ra._write_sidecar_atomic(row["sidecar_path"], row["fp_new"])
    if prod_cfg is not None:
        try:
            _clear_file_media_url(prod_cfg, cid)
        except Exception as exc:
            get_logger(prod_cfg)("overlay_reburn", cid, "ledger_clear_failed", reason=type(exc).__name__)
    return {**rec, "phase": "commit", "status": "MIGRATED",
            "final": {"media_sha256": ra.sha256_file(row["media_path"]),
                      "fp": ra._stored_fp(Path(row["sidecar_path"])),
                      "bytes": Path(row["media_path"]).stat().st_size}}


def _plan_row(paths: ReframePaths, c, class_row: dict) -> dict:
    cid = c.id
    mp4 = paths.production_clips / f"{cid}.mp4"
    side = paths.production_clips / f"{cid}.render.json"
    ass = paths.production_clips / f"{cid}.ass"
    facts = ra.media_facts(str(mp4)) if mp4.exists() else {}
    tw, th = clipmod._TARGETS.get(c.aspect.value, (None, None))
    st_size = mp4.stat().st_size if mp4.exists() else 0
    return {
        "clip_id": cid, "moment_id": c.parent_id, "aspect": c.aspect.value,
        "media_path": str(mp4), "sidecar_path": str(side), "ass_path": str(ass),
        "preimage": {"media_sha256": ra.sha256_file(mp4), "sidecar_sha256": ra.sha256_file(side),
                     "ass_file_sha256": ra.sha256_file(ass) if ass.exists() else None},
        "fp_old": class_row["fp_stored"], "fp_new": class_row["fp_new"],
        "payload_old": class_row["payload_old"], "payload_new": class_row["payload_new"],
        "payload_delta": class_row["payload_delta"],
        "ass_new": class_row["payload_new"]["ass"],
        "expect": {"duration": facts.get("duration"), "fps": facts.get("fps"),
                   "has_audio": facts.get("has_audio"), "audio_channels": facts.get("audio_channels"),
                   "audio_rate": facts.get("audio_rate"), "vcodec": facts.get("vcodec"),
                   "audio_codec": facts.get("audio_codec"),
                   "target_w": tw, "target_h": th, "st_size": st_size},
    }


def _iter_awaiting_clips(led: Ledger):
    seen = set()
    for p in led.posts.values():
        if p.state is not PostState.awaiting_approval:
            continue
        cid = p.parent_id
        if cid in seen or cid not in led.clips:
            continue
        seen.add(cid)
        yield led.clips[cid]


def _scan(paths: ReframePaths, scratch_cfg: Config, led: Ledger, *, limit: int | None = None) -> list:
    rows = []
    for c in _iter_awaiting_clips(led):
        rows.append(classify_clip(paths, scratch_cfg, led, c))
        if limit is not None and len(rows) >= limit:
            break
    return rows


def _log_counts(cfg: Config, totals: dict, *, outcome: str) -> None:
    log = get_logger(cfg)
    log("overlay_reburn", "-", outcome, totals=json.dumps(totals, sort_keys=True))
    for k, v in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])):
        log("overlay_reburn", "-", k, count=v)


def run_dry_run(cfg: Config, *, limit: int | None = None, scratch=None) -> dict:
    scratch_root = Path(scratch) if scratch else Path(tempfile.mkdtemp(prefix="fanops_or_"))
    paths = ReframePaths.build(cfg.root, scratch_root)
    snapshot_ledger(paths)
    led = Ledger.load(paths.scratch_cfg)
    clips = _scan(paths, paths.scratch_cfg, led, limit=limit)
    totals = dict(Counter(r["classification"] for r in clips))
    _log_counts(cfg, totals, outcome="dry_run")
    return {"clips": clips, "totals": totals, "partial": bool(limit), "scratch": str(scratch_root)}


def run_apply(cfg: Config, *, limit: int | None = None, scratch=None) -> dict:
    """Pause the pump, lock, re-assert eligibility, stage, then replace three files."""
    set_paused(cfg, True)
    scratch_root = Path(scratch) if scratch else Path(tempfile.mkdtemp(prefix="fanops_or_"))
    paths = ReframePaths.build(cfg.root, scratch_root)
    snapshot_ledger(paths)
    led = Ledger.load(paths.scratch_cfg)
    class_rows = _scan(paths, paths.scratch_cfg, led, limit=limit)
    eligible = [r for r in class_rows if r.get("classification") == "eligible"]
    run_id = new_run_id()
    dirs = RunDirs.build(cfg, run_id)
    dirs.mkdirs()

    plan_rows = []
    for r in eligible:
        c = led.clips[r["clip_id"]]
        plan_rows.append(_plan_row(paths, c, r))
    dirs.plan.write_text(json.dumps({"run_id": run_id, "clips": [
        {k: v for k, v in row.items() if k not in ("payload_old", "payload_new", "ass_new")}
        for row in plan_rows
    ]}, indent=2, sort_keys=True, default=str))

    need = 0
    for row in plan_rows:
        for key in ("media_path", "ass_path", "sidecar_path"):
            p = Path(row[key])
            if p.exists():
                need += p.stat().st_size
    required = need * 2 * 2
    have = ra.free_bytes(cfg.clips)
    if have < required:
        get_logger(cfg)("overlay_reburn", "-", "aborted_disk", have=have, required=required)
        return {"run_id": run_id, "aborted": True, "status": "ABORTED_DISK",
                "error": f"free {have} < required {required} (2x(backup+stage))",
                "totals": dict(Counter(r["classification"] for r in class_rows))}

    lock = ra.MigrationLock(cfg, run_id)
    lock.acquire()
    results = []
    try:
        live = Ledger.load(cfg)
        for row in plan_rows:
            c = live.clips.get(row["clip_id"])
            if c is None:
                results.append({"clip_id": row["clip_id"], "status": "PREIMAGE_MISMATCH",
                                "error": "clip vanished"})
                continue
            again = classify_clip(paths, paths.scratch_cfg, live, c)
            if again.get("classification") != "eligible":
                results.append({"clip_id": row["clip_id"], "status": "REASSERT_SKIP",
                                "error": again.get("classification")})
                continue
            try:
                out = apply_clip(paths, dirs, live, row, run_id=run_id, prod_cfg=cfg)
            except Exception as exc:
                get_logger(cfg)("overlay_reburn", row["clip_id"], "apply_failed", reason=type(exc).__name__)
                out = {"clip_id": row["clip_id"], "status": "ERROR", "error": type(exc).__name__}
            results.append(out)
            ra.journal_append(dirs, {**out, "phase": out.get("phase", "apply")})
    finally:
        lock.release()

    totals = dict(Counter(r.get("status") or r.get("classification") for r in results))
    class_totals = dict(Counter(r["classification"] for r in class_rows))
    _log_counts(cfg, {**class_totals, **{f"apply:{k}": v for k, v in totals.items()}}, outcome="apply")
    summary = {"run_id": run_id, "aborted": False, "totals": class_totals, "apply_totals": totals,
               "planned": len(plan_rows), "results": results, "run_dir": str(dirs.root)}
    dirs.summary.write_text(json.dumps({k: v for k, v in summary.items() if k != "results"},
                                       indent=2, sort_keys=True, default=str))
    return summary
