"""Canary publish-path tooling — an ISOLATED single-lineage probe, decoupled from the pipeline.

Five operator verbs (`fanops canary …`): `prepare` mints exactly one Source+Moment+Clip+Batch (ZERO
Renders, ZERO Posts) for the reserved local account `fanops_canary`; `discard` retires that lineage
pre-mint; `cancel` retires an awaiting/queued canary Post before any possible network acceptance;
`baseline`/`compare` capture and diff a read-only multilayer ledger manifest.

INVARIANTS (each has a test): this module NEVER calls advance / crosspost_clips / crosspost_to_account /
publish_due / publish_post / reconcile_due / Zernio / Postiz / HTTP / an LLM / an agent gate. Every id is
content-addressed (idempotent). Every filesystem path is realpath-contained to the run-owned directory.
Rendering is ledger-free and runs OUTSIDE the ledger lock; adoption is ONE short transaction. A discarded
run is terminal. Baseline capture is always `candidate` — it never self-accepts.
"""
from __future__ import annotations
import hashlib, json, os, re, shutil, sqlite3, subprocess, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:                                   # type-only: no runtime/compile edge to S16 (studio)
    from fanops.studio.actions_common import ActionResult

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.ids import child_id
from fanops.errors import redact
from fanops.models import (Source, Moment, Clip, Batch, SourceState, MomentState, ClipState, BatchState,
                           Fmt, Platform, PostState, PLATFORM_MAX_SECONDS, is_real_submission_id)
from fanops.accounts import Accounts, AccountStatus
from fanops.audit import write_audit
from fanops.log import get_logger


# ActionResult lives under S16 (studio); import it LAZILY so canary carries no compile-time studio edge
# (it belongs to S17_cli_daemon, which lazy-depends on studio). `from __future__ import annotations` keeps
# the `-> ActionResult` return hints as un-evaluated strings, so no module-level import is needed.
def _ok(detail=None):
    from fanops.studio.actions_common import ActionResult
    return ActionResult.success(detail)

def _err(msg):
    from fanops.studio.actions_common import ActionResult
    return ActionResult.failure(msg)


# Ledger-FREE render + probe primitives, wrapped so the heavy `clip`/`ingest` imports stay LAZY (no
# import-time cycle) yet remain monkeypatchable in tests (patch `fanops.canary._do_render_single`, etc.).
def _do_probe(path: Path):
    from fanops.ingest import probe_dimensions
    return probe_dimensions(path)

def _do_render_single(src: str, dst: str, cs: float, ce: float, aspect_value: str, *, src_w: int, src_h: int):
    from fanops.clip import render_reframed
    return render_reframed(src, dst, cs, ce, aspect_value, src_w=src_w, src_h=src_h)

def _do_render_supercut(src: str, dst: str, spans: list, aspect_value: str, *, src_w: int, src_h: int):
    from fanops.clip import render_supercut_reframed
    return render_supercut_reframed(src, dst, spans, aspect_value, src_w=src_w, src_h=src_h)

# ---- pinned, PERMANENT identity contract (never change these) ----
CANARY_HANDLE = "fanops_canary"                 # reserved LOCAL account alias (the remote TikTok handle may differ)
CANARY_RUN_ID_VERSION = "1"
BASELINE_FORMAT_VERSION = "1"
_ENTITY_TOKEN_VERSION = "1"
# Concrete, hardcoded UUIDv5 namespace for canary run-id derivation. Chosen ONCE and permanent: changing it
# would make a re-run derive a different run_id for identical inputs, silently breaking idempotency.
CANARY_RUN_NAMESPACE = uuid.UUID("a1c9e6d2-7b34-5f81-9e0a-2d6f4c8b1e73")

_TARGET_PLATFORM = Platform.tiktok
_TARGET_ASPECT = Fmt.r9x16
_MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
_RUN_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_RUN_ID_RE = re.compile(r"^canary_[0-9a-f]{32}$")
_REASON_MAX = 180
_CANARY_REASON_PREFIX = "canary_cancelled: "


# ---------- canonicalization helpers ----------

def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

def _sha256_bytes_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def _sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()

