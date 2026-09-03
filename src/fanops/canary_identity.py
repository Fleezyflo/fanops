"""Canary identity subsystem — canonicalization, run-id derivation, window rules, filesystem ownership.

Extracted from canary.py (SA-C8-1). Public callers should continue to use `fanops.canary`; this module is
the implementation home for identity-bearing primitives that prepare/discard/cancel authenticate against.
"""
from __future__ import annotations
import hashlib, json, math, os, re, tempfile, uuid
from pathlib import Path
from typing import Optional

from fanops.config import Config
from fanops.ids import child_id
from fanops.log import get_logger
from fanops.models import Fmt, Platform, PLATFORM_MAX_SECONDS


# ---- pinned, PERMANENT identity contract (never change these) ----
CANARY_HANDLE = "fanops_canary"                 # reserved LOCAL account alias (the remote TikTok handle may differ)
CANARY_RUN_ID_VERSION = "1"
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
# the EXACT key-set of a canonical run name — any extra/missing key is a hard refusal
_EXPECTED_CANON_KEYS = frozenset({"version", "handle", "platform", "media_sha256", "start", "end",
                                  "segments", "caption_sha256", "hashtags", "hook_sha256", "run_label"})
_MIN_SEG_SECONDS = 0.5                           # mirrors models._MIN_MOMENT_S — a shorter segment is noise
_RENDER_TMP_PREFIX = "clip."                     # unique render temp: clip.<rand>.part.mp4 (never the final
_RENDER_TMP_SUFFIX = ".part.mp4"                 #   clip.mp4; swept on entry so a crash-orphan is never final)


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
