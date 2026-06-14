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
    # The happy path stays silent: rc 0 -> no raise, download_url returns None as before.
    cfg = Config(root=tmp_path)
    class R: returncode = 0; stdout = ""; stderr = ""
    mocker.patch("fanops.ingest.subprocess.run", return_value=R())
    assert download_url(cfg, "https://example.com/ok") is None

def test_catalogues_and_probes(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put(cfg.inbox / "a.mp4", b"V")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1920, 1080, 12.0))
    led = ingest_drops(Ledger.load(cfg), cfg)
    s = next(iter(led.sources.values()))
    assert s.state is SourceState.catalogued and s.source_origin == "drop" and s.sha256
    assert s.width == 1920 and s.height == 1080 and s.duration == 12.0

def test_dedupe_by_content_not_path(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(0, 0, 0.0))
    _put(cfg.inbox / "a.mp4", b"SAME"); _put(cfg.inbox / "b.mp4", b"SAME")
    led = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1
    led = ingest_drops(led, cfg)
    assert len(led.sources) == 1

def test_skips_audio_only_drop(tmp_path, mocker):
    # An audio-only file (no video stream) is NOT catalogued: the clip pipeline reframes via
    # ffmpeg -vf, which is silently ignored on audio-only input and would emit a videoless
    # 'clip'. has_video_stream() gates it out at ingest.
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "voice.wav", b"A"); _put(cfg.inbox / "perf.mp4", b"V")
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 5.0))
    mocker.patch("fanops.ingest.has_video_stream",
                 side_effect=lambda p: p.suffix.lower() != ".wav")
    led = ingest_drops(Ledger.load(cfg), cfg)
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
    led = ingest_drops(Ledger.load(cfg), cfg)
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
    led = ingest_drops(Ledger.load(cfg), cfg)
    s = next(iter(led.sources.values()))
    assert "original_name" not in s.meta
    assert "MY-PRIVATE-NAME" not in json.dumps(s.model_dump())   # the filename is nowhere in the unit