def _norm_hashtags(tags) -> list:
    seen, out = set(), []
    for t in (tags or []):
        t = ("" if t is None else str(t)).strip().lstrip("#").lower()
        if t and t not in seen: seen.add(t); out.append(t)
    return out

def _norm_label(label: Optional[str]) -> Optional[str]:
    if label is None: return None
    label = str(label).strip().lower()
    return label or None

def _norm_segments(segments) -> Optional[list]:
    if not segments: return None
    return [[float(s), float(e)] for s, e in segments]

def _canon_time(v) -> float:
    return float(v)


# ---------- identity (BC3): canonical JSON name -> UUIDv5 run id; full-sha256 content tokens ----------

def _canonical_run_name(*, media_sha256: str, start, end, segments, caption: str,
                        hashtags, hook: Optional[str], run_label: Optional[str]) -> str:
    return _canon({
        "version": CANARY_RUN_ID_VERSION,
        "handle": CANARY_HANDLE,
        "platform": _TARGET_PLATFORM.value,
        "media_sha256": media_sha256,
        "start": _canon_time(start),
        "end": (_canon_time(end) if end is not None else None),
        "segments": _norm_segments(segments),
        "caption_sha256": _sha256_text(caption),
        "hashtags": _norm_hashtags(hashtags),
        "hook_sha256": (_sha256_text(hook) if hook is not None else None),
        "run_label": _norm_label(run_label),
    })

def _run_id_from_name(name: str) -> str:
    return "canary_" + uuid.uuid5(CANARY_RUN_NAMESPACE, name).hex

def _entity_token(kind: str, fields: dict) -> str:
    """Full SHA-256 of a VERSIONED canonical JSON object — never a delimiter join (BC3)."""
    return _sha256_text(_canon({"v": _ENTITY_TOKEN_VERSION, "kind": kind, **fields}))

def _lineage_ids(*, run_id: str, media_sha256: str, start, end, segments) -> dict:
    src_tok = _entity_token("src", {"run_id": run_id, "media_sha256": media_sha256})
    source_id = child_id("src", run_id, src_tok)
    mom_tok = _entity_token("moment", {"source_id": source_id, "start": _canon_time(start),
                                       "end": (_canon_time(end) if end is not None else None),
                                       "segments": _norm_segments(segments)})
    moment_id = child_id("moment", source_id, mom_tok)
    clip_tok = _entity_token("clip", {"moment_id": moment_id, "aspect": _TARGET_ASPECT.value})
    clip_id = child_id("clip", moment_id, clip_tok)
    batch_tok = _entity_token("batch", {"run_id": run_id})
    batch_id = child_id("batch", run_id, batch_tok)
    return {"source_id": source_id, "moment_id": moment_id, "clip_id": clip_id, "batch_id": batch_id}


# ---------- filesystem ownership (BC4/Phase 5) ----------

def _canary_root(cfg: Config) -> Path:
    return Path(cfg.base) / "canary"

def _run_dir(cfg: Config, run_id: str) -> Path:
    if not _RUN_ID_RE.match(run_id):
        raise ValueError(f"invalid canary run id shape: {run_id!r}")
    return _canary_root(cfg) / run_id           # basename is ALWAYS the generated hex, never user input

def _assert_contained(root: Path, target: Path) -> Path:
    """Prove `target` resolves to a STRICT descendant of `root` (symlink/traversal safe). Returns realpath."""
    root_r = Path(os.path.realpath(root))
    tgt_r = Path(os.path.realpath(target))
    if tgt_r == root_r or root_r not in tgt_r.parents:
        raise ValueError(f"path escapes canary root: {target}")
    return tgt_r

def _media_ext(media_path: str) -> str:
    ext = Path(media_path).suffix.lower()
    return ext if ext in _MEDIA_EXTS else ".mp4"


# ---------- account contract (Phase 4) ----------

