"""Studio make/ingest mutations (no Flask): catalogue inbox footage, pull a URL, accept browser uploads
(traversal-safe), and drive the produce pipeline (run advance/prepare) under one Ledger.transaction each. Pure
of the post-production surfaces — depends only on actions_common (ActionResult/_now); never on a sibling action
module, so the import graph stays acyclic."""
from __future__ import annotations
import hashlib, os, subprocess, uuid
from pathlib import Path
from typing import Optional, Sequence, Union

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from fanops.config import Config
from fanops.errors import AuthError, ToolchainMissingError
from fanops.ingest import MEDIA_EXT
from fanops.ledger import Ledger
from fanops.timeutil import iso_z
from fanops.studio.actions_common import ActionResult, _now


_VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}   # the has_video_stream subset of MEDIA_EXT
if not (_VIDEO_EXT <= MEDIA_EXT): raise ValueError("_VIDEO_EXT drifted out of ingest.MEDIA_EXT")  # import-time drift guard (not assert — survives -O)

def kick_prepare(cfg: Config) -> bool:
    """WS-D1 Phase 3 — de-lazify: spawn a DETACHED `fanops run` so a fresh browser ingest starts processing
    IMMEDIATELY instead of waiting up to one daemon interval. BEST-EFFORT + FAIL-OPEN: every failure is
    swallowed (logged), so the kick is an optimization, never a precondition for ingest — the launchd daemon
    remains the GUARANTEED driver. Debounced by the run lease: if a driver already owns the workspace, the
    kick is a no-op (the owner drives the next tick). Uses the daemon's own spawn helpers (the codebase-blessed
    `fanops run` invocation). Returns True iff it spawned. DECOUPLED: the kick injects NO responder default —
    the spawned `fanops run` resolves it via .env / Config.responder_mode (the single source of truth), the
    same path the daemon uses. No hidden third default that silently spends LLM; an operator's explicit
    FANOPS_RESPONDER still rides os.environ."""
    from fanops.daemon import _fanops_bin, _daemon_path
    from fanops.log import get_logger
    from fanops.pipeline_run import run_held
    try:
        if run_held(cfg):
            return False                           # a driver already owns the workspace — no stacked run
        env = cfg.spawn_env(path=_daemon_path())       # responder resolved by the run itself, not forced here
        subprocess.Popen([_fanops_bin(), "run", "--base-time", iso_z(_now(None))],
                                cwd=str(cfg.root), env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)   # detached: survives the request, OS-reaped
        return True
    except Exception as exc:
        get_logger(cfg)("run", "-", "kick_failed", err=str(exc)[:160]); return False


def catalogue_inbox(cfg: Config) -> ActionResult:
    """Gate-ON intake: stage+ingest inbox footage as pending — no batch mint, no kick_prepare."""
    from fanops.ingest import stage_inbox_candidates, ingest_staged, _archive_staged
    from fanops.digest import write_digest
    n = added = 0; counts = None
    try:
        staged = stage_inbox_candidates(cfg)
        with Ledger.transaction(cfg) as led:
            led, counts = ingest_staged(led, cfg, staged)
            added = counts.added; n = len(led.sources)
        _archive_staged(cfg, staged)
        write_digest(Ledger.load(cfg), cfg)
    except Exception as exc:
        return ActionResult(ok=False, error=f"ingest failed: {str(exc)[:160]}")
    detail = {"sources": n, "added": added}
    if counts is not None and counts.excluded: detail["excluded"] = counts.excluded
    if counts is not None and counts.skipped: detail["skipped"] = counts.skipped
    if counts is not None and counts.retired_dedup: detail["retired_dedup"] = counts.retired_dedup
    return ActionResult(ok=True, detail=detail)


