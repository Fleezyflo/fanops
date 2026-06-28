# tests/test_ingest.py
import json
import subprocess
import pytest
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import SourceState
from fanops.errors import ToolchainMissingError
from fanops.ingest import (ingest_drops, sha256_of, is_excluded, scan_local, probe_dimensions,
                           has_video_stream, download_source, download_url)

def _put(p, b):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b)

def test_sha256_stable(tmp_path):
    f = tmp_path / "a.bin"; f.write_bytes(b"hi")
    assert sha256_of(f) == sha256_of(f)

def test_ingest_drops_skips_symlinks(tmp_path, mocker):
    # ECC fix #9: a symlink in the inbox must NOT be dereferenced + ingested (it could point to a
    # file outside the data boundary). It is skipped BEFORE any ffprobe/copy, so this never touches
    # the toolchain — a spy on has_video_stream proves the symlink short-circuited earlier.
    cfg = Config(root=tmp_path)
    outside = tmp_path / "outside" / "secret.mp4"; _put(outside, b"OUTSIDE")
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    (cfg.inbox / "link.mp4").symlink_to(outside)
    spy = mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 0, "a symlinked inbox entry was ingested — links must be skipped"
    spy.assert_not_called()                    # skipped before the video-stream probe

def test_ingest_raises_clean_toolchain_error_when_ffprobe_absent(tmp_path, mocker):
    # ffprobe off PATH -> subprocess.run raises FileNotFoundError before the process starts.
    # ingest_drops runs OUTSIDE the pipeline's per-unit quarantine, so without a guard this
    # crashes `fanops advance` with a raw traceback + exit 1. ffprobe-at-ingest is an operator
    # config error (install ffmpeg), NOT a per-unit failure to record and NOT something to
    # silently skip (skipping would DROP a real video) — so it must raise the typed,
    # cli-catchable ToolchainMissingError naming the missing binary, never a bare FileNotFoundError.
    cfg = Config(root=tmp_path); _put(cfg.inbox / "a.mp4", b"V")
    def absent(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])
    mocker.patch("fanops.ingest.subprocess.run", side_effect=absent)
    with pytest.raises(ToolchainMissingError, match="ffprobe"):
        ingest_drops(Ledger.load(cfg), cfg)

def test_has_video_stream_raises_clean_toolchain_error_when_ffprobe_absent(tmp_path, mocker):
    # The guard lives at the subprocess call site, so the lower-level helper raises too (not just
    # the ingest_drops loop) — proves there's no unguarded ffprobe path.
    def absent(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])
    mocker.patch("fanops.ingest.subprocess.run", side_effect=absent)
    with pytest.raises(ToolchainMissingError, match="ffprobe"):
        has_video_stream(tmp_path / "a.mp4")

def test_download_source_raises_clean_toolchain_error_when_ytdlp_absent(tmp_path, mocker):
    # yt-dlp off PATH -> FileNotFoundError before the process starts. download_source backs the
    # one-shot `fanops pull <url>` command (pre-Source, outside any quarantine), so without a guard
    # it crashes `pull` with a traceback. yt-dlp absent is an operator config error -> typed
    # ToolchainMissingError naming yt-dlp -> cli.main exit 2, never a bare FileNotFoundError.
    cfg = Config(root=tmp_path)
    def absent(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])
    mocker.patch("fanops.ingest.subprocess.run", side_effect=absent)
    with pytest.raises(ToolchainMissingError, match="yt-dlp"):
        download_source(Ledger.load(cfg), cfg, "https://example.com/v")