def _validate_canary_account(cfg: Config, handle: str, led: Ledger, ids: dict) -> tuple[Optional[str], Optional[str]]:
    """Return (integration_id, None) when the reserved account passes every precondition, else (None, error)."""
    if handle != CANARY_HANDLE:
        return None, f"canary tooling accepts only the reserved local handle {CANARY_HANDLE!r}, not {handle!r}"
    accts = Accounts.load(cfg)
    acct = next((a for a in accts.accounts if a.handle == CANARY_HANDLE), None)
    if acct is None:
        return None, f"no local account {CANARY_HANDLE!r} — provision it (planned) before preparing a canary"
    if acct.status is not AccountStatus.planned:
        return None, f"{CANARY_HANDLE} must be status=planned (is {acct.status.value})"
    if list(acct.platforms) != [_TARGET_PLATFORM]:
        return None, f"{CANARY_HANDLE} platforms must be exactly ['tiktok'] (is {[p.value for p in acct.platforms]})"
    integ = (acct.integrations or {}).get("tiktok")
    if not integ:
        return None, f"{CANARY_HANDLE} has no integrations.tiktok"
    if (acct.backends or {}).get("tiktok") != "zernio":
        return None, f"{CANARY_HANDLE} backends.tiktok must be 'zernio' (is {(acct.backends or {}).get('tiktok')!r})"
    # integration id unique across every OTHER (account, platform) integration + account_id
    for a in accts.accounts:
        if a.handle == CANARY_HANDLE: continue
        for v in list((a.integrations or {}).values()) + [a.account_id]:
            if v and str(v) == str(integ):
                return None, f"integration id {integ} is not unique — also used by {a.handle}"
    pid = (acct.persona_id or "").strip()
    if not pid:
        return None, f"{CANARY_HANDLE} has no persona_id — link a dedicated canary Persona"
    try:
        from fanops.personas import Personas
        reg = Personas.load(cfg)
        if reg.get(pid) is None:
            return None, f"{CANARY_HANDLE} persona_id {pid!r} does not resolve to a Persona"
    except Exception as exc:
        get_logger(cfg)("canary", handle, "persona_registry_error", level="error", err=str(exc)[:120])
        return None, f"persona registry error: {str(exc)[:120]}"
    if sum(1 for a in accts.accounts if (a.persona_id or "").strip() == pid) > 1:
        return None, f"canary Persona {pid!r} is shared with another account — it must be dedicated"
    # no existing Post targets the handle or integration id
    for p in led.posts.values():
        if p.account == CANARY_HANDLE or (p.account_id and str(p.account_id) == str(integ)):
            return None, f"an existing Post ({p.id}) already targets the canary handle/integration"
    # no FOREIGN, LIVE Moment affinity / Batch target uses the handle (outside this run's own entities). A
    # retired Moment / closed Batch from a DISCARDED prior canary run is inert and must NOT block a new run.
    for m in led.moments.values():
        if (CANARY_HANDLE in (m.affinities or []) and m.id != ids["moment_id"]
                and m.state is not MomentState.retired):
            return None, f"foreign live Moment {m.id} already casts {CANARY_HANDLE}"
    for b in led.batches.values():
        if (CANARY_HANDLE in (b.target_accounts or []) and b.id != ids["batch_id"]
                and b.state is not BatchState.closed):
            return None, f"foreign open Batch {b.id} already targets {CANARY_HANDLE}"
    return str(integ), None


# ---------- prepare ----------