def bind_queue(cfg: Config, *, source_ids, batch_name: str = "", target_accounts=(),
               burn_subs: bool | None = None) -> ActionResult:
    """Stamp unbound pending sources onto a new Batch (one transaction). Repeatable → multiple queue lines."""
    from fanops.batches import create_batch
    from fanops.accounts import Accounts
    from fanops.models import SourceState, batch_id as _batch_id
    name = (batch_name or "").strip()
    if not name:
        return ActionResult(ok=False, error="batch name required")
    ids = [s for s in (source_ids or []) if s]
    if not ids:
        return ActionResult(ok=False, error="select at least one pending source")
    try:
        with Ledger.transaction(cfg) as led:
            now_iso = iso_z(_now(None))
            stamp = [sid for sid in ids if (s := led.sources.get(sid)) and s.state is SourceState.pending and not s.batch_id]
            if not stamp:
                return ActionResult(ok=False, error="no unbound pending sources in selection")
            bid = _batch_id(name, now_iso)
            for sid in stamp:
                led.sources[sid] = led.sources[sid].model_copy(update={"batch_id": bid})
            active = {a.handle for a in Accounts.load(cfg).active()}
            batch = create_batch(led, name=name, target_accounts=list(target_accounts),
                                 now_iso=now_iso, active_handles=active, burn_subs=burn_subs)
    except Exception as exc:
        return ActionResult(ok=False, error=f"bind failed: {str(exc)[:160]}")
    detail = {"batch": batch.name, "batch_id": batch.id, "sources": len(stamp), "target_accounts": batch.target_accounts}
    if batch.error_reason: detail["warnings"] = [batch.error_reason]
    return ActionResult(ok=True, detail=detail)


def release_batch(cfg: Config, batch_id: str, *, confirmed: bool = True) -> ActionResult:
    """Release one queue line: held pending→catalogued for that batch; kick when ≥1 released."""
    from fanops.models import SourceState
    if cfg.is_live and not confirmed:
        return ActionResult(ok=False, error=f"LIVE backend ({cfg.effective_publish_mode()}): releasing "
                            "starts clip production — tick the confirm box, then run again.")
    released = 0
    try:
        with Ledger.transaction(cfg) as led:
            for sid, s in list(led.sources.items()):
                if s.state is SourceState.pending and s.batch_id == batch_id:
                    led.set_source_state(sid, SourceState.catalogued); released += 1
    except Exception as exc:
        return ActionResult(ok=False, error=f"release failed: {str(exc)[:160]}")
    if released >= 1: kick_prepare(cfg)
    return ActionResult(ok=True, detail={"released": released, "batch_id": batch_id})


def release_all_held(cfg: Config, *, confirmed: bool = True) -> ActionResult:
    """Release every held queue line (pending + batch_id set); one kick when ≥1 released."""
    from fanops.models import SourceState
    if cfg.is_live and not confirmed:
        return ActionResult(ok=False, error=f"LIVE backend ({cfg.effective_publish_mode()}): releasing "
                            "starts clip production — tick the confirm box, then run again.")
    released = 0
    try:
        with Ledger.transaction(cfg) as led:
            for sid, s in list(led.sources.items()):
                if s.state is SourceState.pending and s.batch_id:
                    led.set_source_state(sid, SourceState.catalogued); released += 1
    except Exception as exc:
        return ActionResult(ok=False, error=f"release failed: {str(exc)[:160]}")
    if released >= 1: kick_prepare(cfg)
    return ActionResult(ok=True, detail={"released": released})


