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
import hashlib, json, math, os, re, shutil, sqlite3, subprocess, tempfile, uuid
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


class _Refuse(Exception):
    """Raised INSIDE a Ledger.transaction to abort with a refusal WITHOUT persisting any partial state.
    Ledger.transaction saves only on a clean exit, so an uncaught raise rolls back to the prior snapshot —
    this is how every under-lock precondition refuses without touching the lineage."""
    def __init__(self, msg: str):
        super().__init__(msg); self.msg = msg


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
_TARGET_SURFACE = f"{CANARY_HANDLE}/tiktok"
_MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
_RUN_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_RUN_ID_RE = re.compile(r"^canary_[0-9a-f]{32}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_REASON_MAX = 180
_CANARY_REASON_PREFIX = "canary_cancelled: "
# the EXACT key-set of a canonical run name — any extra/missing key is a hard refusal
_EXPECTED_CANON_KEYS = frozenset({"version", "handle", "platform", "media_sha256", "start", "end",
                                  "segments", "caption_sha256", "hashtags", "hook_sha256", "run_label"})
_MIN_SEG_SECONDS = 0.5                           # mirrors models._MIN_MOMENT_S — a shorter segment is noise
_SOURCE_DUR_TOL = 0.5                            # a clip window may exceed the probed source by at most this
_PROBE_DUR_ABS_TOL = 1.5                         # rendered-clip duration tolerance vs the requested window:
_PROBE_DUR_REL_TOL = 0.25                        #   max(abs, rel*expected) — container/keyframe padding drift
_SOURCE_PROBE_DUR_TOL = 1.0                      # stored Source.duration vs a fresh probe of the owned media
_RENDER_TMP_PREFIX = "clip."                     # unique render temp: clip.<rand>.part.mp4 (never the final
_RENDER_TMP_SUFFIX = ".part.mp4"                 #   clip.mp4; swept on entry so a crash-orphan is never final)

# validation modes for the single expected-lineage validator
_MODE_LIVE = "live"                              # the lineage must be in its exact minted pre-mint states
_MODE_DISCARD = "discard"                        # ...or already in its permitted terminal state (re-discard)


# ---------- canonicalization helpers ----------

def _canon(obj) -> str:
    # allow_nan=False: identity-bearing canonical JSON must NEVER contain NaN/Infinity. A non-finite value
    # raises here and fails CLOSED rather than emitting non-standard JSON tokens. Byte-identical to the
    # prior behaviour for all valid (finite) inputs.
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

def _sha256_bytes_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def _sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()

def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(_HEX64_RE.match(s))

def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)

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


# ---------- the SINGLE structural window rule (used by prepare AND canonical-name authentication) ----------

