# src/fanops/reframe.py
"""`fanops reframe --dry-run` — READ-ONLY corpus classification for the reframe migration decision.

WHY THIS EXISTS. `fp_new == fp_old` is NOT evidence that a clip was legitimately centred. A clip centred
because ffmpeg was MISSING is byte-indistinguishable from one centred because the room was EMPTY — the
reason was erased three levels down (see framing_outcomes). Re-rendering on a fingerprint match alone
would bake blind centre-crops into clips a working detector would have framed. This module measures the
corpus so the mutation decision can rest on evidence instead of on a hash.

IT MUTATES NOTHING. Two invariants, and they are structural, not conventional:

  1. EVERY production access is a READ, and goes through a CONTENT accessor on ReframePaths that returns
     content — never a writable Path. A caller cannot be handed a production path and write through it.
  2. EVERY write lands under `scratch_root`. Not by discipline: Config.__init__ derives control / clips /
     agent_io / ledger_path from `root`, so `Config(root=scratch)` STRUCTURALLY redirects the detect /
     track / saliency sidecars, the stage_lock lockfiles, the stamp_stage manifests, the keyframe jpgs
     and `.complete` markers, the pick_visual_start sidecars, and the SQLite WAL/SHM. `assert_write_target`
     is the belt; the protected-root before/after scan is the proof.

RECONSTRUCTION IS A PROOF, NEVER A GUESS. `{cid}.render.json` holds ONLY `{"fp": <sha256>}` — the
historical payload was never persisted, and you cannot diff a hash. So we ENUMERATE a small, explicit,
finite set of candidate legacy payloads, dedup them on CANONICAL SERIALIZED BYTES, and hash each. The
distinct payload whose sha256 equals the stored one IS the reconstruction. Zero matches ->
UNRECONSTRUCTABLE (we could not reproduce it; the cause is UNKNOWN and we do not name one). Two or more
-> RECONSTRUCTION_AMBIGUOUS, and we abort that clip rather than pick.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from fanops import clip as clipmod
from fanops import framing
from fanops.bands import band_for
from fanops.config import Config
from fanops.framing_outcomes import (LEGITIMATE_CENTER_OUTCOMES, RESOLVED_OUTCOMES, UNRESOLVED_OUTCOMES,
                                     FramingOutcome as _FO, FramingStrategy as _FS, UnknownFramingOutcome)
from fanops.ids import child_id
from fanops.ledger import Ledger
from fanops.log import get_logger

MANIFEST_SCHEMA_VERSION = 1

# The ONLY payload keys a reframe is allowed to change. A delta outside this set means re-rendering would
# ALSO re-burn a changed hook, or move the cut window — that is DRIFT, and it is not this migration's job.
APPROVED_FRAMING_KEYS = frozenset({"focus", "track", "ct", "geom"})


class ProductionWriteError(RuntimeError):
    """A write was attempted outside the scratch root. RAISED, never fail-opened: a dry-run that writes
    to production has already failed at the only thing it promised."""


class ReframeClass(str, Enum):
    # Guards. Evaluated FIRST, in this order — each can only DECLINE to reframe, never wrongly reframe.
    SUPERCUT_EXCLUDED = "supercut_excluded"
    STITCH_EXCLUDED = "stitch_excluded"
    REMOTE_ASSET_PRESENT = "remote_asset_present"
    MISSING_INPUT = "missing_input"
    # The classification proper.
    ELIGIBLE = "eligible"                          # STRUCTURALLY reframable (proven-centred, framing-key-only delta) — NOT a visual verdict
    ALREADY_REFRAMED = "already_reframed"
    LEGITIMATE_CENTER = "legitimate_center"        # centred because the room really was empty
    FRAMING_UNRESOLVED = "framing_unresolved"      # we do not know why it is centred -> never reframe blind
    UNRECONSTRUCTABLE = "unreconstructable"        # could not reproduce fp_stored. Cause UNKNOWN — not "drift".
    RECONSTRUCTION_AMBIGUOUS = "reconstruction_ambiguous"
    DRIFT = "drift"                                # a reframe would also change a NON-framing input
    ERROR = "error"                                # this clip blew up; the scan continues


@dataclass(frozen=True)
class ReframePaths:
    """The single frozen path context. NO helper ever receives a bare production Config."""
    production_root: Path
    scratch_root: Path

    @classmethod
    def build(cls, production_root, scratch_root) -> "ReframePaths":
        return cls(production_root=Path(production_root).resolve(), scratch_root=Path(scratch_root).resolve())

    @property
    def production_cfg(self) -> Config:
        """READS ONLY. Never passed to a framing/clip helper, and never to get_logger (which mkdirs)."""
        return Config(root=self.production_root)

    @property
    def scratch_cfg(self) -> Config:
        """The ONLY Config a helper ever receives.

        The FLAGS are @property reads of os.environ (smart_framing, aware_reframe, visual_start, burn_subs,
        clip_profile), so this INHERITS production's flag values — exactly what reconstruction requires.
        Pass root=; NEVER mutate FANOPS_ROOT (it is in conftest's _LEAKY_ENV for good reason)."""
        return Config(root=self.scratch_root)

    @property
    def production_clips(self) -> Path:
        return self.production_cfg.clips

    # ---- PRODUCTION reads: CONTENT accessors. They return content, never a writable Path. ----
    def read_stored_fingerprint(self, clip_id: str) -> str | None:
        try:
            d = json.loads((self.production_clips / f"{clip_id}.render.json").read_text())
            fp = d.get("fp")
            return fp if isinstance(fp, str) else None
        except (OSError, json.JSONDecodeError, ValueError, AttributeError):
            return None

    def read_ass_text(self, clip_id: str) -> str | None:
        """The .ass VERBATIM, or None when absent. Never normalized — it is hashed byte-for-byte."""
        try:
            return (self.production_clips / f"{clip_id}.ass").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def read_vstart(self, key: str) -> tuple[float, str] | None:
        """The historical visual-start DECISION, recovered from its sidecar with NO ffmpeg probe."""
        try:
            d = json.loads((self.production_clips / f"vstart_{key}.json").read_text())
            if d.get("v") != clipmod._VSTART_V:
                return None                                    # stale schema: the decision could have changed
            return float(d["start"]), str(d["kind"])
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError):
            return None

    def assert_write_target(self, p) -> None:
        """Raises unless `p` resolves under scratch_root. Called by every write this module performs."""
        rp = Path(p).resolve()
        if rp != self.scratch_root and not str(rp).startswith(str(self.scratch_root) + os.sep):
            raise ProductionWriteError(f"refusing to write outside the scratch root: {rp}")