def run_ingest(cfg: Config, *, batch_name: str = "", target_accounts=(), burn_subs: bool | None = None) -> ActionResult:
    """Drive `fanops ingest` from the browser: catalogue 01_inbox under one transaction (the exact
    cmd_ingest path). When batch_name is non-blank, mint a named, account-targeted Batch in the SAME
    transaction and catalogue the inbox under its id (blank name => today's ungrouped ingest, byte-
    identical). A toolchain-absent / control-file error is surfaced as a clean ActionResult, never a 500."""
    if cfg.queue_gate:
        return catalogue_inbox(cfg)
    from fanops.ingest import stage_inbox_candidates, ingest_staged, _archive_staged
    from fanops.digest import write_digest
    from fanops.batches import create_batch
    from fanops.accounts import Accounts
    from fanops.models import batch_id as _batch_id
    n = added = 0; batch = None; counts = None
    try:
        staged = stage_inbox_candidates(cfg)
        with Ledger.transaction(cfg) as led:
            bid = None; now_iso = iso_z(_now(None))
            if batch_name.strip():
                bid = _batch_id(batch_name.strip(), now_iso)   # ING-3: deterministic id, computed BEFORE catalogue (write-once on the Source)
            led, counts = ingest_staged(led, cfg, staged, batch_id=bid)
            added = counts.added
            if bid is not None and added >= 1:                 # ING-3: mint the Batch RECORD only when ≥1 source actually attached
                # Account-First (T1/T4): feed the active-handle set so a batch targeting a dead/typo'd
                # handle is FLAGGED at creation (else crosspost silently skips every surface -> 0 posts).
                active = {a.handle for a in Accounts.load(cfg).active()}   # loaded only on the batched path (byte-identical otherwise)
                batch = create_batch(led, name=batch_name, target_accounts=list(target_accounts),
                                     now_iso=now_iso, active_handles=active, burn_subs=burn_subs)   # same (name, now_iso) -> same id == bid stamped above
            n = len(led.sources)
        _archive_staged(cfg, staged)
        write_digest(Ledger.load(cfg), cfg)
    except Exception as exc:
        return ActionResult(ok=False, error=f"ingest failed: {str(exc)[:160]}")
    if added >= 1: kick_prepare(cfg)                        # WS-D1: new footage -> drive NOW (best-effort; daemon backstops). Covers the one-click upload path too (it delegates here).
    detail = {"sources": n, "added": added}
    if counts is not None and counts.excluded: detail["excluded"] = counts.excluded   # ING-5: PII drops visible on native path too
    if counts is not None and counts.skipped: detail["skipped"] = counts.skipped       # copy-failed/unverifiable files left in inbox — VISIBLE, not a silent "Done" while the inbox jams
    if counts is not None and counts.retired_dedup: detail["retired_dedup"] = counts.retired_dedup
    if batch is not None:
        detail.update(batch=batch.name, batch_id=batch.id)
        if batch.error_reason: detail["warnings"] = [batch.error_reason]   # zero-target advisory -> Studio Run panel
    elif batch_name.strip() and added == 0:                    # named a batch but nothing landed → no orphan, tell the operator
        detail["batch_skipped"] = "no new footage — batch not created (inbox empty or all duplicates)"
    return ActionResult(ok=True, detail=detail)


def run_pull(cfg: Config, url: str) -> ActionResult:
    """Drive `fanops pull <url>`: yt-dlp the URL (network, NO lock) then ingest under a transaction.
    Rejects a non-http(s) URL up front (mirrors the CLI's _http_url validator)."""
    from fanops.ingest import download_url, _pull_stage, stage_inbox_candidates, ingest_staged, _archive_staged
    from fanops.digest import write_digest
    if not (url or "").strip().startswith(("http://", "https://")):
        return ActionResult(ok=False, error=f"url must be http(s):// — got {url!r}")
    n = added = 0
    try:
        produced = download_url(cfg, url.strip())
        staged = stage_inbox_candidates(cfg, origin="url", inbox=_pull_stage(cfg), origin_paths=produced)
        with Ledger.transaction(cfg) as led:
            # per-file origin (audit c0-f1 / ING-6): the pull catalogues ONLY its isolated .pull stage, so a
            # manual drop sitting in the inbox is never re-scanned or mislabeled — same as the CLI's cmd_pull.
            led, counts = ingest_staged(led, cfg, staged)
            n = len(led.sources); added = counts.added
        _archive_staged(cfg, staged)
        write_digest(Ledger.load(cfg), cfg)
    except Exception as exc:
        return ActionResult(ok=False, error=f"pull failed: {str(exc)[:160]}")
    if added >= 1 and not cfg.queue_gate: kick_prepare(cfg)
    return ActionResult(ok=True, detail={"sources": n, "added": added})