def test_probe_timeout_is_per_file_fail_soft(tmp_path, mocker):
    # A PER-FILE ffprobe hang (corrupt media, stuck mount) is NOT the binary-absent case above:
    # ingest_drops runs outside the per-unit quarantine, INSIDE advance()'s transaction, so a raise
    # would abort the whole pass and roll back its committed transitions over one bad file. Bound
    # the probe and fail SOFT per file: probe_dimensions -> zeros (its documented failure shape),
    # has_video_stream -> False; the file stays in the inbox and is retried next pass — bounded
    # every time, never a crash, never a dropped pass.
    seen = {}
    def hung(cmd, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
    mocker.patch("fanops.ingest.subprocess.run", side_effect=hung)
    assert probe_dimensions(tmp_path / "a.mp4") == (0, 0, 0.0)
    assert has_video_stream(tmp_path / "a.mp4") is False
    assert seen.get("timeout") == 30.0                                # the bound is actually wired

def test_has_video_stream_tolerates_trailing_csv_comma(tmp_path, mocker):
    # ffprobe `-of csv=p=0` emits "video," (a trailing empty field) on some HEVC .mov muxings — a
    # REAL case from real footage (two clips were silently dropped). An exact `== "video"` check
    # then reads "video," != "video" and DROPS a genuine video as audio-only — the exact data-loss
    # this guard exists to prevent, inverted. Parse the codec_type token robustly, not by equality.
    cp = subprocess.CompletedProcess(["ffprobe"], 0, stdout="video,\n", stderr="")
    mocker.patch("fanops.ingest.subprocess.run", return_value=cp)
    assert has_video_stream(tmp_path / "a.mov") is True

def test_has_video_stream_still_false_for_audio_only(tmp_path, mocker):
    # The robust parse must NOT regress the audio-only drop: `-select_streams v:0` matches nothing,
    # ffprobe prints an empty stdout -> still False (audio masquerading as a 9:16 clip stays out).
    cp = subprocess.CompletedProcess(["ffprobe"], 0, stdout="\n", stderr="")
    mocker.patch("fanops.ingest.subprocess.run", return_value=cp)
    assert has_video_stream(tmp_path / "a.m4a") is False

def test_download_url_is_time_bounded(tmp_path, mocker):
    # yt-dlp gets a hard bound too. It holds NO ledger lock (download runs outside the
    # transaction by design), but `fanops pull` must not hang forever on a dead CDN. The raise
    # propagates by design; cli.main turns it into one clean stderr line + exit 2 (test_cli).
    cfg = Config(root=tmp_path)
    seen = {}
    def hung(cmd, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
    mocker.patch("fanops.ingest.subprocess.run", side_effect=hung)
    with pytest.raises(subprocess.TimeoutExpired):
        download_url(cfg, "https://example.com/v")
    assert seen.get("timeout") == 600.0                               # the bound is actually wired

def test_download_url_surfaces_ytdlp_failure(tmp_path, mocker):
    # A dead/geoblocked/format-gone URL: yt-dlp RUNS but exits non-zero with a stderr reason. Today
    # the returncode and stderr are DISCARDED (check=False + result ignored) -> download_url returns
    # None and cmd_pull goes on to ingest an empty inbox, printing "pulled -> 0 sources" as if it
    # succeeded. The operator gets NO signal the pull failed (silent failure). A non-zero rc must
    # surface a typed, cli.main-catchable error carrying the stderr tail -> clean exit 2. This is NOT
    # ToolchainMissingError (yt-dlp is present, the URL is dead) and NOT TimeoutExpired (it returned).
    from fanops.errors import DownloadError
    cfg = Config(root=tmp_path)
    class R: returncode = 1; stdout = ""; stderr = "ERROR: [youtube] xyz: Video unavailable"
    mocker.patch("fanops.ingest.subprocess.run", return_value=R())
    with pytest.raises(DownloadError, match="Video unavailable"):
        download_url(cfg, "https://example.com/dead")

def test_download_url_succeeds_on_zero_rc(tmp_path, mocker):
    # The happy path: rc 0 -> no raise. download_url now returns the media files it produced (audit c0-f1);
    # a no-op download (yt-dlp wrote nothing — mocked) yields the empty set, never an error.
    cfg = Config(root=tmp_path)
    class R: returncode = 0; stdout = ""; stderr = ""
    mocker.patch("fanops.ingest.subprocess.run", return_value=R())
    assert download_url(cfg, "https://example.com/ok") == set()

def test_catalogues_and_probes(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put(cfg.inbox / "a.mp4", b"V")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1920, 1080, 12.0))
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    s = next(iter(led.sources.values()))
    assert s.state is SourceState.catalogued and s.source_origin == "drop" and s.sha256
    assert s.width == 1920 and s.height == 1080 and s.duration == 12.0

def test_dedupe_by_content_not_path(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(0, 0, 0.0))
    _put(cfg.inbox / "a.mp4", b"SAME"); _put(cfg.inbox / "b.mp4", b"SAME")
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1
    led, _ = ingest_drops(led, cfg)
    assert len(led.sources) == 1

def test_ingest_stamps_batch_id_write_once(tmp_path, mocker):
    # batch_id is stamped on the Source at catalogue and is WRITE-ONCE: re-dropping the same bytes under
    # a DIFFERENT batch keeps the first (the prior batch wins; a conflict breadcrumb is logged).
    cfg = Config(root=tmp_path); _put(cfg.inbox / "a.mp4", b"V")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(0, 0, 1.0))
    led, _ = ingest_drops(Ledger.load(cfg), cfg, batch_id="batch_x")
    src = next(iter(led.sources.values()))
    assert src.batch_id == "batch_x"
    _put(cfg.inbox / "a.mp4", b"V")                           # operator RE-drops the same bytes (inbox drained on pass 1)
    led, _ = ingest_drops(led, cfg, batch_id="batch_y")          # same bytes, new batch
    assert led.sources[src.id].batch_id == "batch_x" and len(led.sources) == 1   # write-once: prior wins
    assert "batch_conflict" in cfg.log_path.read_text()       # the conflict is visible (mirrors origin_conflict)

