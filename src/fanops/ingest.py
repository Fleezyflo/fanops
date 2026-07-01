# src/fanops/ingest.py
"""Ingest Moh's OWN videos: drop (01_inbox), url (yt-dlp), local scan. Identity is the
CONTENT sha256 (FIX F35). Probe width/height/duration at ingest for safe reframe (FIX F68).
Exclude PII/legal/financial by name — necessary but NOT sufficient (FIX F46): a private file
misnamed slips through; a human still reviews held/odd clips before posting."""
from __future__ import annotations
import hashlib, os, re, shutil, subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.models import Source, SourceState
from fanops.ids import make_id
from fanops.timeutil import iso_z
from fanops.errors import ToolchainMissingError, DownloadError

MEDIA_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi",
             ".jpg", ".jpeg", ".png", ".heic", ".mp3", ".wav", ".m4a"}
_PII = re.compile(r"passport|\bid\b|\bvisa\b|licen[cs]e|agreement|contract|invoice|"
                  r"\bnda\b|tax|bank|ssn|emirates.?id|national.?id", re.IGNORECASE)

def is_excluded(name: str) -> bool:
    return bool(_PII.search(name))

_ARCHIVE_NAME = ".ingested"   # per-inbox archive subdir: a DISPOSED drop moves here so steady-state inbox is empty (ING-1)
_PARTIAL_EXT = {".uploadpart", ".part"}   # leaked stream temps (Studio upload / yt-dlp) — sweep on ingest start (ING-10)
_PULL_STAGE = ".pull"   # per-pull download staging (peer of .ingested under the inbox): isolates a pull's yt-dlp
                        # output from any manual drop, so ingest_drops(inbox=stage) catalogues ONLY the pull (ING-6/12)

@dataclass
class IngestCounts:
    """This-pass tally (ING-2/ING-5): the delta the operator actually sees, never the cumulative library size."""
    added: int = 0          # newly catalogued THIS pass (the delta)
    deduped: int = 0        # archived because already known (a re-drop)
    excluded: int = 0       # PII/legal name-filtered
    skipped: int = 0        # audio-only / copy-failed / unverifiable

def _archive_dir(inbox: Path) -> Path:
    return inbox / _ARCHIVE_NAME

def _pull_stage(cfg: Config) -> Path:
    d = cfg.inbox / _PULL_STAGE; d.mkdir(parents=True, exist_ok=True); return d

def _archive_inbox_file(inbox: Path, f: Path, cfg: Config) -> None:
    """Move a DISPOSED inbox file (catalogued, dedup-matched, excluded, or audio-only-skipped) out of the scan
    domain so the next pass re-hashes nothing already handled (ING-1 root). Same-fs os.replace -> atomic; a name
    collision in the archive (a re-drop of an old name) is disambiguated by an mtime-ns suffix so a second drop
    never clobbers the first archived copy. Fail-open: an archive that can't move (perms/cross-device) leaves the
    file in place + a breadcrumb — worst case is today's behavior (re-hash next pass), never a lost original."""
    adir = _archive_dir(inbox); adir.mkdir(parents=True, exist_ok=True)
    dest = adir / f.name
    if dest.exists():
        dest = adir / f"{f.stem}.{int(f.stat().st_mtime_ns)}{f.suffix}"   # never clobber an earlier archived original
    try:
        os.replace(f, dest)
    except OSError as e:
        get_logger(cfg)("ingest", f.name, "archive_failed", why=str(e)[:120])   # left in inbox; re-tried next pass

def _sweep_partials(inbox: Path, cfg: Config) -> None:
    """Delete leaked *.uploadpart / *.part temps (a crashed Studio upload / killed yt-dlp) BEFORE the scan
    (ING-10). They are not in MEDIA_EXT so they were never ingested, but they accumulate; clear them each pass.
    A temp that won't unlink (perms/race) is a labeled breadcrumb, never a swallowed error or a pass abort."""
    for f in inbox.glob("*"):
        if f.is_file() and not f.is_symlink() and f.suffix.lower() in _PARTIAL_EXT:
            try:
                f.unlink()
            except OSError as e:
                get_logger(cfg)("ingest", f.name, "sweep_failed", why=str(e)[:120])