def vstart_key(source_path: str, cs: float, ce: float) -> str:
    """clip.pick_visual_start's sidecar key, verbatim (clip.py:176)."""
    return hashlib.sha256(f"{source_path}|{round(cs, 3)}|{round(ce, 3)}".encode()).hexdigest()[:16]


def snapshot_ledger(paths: ReframePaths) -> Path:
    """A read-only SQLite .backup() of the live ledger INTO SCRATCH.

    Deliberately NOT Ledger.snapshot(): that writes its backup into 00_control — a PRODUCTION directory —
    and takes the ledger lock. We open the live DB `mode=ro` (so we cannot write it even by accident) and
    back it up to the scratch tree, which Ledger.load(scratch_cfg) then reads."""
    dest = paths.scratch_cfg.ledger_path
    paths.assert_write_target(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{paths.production_cfg.ledger_path}?mode=ro", uri=True)
    try:
        out = sqlite3.connect(str(dest))
        try:
            con.backup(out)
        finally:
            out.close()
    finally:
        con.close()
    return dest


# ---- Reconstruction ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    payload: dict
    labels: tuple            # a SET of provenance labels: several can collapse onto ONE payload


@dataclass(frozen=True)
class Reconstruction:
    proved: bool
    payload: dict | None
    labels: tuple            # the WHOLE label set of the winning payload — never one fabricated label
    tried: int
    matches: int


def _is_bare_clip(c) -> bool:
    """A bare (content-addressed) clip, vs an M4 STITCH whose id is caller-supplied.

    State-independent: render_moment computes `cid = clip_id if is_stitch else child_id(...)`, so a clip
    whose id IS its content address cannot be a stitch. A stitch takes cs/ce VERBATIM from its caller —
    nothing on disk records that window, so it is unreconstructible by construction."""
    return c.id == child_id("clip", c.parent_id, c.aspect.value)


def _window_candidates(paths: ReframePaths, cfg: Config, m, src) -> list:
    """The historical cut window. Pure recomputation (band -> fit -> snap), plus the visual-start
    refinement recovered from its PRODUCTION sidecar — the decision, not a re-probe."""
    band = band_for(clipmod._moment_profile(m, cfg))
    cs, ce = clipmod.fit_window(m.start, m.end, src.duration or 0.0, lo=band.lo, hi=band.hi)
    cs, ce = clipmod.snap_window(cs, ce, clipmod._trusted_transcript(src), duration=src.duration or 0.0)
    out = [((cs, ce), "window:band+snap")]
    if cfg.visual_start:
        v = paths.read_vstart(vstart_key(src.source_path, cs, ce))
        if v is not None:
            # kind == "transcript" means pick_visual_start kept the band+snap start, so this candidate
            # COLLAPSES onto the one above — one payload, two labels. That is recorded, not resolved.
            out.append(((v[0], ce), f"window:vstart[{v[1]}]"))
    return out


def _top_bias_candidates(m, cfg: Config) -> list:
    """_moment_top_bias is deterministic when the moment PINS it; only the cfg fallback is ambiguous."""
    if m.framing == "top":
        return [(True, "top_bias:moment=top")]
    if m.framing == "center":
        return [(False, "top_bias:moment=center")]
    return [(True, "top_bias:cfg=on"), (False, "top_bias:cfg=off")]      # historical cfg.aware_reframe unknown


def reconstruct(paths: ReframePaths, cfg: Config, led: Ledger, c, fp_stored: str) -> Reconstruction:
    """PROVE what this clip was rendered from, or admit we cannot.

    payload_old is the CENTERED payload — no focus, no track, no ct, no geom. That is the premise of the
    whole migration: these are clips a broken, absent, or switched-off detector centred. A clip that was
    rendered WITH a focus simply will not match, and lands in ALREADY_REFRAMED (if it matches the new
    payload) or UNRECONSTRUCTABLE (if it matches neither) — never in a guess."""
    m = led.moments[c.parent_id]
    src = led.sources[m.parent_id]

    ass_disk = paths.read_ass_text(c.id)
    ass_cands = []
    if ass_disk is not None:
        ass_cands.append((ass_disk, "ass:disk"))
    # D6, the stale-.ass trap: _subtitles_vf returns (None, False) when there is no hook and no opted-in
    # transcript, and it does NOT delete an existing {cid}.ass. render_moment then hashes ass_text="" WHILE
    # THE FILE STILL EXISTS. So "" is always a live candidate, file or no file. The hash adjudicates.
    ass_cands.append(("", "ass:empty"))

    by_bytes: dict = {}
    for win, wlab in _window_candidates(paths, cfg, m, src):
        for ass, alab in ass_cands:
            for tb, tlab in _top_bias_candidates(m, cfg):
                payload = clipmod._render_fingerprint_payload(
                    src.source_path, win[0], win[1], c.aspect.value, src.width or 0, src.height or 0,
                    ass, top_bias=tb, focus=None, track=None, content_type=None)
                key = clipmod.fingerprint_payload_bytes(payload)      # dedup on the EXACT bytes we hash
                if key in by_bytes:
                    prev = by_bytes[key]
                    by_bytes[key] = Candidate(prev.payload, prev.labels + (f"{wlab}|{alab}|{tlab}",))
                else:
                    by_bytes[key] = Candidate(payload, (f"{wlab}|{alab}|{tlab}",))

    hits = [cand for blob, cand in by_bytes.items()
            if hashlib.sha256(blob).hexdigest() == fp_stored]
    if len(hits) == 1:
        return Reconstruction(True, hits[0].payload, hits[0].labels, len(by_bytes), 1)
    return Reconstruction(False, None, (), len(by_bytes), len(hits))


def _seed_scratch_vstart(paths: ReframePaths, cfg: Config, m, src) -> None:
    """Copy the production visual-start DECISION into scratch so the new payload's window is the one a
    re-render would actually use — and so nothing re-probes ffmpeg for a decision already on disk.
    Read production, WRITE SCRATCH. The one direction that is allowed."""
    band = band_for(clipmod._moment_profile(m, cfg))
    cs, ce = clipmod.fit_window(m.start, m.end, src.duration or 0.0, lo=band.lo, hi=band.hi)
    cs, ce = clipmod.snap_window(cs, ce, clipmod._trusted_transcript(src), duration=src.duration or 0.0)
    key = vstart_key(src.source_path, cs, ce)
    v = paths.read_vstart(key)
    if v is None:
        return
    dest = cfg.clips / f"vstart_{key}.json"
    paths.assert_write_target(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"v": clipmod._VSTART_V, "start": v[0], "kind": v[1]}))