def test_ingest_no_batch_auto_resolves_drop_batch(tmp_path, mocker):
    # ROOT CONTRACT (supersedes the earlier "no batch => None" path): when the caller does not pass a
    # batch_id, ingest_drops auto-resolves a day-stable `drop-{date}` batch and stamps it onto the new
    # Source — so every catalogued Source carries a real batch_id and the Studio Review "Ungrouped"
    # group can never be constructed from this path. Detailed contract in test_ingest_auto_batch.py.
    cfg = Config(root=tmp_path); _put(cfg.inbox / "a.mp4", b"V")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(0, 0, 1.0))
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    src = next(iter(led.sources.values()))
    assert src.batch_id is not None and led.get_batch(src.batch_id).name.startswith("drop-")

def test_catalogue_stamps_created_at(tmp_path, mocker):
    # content-lifecycle Phase 2: a freshly catalogued Source carries a parseable ISO-Z created_at (ingest day).
    from fanops.timeutil import parse_iso
    cfg = Config(root=tmp_path); _put(cfg.inbox / "a.mp4", b"V")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 5.0))
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    s = next(iter(led.sources.values()))
    assert s.created_at and parse_iso(s.created_at).tzinfo is not None     # aware (no naive-tz shift)
    first = s.created_at
    _put(cfg.inbox / "a.mp4", b"V")                                          # operator RE-drops same bytes (inbox drained on pass 1)
    led, _ = ingest_drops(led, cfg)                                           # re-ingest same bytes -> setdefault no-op
    assert next(iter(led.sources.values())).created_at == first           # write-once at first catalogue

def test_skips_audio_only_drop(tmp_path, mocker):
    # An audio-only file (no video stream) is NOT catalogued: the clip pipeline reframes via
    # ffmpeg -vf, which is silently ignored on audio-only input and would emit a videoless
    # 'clip'. has_video_stream() gates it out at ingest.
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "voice.wav", b"A"); _put(cfg.inbox / "perf.mp4", b"V")
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 5.0))
    mocker.patch("fanops.ingest.has_video_stream",
                 side_effect=lambda p: p.suffix.lower() != ".wav")
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1
    assert "original_name" not in next(iter(led.sources.values())).meta

def test_is_excluded():
    assert is_excluded("Moh Flow passport & ID.zip")
    assert is_excluded("Agreement - Accelerator.pdf")
    assert not is_excluded("adidas - day 01 moh flow.MOV")