def _normalized_window(start, end, segments) -> tuple[Optional[dict], Optional[str]]:
    """The one structural time/segment validator. Returns (window, None) or (None, field-specific error).

    window = {eff_start, end, segments, envelope_end, realized}. `eff_start` is segs[0][0] for a segmented run
    (the segments ARE the window — a separate --start is not independently meaningful), else `start`.

    Enforced: finite start ≥ 0; finite end when present; EXACTLY one of end/segments; every segment boundary
    finite and ≥ 0; end > start; every segment ≥ _MIN_SEG_SECONDS; segments ordered by start and
    non-overlapping; canonical normalized numeric representation; realized duration finite, positive and
    within the TikTok cap. `_parse_canonical_name` calls this too, so a canonical identity that prepare would
    refuse structurally cannot authenticate a run record either."""
    try:
        start_f = _canon_time(start)
        end_f = _canon_time(end) if end is not None else None
        segs = _norm_segments(segments)
    except (TypeError, ValueError) as exc:
        return None, f"bad time value: {str(exc)[:120]}"
    if not _finite(start_f):
        return None, f"start must be a finite number (got {start!r})"
    if start_f < 0:
        return None, f"start must be non-negative (got {start_f})"
    if end_f is not None and not _finite(end_f):
        return None, f"end must be a finite number (got {end!r})"
    if segs is not None and end_f is not None:
        return None, "pass EITHER an end OR segments, not both"
    if segs is None and end_f is None:
        return None, "a single-window canary needs an end (or use segments)"
    if segs is not None:
        prev_end = -math.inf
        for s, e in segs:
            if not (_finite(s) and _finite(e)):
                return None, "every segment boundary must be a finite number (no NaN/Infinity)"
            if s < 0:
                return None, f"every segment must start at a non-negative time (got start {s})"
            if e <= s:
                return None, f"every segment must have end > start (got {s}->{e})"
            if (e - s) < _MIN_SEG_SECONDS:
                return None, f"segment {s}->{e} is shorter than the {_MIN_SEG_SECONDS}s minimum"
            if s < prev_end:
                return None, f"segments must be strictly ascending and non-overlapping ({s} < prior end {prev_end})"
            prev_end = e
        eff_start = segs[0][0]
        envelope_end = segs[-1][1]
        realized = sum(e - s for s, e in segs)
    else:
        if end_f <= start_f:
            return None, "end must be greater than start"
        eff_start = start_f
        envelope_end = end_f
        realized = end_f - start_f
    if not _finite(realized) or realized <= 0:
        return None, f"realized duration must be finite and positive (got {realized!r})"
    cap = PLATFORM_MAX_SECONDS.get(_TARGET_PLATFORM)
    if cap is not None and realized > cap:
        return None, f"clip duration {realized:.1f}s exceeds tiktok cap {cap}s"
    return {"eff_start": eff_start, "end": end_f, "segments": segs,
            "envelope_end": envelope_end, "realized": realized}, None


# ---------- identity: canonical JSON name -> UUIDv5 run id; full-sha256 content tokens ----------

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
    """Full SHA-256 of a VERSIONED canonical JSON object — never a delimiter join."""
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


def _identity_dict(*, run_id: str, fingerprint: str, media_sha256: str, start, end, segments,
                   caption_sha256: str, hashtags, hook_sha256: Optional[str], run_label: Optional[str],
                   ids: dict) -> dict:
    """The single in-memory identity carried through prepare / discard / cancel validation. It carries the
    FULL canonical content (caption/hashtags/hook/label hashes), not just the times, so the shared validator
    can check the stored hook, caption and hashtags without ever holding their plaintext."""
    return {"run_id": run_id, "fingerprint": fingerprint, "media_sha256": media_sha256,
            "canon_start": _canon_time(start), "canon_end": (_canon_time(end) if end is not None else None),
            "canon_segments": _norm_segments(segments), "caption_sha256": caption_sha256,
            "hashtags": _norm_hashtags(hashtags), "hook_sha256": hook_sha256,
            "run_label": _norm_label(run_label), **ids}


