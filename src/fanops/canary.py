"""Canary publish-path tooling — an ISOLATED single-lineage probe, decoupled from the pipeline.

Five operator verbs (`fanops canary …`): `prepare` mints exactly one Source+Moment+Clip+Batch (ZERO
Renders, ZERO Posts) for the reserved local account `fanops_canary`; `discard` retires that lineage
pre-mint; `cancel` retires an awaiting/queued canary Post before any possible network acceptance;
`baseline`/`compare` capture and diff a read-only, NON-DISCLOSIVE multilayer ledger manifest.

INVARIANTS (each has a test): this module NEVER calls advance / crosspost_clips / crosspost_to_account /
publish_due / publish_post / reconcile_due / Zernio / Postiz / HTTP / an LLM / an agent gate. Every id is
content-addressed (idempotent). Every filesystem path is realpath-contained to the run-owned directory.

ONE VALIDATOR, ONE WINDOW RULE:
- `_validate_expected_lineage` is the SINGLE complete expected-projection validator. All four consumers use
  it: the initial idempotent path, the under-lock concurrent-lineage path, discard authentication, and
  cancellation run authentication. It validates the whole lineage — ids, states, ownership, parent-links,
  affinities, hook, caption + hashtags, times/segments, source bytes + probed geometry, clip artifact +
  bytes, batch identity, and the run record — and returns FIELD-SPECIFIC refusals.
- `_normalized_window` is the SINGLE structural time/segment rule. Both `prepare` (raw operator input) and
  `_parse_canonical_name` (run-record authentication) call it, so a canonical identity that prepare would
  refuse structurally cannot authenticate either.

Identity is the only trusted record field: `discard`/`cancel` recompute all four entity ids FROM the
self-verifying `canonical_name` and require the record's stored ids to equal the recomputation, so a mutable
record can never point retirement at a foreign lineage. Every ledger-dependent discard precondition is
re-checked INSIDE the retirement transaction. Rendering is atomic (unique owned temp → strict probe →
os.replace) and fails CLOSED. Baselines are non-disclosive (per-field hashes / categorical projections only)
and strictly shape-validated against a pinned contract before comparison. Rendering is ledger-free and runs
OUTSIDE the ledger lock; adoption is one short transaction. A discarded run is terminal; a minted canary run
is ONE-SHOT. Baseline capture is always `candidate` — it never self-accepts.
"""
from __future__ import annotations
import json, os, shutil, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:                                   # type-only: no runtime/compile edge to S16 (studio)
    from fanops.studio.actions_common import ActionResult

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.errors import redact
from fanops.models import (Source, Moment, Clip, Batch, SourceState, MomentState, ClipState, BatchState,
                           PostState, is_real_submission_id)
from fanops.accounts import Accounts, AccountStatus
from fanops.audit import write_audit
from fanops.log import get_logger
from fanops.canary_identity import (
    CANARY_HANDLE,
    _RUN_ID_RE,
    _RUN_LABEL_RE,
    _TARGET_ASPECT,
    _TARGET_PLATFORM,
    _TARGET_SURFACE,
    _assert_contained,
    _canary_root,
    _canonical_run_name,
    _expected_batch_name,
    _expected_moment_window,
    _finite,
    _identity_dict,
    _is_hex64,
    _lineage_ids,
    _media_ext,
    _new_render_tmp,
    _norm_hashtags,
    _normalized_window,
    _path_contained,
    _realized_seconds,
    _recompute_identity_from_record,
    _run_dir,
    _run_id_from_name,
    _sha256_bytes_of,
    _sha256_text,
    _sweep_render_temps,
)
from fanops import canary_identity as _canary_identity
from fanops import canary_baseline as _canary_baseline
from fanops.canary_baseline import _sep_digest

# Public re-exports (tests import via fanops.canary)
CANARY_RUN_ID_VERSION = _canary_identity.CANARY_RUN_ID_VERSION
CANARY_RUN_NAMESPACE = _canary_identity.CANARY_RUN_NAMESPACE
_HEX64_RE = _canary_identity._HEX64_RE
_parse_canonical_name = _canary_identity._parse_canonical_name
BASELINE_FORMAT_VERSION = _canary_baseline.BASELINE_FORMAT_VERSION
capture_canary_baseline = _canary_baseline.capture_canary_baseline
compare_canary_baseline = _canary_baseline.compare_canary_baseline


# ActionResult lives under S16 (studio); import it LAZILY so canary carries no compile-time studio edge
# (it belongs to S17_cli_daemon, which lazy-depends on studio). `from __future__ import annotations` keeps
# the `-> ActionResult` return hints as un-evaluated strings, so no module-level import is needed.
def _ok(detail=None):
    from fanops.studio.actions_common import ActionResult
    return ActionResult.success(detail)

def _err(msg):
    from fanops.studio.actions_common import ActionResult
    return ActionResult.failure(msg)


class _Refuse(Exception):
    """Raised INSIDE a Ledger.transaction to abort with a refusal WITHOUT persisting any partial state.
    Ledger.transaction saves only on a clean exit, so an uncaught raise rolls back to the prior snapshot —
    this is how every under-lock precondition refuses without touching the lineage."""
    def __init__(self, msg: str):
        super().__init__(msg); self.msg = msg


