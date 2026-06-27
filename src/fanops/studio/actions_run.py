"""Studio make/ingest mutations (no Flask): catalogue inbox footage, pull a URL, accept browser uploads
(traversal-safe), and drive the produce pipeline (run advance/prepare) under one Ledger.transaction each. Pure
of the post-production surfaces — depends only on actions_common (ActionResult/_now); never on a sibling action
module, so the import graph stays acyclic."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional, Sequence

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
        produced = download_url(cfg, url.strip())
        with Ledger.transaction(cfg) as led:
            # per-file origin (audit c0-f1): only the freshly-pulled files are "url"; a manual drop already
            # in the inbox keeps "drop" — same correlation as the CLI's cmd_pull, so neither surface mislabels.
            led = ingest_drops(led, cfg, origin="url", origin_paths=produced)
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