def _parse_canonical_name(cn: str) -> tuple[Optional[dict], Optional[str]]:
    """Strictly parse + schema-validate a canonical run name. The stored string is the ONLY trusted field in a
    run record; everything else is recomputed from it. Refuses unknown/missing/extra keys, non-versioned or
    mis-typed forms, a non-canonical serialization, and — via `_normalized_window` — ANY structural
    time/segment shape that `prepare` itself would refuse."""
    try:
        obj = json.loads(cn)
    except (ValueError, TypeError) as exc:
        return None, f"canonical_name is not valid JSON: {str(exc)[:80]}"
    if not isinstance(obj, dict):
        return None, "canonical_name is not a JSON object"
    if set(obj.keys()) != _EXPECTED_CANON_KEYS:
        return None, (f"canonical_name key-set {sorted(obj.keys())} != expected {sorted(_EXPECTED_CANON_KEYS)} "
                      f"— refusing (unknown/missing identity field)")
    if obj["version"] != CANARY_RUN_ID_VERSION: return None, f"canonical_name version {obj['version']!r} != {CANARY_RUN_ID_VERSION!r}"
    if obj["handle"] != CANARY_HANDLE: return None, f"canonical_name handle {obj['handle']!r} != {CANARY_HANDLE!r}"
    if obj["platform"] != _TARGET_PLATFORM.value: return None, f"canonical_name platform {obj['platform']!r} != tiktok"
    if not _is_hex64(obj["media_sha256"]): return None, "canonical_name media_sha256 is not a sha256"
    if not _is_hex64(obj["caption_sha256"]): return None, "canonical_name caption_sha256 is not a sha256"
    if obj["hook_sha256"] is not None and not _is_hex64(obj["hook_sha256"]): return None, "canonical_name hook_sha256 is not a sha256"
    ht = obj["hashtags"]
    if not isinstance(ht, list) or ht != _norm_hashtags(ht): return None, "canonical_name hashtags are not a normalized list"
    if obj["run_label"] is not None and not _RUN_LABEL_RE.match(str(obj["run_label"])): return None, "canonical_name run_label is malformed"
    # the SAME structural window rule prepare uses (parity: prepare-refused shapes cannot authenticate)
    win, werr = _normalized_window(obj["start"], obj["end"], obj["segments"])
    if werr is not None:
        return None, f"canonical_name window is structurally invalid: {werr}"
    if win["eff_start"] != _canon_time(obj["start"]):
        return None, "canonical_name start is not the segmented effective start (segs[0][0])"
    # the stored string must be EXACTLY the canonical serialization (no reordered keys / stray whitespace),
    # so run_id = uuid5(NS, cn) and fingerprint = sha256(cn) are unambiguous.
    if _canon(obj) != cn:
        return None, "canonical_name is not in canonical form (byte-exact)"
    return obj, None


def _recompute_identity_from_record(rec: dict) -> tuple[Optional[dict], Optional[str]]:
    """Derive the WHOLE identity from the record's self-verifying canonical_name, then require every mutable
    id field stored in the record to EQUAL the recomputation. A record can therefore never select a different
    (even valid-canary) lineage merely by swapping its four ids."""
    if not isinstance(rec, dict):
        return None, "run record is not a JSON object"
    cn = rec.get("canonical_name")
    if not isinstance(cn, str) or not cn:
        return None, "run record has no canonical_name — refusing"
    obj, perr = _parse_canonical_name(cn)
    if perr is not None:
        return None, perr
    run_id = _run_id_from_name(cn)
    fingerprint = _sha256_text(cn)
    ids = _lineage_ids(run_id=run_id, media_sha256=obj["media_sha256"], start=obj["start"],
                       end=obj["end"], segments=obj["segments"])
    identity = _identity_dict(run_id=run_id, fingerprint=fingerprint, media_sha256=obj["media_sha256"],
                              start=obj["start"], end=obj["end"], segments=obj["segments"],
                              caption_sha256=obj["caption_sha256"], hashtags=obj["hashtags"],
                              hook_sha256=obj["hook_sha256"], run_label=obj["run_label"], ids=ids)
    if str(rec.get("run_id")) != run_id:
        return None, "run record run_id does not match its canonical_name — refusing (stale/tampered)"
    if str(rec.get("fingerprint")) != fingerprint:
        return None, "run record fingerprint does not match its canonical_name — refusing (stale/tampered)"
    if str(rec.get("media_sha256")) != obj["media_sha256"]:
        return None, "run record media_sha256 does not match its canonical_name — refusing"
    for k in ("source_id", "moment_id", "clip_id", "batch_id"):
        if str(rec.get(k)) != identity[k]:
            return None, (f"run record {k} does not match the id recomputed from canonical_name — "
                          f"refusing (a record cannot select a foreign lineage)")
    return identity, None


def _expected_moment_window(identity: dict):
    cs, ce, segs = identity["canon_start"], identity["canon_end"], identity["canon_segments"]
    if segs:
        return segs[0][0], segs[-1][1], [list(x) for x in segs]
    return cs, (ce if ce is not None else cs), []