def current_payload(paths: ReframePaths, cfg: Config, led: Ledger, c):
    """What a re-render TODAY would hash: current window, current hook -> current .ass, current top_bias,
    and the NEW resolver's framing. Derived independently of which historical candidate won, so a
    collapsed provenance label can never leak into a classification.

    Returns (payload, FramingResolution)."""
    m = led.moments[c.parent_id]
    src = led.sources[m.parent_id]
    _seed_scratch_vstart(paths, cfg, m, src)

    band = band_for(clipmod._moment_profile(m, cfg))
    cs, ce = clipmod.fit_window(m.start, m.end, src.duration or 0.0, lo=band.lo, hi=band.hi)
    cs, ce = clipmod.snap_window(cs, ce, clipmod._trusted_transcript(src), duration=src.duration or 0.0)
    if cfg.visual_start:
        cs, _kind = clipmod.pick_visual_start(src.source_path, cs, ce, scene_peaks=src.signal_peaks,
                                              out_dir=cfg.clips)          # scratch out_dir; sidecar seeded above

    ass_text, _hbf = clipmod._build_ass_text(led, cfg, c.parent_id, c.id, c.aspect,
                                             clip_start=cs, clip_end=ce)   # PURE — no write
    res = framing._resolve(cfg, src, cs, ce, capture_failures=True)        # the dry-run seam
    payload = clipmod._render_fingerprint_payload(
        src.source_path, cs, ce, c.aspect.value, src.width or 0, src.height or 0, ass_text or "",
        top_bias=clipmod._moment_top_bias(m, cfg), focus=res.focus, track=res.track,
        content_type=res.content_type)
    return payload, res