def _resolve_upload_dest(inbox: Path, raw: str, allowed_ext: set[str]) -> Union[tuple[str, Path, Path], tuple[None, str]]:
    """Path-safety triad for one upload: secure_filename + traversal reject + inbox-bound resolve. Returns
    (name, dest, tmp) on success or (None, skip_reason) on rejection — shared by save_uploads and chunked upload."""
    name = secure_filename(raw)                                    # strips path, .., unsafe chars; "" if hostile
    if not name or "/" in raw or "\\" in raw or ".." in raw:       # reject traversal on the RAW name (mirror approve_candidate)
        return None, "unsafe name"
    if Path(name).suffix.lower() not in allowed_ext:
        return None, "unsupported type"
    suffix = Path(name).suffix
    name = Path(name).stem[:255 - len(suffix) - len(".uploadpart")] + suffix   # keep name + temp-suffix ≤ NAME_MAX
    dest = (inbox / name).resolve()
    if not dest.is_relative_to(inbox):                             # belt-and-braces: final path MUST stay in the inbox
        return None, "escapes inbox"
    if dest.exists():                                             # ING-4: a truncated/sanitized collision must NOT os.replace over a DIFFERENT video
        stem = Path(name).stem[:255 - len(suffix) - len(".uploadpart") - 9]   # leave room for a -xxxxxxxx discriminator
        name = f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"; dest = (inbox / name).resolve()   # sha identity is downstream; the inbox name is pure staging
    tmp = inbox / f"{name}.uploadpart"                            # same-dir temp → os.replace is atomic; suffix ∉ MEDIA_EXT so a leaked temp is never ingested
    return name, dest, tmp


def _upload_meta_path(tmp: Path) -> Path:
    return tmp.with_suffix(".uploadmeta.json")   # clip.mp4.uploadpart → clip.mp4.uploadmeta.json


def _find_upload_meta(cfg: Config, inbox: Path, upload_id: str) -> tuple[Optional[Path], Optional[dict]]:
    """Locate a chunked-upload meta file by upload_id. Fail-open to (None, None) when absent."""
    from fanops.log import get_logger
    for p in inbox.glob("*.uploadmeta.json"):
        try:
            meta = __import__("json").loads(p.read_text())
            if meta.get("upload_id") == upload_id:
                return p, meta
        except Exception as exc:
            get_logger(cfg)("upload", upload_id, "meta_read_failed", path=p.name, err=str(exc)[:160]); continue
    return None, None


def upload_init(cfg: Config, filename: str, size: int, sha256: str) -> ActionResult:
    """Begin or resume a chunked inbox upload. Validates the filename like save_uploads, then either resumes
    a matching .uploadpart + .uploadmeta.json (same filename/size/sha256) or creates fresh empty temps."""
    dest_root = cfg.inbox; dest_root.mkdir(parents=True, exist_ok=True)
    inbox = dest_root.resolve()
    resolved = _resolve_upload_dest(inbox, filename or "", _VIDEO_EXT)
    if resolved[0] is None:
        return ActionResult(ok=False, error=resolved[1])
    name, dest, tmp = resolved
    meta_p = _upload_meta_path(tmp)
    if tmp.exists() and meta_p.exists():
        try:
            meta = __import__("json").loads(meta_p.read_text())
            if (meta.get("filename") == filename and meta.get("size") == size
                    and meta.get("sha256") == sha256 and meta.get("name") == name):
                received = tmp.stat().st_size
                return ActionResult(ok=True, detail={"upload_id": meta["upload_id"], "offset": received, "name": name})
        except Exception as exc:
            from fanops.log import get_logger
            get_logger(cfg)("upload", "-", "meta_resume_failed", err=str(exc)[:160])
    upload_id = uuid.uuid4().hex
    try:
        tmp.write_bytes(b"")                                      # truncate / create the part file
        from fanops.controlio import write_json_atomic
        write_json_atomic(meta_p, {"upload_id": upload_id, "filename": filename, "name": name,
                                     "size": size, "sha256": sha256, "received": 0, "dest": str(dest)})
    except OSError as exc:
        try: tmp.unlink(missing_ok=True); meta_p.unlink(missing_ok=True)
        except OSError: pass
        return ActionResult(ok=False, error=exc.strerror or "write failed")
    return ActionResult(ok=True, detail={"upload_id": upload_id, "offset": 0, "name": name})