def _realized_seconds(identity: dict) -> float:
    segs = identity["canon_segments"]
    if segs:
        return sum(e - s for s, e in segs)
    ce = identity["canon_end"]
    return (ce - identity["canon_start"]) if ce is not None else 0.0

def _expected_batch_name(identity: dict) -> str:
    return identity["run_label"] or f"canary {identity['run_id']}"


# ---------- filesystem ownership ----------

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

def _path_contained(path_str, container: Path) -> bool:
    if not path_str: return False
    try:
        _assert_contained(container, Path(path_str)); return True
    except (ValueError, OSError):
        return False

def _media_ext(media_path: str) -> str:
    ext = Path(media_path).suffix.lower()
    return ext if ext in _MEDIA_EXTS else ".mp4"

def _new_render_tmp(run_dir: Path) -> Path:
    """A UNIQUE render temp inside the owned run dir (never the final clip.mp4), realpath-contained."""
    fd, name = tempfile.mkstemp(prefix=_RENDER_TMP_PREFIX, suffix=_RENDER_TMP_SUFFIX, dir=str(run_dir))
    os.close(fd)
    return _assert_contained(run_dir, Path(name))

def _sweep_render_temps(cfg: Config, run_dir: Path) -> None:
    """Remove crash-orphan render temps on entry. A temp's bytes are NOT identity-bound, so it is never trusted
    as the final artifact — the only correct move is to drop it and (re)render / reuse the validated final."""
    for p in run_dir.glob(_RENDER_TMP_PREFIX + "*" + _RENDER_TMP_SUFFIX):
        try:
            _assert_contained(run_dir, p); p.unlink(missing_ok=True)
        except (ValueError, OSError) as exc:
            get_logger(cfg)("canary", run_dir.name, "orphan_temp_sweep_failed", level="warning", err=str(exc)[:120])


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
                led.batches[bid] = bat.model_copy(update={"state": BatchState.closed})
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
            led.posts[post_id] = cur.model_copy(update={"state": PostState.retired, "error_reason": bounded})
    except _Refuse as r:
        return _err(r.msg)
    warn = None
    try:
        write_audit(cfg, "canary_cancel", [post_id], reason="canary_cancel", canary_reason=bounded)
    except Exception as exc:
        get_logger(cfg)("canary", post_id, "audit_write_failed", level="error", err=str(exc)[:120])
        warn = f"audit write failed (post is safely retired): {str(exc)[:120]}"
    return _ok({"post_id": post_id, "state": "retired", "reason": bounded, "audit_warning": warn})


# ---------- read-only, NON-DISCLOSIVE multilayer baseline capture + compare ----------

# --- the PINNED baseline contract. _validate_baseline_shape holds a supplied baseline to EXACT equality
# against these sets, so an extra/missing/renamed field is an error rather than a silently-clean diff. ---
_BASELINE_TOP_KEYS = frozenset({"format_version", "status", "canonicalization", "schema_version",
                                "repo_commit", "post_count", "state_distribution", "digests",
                                "per_post_manifest", "per_post_layers", "frozen_incident"})
_BASELINE_STATUS = "candidate"
_BASELINE_CANONICALIZATION = {
    "json": "sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False",
    "row_order": "sorted by row_id",
    "aggregate": "sha256 of concat(row_id + 0x00 + blob + 0x1e)",
    "hash": "sha256",
}
_BASELINE_DIGEST_KEYS = frozenset({"raw_posts", "safety_critical", "scheduling", "content", "manifest"})
_SAFE_LAYER_KEYS = frozenset({"state", "account", "platform", "aspect", "has_public_url", "has_media_urls",
                              "has_submission_id", "is_real_submission_id", "has_reconcile_candidate",
                              "has_published_at", "account_id_sha256", "parent_id_sha256",
                              "submission_id_sha256", "reconcile_candidate_id_sha256", "public_url_sha256",
                              "media_urls_sha256", "error_reason_sha256", "published_at_sha256",
                              "created_at_sha256"})