def _reprobe_degraded(led: Ledger, cfg: Config) -> None:
    """Re-probe sources catalogued with degraded_reason='probe_failed' (a transient ffprobe timeout / stuck
    mount at first ingest left them 0×0). sha-dedup never revisits a known source, so without this they stay
    frozen forever (ING-7). Cheap + bounded: only the degraded rows, only if their file still exists; a probe
    that now succeeds fills real dimensions + clears the flag, a still-failing probe leaves it for a later pass."""
    for sid, s in list(led.sources.items()):
        if s.degraded_reason != "probe_failed": continue
        p = Path(s.source_path)
        if not p.exists(): continue
        w, h, dur = probe_dimensions(p)
        if w and h:                                                 # the probe recovered
            led.sources[sid] = s.model_copy(update={
                "width": w, "height": h, "duration": dur or None, "degraded_reason": None})
            get_logger(cfg)("ingest", sid, "reprobe_ok", width=w, height=h)

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

# Hard bounds (the llm.py timeout idiom). ffprobe is a sub-second metadata read — a hang means a
# corrupt file or stuck mount, and ingest runs INSIDE advance()'s transaction with no per-unit
# quarantine, so it must fail soft per file, fast. yt-dlp is a full network download (NO lock
# held — see download_url) but `fanops pull` still must not hang forever on a dead CDN.
_FFPROBE_TIMEOUT = 30.0
_YTDLP_TIMEOUT = 600.0

def _run_ffprobe(args: list[str]) -> subprocess.CompletedProcess:
    """Run ffprobe, translating a PRE-LAUNCH FileNotFoundError/OSError (ffprobe absent from PATH)
    into a typed, cli-catchable ToolchainMissingError. `check=False`-style: a nonzero ffprobe
    RETURNCODE is NOT an error here (callers interpret stdout, defaulting to 0/False) — only the
    binary being ABSENT is. This runs at ingest, OUTSIDE the pipeline's per-unit quarantine, so an
    uncaught raise would crash `fanops advance` with a traceback; the typed error -> clean exit 2."""
    try:
        return subprocess.run(["ffprobe", *args], capture_output=True, text=True,
                              timeout=_FFPROBE_TIMEOUT)
    except (FileNotFoundError, OSError) as e:
        raise ToolchainMissingError(
            "ffprobe not found on PATH — install ffmpeg (it provides ffprobe) to ingest media "
            f"({type(e).__name__})") from e
    except subprocess.TimeoutExpired:
        # PER-FILE hang (corrupt media, stuck mount) — NOT the binary-absent case: raising here
        # would abort the whole ingest pass and roll back the transaction over one bad file.
        # Fail SOFT with an empty result instead: probe_dimensions -> zeros (its documented
        # failure shape), has_video_stream -> False — the file stays in the inbox and is retried
        # next pass, bounded each time, never a crash or a dropped pass.
        return subprocess.CompletedProcess(["ffprobe", *args], returncode=124,
                                           stdout="", stderr="ffprobe timed out")