def prepare_canary_lineage(cfg: Config, *, media_path: str, handle: str = CANARY_HANDLE,
                           run_label: Optional[str] = None, start: str, end: Optional[str] = None,
                           segments: Optional[list] = None, caption: str,
                           hashtags=(), hook: Optional[str] = None, plan_only: bool = False) -> ActionResult:
    # ---- 1. argument validation (before any mutation) ----
    if run_label is not None and not _RUN_LABEL_RE.match(str(run_label)):
        return _err(f"invalid --run-label {run_label!r} (must match {_RUN_LABEL_RE.pattern})")
    if segments and end is not None:
        return _err("pass EITHER --end OR --segments, not both")
    try:
        start_f = _canon_time(start)
        end_f = _canon_time(end) if end is not None else None
        segs = _norm_segments(segments)
    except (TypeError, ValueError) as exc:
        return _err(f"bad time value: {str(exc)[:120]}")
    if segs is None and end_f is None:
        return _err("a single-window canary needs --end (or use --segments)")
    if segs is not None:
        if any(e <= s for s, e in segs):
            return _err("every segment must have end > start")
        realized = sum(e - s for s, e in segs)
    else:
        if end_f <= start_f:
            return _err("--end must be greater than --start")
        realized = end_f - start_f
    cap = PLATFORM_MAX_SECONDS.get(_TARGET_PLATFORM)
    if cap is not None and realized > cap:
        return _err(f"clip duration {realized:.1f}s exceeds tiktok cap {cap}s")
    mp = Path(media_path)
    if not mp.is_file():
        return _err(f"media not found: {media_path}")

    # ---- 2. media identity (full sha256) + probe, BEFORE any persistent mutation ----
    try:
        media_sha256 = _sha256_bytes_of(mp)
        src_w, src_h, src_dur = _do_probe(mp)
    except Exception as exc:
        get_logger(cfg)("canary", Path(media_path).name, "media_inspection_failed", level="error", err=str(exc)[:140])
        return _err(f"media inspection failed: {str(exc)[:140]}")
    if not src_w or not src_h:
        return _err("could not probe media dimensions")

    # ---- 3. identity + lineage ids ----
    canonical_name = _canonical_run_name(media_sha256=media_sha256, start=start_f, end=end_f,
                                          segments=segs, caption=caption, hashtags=hashtags,
                                          hook=hook, run_label=run_label)
    run_id = _run_id_from_name(canonical_name)
    ids = _lineage_ids(run_id=run_id, media_sha256=media_sha256, start=start_f, end=end_f, segments=segs)
    fingerprint = _sha256_text(canonical_name)
    run_dir = _run_dir(cfg, run_id)

    # ---- 4. account contract (read-only) ----
    led0 = Ledger.load(cfg)
    integ, err = _validate_canary_account(cfg, handle, led0, ids)
    if err is not None:
        return _err(err)

    plan = {"run_id": run_id, "fingerprint": fingerprint, "integration_id": integ,
            "run_dir": str(run_dir), "media_sha256": media_sha256, "realized_seconds": round(realized, 2),
            **ids, "states": {"source": "moments_decided", "moment": "clipped", "clip": "queued",
                              "batch": "open", "posts": 0, "renders": 0}}

    # ---- 5. ledger-state gate: idempotent no-op / terminal-discarded / mismatch ----
    existing = {k: led0.sources.get(ids["source_id"]) if k == "source" else
                   led0.moments.get(ids["moment_id"]) if k == "moment" else
                   led0.clips.get(ids["clip_id"]) if k == "clip" else
                   led0.batches.get(ids["batch_id"])
                for k in ("source", "moment", "clip", "batch")}
    any_exist = any(v is not None for v in existing.values())
    if any_exist:
        s, m, c, b = existing["source"], existing["moment"], existing["clip"], existing["batch"]
        terminal = ((s is not None and s.state is SourceState.retired) or
                    (m is not None and m.state is MomentState.retired) or
                    (c is not None and c.state is ClipState.retired) or
                    (b is not None and b.state is BatchState.closed))
        if terminal:
            return _err(f"canary run {run_id} is TERMINAL (discarded) — prepare a new run with a changed input/label")
        # intact lineage with identical inputs -> idempotent no-op
        return _ok({**plan, "idempotent": True, "created": False})

    if plan_only:
        return _ok({**plan, "plan_only": True, "created": False})

    # ---- 6. run dir + fingerprint record + verified media copy (owned, atomic) ----
    root = _canary_root(cfg)
    root.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    _assert_contained(root, run_dir)
    run_json = run_dir / "canary-run.json"
    if run_json.exists():                         # orphan/tamper guard: a pre-existing dir must match our fingerprint
        try:
            prior = json.loads(run_json.read_text())
        except (OSError, ValueError):
            prior = {}
        if prior.get("fingerprint") not in (None, fingerprint):
            return _err(f"run dir {run_id} holds a MISMATCHED fingerprint — refusing (stale/tampered orphan)")
    media_dst = _assert_contained(root, run_dir / f"media{_media_ext(media_path)}")
    if not (media_dst.exists() and _sha256_bytes_of(media_dst) == media_sha256):
        tmp = media_dst.with_suffix(media_dst.suffix + ".part")
        shutil.copyfile(mp, tmp)
        if _sha256_bytes_of(tmp) != media_sha256:
            tmp.unlink(missing_ok=True)
            return _err("media copy hash mismatch — aborted before render")
        os.replace(tmp, media_dst)

    # ---- 7. render (LEDGER-FREE, outside the lock); reuse a validated crash-orphan ----
    clip_dst = _assert_contained(root, run_dir / "clip.mp4")
    if not (clip_dst.exists() and (_probe_ok(cfg, clip_dst))):
        try:
            if segs is not None:
                r = _do_render_supercut(str(media_dst), str(clip_dst), [tuple(x) for x in segs],
                                        _TARGET_ASPECT.value, src_w=src_w, src_h=src_h)
            else:
                r = _do_render_single(str(media_dst), str(clip_dst), start_f, end_f, _TARGET_ASPECT.value,
                                      src_w=src_w, src_h=src_h)
        except Exception as exc:
            get_logger(cfg)("canary", run_id, "render_failed", level="error", err=str(exc)[:140])
            return _err(f"render failed (no ledger adoption): {str(exc)[:140]}")
        if not (clip_dst.exists() and clip_dst.stat().st_size > 0):
            rc = getattr(r, "returncode", "n/a")
            return _err(f"render produced no output (rc={rc}) — no ledger adoption")
    if not _probe_ok(cfg, clip_dst):
        return _err("rendered clip failed validation — no ledger adoption")

    _write_json_atomic(run_json, {"run_id": run_id, "canonical_name": canonical_name,
                                  "fingerprint": fingerprint, "media_sha256": media_sha256, **ids})

    # ---- 8. adopt the WHOLE lineage in ONE short transaction (add_* is setdefault = first-write-wins) ----
    now_iso = _now_iso()
    with Ledger.transaction(cfg) as led:
        # terminal re-check under the lock (a concurrent discard could have raced)
        s = led.sources.get(ids["source_id"])
        if s is not None and s.state is SourceState.retired:
            return _err(f"canary run {run_id} became TERMINAL under lock — refusing")
        led.add_batch(Batch(id=ids["batch_id"], name=(run_label or f"canary {run_id}"),
                            target_accounts=[CANARY_HANDLE], state=BatchState.open, created_at=now_iso))
        led.add_source(Source(id=ids["source_id"], state=SourceState.moments_decided, source_path=str(media_dst),
                             sha256=media_sha256, duration=src_dur, width=src_w, height=src_h,
                             batch_id=ids["batch_id"], created_at=now_iso, title=(run_label or "canary")))
        led.add_moment(Moment(id=ids["moment_id"], parent_id=ids["source_id"], state=MomentState.clipped,
                             start=start_f, end=(end_f if end_f is not None else (segs[-1][1] if segs else start_f)),
                             reason="canary publish-path probe", affinities=[CANARY_HANDLE],
                             segments=[tuple(x) for x in (segs or [])], content_token=fingerprint))
        led.add_clip(Clip(id=ids["clip_id"], parent_id=ids["moment_id"], state=ClipState.queued,
                         path=str(clip_dst), aspect=_TARGET_ASPECT,
                         meta_captions={f"{CANARY_HANDLE}/tiktok": {"caption": caption,
                                        "hashtags": _norm_hashtags(hashtags)}}))
    return _ok({**plan, "created": True, "idempotent": False})