def upload_chunk(cfg: Config, upload_id: str, offset: int, data: bytes) -> ActionResult:
    """Append one sequential chunk to an in-progress upload. Offset MUST equal the current part size."""
    inbox = cfg.inbox.resolve()
    meta_p, meta = _find_upload_meta(cfg, inbox, upload_id)
    if not meta_p or not meta:
        return ActionResult(ok=False, error=f"unknown upload: {upload_id}")
    tmp = inbox / f"{meta['name']}.uploadpart"
    if not tmp.exists():
        return ActionResult(ok=False, error=f"unknown upload: {upload_id}")
    received = tmp.stat().st_size
    if offset != received:
        return ActionResult(ok=False, error="offset mismatch", detail={"received": received, "error": "offset mismatch"})
    try:
        with open(tmp, "ab") as fh: fh.write(data)
        received = tmp.stat().st_size
        meta = {**meta, "received": received}
        from fanops.controlio import write_json_atomic
        write_json_atomic(meta_p, meta)
    except OSError as exc:
        return ActionResult(ok=False, error=exc.strerror or "write failed")
    return ActionResult(ok=True, detail={"received": received})


def upload_finalize(cfg: Config, upload_id: str, *, batch_name: str = "", target_accounts=(),
                    burn_subs: bool | None = None, trigger_ingest: bool = True) -> ActionResult:
    """Verify size + sha256, probe the video stream on the .uploadpart, os.replace into the inbox, delete meta.
    When trigger_ingest is True, chains run_ingest like save_uploads_and_ingest."""
    inbox = cfg.inbox.resolve()
    meta_p, meta = _find_upload_meta(cfg, inbox, upload_id)
    if not meta_p or not meta:
        return ActionResult(ok=False, error=f"unknown upload: {upload_id}")
    tmp = inbox / f"{meta['name']}.uploadpart"
    dest = Path(meta.get("dest") or str(inbox / meta["name"]))
    if not tmp.exists():
        return ActionResult(ok=False, error=f"unknown upload: {upload_id}")
    got_size = tmp.stat().st_size
    if got_size != meta.get("size"):
        return ActionResult(ok=False, error=f"size mismatch — expected {meta.get('size')}, got {got_size}")
    got_sha = hashlib.sha256(tmp.read_bytes()).hexdigest()
    if got_sha != meta.get("sha256"):
        try: tmp.unlink(missing_ok=True); meta_p.unlink(missing_ok=True)
        except OSError: pass
        return ActionResult(ok=False, error="sha256 mismatch — upload rejected")
    try:
        from fanops.ingest import has_video_stream                # local import so a test's mocker.patch is seen
        try:
            if not has_video_stream(tmp):
                tmp.unlink(missing_ok=True); meta_p.unlink(missing_ok=True)
                return ActionResult(ok=False, error="no video stream")
        except ToolchainMissingError:
            tmp.unlink(missing_ok=True); meta_p.unlink(missing_ok=True)
            return ActionResult(ok=False, error="cannot verify video — install ffmpeg")
        os.replace(tmp, dest)
        meta_p.unlink(missing_ok=True)
    except OSError as exc:
        try: tmp.unlink(missing_ok=True)
        except OSError: pass
        return ActionResult(ok=False, error=exc.strerror or "write failed")
    if not trigger_ingest:
        return ActionResult(ok=True, detail={"saved": [meta["name"]]})
    ing = catalogue_inbox(cfg) if cfg.queue_gate else run_ingest(cfg, batch_name=batch_name,
                                                                  target_accounts=target_accounts, burn_subs=burn_subs)
    detail = {"saved": [meta["name"]], **(ing.detail or {})}
    if not ing.ok:
        return ActionResult(ok=False, detail=detail,
                            error=f"uploaded 1 file(s), but auto-ingest failed (they're in 01_inbox — "
                                  f"click 'Ingest inbox' to retry): {ing.error}")
    return ActionResult(ok=True, detail=detail)


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
        resolved = _resolve_upload_dest(inbox, raw, allowed_ext)
        if resolved[0] is None:
            skipped.append((raw, resolved[1])); continue
        name, dest, tmp = resolved
        try:
            f.save(str(tmp))                                          # FileStorage.save streams in chunks (no full-buffer)
            if probe:
                from fanops.ingest import has_video_stream                # local import so a test's mocker.patch is seen
                try:
                    if not has_video_stream(tmp):
                        tmp.unlink(missing_ok=True); skipped.append((raw, "no video stream")); continue
                except ToolchainMissingError:
                    tmp.unlink(missing_ok=True)                         # ING-9: an unverifiable upload would later ABORT the whole native ingest pass
                    skipped.append((raw, "cannot verify video — install ffmpeg")); continue   # reject, don't keep-then-abort
            os.replace(tmp, dest)                                     # atomic swap-in; a crash mid-stream leaves only the .uploadpart temp
        except OSError as exc:
            try: tmp.unlink()                                         # best-effort cleanup of the partial temp
            except OSError: pass
            skipped.append((raw, exc.strerror or "write failed")); continue   # strerror omits the fs path (no path disclosure in the reason)
        saved.append(name)
    if not saved:                                                     # every file was rejected → a real failure, not a green "0 saved"
        return ActionResult(ok=False, error=f"nothing saved — {len(skipped)} file(s) rejected (wrong type, unsafe name, or unreadable)", detail={"saved": saved, "skipped": skipped})
    return ActionResult(ok=True, detail={"saved": saved, "skipped": skipped})