def _delta_keys(old: dict, new: dict) -> list:
    return sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))


def _clip_has_remote_media(c) -> bool:
    return bool(getattr(c, "media_url", None))


def classify_clip(paths: ReframePaths, cfg: Config, led: Ledger, c) -> dict:
    """Classify ONE clip. Guards first — each can only DECLINE, never wrongly reframe."""
    m = led.moments.get(c.parent_id)
    row: dict = {"clip_id": c.id, "moment_id": c.parent_id, "aspect": c.aspect.value}
    if m is None:
        return {**row, "classification": ReframeClass.MISSING_INPUT.value, "reason": "moment absent from ledger"}
    src = led.sources.get(m.parent_id)

    # The supercut render can FAIL OPEN back to the envelope path, so a segmented moment may not actually
    # have been rendered as a supercut. Excluding it can only mean "we decline to reframe it" — never
    # "we wrongly reframe it". Conservative on purpose.
    if m.segments:
        return {**row, "classification": ReframeClass.SUPERCUT_EXCLUDED.value, "reason": "moment has segments"}
    if not _is_bare_clip(c):
        return {**row, "classification": ReframeClass.STITCH_EXCLUDED.value,
                "reason": "clip id is not its content address (M4 stitch): the cut window is unrecoverable"}
    if _clip_has_remote_media(c):
        return {**row, "classification": ReframeClass.REMOTE_ASSET_PRESENT.value, "reason": "clip.media_url set"}
    if src is None or not src.source_path:
        return {**row, "classification": ReframeClass.MISSING_INPUT.value, "reason": "source absent"}
    if not Path(src.source_path).exists():
        return {**row, "classification": ReframeClass.MISSING_INPUT.value, "reason": "source media absent on disk"}
    fp_stored = paths.read_stored_fingerprint(c.id)
    if fp_stored is None:
        return {**row, "classification": ReframeClass.MISSING_INPUT.value, "reason": "no {cid}.render.json"}

    rec = reconstruct(paths, cfg, led, c, fp_stored)
    payload_new, res = current_payload(paths, cfg, led, c)
    fp_new = clipmod.fingerprint_of_payload(payload_new)

    row.update({"fp_stored": fp_stored, "fp_new": fp_new,
                "reconstruction_proved": rec.proved, "candidate_payloads_tried": rec.tried,
                "winning_provenance_labels": list(rec.labels), "framing": res.to_json()})

    outcome = res.final_outcome
    if outcome not in RESOLVED_OUTCOMES | LEGITIMATE_CENTER_OUTCOMES | UNRESOLVED_OUTCOMES:
        raise UnknownFramingOutcome(str(outcome))              # reject, never silently map

    if outcome in UNRESOLVED_OUTCOMES:
        # We do not know WHY this clip is centred. Reframing it blind is exactly the mistake this tool
        # exists to prevent. Report the root cause and move on.
        return {**row, "classification": ReframeClass.FRAMING_UNRESOLVED.value,
                "reason": f"framing unresolved: {res.root_cause.value if res.root_cause else 'unknown'}"}
    if rec.matches >= 2:
        return {**row, "classification": ReframeClass.RECONSTRUCTION_AMBIGUOUS.value,
                "reason": f"{rec.matches} distinct payloads hash to fp_stored — refusing to pick one"}
    if not rec.proved:
        return {**row, "classification": ReframeClass.UNRECONSTRUCTABLE.value,
                "reason": f"no candidate of {rec.tried} reproduces fp_stored; cause unknown"}

    payload_old = rec.payload
    fp_old = clipmod.fingerprint_of_payload(payload_old)
    row["payload_old"] = payload_old
    row["payload_new"] = payload_new
    row["fp_old"] = fp_old

    if fp_stored == fp_old == fp_new:
        # Nothing would change. That is only a LEGITIMATE centre if the resolver AFFIRMATIVELY says the
        # room was empty — a centered strategy ALONE is not evidence of anything.
        if res.final_strategy is _FS.CENTERED and outcome is _FO.CENTERED_NO_SUBJECT:
            return {**row, "classification": ReframeClass.LEGITIMATE_CENTER.value,
                    "reason": "centred, and the detector ran to completion and found no subject"}
        return {**row, "classification": ReframeClass.FRAMING_UNRESOLVED.value,
                "reason": "unchanged fingerprint, but the centre is not affirmatively evidenced"}
    if fp_stored == fp_new:
        return {**row, "classification": ReframeClass.ALREADY_REFRAMED.value, "reason": "stored == new"}

    dk = _delta_keys(payload_old, payload_new)
    row["payload_delta"] = dk                                  # old -> new. NEVER vs stored: stored has no payload.
    row["delta_keys_ok"] = set(dk) <= APPROVED_FRAMING_KEYS
    if not row["delta_keys_ok"]:
        return {**row, "classification": ReframeClass.DRIFT.value,
                "reason": f"a re-render would also change {sorted(set(dk) - APPROVED_FRAMING_KEYS)}"}
    return {**row, "classification": ReframeClass.ELIGIBLE.value,
            "reason": f"proven-centred; a reframe changes only {dk}"}