def _probe_ok(cfg: Config, path: Path) -> bool:
    try:
        _, _, dur = _do_probe(path)
        return bool(dur and dur > 0) or (path.exists() and path.stat().st_size > 0)
    except Exception as exc:
        get_logger(cfg)("canary", "probe", "probe_fallback", err=str(exc)[:120])
        return path.exists() and path.stat().st_size > 0


# ---------- discard (pre-mint only) ----------

def discard_canary(cfg: Config, run_id: str) -> ActionResult:
    if not _RUN_ID_RE.match(run_id or ""):
        return _err(f"invalid canary run id: {run_id!r}")
    run_dir = _run_dir(cfg, run_id)
    run_json = run_dir / "canary-run.json"
    if not run_json.exists():
        return _err(f"no canary run record for {run_id}")
    try:
        rec = json.loads(run_json.read_text())
    except (OSError, ValueError) as exc:
        return _err(f"unreadable run record: {str(exc)[:120]}")
    sid, mid, cid, bid = rec.get("source_id"), rec.get("moment_id"), rec.get("clip_id"), rec.get("batch_id")
    accts = Accounts.load(cfg)
    acct = next((a for a in accts.accounts if a.handle == CANARY_HANDLE), None)
    if acct is None or acct.status is not AccountStatus.planned:
        return _err(f"{CANARY_HANDLE} must be planned to discard (is {acct.status.value if acct else 'absent'})")
    led0 = Ledger.load(cfg)
    # pre-mint proof: NO Post references the canary Clip or the canary Batch
    for p in led0.posts.values():
        if p.parent_id == cid or (p.batch_id and p.batch_id == bid) or p.account == CANARY_HANDLE:
            return _err(f"a Post ({p.id}) exists for this run — discard is pre-mint only (use `canary cancel`)")
    if _audit_has_mint_evidence(cfg, bid=bid, cid=cid, run_id=run_id):
        return _err(f"audit log shows mint/approve/publish/cancel evidence for {run_id} — refusing discard")
    before = _map_digests(cfg)
    with Ledger.transaction(cfg) as led:
        # RETIRE each entity in place (NOT `retire_source`, which reconcile_moments(sid, {}) CASCADE-DELETES
        # the unprotected canary moment/clip — there is no protecting Post). set_*_state is a plain, no-cascade
        # state flip, so the retained lineage survives + is inert (retired ∉ _seed_clips / publish / requeue).
        if sid in led.sources: led.set_source_state(sid, SourceState.retired)
        if mid in led.moments: led.set_moment_state(mid, MomentState.retired)
        if cid in led.clips: led.retire_clip(cid)
        if bid in led.batches:
            led.batches[bid] = led.batches[bid].model_copy(update={"state": BatchState.closed})
    removed = _remove_run_dir(cfg, run_id)
    after = _map_digests(cfg)
    changed = {k: [before.get(k), after.get(k)] for k in (set(before) | set(after)) if before.get(k) != after.get(k)}
    return _ok({"run_id": run_id, "retired": {"source": sid, "moment": mid, "clip": cid},
                                 "batch_closed": bid, "files_removed": removed,
                                 "map_digests_changed": changed, "terminal": True})