_SAFE_BOOL_KEYS = frozenset({"has_public_url", "has_media_urls", "has_submission_id", "is_real_submission_id",
                             "has_reconcile_candidate", "has_published_at"})
_SAFE_CATEGORICAL_KEYS = frozenset({"state", "account", "platform", "aspect"})
_SCHED_LAYER_KEYS = frozenset({"scheduled_time_sha256", "approval"})
_CONTENT_LAYER_KEYS = frozenset({"caption_sha256", "hashtags_sha256", "parent_id_sha256", "aspect",
                                 "media_urls_sha256", "media_id_sha256"})
_FROZEN_INCIDENT_IDS = ("post_04b29c9f7f2d", "post_07e45c69ac0d", "post_0943840705ce", "post_0a12cff53619")
_FROZEN_ENTRY_KEYS = frozenset({"raw_sha256", "state", "submission_id_sha256",
                                "reconcile_candidate_id_sha256", "public_url_sha256", "has_public_url"})


def _read_posts_ro(cfg: Config):
    if not Path(cfg.ledger_path).exists():
        return [], None
    con = sqlite3.connect(f"file:{cfg.ledger_path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT row_id,payload FROM ledger_rows WHERE map_name='posts'").fetchall()
        sv = con.execute("SELECT value FROM ledger_meta WHERE key='schema_version'").fetchone()
    finally:
        con.close()
    return rows, (sv[0] if sv else None)

def _sep_digest(pairs) -> str:
    h = hashlib.sha256()
    for rid, blob in pairs:
        h.update(rid.encode()); h.update(b"\x00"); h.update(blob.encode()); h.update(b"\x1e")
    return h.hexdigest()

def _field_hash(v) -> str:
    """sha256 of a value's canonical JSON — the change-detection primitive that discloses NOTHING of the value."""
    return _sha256_text(_canon(v))

def _post_layers(d: dict) -> dict:
    """Per-post comparison layers. Every URL / token / id / timestamp is a per-field HASH or a categorical
    presence flag — NO raw public_url, media_urls, submission_id, error_reason, caption ever appears."""
    state = d.get("state"); sid = d.get("submission_id")
    pub = d.get("public_url")
    safe = {
        "state": state, "account": d.get("account"), "platform": d.get("platform"), "aspect": d.get("aspect"),
        "has_public_url": bool((pub or "").strip()) if isinstance(pub, str) else bool(pub),
        "has_media_urls": bool(d.get("media_urls")),
        "has_submission_id": bool(sid),
        "is_real_submission_id": bool(is_real_submission_id(sid)),
        "has_reconcile_candidate": d.get("reconcile_candidate_id") is not None,
        "has_published_at": bool(d.get("published_at")),
        "account_id_sha256": _field_hash(d.get("account_id")),
        "parent_id_sha256": _field_hash(d.get("parent_id")),
        "submission_id_sha256": _field_hash(sid),
        "reconcile_candidate_id_sha256": _field_hash(d.get("reconcile_candidate_id")),
        "public_url_sha256": _field_hash(pub),
        "media_urls_sha256": _field_hash(d.get("media_urls") or []),
        "error_reason_sha256": _field_hash(d.get("error_reason")),
        "published_at_sha256": _field_hash(d.get("published_at")),
        "created_at_sha256": _field_hash(d.get("created_at")),
    }
    sched = {"scheduled_time_sha256": _field_hash(d.get("scheduled_time")), "approval": state}
    content = {"caption_sha256": _sha256_text(d.get("caption") or ""),
               "hashtags_sha256": _field_hash(d.get("hashtags") or []),
               "parent_id_sha256": _field_hash(d.get("parent_id")), "aspect": d.get("aspect"),
               "media_urls_sha256": _field_hash(d.get("media_urls") or []),
               "media_id_sha256": _field_hash(d.get("media_id"))}
    return {"safe": _canon(safe), "sched": _canon(sched), "content": _canon(content)}