# ---- Protected-root verification --------------------------------------------------------------------

def scan_tree(root: Path, *, exclude_prefixes=()) -> dict:
    """Existence, type, size, mode, mtime_ns, inode, content hash, symlink target (NEVER followed out of
    the tree). This is the PROOF that the dry-run wrote nothing to production — not the mechanism."""
    out: dict = {}
    root = Path(root)
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        sp = str(p)
        if any(sp.startswith(str(x)) for x in exclude_prefixes):
            continue
        st = p.lstat()
        if stat.S_ISLNK(st.st_mode):
            out[sp] = ("lnk", st.st_mode, st.st_mtime_ns, st.st_ino, os.readlink(sp))
        elif p.is_dir():
            out[sp] = ("dir", st.st_mode, st.st_mtime_ns, st.st_ino, None)
        else:
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            out[sp] = ("file", st.st_mode, st.st_mtime_ns, st.st_ino, st.st_size, h)
    return out


def diff_tree(before: dict, after: dict) -> dict:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    return {"added": added, "removed": removed, "changed": changed}


def _snapshot_diff_ok(d: dict, prod_cfg: Config) -> bool:
    """The snapshot phase may touch EXACTLY two things and nothing else: SQLite's own `-wal` / `-shm`
    coordination sidecars beside the live ledger, and the mtime of the directory that now contains them.

    Anything else — a real file, a clip, a sidecar — means the snapshot mutated production, and the run
    is void. This bound is what keeps 'the snapshot phase may write' from becoming a blanket excuse."""
    control = str(prod_cfg.control)
    ledger = str(prod_cfg.ledger_path)
    for k in d["added"] + d["removed"] + d["changed"]:
        if k in (ledger + "-wal", ledger + "-shm"):
            continue
        if k == control:                      # its mtime moves when SQLite creates a child in it
            continue
        return False
    return True