def _remove_run_dir(cfg: Config, run_id: str) -> int:
    root = _canary_root(cfg)
    run_dir = _run_dir(cfg, run_id)
    if not run_dir.exists():
        return 0
    try:
        contained = _assert_contained(root, run_dir)
    except ValueError:
        return 0                                  # refuse to delete anything outside the owned root
    n = sum(1 for _ in contained.rglob("*") if _.is_file())
    shutil.rmtree(contained)
    return n


# ---------- cancel an awaiting/queued canary Post (before possible network acceptance) ----------

def cancel_canary_post(cfg: Config, post_id: str, *, reason: str) -> ActionResult:
    led0 = Ledger.load(cfg)
    post = led0.posts.get(post_id)
    if post is None:
        return _err(f"no such post: {post_id}")
    if post.account != CANARY_HANDLE:
        return _err(f"{post_id} is not a {CANARY_HANDLE} post (account={post.account})")
    batch = led0.get_batch(post.batch_id) if post.batch_id else None
    if batch is None or list(batch.target_accounts or []) != [CANARY_HANDLE]:
        return _err(f"{post_id} does not map to a canary batch")
    if post.state not in (PostState.awaiting_approval, PostState.queued):
        return _err(f"cancel refuses state={post.state.value} — only awaiting_approval/queued (before network)")
    if is_real_submission_id(post.submission_id):
        return _err(f"cancel refuses a real submission_id ({post.submission_id}) — possible network acceptance")
    if post.reconcile_candidate_id is not None:
        return _err("cancel refuses a non-null reconcile_candidate_id — possible provider duplicate")
    if (post.public_url or "").strip() or (getattr(post, "published_at", None) or ""):
        return _err("cancel refuses a post carrying a public_url/published_at")
    bounded = (_CANARY_REASON_PREFIX + redact(reason or "", limit=_REASON_MAX))[:_REASON_MAX + len(_CANARY_REASON_PREFIX)]
    with Ledger.transaction(cfg) as led:
        cur = led.posts.get(post_id)
        if cur is None or cur.state not in (PostState.awaiting_approval, PostState.queued):
            return _err("post state changed under lock — refusing")
        if is_real_submission_id(cur.submission_id) or cur.reconcile_candidate_id is not None:
            return _err("post gained provider identity under lock — refusing")
        led.posts[post_id] = cur.model_copy(update={"state": PostState.retired, "error_reason": bounded})
    warn = None
    try:
        write_audit(cfg, "canary_cancel", [post_id], reason="canary_cancel", canary_reason=bounded)
    except Exception as exc:
        get_logger(cfg)("canary", post_id, "audit_write_failed", level="error", err=str(exc)[:120])
        warn = f"audit write failed (post is safely retired): {str(exc)[:120]}"
    return _ok({"post_id": post_id, "state": "retired", "reason": bounded, "audit_warning": warn})