# Ledger-FREE render + probe primitives, wrapped so the heavy `clip`/`ingest` imports stay LAZY (no
# import-time cycle) yet remain monkeypatchable in tests (patch `fanops.canary._do_render_single`, etc.).
def _do_probe(path: Path):
    from fanops.media_probe import probe_dimensions
    return probe_dimensions(path)

def _do_render_single(src: str, dst: str, cs: float, ce: float, aspect_value: str, *, src_w: int, src_h: int):
    from fanops.clip import render_reframed
    return render_reframed(src, dst, cs, ce, aspect_value, src_w=src_w, src_h=src_h)

def _do_render_supercut(src: str, dst: str, spans: list, aspect_value: str, *, src_w: int, src_h: int):
    from fanops.clip import render_supercut_reframed
    return render_supercut_reframed(src, dst, spans, aspect_value, src_w=src_w, src_h=src_h)

_REASON_MAX = 180
_CANARY_REASON_PREFIX = "canary_cancelled: "
_SOURCE_DUR_TOL = 0.5                            # a clip window may exceed the probed source by at most this
_PROBE_DUR_ABS_TOL = 1.5                         # rendered-clip duration tolerance vs the requested window:
_PROBE_DUR_REL_TOL = 0.25                        #   max(abs, rel*expected) — container/keyframe padding drift
_SOURCE_PROBE_DUR_TOL = 1.0                      # stored Source.duration vs a fresh probe of the owned media

# validation modes for the single expected-lineage validator
_MODE_LIVE = "live"                              # the lineage must be in its exact minted pre-mint states
_MODE_DISCARD = "discard"                        # ...or already in its permitted terminal state (re-discard)


# ---------- strict media probe ----------

def _strict_probe(cfg: Config, path: Path, *, expect_seconds: Optional[float] = None) -> tuple[bool, Optional[str]]:
    """Fail-CLOSED strict validation of a media artifact: it must exist, be non-empty, and probe to positive
    finite dimensions AND a finite positive duration; when a window is known, the playable duration must fall
    within a documented tolerance. A probe error / zero / non-finite duration / non-positive dims / truncation
    is a REJECTION — an unprobe-able artifact is never treated as valid (no fail-open nonempty fallback)."""
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return False, "artifact is missing or empty"
        w, h, dur = _do_probe(path)
    except Exception as exc:
        get_logger(cfg)("canary", path.name, "strict_probe_error_reject", level="warning", err=str(exc)[:120])
        return False, f"probe error: {str(exc)[:80]}"
    if not _finite(w) or not _finite(h) or w <= 0 or h <= 0:
        return False, f"non-positive / non-finite dimensions ({w!r}x{h!r})"
    if dur is None or not _finite(dur) or dur <= 0:
        return False, f"non-finite / zero duration ({dur!r})"
    if expect_seconds is not None and expect_seconds > 0:
        tol = max(_PROBE_DUR_ABS_TOL, expect_seconds * _PROBE_DUR_REL_TOL)
        if abs(dur - expect_seconds) > tol:
            return False, f"duration {dur:.2f}s not within {tol:.2f}s of expected {expect_seconds:.2f}s"
    return True, None


# ---------- the SINGLE complete expected-lineage validator ----------

