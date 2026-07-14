# src/fanops/reframe_apply.py
"""`fanops reframe --apply` — the MUTATION phase of the reframe migration.

The dry-run (reframe.py) proves WHICH clips were blind-centre-cropped and what a reframe would change.
This module performs that change, and it is built so that every step can be undone and every claim can be
checked. The migration changes FRAMING PIXELS and the FRAMING FINGERPRINT. Nothing else.

FIVE INVARIANTS, structural rather than conventional:

  1. THE PLAN IS IMMUTABLE. It is derived ONCE from a reviewed full-corpus dry-run manifest and pinned to
     disk. Apply re-reads live state only to CHECK it against the plan (the preimage assertion), never to
     regenerate the plan from state that has drifted. A live/plan divergence SKIPS the clip; it never
     silently re-plans it.

  2. render_moment IS FORBIDDEN. It owns the ledger: it constructs Clip rows, sets MomentState, writes
     stamp_stage manifests and can park a moment in a terminal error state. Routing an EXISTING clip
     through it would rewrite workflow state we are contractually required to preserve. We call
     `clip.render_reframed` — the low-level, ledger-free renderer — with EXACTLY the inputs the dry-run
     proved, and we write the two files we declared: `{cid}.mp4` and `{cid}.render.json`.

  3. THE .ass IS NEVER REWRITTEN. `ass` is a fingerprint field. The dry-run proved every eligible clip's
     payload delta is a subset of {focus, track, ct, geom} — so the burned text is IDENTICAL. We reuse the
     EXISTING production `.ass` for the ffmpeg `-vf` token and assert its sha256 against the plan. If the
     ASS changed, the clip is not eligible any more and it is skipped.

  4. BACK UP, VALIDATE, THEN COMMIT. The render goes to a STAGING path. Production is replaced only after
     the staged file passes every validator AND its recomputed fingerprint equals fp_new. A failure at any
     point leaves production byte-identical, because we never wrote to it.

  5. THE LOCK IS THE INVARIANT; STOPPED SERVICES ARE ONLY AN OPERATIONAL GATE. A real inter-process lock
     (`00_control/reframe.lock`, O_EXCL + flock) is held for the whole run, and the shared render entry
     points REFUSE while it is held by anyone else. Relying on "we stopped the daemon" would be relying on
     an operator not making a mistake.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from fanops import clip as clipmod
from fanops import framing
from fanops import overlay
from fanops.bands import band_for
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.reframe import APPROVED_FRAMING_KEYS, ReframeClass, ReframePaths, snapshot_ledger

RUN_SCHEMA_VERSION = 1
LOCK_FILENAME = "reframe.lock"

# The COMPLETE set of production paths this migration may write. Anything else appearing under the
# protected root is a stop condition, not a curiosity. (Relative to the production root's base.)
#   03_clips/{cid}.mp4            -- replaced (the whole point)
#   03_clips/{cid}.render.json    -- replaced (fp_old -> fp_new)
#   00_control/reframe.lock       -- the migration lock
#   00_control/ledger.sqlite-wal  -- SQLite's own coordination, from the read-only snapshot
#   00_control/ledger.sqlite-shm  -- ditto
#   07_reports/reframe/<run>/**   -- the run directory (plan, journal, backups, validation, summary)
_DECLARED_WRITE_SUFFIXES = (".mp4", ".render.json")

# Every ledger collection the migration must leave byte-identical. `posts` alone carries the captions,
# hooks, hashtags, approvals, schedules, account mappings, public/media URLs, analytics and post state —
# hashing the canonical row pins all of them without enumerating fields that could be added later and
# silently escape the proof.
_LEDGER_KINDS = ("clips", "moments", "posts", "sources", "renders", "stitch_plans", "batches")


class MigrationLockHeld(RuntimeError):
    """A render was attempted while a reframe migration holds the lock. RAISED, never fail-opened: a
    concurrent render would race the migration for the same {cid}.mp4 and could publish a half-migrated
    clip, or overwrite a reframed clip with a centred one. Loud is the only safe behaviour."""


class PlanStale(RuntimeError):
    """Live state no longer matches the immutable plan. The clip is skipped, never re-planned."""


class LedgerMutated(RuntimeError):
    """The ledger changed during the migration. This must never happen; it is a hard stop."""


# ---- the global migration lock ----------------------------------------------------------------------

def lock_path(cfg: Config) -> Path:
    return cfg.control / LOCK_FILENAME


# Set ONLY by the migration process that owns the lock, so it can render through its own guard while every
# other process (daemon, Studio, CLI) is refused. Module-level because the guard is called deep inside
# clip.render_moment, far from any object we could thread through.
_OWNED_RUN_ID: str | None = None


def _read_lock(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def migration_holder(cfg: Config) -> dict | None:
    """The lock's owner record, or None when no migration is running. A lock whose PID is dead is STALE
    and reported as such — we never silently steal it (a stale lock is an operator decision), but we also
    never let it block a render forever without saying why."""
    p = lock_path(cfg)
    if not p.exists():
        return None
    rec = _read_lock(p) or {}
    pid = rec.get("pid")
    alive = False
    if isinstance(pid, int):
        try:
            os.kill(pid, 0)
            alive = True
        except OSError as exc:
            alive = exc.errno == errno.EPERM       # EPERM = alive but not ours; ESRCH = gone
    return {**rec, "alive": alive}


def assert_render_allowed(cfg: Config, clip_id: str = "-") -> None:
    """THE GUARD. Called at the shared render entry points (clip.render_moment / clip.render_account_cut).

    Refuses when a reframe migration holds the lock and we are NOT that migration. It raises — it does not
    return a centred crop, and it does not fail open — because both of those would let the daemon quietly
    render over a clip the migration is mid-way through replacing.

    Costs one `Path.exists()` on the normal path. Outside a migration this function is a no-op and every
    existing exception semantic is untouched."""
    h = migration_holder(cfg)
    if h is None:
        return
    if _OWNED_RUN_ID is not None and h.get("run_id") == _OWNED_RUN_ID:
        return                                                  # we ARE the migration: render away
    state = "held" if h.get("alive") else "STALE (owner pid is gone)"
    raise MigrationLockHeld(
        f"reframe migration {h.get('run_id')} holds {LOCK_FILENAME} ({state}); refusing to render {clip_id}. "
        f"Renders are blocked for the duration of the migration — a concurrent render would race it for the "
        f"same media file. If the lock is stale, `fanops reframe --status <run_id>` and clear it deliberately.")


class MigrationLock:
    """A real inter-process lock: an O_EXCL-created file holding the owner record, plus an flock on the
    open fd so a crashed process's lock is released by the kernel rather than wedging the corpus forever."""

    def __init__(self, cfg: Config, run_id: str):
        self.cfg, self.run_id, self._fd = cfg, run_id, None

    def acquire(self) -> None:
        global _OWNED_RUN_ID
        p = lock_path(self.cfg)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o644)
        except FileExistsError:
            h = migration_holder(self.cfg) or {}
            raise MigrationLockHeld(
                f"another reframe migration already holds the lock: run_id={h.get('run_id')} pid={h.get('pid')} "
                f"alive={h.get('alive')}. Refusing to run two migrations over one corpus.") from None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise MigrationLockHeld("reframe.lock exists and is flocked by a live process") from None
        os.write(fd, json.dumps({"run_id": self.run_id, "pid": os.getpid(),
                                 "acquired_at": time.time()}).encode("utf-8"))
        os.fsync(fd)
        self._fd = fd
        _OWNED_RUN_ID = self.run_id

    def release(self) -> None:
        global _OWNED_RUN_ID
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
        try:
            lock_path(self.cfg).unlink()
        except OSError as exc:
            get_logger(self.cfg)("reframe", self.run_id, "lock_unlink_failed", reason=type(exc).__name__)
        _OWNED_RUN_ID = None

    def held(self) -> bool:
        return self._fd is not None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