# ---------- read-only multilayer baseline capture + compare ----------

_SAFE_FIELDS = ["state", "account", "account_id", "platform", "parent_id", "submission_id",
                "reconcile_candidate_id", "public_url", "media_urls", "error_reason", "created_at", "published_at"]

def _read_posts_ro(cfg: Config) -> list:
    if not Path(cfg.ledger_path).exists():
        return [], None
    con = sqlite3.connect(f"file:{cfg.ledger_path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT row_id,payload FROM ledger_rows WHERE map_name='posts' ORDER BY row_id").fetchall()
        sv = con.execute("SELECT value FROM ledger_meta WHERE key='schema_version'").fetchone()
    finally:
        con.close()
    return rows, (sv[0] if sv else None)

def _sep_digest(pairs) -> str:
    h = hashlib.sha256()
    for rid, blob in pairs:
        h.update(rid.encode()); h.update(b"\x00"); h.update(blob.encode()); h.update(b"\x1e")
    return h.hexdigest()

def _post_layers(rid: str, d: dict) -> dict:
    safe = _canon({k: d.get(k) for k in _SAFE_FIELDS})
    sched = _canon({"scheduled_time": d.get("scheduled_time"), "approval": d.get("state")})
    content = _canon({"caption_sha256": _sha256_text(d.get("caption") or ""),
                      "hashtags_sha256": _sha256_text(_canon(d.get("hashtags") or [])),
                      "parent_id": d.get("parent_id"), "aspect": d.get("aspect"),
                      "media_urls": d.get("media_urls"), "media_id": d.get("media_id")})
    return {"safe": safe, "sched": sched, "content": content}

def _build_manifest(cfg: Config) -> dict:
    rows, sv = _read_posts_ro(cfg)
    parsed = [(rid, blob, json.loads(blob)) for rid, blob in rows]
    manifest = {rid: hashlib.sha256(blob.encode()).hexdigest() for rid, blob, _ in parsed}
    layers = {rid: _post_layers(rid, d) for rid, _, d in parsed}
    dist = {}
    for _, _, d in parsed:
        dist[str(d.get("state"))] = dist.get(str(d.get("state")), 0) + 1
    incident = {}
    for i in ("post_04b29c9f7f2d", "post_07e45c69ac0d", "post_0943840705ce", "post_0a12cff53619"):
        for rid, blob, d in parsed:
            if rid == i:
                incident[i] = {"raw_sha256": manifest[i], "state": d.get("state"),
                               "submission_id": d.get("submission_id"),
                               "reconcile_candidate_id": d.get("reconcile_candidate_id"),
                               "public_url": d.get("public_url")}
    return {
        "format_version": BASELINE_FORMAT_VERSION,
        "status": "candidate",                    # ALWAYS candidate — capture never self-accepts (BC5)
        "canonicalization": {"json": "sort_keys=True,ensure_ascii=False,separators=(',',':')",
                             "row_order": "ORDER BY row_id",
                             "aggregate": "sha256 of concat(row_id + 0x00 + blob + 0x1e)", "hash": "sha256"},
        "schema_version": sv, "repo_commit": _repo_commit(), "post_count": len(parsed),
        "state_distribution": dist,
        "digests": {
            "raw_posts": _sep_digest([(rid, blob) for rid, blob, _ in parsed]),
            "safety_critical": _sep_digest([(rid, layers[rid]["safe"]) for rid, _, _ in parsed]),
            "scheduling": _sep_digest([(rid, layers[rid]["sched"]) for rid, _, _ in parsed]),
            "content": _sep_digest([(rid, layers[rid]["content"]) for rid, _, _ in parsed]),
        },
        "per_post_manifest": manifest,
        "per_post_layers": layers,                # hash-only (captions redacted through sha256)
        "frozen_incident": incident,
    }