def _validate_expected_lineage(cfg: Config, led: Ledger, identity: dict, run_dir: Path, *,
                               mode: str, rec: Optional[dict] = None) -> list:
    """THE complete expected-projection validator — the one used by the initial idempotent path, the
    under-lock concurrent-lineage path, discard authentication, and cancellation run authentication.

    Validates the WHOLE lineage against the identity recomputed from `canonical_name`: ids, states,
    ownership, parent-links, affinities, hook, caption + normalized hashtags, times/segments, source bytes +
    probed geometry, clip artifact + bytes, batch identity, and the run record. Never trusts a canary-shaped
    parent chain — every field is compared to the recomputation. `mode=_MODE_DISCARD` additionally permits an
    already-terminal state (so a crash-partial re-discard converges); `_MODE_LIVE` requires the exact minted
    pre-mint states. Returns FIELD-SPECIFIC error strings (empty list == valid)."""
    errs: list = []
    terminal_ok = (mode == _MODE_DISCARD)
    sid, mid, cid, bid = identity["source_id"], identity["moment_id"], identity["clip_id"], identity["batch_id"]
    src, mom, clp, bat = led.sources.get(sid), led.moments.get(mid), led.clips.get(cid), led.batches.get(bid)

    def _state_ok(actual, prepared, terminal):
        return actual is prepared or (terminal_ok and actual is terminal)

    for label, ident_key, row in (("source", "source_id", src), ("moment", "moment_id", mom),
                                  ("clip", "clip_id", clp), ("batch", "batch_id", bat)):
        if row is None:
            errs.append(f"{label} {identity[ident_key]} missing from ledger")

    # ---- run record (authenticated identity + clip byte-identity + directory binding) ----
    clip_sha_expected = None
    if rec is not None:
        rid_identity, rerr = _recompute_identity_from_record(rec)
        if rerr is not None:
            errs.append(f"run record: {rerr}")
        elif rid_identity["run_id"] != identity["run_id"]:
            errs.append("run record authenticates to a DIFFERENT run_id than the one under validation")
        elif run_dir.name != rid_identity["run_id"]:
            errs.append(f"run record directory {run_dir.name} != authenticated run_id {rid_identity['run_id']}")
        else:
            clip_sha_expected = rec.get("clip_sha256")
            if not _is_hex64(clip_sha_expected):
                errs.append("run record clip_sha256 is missing or not a sha256")

    # ---- Source ----
    if src is not None:
        if not _state_ok(src.state, SourceState.moments_decided, SourceState.retired):
            errs.append(f"source state {src.state.value} != moments_decided")
        if src.batch_id != bid:
            errs.append(f"source.batch_id {src.batch_id!r} != {bid!r}")
        if (src.sha256 or "") != identity["media_sha256"]:
            errs.append("source.sha256 != canonical media_sha256")
        if not _path_contained(src.source_path, run_dir):
            errs.append("source.source_path is not inside the owned run dir")
        else:
            sp = Path(src.source_path)
            if not sp.is_file():
                errs.append("source.source_path does not exist on disk")
            else:
                if _sha256_bytes_of(sp) != identity["media_sha256"]:
                    errs.append("source file bytes do not match the canonical media_sha256")
                if not (_finite(src.width) and _finite(src.height) and (src.width or 0) > 0 and (src.height or 0) > 0):
                    errs.append(f"source width/height are not positive ({src.width!r}x{src.height!r})")
                if not (_finite(src.duration) and (src.duration or 0) > 0):
                    errs.append(f"source.duration is not finite-positive ({src.duration!r})")
                ok, why = _strict_probe(cfg, sp)
                if not ok:
                    errs.append(f"source file failed strict probe ({why})")
                else:
                    pw, ph, pdur = _do_probe(sp)
                    if (src.width, src.height) != (pw, ph):
                        errs.append(f"source dimensions {src.width}x{src.height} != probed {pw}x{ph}")
                    if _finite(src.duration) and _finite(pdur) and abs(float(src.duration) - float(pdur)) > _SOURCE_PROBE_DUR_TOL:
                        errs.append(f"source.duration {src.duration} != probed {pdur} (tol {_SOURCE_PROBE_DUR_TOL}s)")

    # ---- Moment ----
    if mom is not None:
        if not _state_ok(mom.state, MomentState.clipped, MomentState.retired):
            errs.append(f"moment state {mom.state.value} != clipped")
        if mom.parent_id != sid:
            errs.append("moment.parent_id != source_id")
        if list(mom.affinities or []) != [CANARY_HANDLE]:
            errs.append(f"moment.affinities {list(mom.affinities or [])} != [{CANARY_HANDLE}]")
        if (mom.content_token or "") != identity["fingerprint"]:
            errs.append("moment.content_token != fingerprint")
        exp_start, exp_end, exp_segs = _expected_moment_window(identity)
        if mom.start != exp_start:
            errs.append(f"moment.start {mom.start} != {exp_start}")
        if mom.end != exp_end:
            errs.append(f"moment.end {mom.end} != {exp_end}")
        if [list(x) for x in (mom.segments or [])] != exp_segs:
            errs.append("moment.segments != canonical segments")
        # the HOOK is checked by value-hash, not merely by the fingerprint it contributed to: a tampered hook
        # that left content_token untouched must NOT be silently accepted.
        exp_hook_sha = identity["hook_sha256"]
        if exp_hook_sha is None:
            if mom.hook is not None:
                errs.append("moment.hook is set but the canonical identity carries no hook")
        elif mom.hook is None:
            errs.append("moment.hook is absent but the canonical identity carries one")
        elif _sha256_text(mom.hook) != exp_hook_sha:
            errs.append("moment.hook does not match the canonical hook_sha256")

    # ---- Clip ----
    if clp is not None:
        if not _state_ok(clp.state, ClipState.queued, ClipState.retired):
            errs.append(f"clip state {clp.state.value} != queued")
        if clp.parent_id != mid:
            errs.append("clip.parent_id != moment_id")
        if clp.aspect is not _TARGET_ASPECT:
            errs.append(f"clip.aspect {clp.aspect.value} != 9x16")
        if not _path_contained(clp.path, run_dir):
            errs.append("clip.path is not inside the owned run dir")
        else:
            cp = Path(clp.path)
            if not cp.is_file():
                errs.append("clip.path does not exist on disk")
            else:
                ok, why = _strict_probe(cfg, cp, expect_seconds=_realized_seconds(identity))
                if not ok:
                    errs.append(f"clip artifact failed strict validation ({why})")
                if clip_sha_expected is not None and _is_hex64(clip_sha_expected):
                    if _sha256_bytes_of(cp) != clip_sha_expected:
                        errs.append("clip bytes differ from the authenticated run record's clip_sha256")
        # meta_captions must carry EXACTLY the expected canary surface — no unexpected canary surface metadata
        mc = clp.meta_captions or {}
        canary_surfaces = sorted(k for k in mc if str(k).startswith(CANARY_HANDLE + "/"))
        if canary_surfaces != [_TARGET_SURFACE]:
            errs.append(f"clip canary surfaces {canary_surfaces} != ['{_TARGET_SURFACE}']")
        else:
            surf = mc.get(_TARGET_SURFACE) or {}
            if _sha256_text(surf.get("caption") or "") != identity["caption_sha256"]:
                errs.append("clip caption does not match the canonical caption_sha256")
            if list(surf.get("hashtags") or []) != identity["hashtags"]:
                errs.append("clip hashtags do not match the canonical normalized hashtags")

    # ---- Batch ----
    if bat is not None:
        if not _state_ok(bat.state, BatchState.open, BatchState.closed):
            errs.append(f"batch state {bat.state.value} != open")
        if list(bat.target_accounts or []) != [CANARY_HANDLE]:
            errs.append(f"batch.target_accounts {list(bat.target_accounts or [])} != [{CANARY_HANDLE}]")
        if bat.name != _expected_batch_name(identity):
            errs.append(f"batch.name {bat.name!r} != expected {_expected_batch_name(identity)!r}")
    return errs