# ---- hashing / probing ------------------------------------------------------------------------------

def sha256_file(p) -> str | None:
    h = hashlib.sha256()
    try:
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def ffprobe_json(path: str) -> dict | None:
    """Full stream+format probe. None on any failure — the caller treats an unprobeable output as INVALID
    (never as 'probably fine')."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                            str(path)], capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, ValueError):
        return None


def _vstream(probe: dict) -> dict:
    for s in (probe or {}).get("streams", []):
        if s.get("codec_type") == "video":
            return s
    return {}


def _astream(probe: dict) -> dict:
    for s in (probe or {}).get("streams", []):
        if s.get("codec_type") == "audio":
            return s
    return {}


def _fps(probe: dict) -> float | None:
    v = _vstream(probe).get("avg_frame_rate") or _vstream(probe).get("r_frame_rate") or ""
    try:
        n, d = str(v).split("/")
        return round(float(n) / float(d), 4) if float(d) else None
    except (ValueError, ZeroDivisionError):
        return None


def media_facts(path: str) -> dict:
    """The non-framing properties a reframe must PRESERVE. Recorded in the plan from the CURRENT production
    file, and re-asserted on the staged output before it is allowed to replace it."""
    pr = ffprobe_json(path)
    if pr is None:
        return {"probe_ok": False}
    v, a = _vstream(pr), _astream(pr)
    try:
        dur = float((pr.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        dur = None
    return {"probe_ok": True, "duration": dur, "width": v.get("width"), "height": v.get("height"),
            "fps": _fps(pr), "has_audio": bool(a), "audio_codec": a.get("codec_name"),
            "audio_channels": a.get("channels"), "audio_rate": a.get("sample_rate"),
            "nb_frames": v.get("nb_frames"), "vcodec": v.get("codec_name")}


# Tolerances. Explicit, and deliberately tight: a reframe is a CROP — it re-encodes the same window of the
# same source, so duration/fps/audio must land on the same values, not merely nearby ones.
_DUR_TOL_S = 0.25          # mirrors the repo's stitch duration check (a re-mux can shift the last frame)
_FPS_TOL = 0.02


def validate_output(staged: str, plan_row: dict) -> tuple[bool, list]:
    """Every guard that must pass before a staged file is allowed to touch production. Returns
    (ok, failures) — the failure list is the evidence, and it is journaled verbatim."""
    fails: list = []
    p = Path(staged)
    if not p.exists():
        return False, ["staged file absent"]
    if p.stat().st_size == 0:
        return False, ["staged file is 0 bytes"]
    got = media_facts(staged)
    if not got.get("probe_ok"):
        return False, ["ffprobe failed on the staged output"]
    want = plan_row["expect"]
    if want.get("duration") is not None and got.get("duration") is not None:
        if abs(got["duration"] - want["duration"]) > _DUR_TOL_S:
            fails.append(f"duration {got['duration']} vs expected {want['duration']} (tol {_DUR_TOL_S}s)")
    if want.get("target_w") and got.get("width") != want["target_w"]:
        fails.append(f"width {got.get('width')} != target {want['target_w']}")
    if want.get("target_h") and got.get("height") != want["target_h"]:
        fails.append(f"height {got.get('height')} != target {want['target_h']}")
    if want.get("fps") is not None and got.get("fps") is not None:
        if abs(got["fps"] - want["fps"]) > _FPS_TOL:
            fails.append(f"fps {got['fps']} vs expected {want['fps']} (tol {_FPS_TOL})")
    if bool(want.get("has_audio")) != bool(got.get("has_audio")):
        fails.append(f"audio presence changed: {want.get('has_audio')} -> {got.get('has_audio')}")
    if want.get("has_audio"):
        for k in ("audio_channels", "audio_rate"):
            if want.get(k) is not None and got.get(k) != want.get(k):
                fails.append(f"{k} {got.get(k)} != expected {want.get(k)}")
    return (not fails), fails


def decodes(path: str, frames: int = 3) -> bool:
    """Actually DECODE representative frames. A file can probe fine and still be undecodable."""
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", str(frames),
                            "-f", "null", "-"], capture_output=True, text=True, timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


# ---- the immutable plan -----------------------------------------------------------------------------

def _render_inputs(paths: ReframePaths, cfg: Config, led: Ledger, c):
    """The EXACT inputs `clip.render_moment` would pass to `render_reframed` for this clip today — window,
    ass text, top_bias, and the resolver's (focus, track, content_type). This is the same derivation
    `reframe.current_payload` hashes, so the render and the fingerprint cannot disagree."""
    m = led.moments[c.parent_id]
    src = led.sources[m.parent_id]
    band = band_for(clipmod._moment_profile(m, cfg))
    cs, ce = clipmod.fit_window(m.start, m.end, src.duration or 0.0, lo=band.lo, hi=band.hi)
    cs, ce = clipmod.snap_window(cs, ce, clipmod._trusted_transcript(src), duration=src.duration or 0.0)
    if cfg.visual_start:
        cs, _k = clipmod.pick_visual_start(src.source_path, cs, ce, scene_peaks=src.signal_peaks,
                                           out_dir=cfg.clips)          # scratch out_dir (seeded sidecar)
    ass_text, _hbf = clipmod._build_ass_text(led, cfg, c.parent_id, c.id, c.aspect,
                                             clip_start=cs, clip_end=ce)     # PURE — no write
    res = framing._resolve(cfg, src, cs, ce)                                 # production semantics: fail LOUD
    return {"src_path": src.source_path, "cs": cs, "ce": ce, "aspect": c.aspect.value,
            "src_w": src.width or 0, "src_h": src.height or 0, "ass_text": ass_text,
            "top_bias": clipmod._moment_top_bias(m, cfg), "focus": res.focus, "track": res.track,
            "content_type": res.content_type, "source_id": src.id, "moment_id": m.id}


def build_plan(manifest: dict, paths: ReframePaths, *, source_id: str | None = None,
               limit: int | None = None) -> dict:
    """Derive the mutation plan from a REVIEWED full-corpus dry-run manifest. Refuses a partial manifest —
    a corpus-wide mutation may not rest on a corpus-wide claim the dry-run itself declined to make."""
    if manifest.get("partial"):
        raise PlanStale("refusing to plan from a PARTIAL dry-run manifest — run the full corpus first")
    scratch_cfg = paths.scratch_cfg
    snapshot_ledger(paths)
    led = Ledger.load(scratch_cfg)
    rows: list = []
    for r in manifest["clips"]:
        if r.get("classification") != ReframeClass.ELIGIBLE.value:
            continue
        c = led.clips.get(r["clip_id"])
        if c is None:
            continue
        m = led.moments.get(c.parent_id)
        if m is None:
            continue
        if source_id and m.parent_id != source_id:
            continue
        mp4 = paths.production_clips / f"{c.id}.mp4"
        side = paths.production_clips / f"{c.id}.render.json"
        ass = paths.production_clips / f"{c.id}.ass"
        ri = _render_inputs(paths, scratch_cfg, led, c)
        # The delta the dry-run proved. Re-asserted here so a plan can never widen it.
        delta = sorted(r.get("payload_delta") or [])
        if not set(delta) <= APPROVED_FRAMING_KEYS:
            raise PlanStale(f"{c.id}: manifest delta {delta} is outside {sorted(APPROVED_FRAMING_KEYS)}")
        facts = media_facts(str(mp4))
        tw, th = clipmod._TARGETS.get(c.aspect.value, (None, None)) if hasattr(clipmod, "_TARGETS") else (None, None)
        rows.append({
            "clip_id": c.id, "moment_id": c.parent_id, "source_id": m.parent_id, "aspect": c.aspect.value,
            "media_path": str(mp4), "sidecar_path": str(side), "ass_path": str(ass),
            "preimage": {"media_sha256": sha256_file(mp4), "sidecar_sha256": sha256_file(side),
                         "ass_sha256": (hashlib.sha256((ri["ass_text"] or "").encode("utf-8")).hexdigest()),
                         "ass_file_sha256": sha256_file(ass) if ass.exists() else None,
                         "source_sha256_path": ri["src_path"],
                         "clip_state": getattr(c.state, "value", str(c.state)),
                         "moment_state": getattr(m.state, "value", str(m.state)),
                         "clip_media_url": getattr(c, "media_url", None)},
            "fp_old": r["fp_old"], "fp_new": r["fp_new"],
            "payload_old": r["payload_old"], "payload_new": r["payload_new"], "payload_delta": delta,
            "render": {"src_path": ri["src_path"], "cs": ri["cs"], "ce": ri["ce"], "aspect": ri["aspect"],
                       "src_w": ri["src_w"], "src_h": ri["src_h"], "top_bias": ri["top_bias"],
                       "focus": list(ri["focus"]) if ri["focus"] else None,
                       "track": [list(s) for s in ri["track"]] if ri["track"] else None,
                       "content_type": ri["content_type"], "has_ass": bool(ri["ass_text"])},
            "expect": {"duration": facts.get("duration"), "fps": facts.get("fps"),
                       "has_audio": facts.get("has_audio"), "audio_channels": facts.get("audio_channels"),
                       "audio_rate": facts.get("audio_rate"),
                       "target_w": tw, "target_h": th,
                       "src_width": facts.get("width"), "src_height": facts.get("height")},
            "framing": r.get("framing"),
        })
    rows.sort(key=lambda x: x["clip_id"])
    if limit is not None:
        rows = rows[:limit]
    return {"run_schema_version": RUN_SCHEMA_VERSION, "planned": len(rows),
            "source_filter": source_id, "clips": rows,
            "manifest_argv": manifest.get("argv"), "manifest_attribution": manifest.get("attribution")}


# ---- the run directory + append-only journal --------------------------------------------------------

@dataclass
class RunDirs:
    root: Path                       # 07_reports/reframe/<run_id>
    backups: Path
    staging: Path
    review: Path
    journal: Path
    plan: Path
    meta: Path
    summary: Path

    @classmethod
    def build(cls, cfg: Config, run_id: str) -> "RunDirs":
        root = cfg.reports / "reframe" / run_id
        return cls(root=root, backups=root / "backups", staging=root / "staging", review=root / "review",
                   journal=root / "journal.jsonl", plan=root / "plan.json", meta=root / "run.json",
                   summary=root / "summary.json")

    def mkdirs(self) -> None:
        for d in (self.root, self.backups, self.staging, self.review):
            d.mkdir(parents=True, exist_ok=True)


def journal_append(dirs: RunDirs, rec: dict) -> None:
    """APPEND-ONLY, crash-safe. History is never rewritten — a retry appends a new record, it does not
    erase the failure that preceded it."""
    rec = {**rec, "ts": time.time()}
    with open(dirs.journal, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def journal_read(dirs: RunDirs) -> list:
    out: list = []
    try:
        for line in dirs.journal.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue                     # a torn final line from a crash: skip it, never rewrite it
    except OSError:
        return []
    return out


# ---- ledger preservation proof ----------------------------------------------------------------------

def ledger_fingerprint(paths: ReframePaths, clip_ids=None) -> dict:
    """Canonical serialization hashes for everything the migration must NOT touch. Taken from a read-only
    SQLite snapshot, so this never opens the live ledger read-write."""
    snapshot_ledger(paths)
    led = Ledger.load(paths.scratch_cfg)

    def _canon(o) -> str:
        # model_dump() is the repo's OWN canonical form (Ledger.save uses it verbatim), so this hashes the
        # exact representation that is persisted — not a parallel one free to drift from it.
        d = o.model_dump(mode="json")
        return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    ids = set(clip_ids) if clip_ids else set(led.clips)
    out: dict = {k: {} for k in _LEDGER_KINDS}
    for cid in sorted(ids):
        c = led.clips.get(cid)
        if c is None:
            continue
        out["clips"][cid] = _canon(c)
        m = led.moments.get(c.parent_id)
        if m is not None:
            out["moments"][m.id] = _canon(m)
    for pid, p in sorted(led.posts.items()):
        if not clip_ids or getattr(p, "parent_id", None) in ids:
            out["posts"][pid] = _canon(p)        # captions, hooks, hashtags, approvals, schedules, URLs,
                                                 # analytics, account_id and state ALL live on the Post row,
                                                 # so one canonical hash pins every one of them at once.
    for sid, s in sorted(led.sources.items()):
        out["sources"][sid] = _canon(s)
    for kind in ("renders", "stitch_plans", "batches"):
        for k, v in sorted(getattr(led, kind, {}).items()):
            out[kind][k] = _canon(v)
    out["_ledger_file_sha256"] = sha256_file(paths.production_cfg.ledger_path)
    return out


def ledger_diff(before: dict, after: dict) -> list:
    changed: list = []
    for kind in _LEDGER_KINDS:
        b, a = before.get(kind, {}), after.get(kind, {})
        for k in sorted(set(b) | set(a)):
            if b.get(k) != a.get(k):
                changed.append(f"{kind}.{k}")
    if before.get("_ledger_file_sha256") != after.get("_ledger_file_sha256"):
        changed.append("_ledger_file_sha256")
    return changed


# ---- per-clip state, as READ FROM DISK (never assumed from the journal alone) -------------------------

UNTOUCHED = "untouched"                 # production is exactly the preimage
BACKED_UP = "backed_up"                 # backup exists and is valid; production still the preimage
COMMITTED = "committed"                 # mp4 replaced AND sidecar carries fp_new -- coherent
TORN = "mp4_replaced_sidecar_old"       # THE crash window: new pixels, stale fingerprint
RESTORED = "restored"                   # production is the preimage again and a backup exists
AMBIGUOUS = "ambiguous"                 # we cannot prove which of the above -- STOP, never guess


def _stored_fp(sidecar: Path) -> str | None:
    try:
        v = json.loads(sidecar.read_text()).get("fp")
        return v if isinstance(v, str) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def inspect_clip(dirs: RunDirs, row: dict) -> str:
    """Resume's eyes. Reads ACTUAL disk state and classifies it. Ambiguity is a first-class answer: a
    resume that guesses is worse than one that stops."""
    mp4, side = Path(row["media_path"]), Path(row["sidecar_path"])
    bk_mp4 = dirs.backups / f"{row['clip_id']}.mp4"
    pre = row["preimage"]
    cur_mp4 = sha256_file(mp4) if mp4.exists() else None
    cur_fp = _stored_fp(side)
    bk_ok = bk_mp4.exists() and sha256_file(bk_mp4) == pre["media_sha256"]

    if cur_mp4 is None:
        return AMBIGUOUS                                  # production media is GONE: never invent a story
    if cur_mp4 == pre["media_sha256"] and cur_fp == row["fp_old"]:
        return RESTORED if bk_ok else (BACKED_UP if bk_mp4.exists() else UNTOUCHED)
    if cur_mp4 != pre["media_sha256"] and cur_fp == row["fp_new"]:
        return COMMITTED
    if cur_mp4 != pre["media_sha256"] and cur_fp == row["fp_old"]:
        return TORN                                       # crashed between os.replace(mp4) and the sidecar
    return AMBIGUOUS


# ---- backup ------------------------------------------------------------------------------------------

def backup_clip(dirs: RunDirs, row: dict) -> dict:
    """Byte-exact, deterministic path, verified after copy, NEVER overwritten. An existing valid backup is
    reused (resume is idempotent); an existing INVALID one is a hard error, not something to overwrite --
    overwriting it would destroy the only copy of the original."""
    cid = row["clip_id"]
    out: dict = {}
    for src, name, want in ((Path(row["media_path"]), f"{cid}.mp4", row["preimage"]["media_sha256"]),
                            (Path(row["sidecar_path"]), f"{cid}.render.json", row["preimage"]["sidecar_sha256"])):
        dst = dirs.backups / name
        if dst.exists():
            got = sha256_file(dst)
            if got != want:
                raise PlanStale(f"{cid}: existing backup {name} sha {got} != planned preimage {want} — "
                                f"refusing to overwrite the only copy of the original")
            out[name] = got
            continue
        shutil.copy2(src, dst)                            # copy2 preserves mtime; bytes verified below
        got = sha256_file(dst)
        if got != want:
            raise PlanStale(f"{cid}: backup of {name} verified {got} != {want} — copy is not byte-exact")
        out[name] = got
    return out


# ---- the one-clip mutation ---------------------------------------------------------------------------

def _assert_preimage(paths: ReframePaths, dirs: RunDirs, led: Ledger, row: dict) -> None:
    """Everything that must STILL be true, immediately before we touch this clip. Any failure raises
    PlanStale and the clip is SKIPPED as PREIMAGE_MISMATCH -- never migrated, never counted."""
    cid = row["clip_id"]
    pre = row["preimage"]
    mp4, side, ass = Path(row["media_path"]), Path(row["sidecar_path"]), Path(row["ass_path"])
    got = sha256_file(mp4)
    if got != pre["media_sha256"]:
        raise PlanStale(f"{cid}: media sha {got} != planned preimage {pre['media_sha256']}")
    if sha256_file(side) != pre["sidecar_sha256"]:
        raise PlanStale(f"{cid}: render sidecar changed since planning")
    if _stored_fp(side) != row["fp_old"]:
        raise PlanStale(f"{cid}: stored fingerprint is not fp_old — this clip is no longer the one we planned")
    if row["render"]["has_ass"]:
        if not ass.exists():
            raise PlanStale(f"{cid}: planned .ass is gone")
        if sha256_file(ass) != pre["ass_file_sha256"]:
            raise PlanStale(f"{cid}: .ass changed since planning — the burned text is a fingerprint input")
    elif ass.exists() and pre["ass_file_sha256"] is None:
        raise PlanStale(f"{cid}: an .ass appeared since planning")
    if not Path(row["render"]["src_path"]).exists():
        raise PlanStale(f"{cid}: source media is gone")
    c = led.clips.get(cid)
    m = led.moments.get(row["moment_id"]) if c is not None else None
    if c is None or m is None:
        raise PlanStale(f"{cid}: clip or moment vanished from the ledger")
    if getattr(c, "media_url", None):
        raise PlanStale(f"{cid}: a REMOTE asset appeared (clip.media_url) — the bytes are already published")
    if getattr(c.state, "value", str(c.state)) != pre["clip_state"]:
        raise PlanStale(f"{cid}: clip state moved {pre['clip_state']} -> {getattr(c.state, 'value', c.state)}")
    if getattr(m.state, "value", str(m.state)) != pre["moment_state"]:
        raise PlanStale(f"{cid}: moment state moved since planning")
    if led.post_is_remote_or_publishable_any(cid):
        raise PlanStale(f"{cid}: a post over this clip is now remote/publishable — refusing to swap its bytes")


def apply_clip(paths: ReframePaths, dirs: RunDirs, led: Ledger, row: dict, *, run_id: str) -> dict:
    """BACK UP -> RENDER TO STAGING -> VALIDATE -> COMMIT. Production is written in exactly two places, and
    only after every guard has passed. Any failure before the commit leaves production byte-identical
    because nothing has been written to it."""
    cid = row["clip_id"]
    rec: dict = {"run_id": run_id, "clip_id": cid, "fp_old": row["fp_old"], "fp_new": row["fp_new"],
                 "payload_delta": row["payload_delta"]}
    state = inspect_clip(dirs, row)
    if state == COMMITTED:
        return {**rec, "phase": "skip", "status": "already_committed"}
    if state == AMBIGUOUS:
        return {**rec, "phase": "stop", "status": "AMBIGUOUS",
                "error": "disk state matches no known phase — explicit repair required"}
    if state == TORN:
        # New pixels, stale fingerprint. HEAL it: the staged render already won, only the sidecar is
        # missing. We do not re-render (that would waste the proven output); we finish the commit.
        Path(row["sidecar_path"]).write_text(json.dumps({"fp": row["fp_new"]}))
        return {**rec, "phase": "heal", "status": "healed_sidecar",
                "final": {"media_sha256": sha256_file(row["media_path"]), "fp": _stored_fp(Path(row["sidecar_path"]))}}

    _assert_preimage(paths, dirs, led, row)                       # raises PlanStale -> PREIMAGE_MISMATCH
    backups = backup_clip(dirs, row)
    rec["backup"] = backups

    r = row["render"]
    staged = dirs.staging / f"{cid}.mp4"
    extra_vf = overlay.subtitles_vf(Path(row["ass_path"])) if r["has_ass"] else None
    focus = tuple(r["focus"]) if r["focus"] else None
    track = [tuple(s) for s in r["track"]] if r["track"] else None

    # THE FINGERPRINT GATE, asserted on the inputs we are ABOUT to render with -- not on the plan's copy of
    # them. If these two disagree, the sidecar we would write would attest to a render that never happened.
    ass_text = Path(row["ass_path"]).read_text(encoding="utf-8") if r["has_ass"] else ""
    payload = clipmod._render_fingerprint_payload(r["src_path"], r["cs"], r["ce"], r["aspect"],
                                                  r["src_w"], r["src_h"], ass_text, top_bias=r["top_bias"],
                                                  focus=focus, track=track, content_type=r["content_type"])
    fp_actual = clipmod.fingerprint_of_payload(payload)
    if fp_actual != row["fp_new"]:
        return {**rec, "phase": "refuse", "status": "FINGERPRINT_DIVERGED",
                "error": f"inputs hash to {fp_actual[:16]} but the plan proved {row['fp_new'][:16]}"}
    drift = sorted(k for k in set(row["payload_old"]) | set(payload)
                   if row["payload_old"].get(k) != payload.get(k))
    if not set(drift) <= APPROVED_FRAMING_KEYS:
        return {**rec, "phase": "refuse", "status": "NON_FRAMING_DRIFT",
                "error": f"a re-render would also change {sorted(set(drift) - APPROVED_FRAMING_KEYS)}"}

    try:
        res = clipmod.render_reframed(r["src_path"], str(staged), r["cs"], r["ce"], r["aspect"],
                                      src_w=r["src_w"], src_h=r["src_h"], extra_vf=extra_vf,
                                      top_bias=r["top_bias"], focus=focus, track=track,
                                      content_type=r["content_type"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {**rec, "phase": "render", "status": "RENDER_FAILED", "error": type(exc).__name__}
    if getattr(res, "returncode", 1) != 0 or not staged.exists() or staged.stat().st_size == 0:
        return {**rec, "phase": "render", "status": "RENDER_FAILED",
                "error": f"rc={getattr(res, 'returncode', '?')}"}

    ok, fails = validate_output(str(staged), row)
    if not ok:
        staged.unlink(missing_ok=True)                            # production NEVER touched
        return {**rec, "phase": "validate", "status": "VALIDATION_FAILED", "error": fails}
    if not decodes(str(staged)):
        staged.unlink(missing_ok=True)
        return {**rec, "phase": "validate", "status": "VALIDATION_FAILED", "error": ["staged output does not decode"]}

    staged_sha = sha256_file(staged)
    if staged_sha == row["preimage"]["media_sha256"]:
        # The reframe produced BYTE-IDENTICAL pixels. Keep the original, keep its sidecar, throw the staged
        # file away. Not an error -- just a clip whose new framing happens to render the same bytes.
        staged.unlink(missing_ok=True)
        return {**rec, "phase": "commit", "status": "UNCHANGED_PIXELS", "final": {"media_sha256": staged_sha}}

    # ---- COMMIT. Two writes, both declared, mp4 first so the TORN state is detectable and healable. ----
    os.replace(str(staged), row["media_path"])
    tmp_side = Path(str(row["sidecar_path"]) + ".part")
    tmp_side.write_text(json.dumps({"fp": row["fp_new"]}))
    with open(tmp_side, "r+", encoding="utf-8") as fh:
        os.fsync(fh.fileno())
    os.replace(str(tmp_side), row["sidecar_path"])

    final_mp4 = sha256_file(row["media_path"])
    final_fp = _stored_fp(Path(row["sidecar_path"]))
    if final_mp4 != staged_sha or final_fp != row["fp_new"]:
        return {**rec, "phase": "commit", "status": "COMMIT_INCOHERENT",
                "error": f"post-commit media={final_mp4} fp={final_fp}"}
    return {**rec, "phase": "commit", "status": "MIGRATED",
            "final": {"media_sha256": final_mp4, "fp": final_fp, "bytes": Path(row["media_path"]).stat().st_size}}


# ---- rollback ----------------------------------------------------------------------------------------

def rollback_clip(dirs: RunDirs, row: dict) -> dict:
    """Restore the EXACT original bytes. Verifies the backup BEFORE trusting it, restores atomically,
    verifies afterwards, and is idempotent (a clip already at its preimage is a no-op success)."""
    cid = row["clip_id"]
    bk_mp4, bk_side = dirs.backups / f"{cid}.mp4", dirs.backups / f"{cid}.render.json"
    pre = row["preimage"]
    if not bk_mp4.exists() or not bk_side.exists():
        return {"clip_id": cid, "status": "ROLLBACK_NO_BACKUP"}
    if sha256_file(bk_mp4) != pre["media_sha256"] or sha256_file(bk_side) != pre["sidecar_sha256"]:
        return {"clip_id": cid, "status": "ROLLBACK_BACKUP_CORRUPT"}
    if sha256_file(row["media_path"]) == pre["media_sha256"] and _stored_fp(Path(row["sidecar_path"])) == row["fp_old"]:
        return {"clip_id": cid, "status": "ROLLBACK_NOOP"}         # idempotent
    for bk, dst in ((bk_mp4, row["media_path"]), (bk_side, row["sidecar_path"])):
        tmp = Path(str(dst) + ".rbpart")
        shutil.copy2(bk, tmp)
        os.replace(str(tmp), dst)
    if sha256_file(row["media_path"]) != pre["media_sha256"] or sha256_file(row["sidecar_path"]) != pre["sidecar_sha256"]:
        return {"clip_id": cid, "status": "ROLLBACK_VERIFY_FAILED"}
    return {"clip_id": cid, "status": "ROLLED_BACK", "media_sha256": pre["media_sha256"]}


# ---- declared-write verification ----------------------------------------------------------------------

def declared_write_violations(diff: dict, prod_cfg: Config, run_id: str, planned_ids: set) -> list:
    """Every protected-root path this migration touched that it did NOT declare. A non-empty list is a hard
    stop: an undeclared production write means we do not actually know what this run did."""
    clips, control, reports = prod_cfg.clips, prod_cfg.control, prod_cfg.reports
    run_dir = reports / "reframe" / run_id
    allowed_control = {str(control / LOCK_FILENAME), str(control / (prod_cfg.ledger_path.name + "-wal")),
                       str(control / (prod_cfg.ledger_path.name + "-shm")), str(control)}
    bad: list = []
    for key in ("added", "removed", "changed"):
        for p in diff.get(key, []):
            sp = str(p)
            if sp in allowed_control or sp.startswith(str(run_dir)) or sp == str(reports / "reframe") or sp == str(reports):
                continue
            q = Path(sp)
            if q.parent == clips and q.name.split(".")[0] in planned_ids and sp.endswith(_DECLARED_WRITE_SUFFIXES):
                continue
            if sp == str(clips):
                continue                                   # the clips DIRECTORY mtime moves when we replace a file
            bad.append(f"{key}:{sp}")
    return bad


def free_bytes(p: Path) -> int:
    st = os.statvfs(str(p))
    return st.f_bavail * st.f_frsize


def new_run_id(stamp: float) -> str:
    return "rf_" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(stamp))


# ---- the run ------------------------------------------------------------------------------------------

_FAILURE_THRESHOLD = 0.10       # >10% of planned clips failing is a systemic fault, not bad luck: STOP.


def apply_run(paths: ReframePaths, *, manifest: dict, run_id: str, source_id: str | None = None,
              limit: int | None = None, dry_plan_only: bool = False) -> dict:
    """Execute (or resume) one migration. Serial by construction — ffmpeg is already multi-threaded, and
    parallel renders would contend for CPU, the detector's memory, the stage locks and the journal's order
    for no benefit we were asked to buy."""
    prod = paths.production_cfg
    dirs = RunDirs.build(prod, run_id)
    dirs.mkdirs()

    # ---- the IMMUTABLE plan: written once, re-used verbatim on resume. NEVER regenerated from live state.
    if dirs.plan.exists():
        plan = json.loads(dirs.plan.read_text())
    else:
        plan = build_plan(manifest, paths, source_id=source_id, limit=limit)
        dirs.plan.write_text(json.dumps(plan, indent=2, sort_keys=True))
    rows = plan["clips"]
    planned_ids = {r["clip_id"] for r in rows}
    if dry_plan_only:
        return {"run_id": run_id, "planned": len(rows), "plan_path": str(dirs.plan)}

    # ---- disk: backups + staging, with 100% headroom over the calculated requirement.
    need = sum(Path(r["media_path"]).stat().st_size for r in rows if Path(r["media_path"]).exists())
    required = need * 2 * 2                    # (backup + staged) x 100% safety headroom
    have = free_bytes(prod.clips)
    if have < required:
        return {"run_id": run_id, "status": "ABORTED_DISK",
                "error": f"free {have/1e9:.1f}GB < required {required/1e9:.1f}GB (2x(backup+stage))"}

    lock = MigrationLock(prod, run_id)
    lock.acquire()
    t0 = time.monotonic()
    try:
        led_before = ledger_fingerprint(paths, planned_ids)
        root_before = _scan(paths)
        journal_append(dirs, {"phase": "run_begin", "run_id": run_id, "planned": len(rows),
                              "ledger_sha": led_before["_ledger_file_sha256"],
                              "attribution": manifest.get("attribution")})

        led = Ledger.load(paths.scratch_cfg)                # the read-only SNAPSHOT: never the live handle
        results: list = []
        fails = 0
        for i, row in enumerate(rows, 1):
            try:
                out = apply_clip(paths, dirs, led, row, run_id=run_id)
            except PlanStale as exc:
                out = {"run_id": run_id, "clip_id": row["clip_id"], "phase": "preimage",
                       "status": "PREIMAGE_MISMATCH", "error": str(exc)[:300]}
            except Exception as exc:                        # one clip must never abort the corpus...
                out = {"run_id": run_id, "clip_id": row["clip_id"], "phase": "error",
                       "status": "ERROR", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
                get_logger(prod)("reframe", row["clip_id"], "apply_failed", reason=type(exc).__name__)
            journal_append(dirs, {**out, "i": i, "of": len(rows)})
            results.append(out)
            if out["status"] in ("MIGRATED", "UNCHANGED_PIXELS", "already_committed", "healed_sidecar"):
                pass
            elif out["status"] == "AMBIGUOUS":
                journal_append(dirs, {"phase": "run_stop", "reason": "ambiguous clip state", "clip_id": row["clip_id"]})
                break                                       # ...but an AMBIGUOUS clip stops the RUN. Never guess.
            else:
                fails += 1
                if fails > max(2, int(_FAILURE_THRESHOLD * len(rows))):
                    journal_append(dirs, {"phase": "run_stop", "reason": "failure threshold exceeded",
                                          "fails": fails, "planned": len(rows)})
                    break

        led_after = ledger_fingerprint(paths, planned_ids)
        led_changed = ledger_diff(led_before, led_after)
        root_after = _scan(paths)
        undeclared = declared_write_violations(_diff(root_before, root_after), prod, run_id, planned_ids)

        summary = _summarize(run_id, plan, results, led_changed, undeclared, time.monotonic() - t0, dirs)
        dirs.summary.write_text(json.dumps(summary, indent=2, sort_keys=True))
        journal_append(dirs, {"phase": "run_end", **{k: summary[k] for k in ("totals", "ledger_changed",
                                                                             "undeclared_writes")}})
        return summary
    finally:
        lock.release()


def _scan(paths: ReframePaths) -> dict:
    from fanops.reframe import scan_tree
    return scan_tree(paths.production_root)


def _diff(a: dict, b: dict) -> dict:
    from fanops.reframe import diff_tree
    return diff_tree(a, b)


def _summarize(run_id, plan, results, led_changed, undeclared, secs, dirs) -> dict:
    totals: dict = {}
    for r in results:
        totals[r["status"]] = totals.get(r["status"], 0) + 1
    migrated = [r for r in results if r["status"] == "MIGRATED"]
    return {
        "run_id": run_id, "run_schema_version": RUN_SCHEMA_VERSION,
        "planned": plan["planned"], "attempted": len(results),
        "totals": totals,
        "migrated": len(migrated),
        "unchanged_pixels": totals.get("UNCHANGED_PIXELS", 0),
        "failed": sum(v for k, v in totals.items()
                      if k not in ("MIGRATED", "UNCHANGED_PIXELS", "already_committed", "healed_sidecar")),
        "bytes_replaced": sum((r.get("final") or {}).get("bytes", 0) for r in migrated),
        "duration_s": round(secs, 1),
        # THE two invariants. Non-empty either one and the run is not a success, whatever the totals say.
        "ledger_changed": led_changed,
        "undeclared_writes": undeclared,
        "clean": (not led_changed) and (not undeclared),
        "run_dir": str(dirs.root), "journal": str(dirs.journal), "backups": str(dirs.backups),
        "clips": results,
    }


def rollback_run(paths: ReframePaths, run_id: str, *, clip_id: str | None = None) -> dict:
    """Undo. One clip, or the whole run. Verifies each backup before trusting it and re-verifies after."""
    prod = paths.production_cfg
    dirs = RunDirs.build(prod, run_id)
    plan = json.loads(dirs.plan.read_text())
    rows = [r for r in plan["clips"] if (clip_id is None or r["clip_id"] == clip_id)]
    lock = MigrationLock(prod, run_id + ":rollback")
    lock.acquire()
    try:
        out = []
        for row in rows:
            res = rollback_clip(dirs, row)
            journal_append(dirs, {"phase": "rollback", **res})
            out.append(res)
        totals: dict = {}
        for r in out:
            totals[r["status"]] = totals.get(r["status"], 0) + 1
        return {"run_id": run_id, "rolled_back": len(out), "totals": totals, "clips": out}
    finally:
        lock.release()


def run_status(paths: ReframePaths, run_id: str) -> dict:
    """What ACTUALLY happened, read from disk + the journal. Every clip is re-inspected, so a status that
    disagrees with the journal (a crash, an out-of-band edit) is visible rather than papered over."""
    prod = paths.production_cfg
    dirs = RunDirs.build(prod, run_id)
    if not dirs.plan.exists():
        return {"run_id": run_id, "error": "no such run"}
    plan = json.loads(dirs.plan.read_text())
    states: dict = {}
    for row in plan["clips"]:
        st = inspect_clip(dirs, row)
        states[st] = states.get(st, 0) + 1
    h = migration_holder(prod)
    return {"run_id": run_id, "planned": plan["planned"], "disk_states": states,
            "journal_records": len(journal_read(dirs)),
            "lock": h, "terminal": h is None or h.get("run_id") != run_id,
            "summary": json.loads(dirs.summary.read_text()) if dirs.summary.exists() else None}


def cleanup_run(paths: ReframePaths, run_id: str) -> dict:
    """Delete the backups. EXPLICIT, separate, and refused unless the run is terminal, unambiguous, and the
    backups still verify. Default behaviour everywhere else in this module is to RETAIN them."""
    prod = paths.production_cfg
    dirs = RunDirs.build(prod, run_id)
    st = run_status(paths, run_id)
    if st.get("error"):
        return st
    if not st["terminal"]:
        return {"run_id": run_id, "refused": "the migration lock is still held by this run"}
    if st["disk_states"].get(AMBIGUOUS):
        return {"run_id": run_id, "refused": f"{st['disk_states'][AMBIGUOUS]} clip(s) are AMBIGUOUS — repair first"}
    if st["disk_states"].get(TORN):
        return {"run_id": run_id, "refused": f"{st['disk_states'][TORN]} clip(s) are TORN — resume first"}
    plan = json.loads(dirs.plan.read_text())
    for row in plan["clips"]:
        bk = dirs.backups / f"{row['clip_id']}.mp4"
        if bk.exists() and sha256_file(bk) != row["preimage"]["media_sha256"]:
            return {"run_id": run_id, "refused": f"backup for {row['clip_id']} no longer verifies"}
    n = sum(1 for _ in dirs.backups.iterdir()) if dirs.backups.exists() else 0
    shutil.rmtree(dirs.backups)
    journal_append(dirs, {"phase": "cleanup", "removed_backups": n})
    return {"run_id": run_id, "cleaned": True, "removed_backups": n}