def test_skips_pii(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(0, 0, 0.0))
    _put(cfg.inbox / "passport scan.jpg", b"S"); _put(cfg.inbox / "perf.mp4", b"V")
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1
    assert "original_name" not in next(iter(led.sources.values())).meta

def test_scan_excludes_pii(tmp_path):
    d = tmp_path / "D"; d.mkdir()
    (d / "passport.jpg").write_bytes(b"x"); (d / "clip.mp4").write_bytes(b"y")
    assert {Path(c).name for c in scan_local([d])} == {"clip.mp4"}

def test_ingest_does_not_persist_original_filename(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put(cfg.inbox / "MY-PRIVATE-NAME.mp4", b"V")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1920, 1080, 12.0))
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    s = next(iter(led.sources.values()))
    assert "original_name" not in s.meta
    assert "MY-PRIVATE-NAME" not in json.dumps(s.model_dump())   # the filename is nowhere in the unit

# ---- M1 (structural-hooks): origin_kind + inbox threading + dedup-conflict visibility ----
def test_ingest_stamps_third_party_origin_kind(tmp_path, mocker):
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 5.0))
    cfg = Config(root=tmp_path); _put(cfg.inbox / "a.mp4", b"AAA")
    led, _ = ingest_drops(Ledger.load(cfg), cfg, origin_kind="third_party")
    assert next(iter(led.sources.values())).origin_kind == "third_party"

def test_ingest_default_origin_kind_is_native(tmp_path, mocker):
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 5.0))
    cfg = Config(root=tmp_path); _put(cfg.inbox / "a.mp4", b"AAA")
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    assert next(iter(led.sources.values())).origin_kind == "native"

def test_ingest_scans_explicit_inbox(tmp_path, mocker):
    # inbox= lets a caller catalogue from a non-default dir (the third-party staging dir)
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 5.0))
    cfg = Config(root=tmp_path); staging = tmp_path / "staging"; _put(staging / "b.mp4", b"BBB")
    led, _ = ingest_drops(Ledger.load(cfg), cfg, inbox=staging, origin_kind="third_party")
    assert len(led.sources) == 1 and next(iter(led.sources.values())).origin_kind == "third_party"

def test_ingest_same_sha_keeps_origin_and_warns(tmp_path, mocker):
    # same bytes catalogued native, then offered as third_party from staging -> dedup KEEPS native
    # (write-once) and logs an origin_conflict WARN (never a silent flip to third_party).
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(0, 0, 0.0))
    cfg = Config(root=tmp_path); _put(cfg.inbox / "a.mp4", b"SAME")
    with Ledger.transaction(cfg) as led:
        ingest_drops(led, cfg)                                       # native, persisted
    sid = next(iter(Ledger.load(cfg).sources.values())).id
    staging = tmp_path / "staging"; _put(staging / "dup.mp4", b"SAME")
    with Ledger.transaction(cfg) as led:
        ingest_drops(led, cfg, inbox=staging, origin_kind="third_party")
    led3 = Ledger.load(cfg)
    assert led3.sources[sid].origin_kind == "native"                 # unchanged (no silent flip)
    assert "origin_conflict" in cfg.log_path.read_text()             # the conflict is visible


# ---- WS-I1 Task 1 (ING-1/10/11/copy2): per-pass inbox lifecycle ----
def test_ingest_drains_inbox_and_does_not_rehash(tmp_path, mocker):
    # ING-1: after a file is catalogued, the inbox copy is archived OUT of the scan domain. A SECOND pass
    # must NOT sha256 it again (the steady-state re-hash bleed). A spy on sha256_of proves the second pass
    # never reads the disposed file, and the inbox no longer contains it (the original is preserved, archived).
    cfg = Config(root=tmp_path); _put(cfg.inbox / "a.mp4", b"V")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 5.0))
    led, c1 = ingest_drops(Ledger.load(cfg), cfg)
    assert c1.added == 1 and len(led.sources) == 1
    assert not (cfg.inbox / "a.mp4").exists()                        # disposed → archived
    assert (cfg.inbox / ".ingested" / "a.mp4").exists()             # original preserved, not deleted
    spy = mocker.patch("fanops.ingest.sha256_of", wraps=sha256_of)
    led2, c2 = ingest_drops(led, cfg)                                # second pass
    assert c2.added == 0 and len(led2.sources) == 1
    spy.assert_not_called()                                          # the archived file is never re-hashed