# ---------- account contract (one-shot) ----------

def _canary_integration_id(cfg: Config) -> tuple[Optional[str], Optional[str]]:
    accts = Accounts.load(cfg)
    acct = next((a for a in accts.accounts if a.handle == CANARY_HANDLE), None)
    if acct is None:
        return None, f"no local account {CANARY_HANDLE!r}"
    integ = (acct.integrations or {}).get("tiktok")
    if not integ:
        return None, f"{CANARY_HANDLE} has no integrations.tiktok"
    return str(integ), None


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
    # ONE-SHOT: the reserved account carries NO history. ANY Post that targets the handle OR the integration
    # id — even a retired/cancelled one — blocks a new run. (Cancel->new-run reuse would change the
    # account-history isolation contract; it is a separate, unbuilt, separately-authorized extension.)
    for p in led.posts.values():
        if p.account == CANARY_HANDLE or (p.account_id and str(p.account_id) == str(integ)):
            return None, (f"an existing Post ({p.id}) already targets the canary handle/integration — a minted "
                          f"canary run is ONE-SHOT; provision a fresh reserved account for another probe")
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
    # ---- 1. argument + structural window validation (the SHARED rule), BEFORE any mutation ----
    if run_label is not None and not _RUN_LABEL_RE.match(str(run_label)):
        return _err(f"invalid --run-label {run_label!r} (must match {_RUN_LABEL_RE.pattern})")
    win, werr = _normalized_window(start, end, segments)
    if werr is not None:
        return _err(werr)
    eff_start, end_f, segs = win["eff_start"], win["end"], win["segments"]
    realized, envelope_end = win["realized"], win["envelope_end"]
    mp = Path(media_path)
    if not mp.is_file():
        return _err(f"media not found: {media_path}")

    # ---- 2. media identity (full sha256) + STRICT source probe, BEFORE any persistent mutation ----
    try:
        media_sha256 = _sha256_bytes_of(mp)
        src_w, src_h, src_dur = _do_probe(mp)
    except Exception as exc:
        get_logger(cfg)("canary", Path(media_path).name, "media_inspection_failed", level="error", err=str(exc)[:140])
        return _err(f"media inspection failed: {str(exc)[:140]}")
    # FAIL CLOSED on the source probe: an unknown/zero/NaN/Infinite duration must NEVER silently skip the
    # source-bound check below (that would let a window run past the end of the media).
    if not (_finite(src_w) and _finite(src_h) and src_w > 0 and src_h > 0):
        return _err(f"source probe returned non-positive/non-finite dimensions ({src_w!r}x{src_h!r})")
    if not (_finite(src_dur) and src_dur > 0):
        return _err(f"source probe returned a non-finite / non-positive duration ({src_dur!r}) — refusing")
    # every boundary (single-window end AND every segment end) must fit inside the probed source
    for lim in ([e for _s, e in segs] if segs is not None else [end_f]):
        if lim > src_dur + _SOURCE_DUR_TOL:
            return _err(f"clip window ends at {lim:.1f}s but the source is only {src_dur:.1f}s")

    # ---- 3. identity + lineage ids ----
    canonical_name = _canonical_run_name(media_sha256=media_sha256, start=eff_start, end=end_f,
                                          segments=segs, caption=caption, hashtags=hashtags,
                                          hook=hook, run_label=run_label)
    run_id = _run_id_from_name(canonical_name)
    ids = _lineage_ids(run_id=run_id, media_sha256=media_sha256, start=eff_start, end=end_f, segments=segs)
    fingerprint = _sha256_text(canonical_name)
    identity = _identity_dict(run_id=run_id, fingerprint=fingerprint, media_sha256=media_sha256,
                              start=eff_start, end=end_f, segments=segs,
                              caption_sha256=_sha256_text(caption), hashtags=hashtags,
                              hook_sha256=(_sha256_text(hook) if hook is not None else None),
                              run_label=run_label, ids=ids)
    run_dir = _run_dir(cfg, run_id)

    # ---- 4. account contract (read-only) ----
    led0 = Ledger.load(cfg)
    integ, err = _validate_canary_account(cfg, handle, led0, ids)
    if err is not None:
        return _err(err)

    plan = {"run_id": run_id, "fingerprint": fingerprint, "integration_id": integ,
            "run_dir": str(run_dir), "media_sha256": media_sha256, "realized_seconds": round(realized, 2),
            "source_duration": src_dur, "envelope_end": envelope_end,
            **ids, "states": {"source": "moments_decided", "moment": "clipped", "clip": "queued",
                              "batch": "open", "posts": 0, "renders": 0}}

    # ---- 5. ledger-state gate: idempotent no-op / terminal-discarded / mismatch ----
    existing = {"source": led0.sources.get(ids["source_id"]), "moment": led0.moments.get(ids["moment_id"]),
                "clip": led0.clips.get(ids["clip_id"]), "batch": led0.batches.get(ids["batch_id"])}
    any_exist = any(v is not None for v in existing.values())
    if any_exist:
        s, m, c, b = existing["source"], existing["moment"], existing["clip"], existing["batch"]
        terminal = ((s is not None and s.state is SourceState.retired) or
                    (m is not None and m.state is MomentState.retired) or
                    (c is not None and c.state is ClipState.retired) or
                    (b is not None and b.state is BatchState.closed))
        if terminal:
            return _err(f"canary run {run_id} is TERMINAL (discarded) — prepare a new run with a changed input/label")
        if not all(v is not None for v in existing.values()):
            present = sorted(k for k, v in existing.items() if v is not None)
            return _err(f"canary run {run_id} has a PARTIAL lineage (only {present} of source/moment/clip/batch) "
                        f"— refusing an idempotent claim; `canary discard` it and re-prepare")
        # FULL non-terminal lineage: require the EXACT expected projection (the SHARED validator) before
        # claiming idempotent. A malformed record is a tamper signal — refuse, never overwrite.
        rec, rec_err = _read_run_record(cfg, run_id)
        perrs = list(_validate_expected_lineage(cfg, led0, identity, run_dir, mode=_MODE_LIVE, rec=rec))
        if rec_err is not None:
            perrs.append(rec_err)
        if perrs:
            return _err(f"canary run {run_id} lineage MISMATCH — refusing idempotent claim (do NOT repair): "
                        f"{'; '.join(perrs[:4])}")
        # clean idempotent match. Recover a crash in the commit->record-write gap (step 9); plan_only stays read-only.
        if not plan_only and rec is None:
            _ensure_run_record(cfg, run_id, canonical_name, fingerprint, media_sha256,
                               _sha256_bytes_of(run_dir / "clip.mp4"), src_w, src_h, src_dur, ids)
        return _ok({**plan, "idempotent": True, "created": False})

    if plan_only:
        return _ok({**plan, "plan_only": True, "created": False})

    # ---- 6. run dir + verified media copy (owned, atomic) ----
    root = _canary_root(cfg)
    root.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    _assert_contained(root, run_dir)
    run_json = run_dir / "canary-run.json"
    if run_json.exists():                         # orphan/tamper guard: a pre-existing dir must match our fingerprint
        try:
            prior = json.loads(run_json.read_text())
        except (OSError, ValueError) as exc:      # an unreadable/malformed orphan is a tamper signal, NOT an empty one
            return _err(f"run dir {run_id} holds an UNREADABLE/MALFORMED record — refusing (tamper): {str(exc)[:80]}")
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

    # ---- 7. render ATOMICALLY (LEDGER-FREE, outside the lock): temp -> strict probe -> os.replace ----
    clip_final = _assert_contained(root, run_dir / "clip.mp4")
    _sweep_render_temps(cfg, run_dir)             # drop crash-orphan temps; never trust one as the final
    reuse_ok, _ = _strict_probe(cfg, clip_final, expect_seconds=realized) if clip_final.exists() else (False, None)
    if not reuse_ok:
        if clip_final.exists():
            clip_final.unlink(missing_ok=True)    # a partial/invalid final is NEVER treated as complete
        tmp = _new_render_tmp(run_dir)
        try:
            if segs is not None:
                r = _do_render_supercut(str(media_dst), str(tmp), [tuple(x) for x in segs],
                                        _TARGET_ASPECT.value, src_w=src_w, src_h=src_h)
            else:
                r = _do_render_single(str(media_dst), str(tmp), eff_start, end_f, _TARGET_ASPECT.value,
                                      src_w=src_w, src_h=src_h)
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            get_logger(cfg)("canary", run_id, "render_failed", level="error", err=str(exc)[:140])
            return _err(f"render failed (no ledger adoption): {str(exc)[:140]}")
        pok, preason = _strict_probe(cfg, tmp, expect_seconds=realized)
        if not pok:
            rc = getattr(r, "returncode", "n/a")
            tmp.unlink(missing_ok=True)
            return _err(f"rendered clip failed validation ({preason}; rc={rc}) — no ledger adoption")
        os.replace(tmp, clip_final)               # atomic promote of the strictly-validated artifact
    pok, preason = _strict_probe(cfg, clip_final, expect_seconds=realized)
    if not pok:
        return _err(f"final clip failed validation ({preason}) — no ledger adoption")
    clip_sha = _sha256_bytes_of(clip_final)

    # ---- 8. adopt the WHOLE lineage in ONE short transaction ----
    now_iso = _now_iso()
    try:
        with Ledger.transaction(cfg) as led:
            s = led.sources.get(ids["source_id"])
            if s is not None and s.state is SourceState.retired:
                raise _Refuse(f"canary run {run_id} became TERMINAL under lock — refusing")
            # RE-VALIDATE the account / one-shot / foreign-affinity gates against the LOCKED ledger: a
            # concurrent run may have minted a canary Post / Moment / Batch while we rendered.
            _i2, aerr2 = _validate_canary_account(cfg, handle, led, ids)
            if aerr2 is not None:
                raise _Refuse(f"account gate failed under lock — refusing (concurrent canary activity): {aerr2}")
            # ALL-OR-NOTHING under the lock: either none of the four rows exists (create all four), or ALL
            # four exist and must authenticate completely. add_* is setdefault, so it must NEVER run over a
            # partial or semantically mismatched lineage.
            present = {k: v for k, v in (("source", led.sources.get(ids["source_id"])),
                                         ("moment", led.moments.get(ids["moment_id"])),
                                         ("clip", led.clips.get(ids["clip_id"])),
                                         ("batch", led.batches.get(ids["batch_id"]))) if v is not None}
            if present and len(present) != 4:
                raise _Refuse(f"a PARTIAL concurrent lineage exists under lock (only {sorted(present)}) — refusing")
            rec_u, rec_u_err = _read_run_record(cfg, run_id)
            if rec_u_err is not None:
                raise _Refuse(f"run record under lock: {rec_u_err}")
            if present:                            # a concurrent run already created the whole lineage
                cerrs = _validate_expected_lineage(cfg, led, identity, run_dir, mode=_MODE_LIVE, rec=rec_u)
                if cerrs:
                    raise _Refuse(f"a concurrent lineage exists under lock but MISMATCHES — refusing: "
                                  f"{'; '.join(cerrs[:3])}")
            else:
                led.add_batch(Batch(id=ids["batch_id"], name=_expected_batch_name(identity),
                                    target_accounts=[CANARY_HANDLE], state=BatchState.open, created_at=now_iso))
                led.add_source(Source(id=ids["source_id"], state=SourceState.moments_decided, source_path=str(media_dst),
                                     sha256=media_sha256, duration=src_dur, width=src_w, height=src_h,
                                     batch_id=ids["batch_id"], created_at=now_iso, title=(run_label or "canary")))
                led.add_moment(Moment(id=ids["moment_id"], parent_id=ids["source_id"], state=MomentState.clipped,
                                     start=eff_start, end=envelope_end,
                                     reason="canary publish-path probe", affinities=[CANARY_HANDLE], hook=hook,
                                     segments=[tuple(x) for x in (segs or [])], content_token=fingerprint))
                led.add_clip(Clip(id=ids["clip_id"], parent_id=ids["moment_id"], state=ClipState.queued,
                                 path=str(clip_final), aspect=_TARGET_ASPECT,
                                 meta_captions={_TARGET_SURFACE: {"caption": caption,
                                                "hashtags": _norm_hashtags(hashtags)}}))
                # RE-READ all four rows and run the COMPLETE validator before the transaction exits cleanly.
                # (The run record is written after commit, so it is not part of this pass — the clip bytes were
                # strictly validated above and are hashed into the record we are about to publish.)
                verrs = _validate_expected_lineage(cfg, led, identity, run_dir, mode=_MODE_LIVE, rec=None)
                if verrs:
                    raise _Refuse(f"post-creation lineage validation FAILED — rolling back: {'; '.join(verrs[:3])}")
    except _Refuse as r:
        return _err(r.msg)

    # ---- 9. publish the run record ONLY AFTER adoption commits, so a concurrent `discard` can never read the
    # record while the ledger is still empty. A crash in the tiny commit->write gap leaves entities without a
    # record; the idempotent re-prepare path (step 5) re-writes it via the same `_ensure_run_record`. ----
    _ensure_run_record(cfg, run_id, canonical_name, fingerprint, media_sha256, clip_sha,
                       src_w, src_h, src_dur, ids)
    return _ok({**plan, "created": True, "idempotent": False, "clip_sha256": clip_sha})