def capture_canary_baseline(cfg: Config, *, output: str) -> ActionResult:
    try:
        manifest = _build_manifest(cfg)
    except Exception as exc:
        get_logger(cfg)("canary", "baseline", "capture_failed", level="error", err=str(exc)[:140])
        return _err(f"baseline capture failed: {str(exc)[:140]}")
    out = Path(output).expanduser()
    _write_json_atomic(out, manifest)
    return _ok({"output": str(out), "status": "candidate",
                                 "raw_posts": manifest["digests"]["raw_posts"],
                                 "post_count": manifest["post_count"]})

def compare_canary_baseline(cfg: Config, *, baseline: str) -> ActionResult:
    try:
        prior = json.loads(Path(baseline).expanduser().read_text())
    except (OSError, ValueError) as exc:
        return _err(f"cannot read baseline: {str(exc)[:120]}")
    if prior.get("format_version") != BASELINE_FORMAT_VERSION:
        return _err(f"baseline format_version {prior.get('format_version')!r} != {BASELINE_FORMAT_VERSION!r}")
    try:
        cur = _build_manifest(cfg)
    except Exception as exc:
        get_logger(cfg)("canary", "baseline", "compare_manifest_failed", level="error", err=str(exc)[:140])
        return _err(f"current manifest failed: {str(exc)[:140]}")
    p_man, c_man = prior.get("per_post_manifest", {}), cur["per_post_manifest"]
    p_lay, c_lay = prior.get("per_post_layers", {}), cur["per_post_layers"]
    added = sorted(set(c_man) - set(p_man))
    removed = sorted(set(p_man) - set(c_man))
    both = set(p_man) & set(c_man)
    raw_changed = sorted(r for r in both if p_man[r] != c_man[r])
    def _layer_changed(key):
        return sorted(r for r in both if r in p_lay and r in c_lay and p_lay[r].get(key) != c_lay[r].get(key))
    safety_changed = _layer_changed("safe")
    sched_changed = _layer_changed("sched")
    content_changed = _layer_changed("content")
    safety_fields = {}
    for r in safety_changed:
        pf, cf = json.loads(p_lay[r]["safe"]), json.loads(c_lay[r]["safe"])
        safety_fields[r] = [k for k in _SAFE_FIELDS if pf.get(k) != cf.get(k)]
    mismatch = bool(added or removed or raw_changed)
    return _ok({
        "mismatch": mismatch, "added": added, "removed": removed, "raw_changed": raw_changed,
        "safety_critical_changed": safety_fields, "scheduling_changed": sched_changed,
        "content_changed": content_changed,
        "digests_equal": {k: prior.get("digests", {}).get(k) == cur["digests"][k] for k in cur["digests"]},
    })


# ---------- small local helpers ----------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _write_json_atomic(p: Path, obj) -> None:
    from fanops.controlio import write_json_atomic
    write_json_atomic(p, obj)

def _repo_commit() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                           cwd=str(Path(__file__).resolve().parent), timeout=5)
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    except (subprocess.SubprocessError, OSError):
        return "unknown"

def _map_digests(cfg: Config) -> dict:
    if not Path(cfg.ledger_path).exists():
        return {}
    con = sqlite3.connect(f"file:{cfg.ledger_path}?mode=ro", uri=True)
    try:
        out = {}
        for (m,) in con.execute("SELECT DISTINCT map_name FROM ledger_rows").fetchall():
            rows = con.execute("SELECT row_id,payload FROM ledger_rows WHERE map_name=? ORDER BY row_id", (m,)).fetchall()
            out[m] = _sep_digest([(rid, blob) for rid, blob in rows])
    finally:
        con.close()
    return out

def _audit_has_mint_evidence(cfg: Config, *, bid: str, cid: str, run_id: str) -> bool:
    path = cfg.control / "studio_audit.log"
    if not path.exists():
        return False
    try:
        text = path.read_text()
    except OSError:
        return False
    return any(tok and tok in text for tok in (bid, cid, run_id))