def test_ingest_sweeps_partial_orphans(tmp_path, mocker):
    # ING-10: a leaked *.uploadpart / *.part (crashed upload / killed yt-dlp) is cleared on ingest start.
    cfg = Config(root=tmp_path); cfg.inbox.mkdir(parents=True, exist_ok=True)
    (cfg.inbox / "leak.mp4.uploadpart").write_bytes(b"X"); (cfg.inbox / "half.part").write_bytes(b"Y")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(0, 0, 0.0))
    ingest_drops(Ledger.load(cfg), cfg)
    assert not list(cfg.inbox.glob("*.uploadpart")) and not list(cfg.inbox.glob("*.part"))

def test_ingest_uses_one_now_per_pass(tmp_path, mocker):
    # ING-11: two files catalogued in ONE pass share the SAME created_at (one clock read), not two.
    cfg = Config(root=tmp_path); _put(cfg.inbox / "a.mp4", b"AAA"); _put(cfg.inbox / "b.mp4", b"BBB")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 5.0))
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    stamps = {s.created_at for s in led.sources.values()}
    assert len(led.sources) == 2 and len(stamps) == 1               # one now, not per-file

def test_copy2_enospc_is_per_file_skip_not_pass_rollback(tmp_path, mocker):
    # copy2 guard: an ENOSPC on ONE file's copy must NOT raise out of the pass (which would roll back the
    # transaction over one bad file). It is a per-file skip + breadcrumb; a sibling good file still catalogues.
    import shutil as _shutil
    cfg = Config(root=tmp_path); _put(cfg.inbox / "good.mp4", b"G"); _put(cfg.inbox / "bad.mp4", b"B")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 5.0))
    real_copy = _shutil.copy2
    def maybe_enospc(src, dst, *a, **k):
        if "bad" in str(src): raise OSError(28, "No space left on device")
        return real_copy(src, dst, *a, **k)
    mocker.patch("fanops.ingest.shutil.copy2", side_effect=maybe_enospc)
    led, c = ingest_drops(Ledger.load(cfg), cfg)                     # must NOT raise
    assert len(led.sources) == 1 and c.skipped == 1                 # the good one landed; the bad one skipped
    assert "copy_failed" in cfg.log_path.read_text()


# ---- WS-I1 Task 3 (ING-7): probe-fail degraded_reason + reprobe ----
def test_probe_failure_catalogues_degraded_then_reprobes(tmp_path, mocker):
    # ING-7: a first-pass probe TIMEOUT (returns 0×0) must NOT freeze a clean 0×0 source. It catalogues with
    # degraded_reason='probe_failed'; a LATER pass with a working probe fills real dimensions + clears the flag.
    cfg = Config(root=tmp_path); _put(cfg.inbox / "a.mp4", b"V")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(0, 0, 0.0))    # first pass: probe failed
    with Ledger.transaction(cfg) as led:
        ingest_drops(led, cfg)
    s = next(iter(Ledger.load(cfg).sources.values()))
    assert s.width == 0 and s.degraded_reason == "probe_failed"
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 5.0))   # probe recovers
    with Ledger.transaction(cfg) as led:
        ingest_drops(led, cfg)                                        # re-probe pass
    s2 = next(iter(Ledger.load(cfg).sources.values()))
    assert s2.width == 1080 and s2.height == 1920 and s2.degraded_reason is None
    assert "reprobe_ok" in cfg.log_path.read_text()

def test_clean_probe_sets_no_degraded_reason(tmp_path, mocker):
    # non-regression: a successful probe leaves degraded_reason None (byte-identical to today).
    cfg = Config(root=tmp_path); _put(cfg.inbox / "a.mp4", b"V")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1920, 1080, 12.0))
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    assert next(iter(led.sources.values())).degraded_reason is None