def probe_dimensions(path: Path) -> tuple[int, int, float]:
    """(width, height, duration_seconds) via ffprobe; zeros on failure (ffprobe ABSENT raises
    ToolchainMissingError — see _run_ffprobe — rather than masquerading as a 0×0 source)."""
    r = _run_ffprobe(
        ["-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    vals = [x for x in r.stdout.split() if x]
    try:
        w = int(float(vals[0])); h = int(float(vals[1])); dur = float(vals[2])
        return w, h, dur
    except (IndexError, ValueError):
        return 0, 0, 0.0

def has_video_stream(path: Path) -> bool:
    """True if the file carries a decodable video stream (a still image counts — it has a
    video-type stream). Audio-only files (.wav/.mp3/.m4a with no picture) return False. Used
    to keep audio-only drops out of the clip pipeline: ffmpeg's reframe -vf is silently
    ignored on an audio-only input, so without this guard the renderer emits a *videoless*
    'clip' (audio masquerading as a 9:16 post) — a real data-integrity bug confirmed on
    ffmpeg 8.0.1. Audio extensions stay in MEDIA_EXT for a future audiogram path; they just
    aren't catalogued as clip sources today. ffprobe ABSENT raises ToolchainMissingError (via
    _run_ffprobe) — we must NOT return False on a missing binary, which would silently DROP a
    real video as if it were audio-only."""
    r = _run_ffprobe(
        ["-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)])
    # `csv=p=0` emits "video," (trailing empty field) on some HEVC .mov muxings — exact `== "video"`
    # would then read it as audio-only and silently DROP a real clip. Token-match instead: True iff a
    # "video" codec_type appears among the comma/space-separated fields; empty stdout -> still False.
    return "video" in r.stdout.replace(",", " ").split()

def _catalogue_file(led: Ledger, cfg: Config, f: Path, *, origin: str, now_iso: str,
                    origin_kind: Literal["native", "third_party"] = "native",
                    batch_id: str | None = None) -> bool:
    """Catalogue ONE file as a Source (content-addressed, deduped, probed) — the single spine shared by
    the native drop/url scan and the third-party intake; the caller sets origin_kind. Same bytes already
    seen under a DIFFERENT origin_kind = a conflict: keep the first (origin_kind is WRITE-ONCE), surface
    it via a visible log line, never silently flip native<->third_party. batch_id is likewise WRITE-ONCE
    (the prior batch wins); a re-drop under a different batch is logged, never silently re-stamped.

    Returns True if the file was DISPOSED (catalogued OR dedup-matched) and may be archived; False if a
    fallible step (copy ENOSPC) left it un-catalogued, so the caller leaves it in the inbox for a retry."""
    digest = sha256_of(f)
    if led.already_seen(sha256=digest):
        prior = next((s for s in led.sources.values() if s.sha256 == digest), None)
        if prior is not None and prior.origin_kind != origin_kind:   # dedup-suppressed an upload — make it visible
            get_logger(cfg)("ingest", prior.id, "origin_conflict", want=origin_kind, have=prior.origin_kind)
        if prior is not None and batch_id and prior.batch_id != batch_id:   # re-drop under a different batch
            get_logger(cfg)("ingest", prior.id, "batch_conflict", want=batch_id, have=prior.batch_id)
        if prior is not None and prior.source_origin != origin:      # same bytes re-encountered from a DIFFERENT origin (drop vs url vs scan) — write-once keeps the first; surface the alternate so provenance isn't silently dropped
            get_logger(cfg)("ingest", prior.id, "origin_path_conflict", want=origin, have=prior.source_origin)
        return True                                                  # already known → safe to archive the inbox copy
    sid = make_id("src", digest)                  # identity = content, not path
    dest = cfg.sources / f"{sid}{f.suffix.lower()}"
    if not dest.exists():
        try:
            shutil.copy2(f, dest)
        except OSError as e:                                         # ENOSPC / perms: a PER-FILE skip, NOT a pass rollback
            get_logger(cfg)("ingest", f.name, "copy_failed", why=str(e)[:120]); return False
    w, h, dur = probe_dimensions(dest)
    degraded = "probe_failed" if (w == 0 or h == 0) else None        # ING-7: a 0×0 probe is degraded, re-probed next pass
    led.add_source(Source(id=sid, state=SourceState.catalogued, source_path=str(dest),
                          source_origin=origin, origin_kind=origin_kind, sha256=digest, width=w, height=h,
                          duration=dur or None, created_at=now_iso, degraded_reason=degraded,  # ingest-day anchor (aware)
                          batch_id=batch_id, meta={"bytes": f.stat().st_size}))   # AUDIT: no original_name (PII)
    return True

def _inbox_media(inbox: Path) -> set[Path]:
    """Resolved paths of the media files currently in `inbox` — the snapshot domain for per-file origin
    correlation (audit c0-f1). Mirrors ingest_drops' own symlink/MEDIA_EXT filter so a before/after delta
    is apples-to-apples; deliberately skips the has_video_stream probe (a subprocess) — the delta only needs
    to identify which paths are NEW, and a non-video new file is dropped at ingest anyway."""
    if not inbox.exists(): return set()
    return {f.resolve() for f in inbox.rglob("*")
            if not f.is_symlink() and f.is_file() and f.suffix.lower() in MEDIA_EXT}

def ingest_drops(led: Ledger, cfg: Config, *, origin: str = "drop",
                 origin_kind: Literal["native", "third_party"] = "native",
                 inbox: Path | None = None, batch_id: str | None = None,
                 origin_paths: set[Path] | None = None) -> tuple[Ledger, IngestCounts]:
    """Catalogue every NEW media drop in `inbox` (default cfg.inbox), then ARCHIVE each disposed file out of
    the scan domain (ING-1 root): steady-state the inbox empties, so a later pass re-hashes nothing already
    handled. Returns (led, IngestCounts) — the this-pass delta the caller reports (ING-2), never the cumulative
    library size. Fail-soft per file; the OFF/legacy contract is preserved (no batch ⇒ batch_id=None, clean
    probe ⇒ no degraded_reason)."""
    cfg.sources.mkdir(parents=True, exist_ok=True)
    box = (inbox or cfg.inbox); box.mkdir(parents=True, exist_ok=True)   # inbox= lets third-party scan its own staging dir
    _sweep_partials(box, cfg)                                            # ING-10: clear leaked *.uploadpart/*.part first
    _reprobe_degraded(led, cfg)                                          # ING-7: retry sources frozen at 0×0 on a bad probe
    now_iso = iso_z(datetime.now(timezone.utc))                         # ING-11: ONE clock read per pass, threaded below
    counts = IngestCounts()
    # Root contract: every catalogued Source MUST carry a real batch_id, so every downstream Post does
    # too (no more Studio Review "Ungrouped" group). Auto-resolve a day-stable drop-batch on the lazy
    # path — minted on the FIRST file we actually catalogue this pass (mint-on-demand: an empty inbox
    # leaves the ledger untouched). The caller-supplied batch_id (Studio "Add video" named batch) wins.
    _auto_batch_id: str | None = None
    archive = _archive_dir(box).resolve()                              # NEVER re-scan the archive (it lives under the inbox)
    for f in sorted(box.rglob("*")):
        # ECC fix #9: skip symlinks BEFORE any probe/copy. f.is_file() follows links, and the copy2
        # below would dereference a symlink and ingest a file from OUTSIDE the inbox (a zip-extracted
        # or hand-placed link escaping the data boundary). Content-addressing can't undo that.
        if f.is_symlink() or not f.is_file() or f.name == ".gitkeep" or f.suffix.lower() not in MEDIA_EXT:
            continue
        if archive in f.resolve().parents:                            # skip already-archived drops (ING-1: bound the scan)
            continue
        if is_excluded(f.name):
            counts.excluded += 1; get_logger(cfg)("ingest", f.name, "pii_excluded")   # ING-5: visible, not silent
            _archive_inbox_file(box, f, cfg); continue                # archive so it doesn't re-trigger the cost
        if not has_video_stream(f):
            counts.skipped += 1; get_logger(cfg)("ingest", f.name, "skipped", why="no_video_stream")
            _archive_inbox_file(box, f, cfg); continue                # audio-only: not a clip source (FIX); archive it too
        # source_origin is correlated PER-FILE to the actual download (audit c0-f1): when origin_paths is
        # given (a url pull names the files yt-dlp just produced), ONLY those carry the pull `origin`; any
        # pre-existing inbox file (a manual drop awaiting `ingest`, or a prior pull's leftover) keeps the
        # "drop" default instead of being pass-wide mislabeled as this pull's origin. origin_paths=None
        # (a plain `ingest` / third-party scan) is byte-identical: every file gets the pass `origin`.
        file_origin = origin if (origin_paths is None or f.resolve() in origin_paths) else "drop"
        n_before = len(led.sources)
        # Lazy resolve: only mint the drop-batch on the FIRST file we'll actually try to catalogue this
        # pass (empty inbox => no batch litter; idempotent on the day, so a later pass reuses it).
        if batch_id is None and _auto_batch_id is None:
            from fanops.batches import resolve_or_mint_drop_batch
            _auto_batch_id = resolve_or_mint_drop_batch(led).id
        effective_batch_id = batch_id or _auto_batch_id
        disposed = _catalogue_file(led, cfg, f, origin=file_origin, now_iso=now_iso,
                                   origin_kind=origin_kind, batch_id=effective_batch_id)
        if not disposed:                                              # copy failed → leave in inbox for a next-pass retry
            counts.skipped += 1; continue
        if len(led.sources) > n_before: counts.added += 1             # newly minted
        else: counts.deduped += 1                                     # dedup-matched a known source
        _archive_inbox_file(box, f, cfg)                              # ING-1: out of the scan domain
    return led, counts

def download_url(cfg: Config, url: str) -> set[Path]:
    """Network-only half of a URL pull: shell yt-dlp to drop the media into the inbox. Holds NO
    ledger lock — the slow download must run OUTSIDE the ledger transaction (cmd_pull then ingests
    what landed inside a tight transaction), so a download never serializes behind the ledger flock
    (the Phase-B-followup lost-update + no-network-under-lock rule). yt-dlp ABSENT from PATH:
    subprocess.run raises before the process starts (check=False covers only a nonzero RETURNCODE) —
    surface the typed, cli-catchable ToolchainMissingError (-> clean exit 2 + 'install yt-dlp').
    A HUNG download is killed at _YTDLP_TIMEOUT; the TimeoutExpired propagates BY DESIGN to
    cli.main's guard (one clean stderr line + exit 2). yt-dlp's partial *.part file is ignored by
    ingest (not in MEDIA_EXT), so a killed download never catalogues a truncated source.

    RETURNS the resolved media paths THIS download produced (a before/after snapshot of an ISOLATED per-pull
    staging dir — cfg.inbox/.pull, NOT the shared inbox — audit c0-f1 / ING-6 / ING-12): a concurrent manual
    drop in the inbox is never in the stage, so it can never be conflated with this pull. cmd_pull threads the
    produced set into ingest_drops(origin="url", inbox=stage, origin_paths=...). Snapshot-diff is deliberate
    over parsing yt-dlp stdout — version-independent and robust to the merge/post-process rename."""
    stage = _pull_stage(cfg)
    before = _inbox_media(stage)
    try:
        r = subprocess.run(["yt-dlp", "-o", str(stage / "%(title).80s.%(ext)s"),
                            "--no-playlist", "--merge-output-format", "mp4", url],
                           check=False, capture_output=True, text=True, timeout=_YTDLP_TIMEOUT)
    except (FileNotFoundError, OSError) as e:
        raise ToolchainMissingError(
            f"yt-dlp not found on PATH — install yt-dlp to pull from a URL ({type(e).__name__})") from e
    if r.returncode != 0:
        # yt-dlp RAN but failed (dead/geoblocked URL, format gone). check=False only covers the
        # binary-absent case above; without this the rc+stderr were discarded and `pull` silently
        # ingested nothing, printing "pulled -> 0 sources" as success (audit silent-failure). Surface
        # the typed DownloadError with the stderr tail -> cli.main: one clean line + exit 2.
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        why = tail[-1][:200] if tail else f"exit {r.returncode}"
        raise DownloadError(f"yt-dlp failed (exit {r.returncode}): {why}")
    return _inbox_media(stage) - before          # the media files THIS pull produced, in its isolated stage


def download_source(led: Ledger, cfg: Config, url: str) -> Ledger:
    """Download + ingest in one call (kept for any direct caller/test). The CLI's `pull` command
    splits these (download outside the lock, ingest inside a transaction) — see cli.cmd_pull."""
    produced = download_url(cfg, url)
    led, _ = ingest_drops(led, cfg, origin="url", inbox=_pull_stage(cfg), origin_paths=produced)
    return led

def scan_local(roots: list[Path]) -> list[str]:
    out: list[str] = []
    for root in roots:
        for f in Path(root).rglob("*"):
            if f.is_symlink():     # ECC fix #9: don't surface links that escape the scanned root
                continue
            if f.is_file() and f.suffix.lower() in MEDIA_EXT and not is_excluded(f.name):
                out.append(str(f))
    return sorted(out)