def _read_run_record(cfg: Config, run_id: str) -> tuple[Optional[dict], Optional[str]]:
    """Return (record, error). A genuinely ABSENT record is (None, None) — recoverable. An existing but
    unreadable/malformed record is (None, <error>) — a tamper signal that callers must REFUSE, never silently
    overwrite as if it were a crash-recovery gap."""
    p = _run_dir(cfg, run_id) / "canary-run.json"
    if not p.exists():
        return None, None
    try:
        return json.loads(p.read_text()), None
    except (OSError, ValueError) as exc:
        return None, f"unreadable/malformed run record: {str(exc)[:100]}"


def _ensure_run_record(cfg: Config, run_id: str, canonical_name: str, fingerprint: str, media_sha256: str,
                       clip_sha256: str, src_w, src_h, src_dur, ids: dict) -> None:
    """Write the run record (idempotent). Written AFTER ledger adoption so `discard` never observes it before
    the lineage exists. The source geometry is recorded so idempotent validation has the expected values to
    hold the ledger row to (the FILE bytes remain the tamper-proof authority — its sha256 is identity-bound)."""
    run_dir = _run_dir(cfg, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    _assert_contained(_canary_root(cfg), run_dir)
    _write_json_atomic(run_dir / "canary-run.json",
                       {"run_id": run_id, "canonical_name": canonical_name, "fingerprint": fingerprint,
                        "media_sha256": media_sha256, "clip_sha256": clip_sha256,
                        "source_probe": {"width": src_w, "height": src_h, "duration": src_dur}, **ids})


# ---------- discard (pre-mint only) ----------

def _discard_post_block(led: Ledger, identity: dict) -> Optional[str]:
    cid, bid = identity["clip_id"], identity["batch_id"]
    for p in led.posts.values():
        if p.parent_id == cid or (p.batch_id and p.batch_id == bid) or p.account == CANARY_HANDLE:
            return f"a Post ({p.id}) exists for this run — discard is pre-mint only (use `canary cancel`)"
    return None

def _discard_media_evidence(led: Ledger, identity: dict) -> Optional[str]:
    c = led.clips.get(identity["clip_id"])
    if c is not None and (getattr(c, "media_url", None) or "").strip():
        return f"canary clip {c.id} carries a hosted media_url — not pre-mint (media already uploaded)"
    return None


def discard_canary(cfg: Config, run_id: str) -> ActionResult:
    if not _RUN_ID_RE.match(run_id or ""):
        return _err(f"invalid canary run id: {run_id!r}")
    run_dir = _run_dir(cfg, run_id)
    rec, rec_err = _read_run_record(cfg, run_id)
    if rec_err is not None:
        return _err(rec_err)
    if rec is None:
        return _err(f"no canary run record for {run_id}")
    # recompute the WHOLE identity from the record's self-verifying canonical_name, binding its mutable ids.
    identity, aerr = _recompute_identity_from_record(rec)
    if aerr is not None:
        return _err(aerr)
    if identity["run_id"] != run_id:
        return _err(f"run record names {identity['run_id']} but lives in dir {run_id} — refusing")
    sid, mid, cid, bid = identity["source_id"], identity["moment_id"], identity["clip_id"], identity["batch_id"]
    acct = next((a for a in Accounts.load(cfg).accounts if a.handle == CANARY_HANDLE), None)
    if acct is None or acct.status is not AccountStatus.planned:
        return _err(f"{CANARY_HANDLE} must be planned to discard (is {acct.status.value if acct else 'absent'})")
    # audit evidence is FILE-based (NOT transactional). Recheck it immediately before the transaction and fail
    # CLOSED — a lineage whose mint/publish history we cannot inspect is never deleted.
    if _audit_has_mint_evidence(cfg, bid=bid, cid=cid, run_id=run_id):
        return _err(f"audit log shows mint/approve/publish/cancel evidence for {run_id} — refusing discard")

    before = _map_digests(cfg)
    already_terminal = False
    try:
        with Ledger.transaction(cfg) as led:
            # EVERY ledger-dependent precondition is (re-)checked HERE, under the mutation lock, so a mint that
            # committed before we took the lock is seen and refuses discard.
            perrs = _validate_expected_lineage(cfg, led, identity, run_dir, mode=_MODE_DISCARD, rec=rec)
            if perrs:
                raise _Refuse(f"canary lineage for {run_id} fails validation — refusing: {'; '.join(perrs[:4])}")
            pblock = _discard_post_block(led, identity)
            if pblock is not None:
                raise _Refuse(pblock)
            mblock = _discard_media_evidence(led, identity)
            if mblock is not None:
                raise _Refuse(mblock)
            src, mom, clp, bat = led.sources.get(sid), led.moments.get(mid), led.clips.get(cid), led.batches.get(bid)
            already_terminal = ((src is None or src.state is SourceState.retired) and
                                (mom is None or mom.state is MomentState.retired) and
                                (clp is None or clp.state is ClipState.retired) and
                                (bat is None or bat.state is BatchState.closed))
            # RETIRE each present, non-terminal entity IN PLACE (NOT `retire_source`, which
            # reconcile_moments(sid, {}) CASCADE-DELETES the unprotected canary moment/clip). set_*_state is a
            # plain, no-cascade state flip, so the retained lineage survives + is inert. Idempotent.
            if src is not None and src.state is not SourceState.retired: led.set_source_state(sid, SourceState.retired)
            if mom is not None and mom.state is not MomentState.retired: led.set_moment_state(mid, MomentState.retired)
            if clp is not None and clp.state is not ClipState.retired: led.retire_clip(cid)
            if bat is not None and bat.state is not BatchState.closed:
                led.set_batch_state(bid, BatchState.closed)
    except _Refuse as r:
        return _err(r.msg)
    removed = _remove_run_dir(cfg, run_id)
    after = _map_digests(cfg)
    changed = {k: [before.get(k), after.get(k)] for k in (set(before) | set(after)) if before.get(k) != after.get(k)}
    return _ok({"run_id": run_id, "retired": {"source": sid, "moment": mid, "clip": cid},
                                 "batch_closed": bid, "files_removed": removed,
                                 "already_terminal": already_terminal,
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

def _authenticated_run_for_post(cfg: Config, led: Ledger, post) -> tuple[Optional[dict], Optional[Path], Optional[str]]:
    """Locate the ONE authenticated canary run whose recomputed identities match the Post's Clip AND Batch,
    then validate it with the shared complete validator. A hand-inserted Post+Batch with
    target_accounts=[canary] but no real run record matches nothing and is refused."""
    root = _canary_root(cfg)
    if not root.exists():
        return None, None, f"post {post.id} maps to no authenticated canary run (no canary runs on disk) — refusing"
    matches = []
    for run_json in sorted(root.glob("canary_*/canary-run.json")):
        run_dir = run_json.parent
        if not _RUN_ID_RE.match(run_dir.name):
            continue
        try:
            rec = json.loads(run_json.read_text())
        except (OSError, ValueError) as exc:
            get_logger(cfg)("canary", run_dir.name, "run_record_unreadable_skip", level="warning", err=str(exc)[:120])
            continue
        identity, aerr = _recompute_identity_from_record(rec)
        if aerr is not None or identity["run_id"] != run_dir.name:
            continue
        if identity["clip_id"] == post.parent_id and identity["batch_id"] == (post.batch_id or ""):
            matches.append((identity, run_dir, rec))
    if len(matches) != 1:
        return None, None, (f"post {post.id} does not map to exactly one authenticated canary run "
                            f"({len(matches)} matched) — refusing")
    identity, run_dir, rec = matches[0]
    perrs = _validate_expected_lineage(cfg, led, identity, run_dir, mode=_MODE_LIVE, rec=rec)
    if perrs:
        return None, None, f"the canary run for {post.id} fails validation — refusing: {'; '.join(perrs[:3])}"
    return identity, run_dir, None


def _has_provider_evidence(post) -> Optional[str]:
    if is_real_submission_id(post.submission_id):
        return f"a real submission_id ({post.submission_id}) — possible network acceptance"
    if post.reconcile_candidate_id is not None:
        return "a non-null reconcile_candidate_id — possible provider duplicate"
    if (post.public_url or "").strip() or (getattr(post, "published_at", None) or ""):
        return "a public_url/published_at — possible platform publication"
    return None


def cancel_canary_post(cfg: Config, post_id: str, *, reason: str) -> ActionResult:
    led0 = Ledger.load(cfg)
    post = led0.posts.get(post_id)
    if post is None:
        return _err(f"no such post: {post_id}")
    if post.account != CANARY_HANDLE:
        return _err(f"{post_id} is not a {CANARY_HANDLE} post (account={post.account})")
    if post.state not in (PostState.awaiting_approval, PostState.queued):
        return _err(f"cancel refuses state={post.state.value} — only awaiting_approval/queued (before network)")
    integ, ierr = _canary_integration_id(cfg)
    if ierr is not None:
        return _err(ierr)
    if str(post.account_id) != str(integ):
        return _err(f"{post_id} account_id {post.account_id!r} != the canary integration {integ!r} — refusing")
    identity, _run_dir_, ferr = _authenticated_run_for_post(cfg, led0, post)
    if ferr is not None:
        return _err(ferr)
    ev = _has_provider_evidence(post)
    if ev is not None:
        return _err(f"cancel refuses a post carrying {ev}")
    bounded = (_CANARY_REASON_PREFIX + redact(reason or "", limit=_REASON_MAX))[:_REASON_MAX + len(_CANARY_REASON_PREFIX)]
    try:
        with Ledger.transaction(cfg) as led:
            cur = led.posts.get(post_id)
            if cur is None or cur.state not in (PostState.awaiting_approval, PostState.queued):
                raise _Refuse("post state changed under lock — refusing")
            if cur.account != CANARY_HANDLE or str(cur.account_id) != str(integ):
                raise _Refuse("post account/integration changed under lock — refusing")
            if (cur.batch_id or "") != identity["batch_id"] or cur.parent_id != identity["clip_id"]:
                raise _Refuse("post batch/clip changed under lock — refusing")
            ev2 = _has_provider_evidence(cur)
            if ev2 is not None:
                raise _Refuse(f"post gained {ev2} under lock — refusing (possible acceptance)")
            _id2, _rd2, ferr2 = _authenticated_run_for_post(cfg, led, cur)   # ledger-dependent re-validation
            if ferr2 is not None:
                raise _Refuse(ferr2)
            led.set_post_state(post_id, PostState.retired, error_reason=bounded)
    except _Refuse as r:
        return _err(r.msg)
    warn = None
    try:
        write_audit(cfg, "canary_cancel", [post_id], reason="canary_cancel", canary_reason=bounded)
    except Exception as exc:
        get_logger(cfg)("canary", post_id, "audit_write_failed", level="error", err=str(exc)[:120])
        warn = f"audit write failed (post is safely retired): {str(exc)[:120]}"
    return _ok({"post_id": post_id, "state": "retired", "reason": bounded, "audit_warning": warn})


# ---------- small local helpers ----------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _write_json_atomic(p: Path, obj) -> None:
    from fanops.controlio import write_json_atomic
    write_json_atomic(p, obj)

def _map_digests(cfg: Config) -> dict:
    if not Path(cfg.ledger_path).exists():
        return {}
    con = sqlite3.connect(f"file:{cfg.ledger_path}?mode=ro", uri=True)
    try:
        out = {}
        for (m,) in con.execute("SELECT DISTINCT map_name FROM ledger_rows").fetchall():
            rows = con.execute("SELECT row_id,payload FROM ledger_rows WHERE map_name=?", (m,)).fetchall()
            out[m] = _sep_digest(sorted(((rid, blob) for rid, blob in rows), key=lambda t: t[0]))
    finally:
        con.close()
    return out

def _audit_has_mint_evidence(cfg: Config, *, bid: str, cid: str, run_id: str) -> bool:
    path = cfg.control / "studio_audit.log"
    if not path.exists():
        return False
    try:
        text = path.read_text()
    except OSError as exc:
        # can't read the audit log -> cannot RULE OUT mint/publish evidence. Fail CLOSED (evidence "present")
        # so discard refuses rather than deleting a lineage whose history we cannot inspect.
        get_logger(cfg)("canary", run_id, "audit_unreadable_assume_evidence", level="warning", err=str(exc)[:120])
        return True
    return any(tok and tok in text for tok in (bid, cid, run_id))