# ---- The driver -------------------------------------------------------------------------------------

def run_dry_run(paths: ReframePaths, *, limit: int | None = None, argv=None, now_ts: float | None = None) -> dict:
    """Classify the corpus. Returns the manifest. WRITES NOTHING OUTSIDE `paths.scratch_root`.

    The analysis-phase diff MUST come back empty. If it does not, the run has failed at the one thing it
    promised, and the manifest says so rather than burying it in a summary."""
    cfg = paths.scratch_cfg                                    # get_logger MKDIRS cfg.reports -> must be scratch
    t0 = time.monotonic()

    # TWO diffs, and the distinction is honest rather than convenient.
    #
    # Reading a WAL database — even `mode=ro` — makes SQLite create its `-wal`/`-shm` coordination files.
    # That is unavoidable: the alternative (`immutable=1`) tells SQLite the file cannot change, which is a
    # LIE while the daemon may be writing, and would let us read a torn state. So the snapshot phase MAY
    # touch production, and we DISCLOSE exactly what it touched instead of scanning after the fact and
    # quietly calling it clean. It is bounded: SQLite's own sidecars and nothing else (_snapshot_diff_ok).
    #
    # The ANALYSIS phase — where the framing pass runs, and where a stray sidecar, lockfile, stamp_stage
    # manifest, keyframe jpg or vstart sidecar would land — MUST be empty. That is the real invariant.
    pre = scan_tree(paths.production_root)
    snapshot_ledger(paths)
    snap_scan = scan_tree(paths.production_root)
    snapshot_diff = diff_tree(pre, snap_scan)
    snapshot_ok = _snapshot_diff_ok(snapshot_diff, paths.production_cfg)
    t_scan = time.monotonic() - t0

    led = Ledger.load(cfg)
    clips = [c for c in led.clips.values()]
    clips.sort(key=lambda c: c.id)
    partial = limit is not None and limit < len(clips)
    if limit is not None:
        clips = clips[:limit]

    rows: list = []
    t1 = time.monotonic()
    for c in clips:
        try:
            rows.append(classify_clip(paths, cfg, led, c))
        except ProductionWriteError:
            raise                                             # NEVER swallowed: the one unforgivable failure
        except UnknownFramingOutcome:
            raise                                             # a new outcome must be classified, not defaulted
        except Exception as exc:                              # one bad clip must not abort the corpus scan
            get_logger(cfg)("reframe", c.id, "classify_failed",
                            reason=f"{type(exc).__name__}: {str(exc)[:180]}")
            rows.append({"clip_id": c.id, "moment_id": c.parent_id, "aspect": c.aspect.value,
                         "classification": ReframeClass.ERROR.value, "reason": type(exc).__name__})
    t_classify = time.monotonic() - t1

    after = scan_tree(paths.production_root)
    analysis_diff = diff_tree(snap_scan, after)
    clean = not (analysis_diff["added"] or analysis_diff["removed"] or analysis_diff["changed"])

    totals: dict = {}
    for r in rows:
        totals[r["classification"]] = totals.get(r["classification"], 0) + 1
    unresolved_by_cause: dict = {}
    degraded = 0
    for r in rows:
        f = r.get("framing") or {}
        if f.get("degraded_strategies"):
            degraded += 1
        if r["classification"] == ReframeClass.FRAMING_UNRESOLVED.value:
            k = (f.get("root_cause") or "unknown")
            unresolved_by_cause[k] = unresolved_by_cause.get(k, 0) + 1

    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "argv": list(argv or []),
        "partial": partial,
        "attribution": attribution(cfg),
        "protected_root": str(paths.production_root),
        "scratch_root": str(paths.scratch_root),
        # DISCLOSED, not hidden: SQLite's WAL coordination when we opened the live ledger read-only.
        "snapshot_phase_diff": snapshot_diff,
        "snapshot_phase_sqlite_only": snapshot_ok,
        # THE invariant. Anything here means the framing pass wrote to the live tree.
        "analysis_phase_diff": analysis_diff,
        "analysis_phase_clean": clean and snapshot_ok,
        "scan_durations_s": {"snapshot_and_scan": round(t_scan, 3), "classify": round(t_classify, 3)},
        "clips": rows,
        "summary": {
            "totals": totals,
            "framing_unresolved_by_root_cause": unresolved_by_cause,
            "unreconstructable": totals.get(ReframeClass.UNRECONSTRUCTABLE.value, 0),
            "reconstruction_ambiguous": totals.get(ReframeClass.RECONSTRUCTION_AMBIGUOUS.value, 0),
            "degraded": degraded,
            # ELIGIBLE / go_no_go are STRUCTURAL verdicts only. No clip in this run has been visually
            # reviewed, so nothing here asserts a reframe would LOOK good — that is a separate required gate.
            "visual_review_status": "unreviewed",
            # A PARTIAL run cannot support a corpus-wide claim, so it does not get to make one.
            "go_no_go": None if partial else _go_no_go(totals, clean),
        },
    }