def _layer_digests(manifest: dict, layers: dict) -> dict:
    """Recompute the three per-layer aggregates + the manifest aggregate from the per-post maps alone, in a
    stable sorted row order. Capture stores these; validation RECOMPUTES them and requires equality, so a
    tampered aggregate (or a corrupted raw-hash manifest) is detectable without ever storing raw payloads."""
    rids = sorted(manifest)
    return {
        "safety_critical": _sep_digest([(r, layers[r]["safe"]) for r in rids]),
        "scheduling": _sep_digest([(r, layers[r]["sched"]) for r in rids]),
        "content": _sep_digest([(r, layers[r]["content"]) for r in rids]),
        "manifest": _sep_digest([(r, manifest[r]) for r in rids]),
    }

def _build_manifest(cfg: Config) -> dict:
    rows, sv = _read_posts_ro(cfg)
    parsed = sorted(((rid, blob, json.loads(blob)) for rid, blob in rows), key=lambda t: t[0])
    manifest = {rid: hashlib.sha256(blob.encode()).hexdigest() for rid, blob, _ in parsed}
    layers = {rid: _post_layers(d) for rid, _, d in parsed}
    dist = {}
    for _, _, d in parsed:
        dist[str(d.get("state"))] = dist.get(str(d.get("state")), 0) + 1
    incident = {}
    for i in _FROZEN_INCIDENT_IDS:
        for rid, _blob, d in parsed:
            if rid == i:
                # non-disclosive: raw-payload sha + state (categorical) + per-field hashes; NO raw url/token
                incident[i] = {"raw_sha256": manifest[i], "state": d.get("state"),
                               "submission_id_sha256": _field_hash(d.get("submission_id")),
                               "reconcile_candidate_id_sha256": _field_hash(d.get("reconcile_candidate_id")),
                               "public_url_sha256": _field_hash(d.get("public_url")),
                               "has_public_url": bool((d.get("public_url") or ""))}
    digests = {"raw_posts": _sep_digest([(rid, blob) for rid, blob, _ in parsed]), **_layer_digests(manifest, layers)}
    return {
        "format_version": BASELINE_FORMAT_VERSION,
        "status": _BASELINE_STATUS,               # ALWAYS candidate — capture never self-accepts
        "canonicalization": dict(_BASELINE_CANONICALIZATION),
        "schema_version": sv, "repo_commit": _repo_commit(), "post_count": len(parsed),
        "state_distribution": dist,
        "digests": digests,
        "per_post_manifest": manifest,            # rid -> sha256(raw payload)  (raw bytes never emitted)
        "per_post_layers": layers,                # rid -> {safe, sched, content} canon strings of per-field hashes
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
    return _ok({"output": str(out), "status": _BASELINE_STATUS,
                                 "raw_posts": manifest["digests"]["raw_posts"],
                                 "post_count": manifest["post_count"]})


def _validate_layer(rid: str, name: str, blob, keys: frozenset) -> Optional[str]:
    if not isinstance(blob, str):
        return f"per_post_layers[{rid!r}].{name} is not a string"
    try:
        obj = json.loads(blob)
    except (ValueError, TypeError):
        return f"per_post_layers[{rid!r}].{name} is not JSON"
    if not isinstance(obj, dict):
        return f"per_post_layers[{rid!r}].{name} is not a JSON object"
    if set(obj) != set(keys):
        return (f"per_post_layers[{rid!r}].{name} field-set {sorted(obj)} != pinned {sorted(keys)}")
    if _canon(obj) != blob:
        return f"per_post_layers[{rid!r}].{name} is not byte-canonical JSON"
    for k, v in obj.items():
        if k.endswith("_sha256") and not _is_hex64(v):
            return f"per_post_layers[{rid!r}].{name}.{k} is not a lowercase sha256"
        if name == "safe" and k in _SAFE_BOOL_KEYS and not isinstance(v, bool):
            return f"per_post_layers[{rid!r}].safe.{k} is not a boolean"
        if name == "safe" and k in _SAFE_CATEGORICAL_KEYS and not (v is None or isinstance(v, str)):
            return f"per_post_layers[{rid!r}].safe.{k} is not a categorical string"
    return None


def _validate_baseline_shape(prior) -> Optional[str]:
    """Hold a supplied baseline to the PINNED contract with EXACT equality (not mere field presence). A null /
    missing / extra / renamed / mistyped key, a non-hash hash field, an inconsistent post_count, an altered
    canonicalization block, a missing frozen incident, or a recomputed-aggregate mismatch is an ERROR — never
    an apparently-clean comparison."""
    if not isinstance(prior, dict):
        return "baseline is not a JSON object"
    if set(prior) != set(_BASELINE_TOP_KEYS):
        missing, extra = sorted(_BASELINE_TOP_KEYS - set(prior)), sorted(set(prior) - _BASELINE_TOP_KEYS)
        return f"baseline top-level keys mismatch (missing={missing}, extra={extra})"
    if prior["format_version"] != BASELINE_FORMAT_VERSION:
        return f"baseline format_version {prior['format_version']!r} != {BASELINE_FORMAT_VERSION!r}"
    if prior["status"] != _BASELINE_STATUS:
        return f"baseline status {prior['status']!r} != {_BASELINE_STATUS!r}"
    if prior["canonicalization"] != _BASELINE_CANONICALIZATION:
        return "baseline canonicalization block does not equal the pinned metadata"
    if not (prior["schema_version"] is None or isinstance(prior["schema_version"], (int, str))):
        return "baseline schema_version is not an int/str/null"
    rc = prior["repo_commit"]
    if not (isinstance(rc, str) and (rc == "unknown" or _COMMIT_RE.match(rc))):
        return "baseline repo_commit is not a 40-hex commit or 'unknown'"
    digests, man, lay = prior["digests"], prior["per_post_manifest"], prior["per_post_layers"]
    if not isinstance(digests, dict) or set(digests) != set(_BASELINE_DIGEST_KEYS):
        return f"baseline digests key-set != pinned {sorted(_BASELINE_DIGEST_KEYS)}"
    for k, v in digests.items():
        if not _is_hex64(v):
            return f"baseline digest {k!r} is not a lowercase sha256"
    if not isinstance(man, dict) or not isinstance(lay, dict):
        return "baseline per_post_manifest / per_post_layers is not a map"
    if set(man) != set(lay):
        return "baseline per_post_layers keys != per_post_manifest keys"
    pc = prior["post_count"]
    if not (isinstance(pc, int) and not isinstance(pc, bool) and pc >= 0):
        return "baseline post_count is not a non-negative integer"
    if pc != len(man):
        return f"baseline post_count {pc} != len(per_post_manifest) {len(man)}"
    dist = prior["state_distribution"]
    if not isinstance(dist, dict):
        return "baseline state_distribution is not a map"
    total = 0
    for k, v in dist.items():
        if not isinstance(k, str) or not (isinstance(v, int) and not isinstance(v, bool) and v >= 0):
            return "baseline state_distribution is not a string -> non-negative-integer map"
        total += v
    if total != pc:
        return f"baseline state_distribution sums to {total}, not post_count {pc}"
    for rid, hv in man.items():
        if not isinstance(rid, str) or not rid:
            return "baseline per_post_manifest has a non-string/empty Post ID"
        if not _is_hex64(hv):
            return f"baseline per_post_manifest[{rid!r}] is not a lowercase sha256"
    for rid, entry in lay.items():
        if not isinstance(entry, dict) or set(entry) != {"safe", "sched", "content"}:
            return f"baseline per_post_layers[{rid!r}] does not have exactly {{safe, sched, content}}"
        for name, keys in (("safe", _SAFE_LAYER_KEYS), ("sched", _SCHED_LAYER_KEYS), ("content", _CONTENT_LAYER_KEYS)):
            e = _validate_layer(rid, name, entry[name], keys)
            if e is not None:
                return e
    inc = prior["frozen_incident"]
    if not isinstance(inc, dict):
        return "baseline frozen_incident is not a map"
    for iid, entry in inc.items():
        if iid not in _FROZEN_INCIDENT_IDS:
            return f"baseline frozen_incident has an unexpected id {iid!r}"
        if not isinstance(entry, dict) or set(entry) != set(_FROZEN_ENTRY_KEYS):
            return f"baseline frozen_incident[{iid!r}] field-set != pinned {sorted(_FROZEN_ENTRY_KEYS)}"
        for k, v in entry.items():
            if k.endswith("_sha256") and not _is_hex64(v):
                return f"baseline frozen_incident[{iid!r}].{k} is not a lowercase sha256"
        if not isinstance(entry["has_public_url"], bool):
            return f"baseline frozen_incident[{iid!r}].has_public_url is not a boolean"
    present_incidents = [i for i in _FROZEN_INCIDENT_IDS if i in man]
    if sorted(inc) != sorted(present_incidents):
        return (f"baseline frozen_incident ids {sorted(inc)} != the incident posts present in the manifest "
                f"{sorted(present_incidents)}")
    # INTERNAL CONSISTENCY: the stored aggregates must equal a recomputation from the per-post maps alone.
    recomputed = _layer_digests(man, lay)
    for k, v in recomputed.items():
        if digests[k] != v:
            return f"baseline digest {k!r} does not match a recomputation from per_post_layers/manifest"
    return None


def compare_canary_baseline(cfg: Config, *, baseline: str) -> ActionResult:
    try:
        prior = json.loads(Path(baseline).expanduser().read_text())
    except (OSError, ValueError) as exc:
        return _err(f"cannot read baseline: {str(exc)[:120]}")
    shape_err = _validate_baseline_shape(prior)
    if shape_err is not None:
        return _err(f"invalid baseline — refusing to report a clean comparison: {shape_err}")
    try:
        cur = _build_manifest(cfg)
    except Exception as exc:
        get_logger(cfg)("canary", "baseline", "compare_manifest_failed", level="error", err=str(exc)[:140])
        return _err(f"current manifest failed: {str(exc)[:140]}")
    p_man, c_man = prior["per_post_manifest"], cur["per_post_manifest"]
    p_lay, c_lay = prior["per_post_layers"], cur["per_post_layers"]
    added = sorted(set(c_man) - set(p_man))
    removed = sorted(set(p_man) - set(c_man))
    both = set(p_man) & set(c_man)
    raw_changed = sorted(r for r in both if p_man[r] != c_man[r])
    def _layer_changed(key):
        return sorted(r for r in both if p_lay[r].get(key) != c_lay[r].get(key))
    safety_changed = _layer_changed("safe")
    sched_changed = _layer_changed("sched")
    content_changed = _layer_changed("content")
    safety_fields = {}
    for r in safety_changed:
        pf, cf = json.loads(p_lay[r]["safe"]), json.loads(c_lay[r]["safe"])
        safety_fields[r] = sorted(k for k in (set(pf) | set(cf)) if pf.get(k) != cf.get(k))
    # mismatch is TRUE for ANY divergence — raw, any layer, an added/removed id, OR an aggregate-digest
    # inequality (a modified baseline digest must never return a clean exit).
    digests_equal = {k: prior["digests"].get(k) == cur["digests"][k] for k in cur["digests"]}
    mismatch = bool(added or removed or raw_changed or safety_changed or sched_changed or content_changed
                    or not all(digests_equal.values()))
    return _ok({
        "mismatch": mismatch, "added": added, "removed": removed, "raw_changed": raw_changed,
        "safety_critical_changed": safety_fields, "scheduling_changed": sched_changed,
        "content_changed": content_changed, "digests_equal": digests_equal,
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
        out = r.stdout.strip()
        return out if (r.returncode == 0 and _COMMIT_RE.match(out)) else "unknown"
    except (subprocess.SubprocessError, OSError):
        return "unknown"

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