def save_uploads_and_ingest(cfg: Config, files: Sequence[FileStorage], *, batch_name: str = "",
                            target_accounts=(), burn_subs: bool | None = None) -> ActionResult:
    """One-click upload->catalogue (M5 fast-follow): stream the uploads (save_uploads) and, IF any landed,
    immediately run the ingest pass so the operator doesn't need a second 'Ingest inbox' click. A save
    failure short-circuits (nothing landed -> nothing to ingest). An ingest failure is surfaced but the
    files are SAFELY in 01_inbox — a manual 'Ingest inbox' still catalogues them — so it's a recoverable
    not-fully-done, never a lost upload. Returns the merged detail (saved/skipped + sources). batch_name/
    target_accounts thread through to run_ingest so an upload can mint its named batch in one click."""
    up = save_uploads(cfg, files)
    if not up.ok:
        return up                                          # nothing landed -> nothing to ingest
    if cfg.queue_gate:
        ing = catalogue_inbox(cfg)
    else:
        ing = run_ingest(cfg, batch_name=batch_name, target_accounts=target_accounts, burn_subs=burn_subs)
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
    from fanops.ingest import stage_inbox_candidates, ingest_staged, _archive_staged, is_excluded, MEDIA_EXT
    from fanops.digest import write_digest
    staged = ([f for f in cfg.thirdparty_inbox.rglob("*") if f.is_file() and f.suffix.lower() in MEDIA_EXT]
              if cfg.thirdparty_inbox.exists() else [])
    excluded = sum(1 for f in staged if is_excluded(f.name))      # deliberate uploads the name-filter drops
    n = added = 0
    try:
        staged = stage_inbox_candidates(cfg, origin="upload", origin_kind="third_party", inbox=cfg.thirdparty_inbox)
        with Ledger.transaction(cfg) as led:
            before = sum(1 for s in led.sources.values() if s.origin_kind == "third_party")
            led, _ = ingest_staged(led, cfg, staged)
            n = sum(1 for s in led.sources.values() if s.origin_kind == "third_party")
            added = n - before                                    # THIS call's delta (sha256 dedup → 0 on a repeat)
        _archive_staged(cfg, staged)
        write_digest(Ledger.load(cfg), cfg)
    except Exception as exc:
        return ActionResult(ok=False, error=f"third-party ingest failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"sources": n, "added": added, "excluded": excluded})


def run_advance(cfg: Config, base_time: Optional[str] = None, *, confirmed: bool = True) -> ActionResult:
    """Drive one `fanops advance` pass (transcribe -> moments gate -> render -> captions gate ->
    crosspost -> publish due). Blocks on an unusable accounts config first (mirrors cmd_advance's
    _check_accounts: an empty account_id must never reach the backend). base_time defaults to now, so a
    Studio-triggered pass schedules across today; any advance error (incl. a live auth failure) is
    surfaced cleanly, never a 500. On a LIVE backend a pass PUBLISHES to real accounts, so the Studio
    button must pass confirmed=True (the route derives it from a confirm checkbox); dryrun publishes
    nothing and needs no confirm."""
    from fanops.pipeline import advance
    from fanops.accounts import Accounts
    if cfg.is_live and not confirmed:
        # UI-LIE-FIX: per-channel truth, not the legacy global.
        return ActionResult(ok=False, error=f"LIVE backend ({cfg.effective_publish_mode()}): a pass "
                            "PUBLISHES due posts to real accounts — tick the confirm box, then run again.")
    try:
        problems = Accounts.load(cfg).validate()       # malformed accounts.json -> clean error, not 500
    except Exception as exc:
        return ActionResult(ok=False, error=f"accounts.json: {str(exc)[:160]}")
    if problems:
        return ActionResult(ok=False, error="accounts.json: " + "; ".join(problems))
    bt = base_time or iso_z(_now(None))
    try:
        from fanops.pipeline_run import run_lease
        from fanops.errors import RunBusyError
        with run_lease(cfg):
            summary = advance(cfg, base_time=bt)
    except RunBusyError:
        return ActionResult(ok=False, error="pipeline busy — a run is driving")
    except AuthError as exc:
        # F52 parity: a bad/missing key fails EVERY post — advance's own transaction already rolled
        # back (it saves only on clean exit), but surface the FATAL severity, not a soft "failed".
        # UI-LIE-FIX: a multi-backend deployment can fail auth on either side; surface the FIRST
        # live-ready provider's key (advance iterates accounts in deterministic order, so the first
        # failure is the first provider). Falls back to FANOPS_POSTER if no live channel exists.
        # UI-LIE-FIX: derive the auth-key name from the EXCEPTION CLASS, not a backend guess.
        # The error type IS the truth (PostizAuthError -> POSTIZ_API_KEY, etc).
        key = Config.auth_key_name_from_error(exc)
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
        # UI-LIE-FIX: per-channel truth, not the legacy global.
        return ActionResult(ok=False, error=f"LIVE backend ({cfg.effective_publish_mode()}): a prepare "
                            "pass PUBLISHES due posts to real accounts — tick the confirm box, then run again.")
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
    try:
        from fanops.pipeline_run import run_lease
        from fanops.errors import RunBusyError
        with run_lease(cfg):
            for _ in range(10):                                # respond -> advance until stable (no gate left)
                try:
                    responder.answer_pending(cfg)              # llm answers the gates; manual writes nothing
                    summary = advance(cfg, base_time=bt)
                except AuthError as exc:
                    # UI-LIE-FIX: derive the auth-key name from the EXCEPTION CLASS — the structural truth.
                    key = Config.auth_key_name_from_error(exc)
                    return ActionResult(ok=False, error=f"FATAL auth failure — check {key}: {str(exc)[:160]}")
                except Exception as exc:
                    return ActionResult(ok=False, error=f"prepare failed: {str(exc)[:160]}")
                if summary["awaiting"]["moments"] == 0 and summary["awaiting"]["captions"] == 0:
                    done = True; break
    except RunBusyError:
        return ActionResult(ok=False, error="pipeline busy — a run is driving")
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
