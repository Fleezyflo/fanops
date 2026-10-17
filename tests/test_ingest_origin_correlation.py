# tests/test_ingest_origin_correlation.py — WS6 (audit c0-f1 ingest): source_origin was a PASS-WIDE stamp.
# `cmd pull` runs download_url (drops the yt-dlp media into the inbox) then ingest_drops(origin="url"), which
# re-scans the ENTIRE inbox and stamps EVERY media file "url" — including a file the operator manually dropped
# that's still sitting in the inbox awaiting `ingest`. So provenance lies: a drop becomes "url". The fix
# correlates origin to the actual download: download_url returns the media files IT produced (a before/after
# inbox snapshot, version-independent — no yt-dlp stdout parsing), and ingest_drops stamps only those "url",
# leaving every pre-existing file the "drop" default.
import pytest
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.errors import DownloadError
from fanops.ingest import download_url, ingest_drops, _pull_stage


def _put(p, b):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b)


class _Proc:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode = rc; self.stdout = stdout; self.stderr = stderr


def _ffprobe(duration: float = 1.0):
    def handle(cmd, **kw):
        joined = " ".join(str(c) for c in cmd)
        if "codec_type" in joined:
            return _Proc(stdout="video\n")
        return _Proc(stdout=f"0\n0\n{duration}\n")
    return handle


def _patch_os(mocker, *, ytdlp, ffprobe=None):
    """One subprocess.run stub — yt-dlp (ingest) and ffprobe (media_probe) share stdlib subprocess."""
    def run(cmd, **kw):
        bin = Path(cmd[0]).name
        if bin == "yt-dlp":
            return ytdlp(cmd, **kw)
        if bin == "ffprobe":
            return (ffprobe or _ffprobe())(cmd, **kw)
        raise AssertionError(f"unexpected binary {cmd[0]}")
    return mocker.patch("subprocess.run", side_effect=run)


def test_pull_does_not_mislabel_a_pre_existing_drop_as_url(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "manual_drop.mp4", b"DROPPED")
    def fake_ytdlp(cmd, **kw):
        assert Path(cmd[0]).name == "yt-dlp"
        _put(_pull_stage(cfg) / "pulled_video.mp4", b"PULLED")
        return _Proc()
    _patch_os(mocker, ytdlp=fake_ytdlp, ffprobe=_ffprobe(1.0))
    produced = download_url(cfg, "https://example.com/v")
    assert produced == {(_pull_stage(cfg) / "pulled_video.mp4").resolve()}
    led, _ = ingest_drops(Ledger.load(cfg), cfg, origin="url", inbox=_pull_stage(cfg), origin_paths=produced)
    assert len(led.sources) == 1
    url_src = next(iter(led.sources.values()))
    assert url_src.source_origin == "url" and url_src.meta["bytes"] == len(b"PULLED")
    assert (cfg.inbox / "manual_drop.mp4").exists()


def test_native_ingest_leaves_pull_stage_untouched(tmp_path, mocker):
    # Native catalogue (advance / ingest) rglobs 01_inbox, and pathlib matches the dot dir.
    # A stranded pull file and a disposed `.pull/.ingested` copy must not be minted as
    # source_origin=drop or moved into the inbox archive. The pull scanner still owns that tree.
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1920, 1080, 3.0))
    _put(cfg.inbox / "manual.mp4", b"MANUAL")
    _put(_pull_stage(cfg) / "stranded.mp4", b"PULLFILE")
    _put(_pull_stage(cfg) / ".ingested" / "done.mp4", b"ARCHIVED!")
    led, counts = ingest_drops(Ledger.load(cfg), cfg)
    assert counts.added == 1
    src = next(iter(led.sources.values()))
    assert src.source_origin == "drop" and src.meta["bytes"] == len(b"MANUAL")
    assert (_pull_stage(cfg) / "stranded.mp4").exists()
    assert (_pull_stage(cfg) / ".ingested" / "done.mp4").exists()
    assert not (cfg.inbox / ".ingested" / "stranded.mp4").exists()
    assert not (cfg.inbox / ".ingested" / "done.mp4").exists()
    produced = {(_pull_stage(cfg) / "stranded.mp4").resolve()}
    led, counts = ingest_drops(led, cfg, origin="url", inbox=_pull_stage(cfg), origin_paths=produced)
    assert counts.added == 1 and len(led.sources) == 2
    by_origin = {s.source_origin: s.meta["bytes"] for s in led.sources.values()}
    assert by_origin == {"drop": len(b"MANUAL"), "url": len(b"PULLFILE")}
    assert (_pull_stage(cfg) / ".ingested" / "done.mp4").exists()


def test_download_url_returns_the_files_it_produced(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "preexisting.mp4", b"OLD")
    def fake_ytdlp(cmd, **kw):
        assert Path(cmd[0]).name == "yt-dlp"
        _put(_pull_stage(cfg) / "new.mp4", b"NEW")
        return _Proc()
    _patch_os(mocker, ytdlp=fake_ytdlp)
    produced = download_url(cfg, "https://example.com/v")
    assert produced == {(_pull_stage(cfg) / "new.mp4").resolve()}
    assert (cfg.inbox / "preexisting.mp4").exists()


def test_download_url_empty_delta_is_not_success(tmp_path, mocker):
    # rc=0 with an empty stage delta is not a pull: yt-dlp wrote nothing. download_url must
    # raise DownloadError (cli.main -> exit 2), not return set() so cmd_pull prints success.
    cfg = Config(root=tmp_path)
    def fake_ytdlp(cmd, **kw):
        assert Path(cmd[0]).name == "yt-dlp"
        return _Proc()
    _patch_os(mocker, ytdlp=fake_ytdlp)
    with pytest.raises(DownloadError):
        download_url(cfg, "https://example.com/ok")