def _go_no_go(totals: dict, clean: bool) -> dict:
    blockers = []
    if not clean:
        blockers.append("the analysis phase mutated the protected root")
    for k in (ReframeClass.UNRECONSTRUCTABLE, ReframeClass.RECONSTRUCTION_AMBIGUOUS, ReframeClass.ERROR):
        if totals.get(k.value):
            blockers.append(f"{totals[k.value]} × {k.value}")
    # An empty `blockers` means "reframing this corpus cannot corrupt non-framing state" — a STRUCTURAL
    # clearance, NOT "the reframes look good". Visual review is a separate gate this scan never performs.
    return {"eligible": totals.get(ReframeClass.ELIGIBLE.value, 0), "blockers": blockers,
            "visual_review": "REQUIRED — eligibility is structural, not a visual-quality verdict"}


def attribution(cfg: Config) -> dict:
    """Everything that could change a fingerprint or a detection, stamped. A VERSION is never a substitute
    for a SHA — __init__.py says 0.3.0 while pyproject says 0.4.0, so the stamp has already drifted once."""
    import subprocess
    repo = Path(__file__).resolve().parents[2]

    def _git(*a):
        try:
            return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True,
                                  check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    cv2_v = getattr(framing._cv2(), "__version__", None)       # reuse the lazy import that already fails open
    try:
        import numpy
        numpy_v = numpy.__version__
    except ImportError:
        numpy_v = None
    model = framing._model_path()
    model_sha = hashlib.sha256(model.read_bytes()).hexdigest() if model.exists() else None
    return {
        "git_commit_sha": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        # COMPUTED, never hardcoded: a pinned commit rots the moment the payload changes again.
        "fingerprint_last_changed_commit": (_git("log", "-1", "--format=%h", "-L",
                                                 "619,645:src/fanops/clip.py") or "").splitlines()[:1],
        "cv2_version": cv2_v, "numpy_version": numpy_v,
        "yunet_model_path": model.name, "yunet_model_sha256": model_sha,
        "reframe_geom_v": clipmod._REFRAME_GEOM_V, "vstart_cache_v": clipmod._VSTART_V,
        "detect_cache_v": framing._DETECT_V, "sidecar_v": framing._SIDECAR_V,
        # These enter the payload: a flipped flag drifts EVERY clip.
        "smart_framing": cfg.smart_framing, "aware_reframe": cfg.aware_reframe,
        "visual_start": cfg.visual_start, "burn_subs": cfg.burn_subs, "clip_profile": cfg.clip_profile,
    }
