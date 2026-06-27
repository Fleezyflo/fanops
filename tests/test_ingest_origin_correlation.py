# tests/test_ingest_origin_correlation.py — WS6 (audit c0-f1 ingest): source_origin was a PASS-WIDE stamp.
# `cmd pull` runs download_url (drops the yt-dlp media into the inbox) then ingest_drops(origin="url"), which
# re-scans the ENTIRE inbox and stamps EVERY media file "url" — including a file the operator manually dropped
# that's still sitting in the inbox awaiting `ingest`. So provenance lies: a drop becomes "url". The fix
# correlates origin to the actual download: download_url returns the media files IT produced (a before/after
# inbox snapshot, version-independent — no yt-dlp stdout parsing), and ingest_drops stamps only those "url",
# leaving every pre-existing file the "drop" default.
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.ingest import download_url, download_source


def _put(p, b):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b)


def test_pull_does_not_mislabel_a_pre_existing_drop_as_url(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(0, 0, 1.0))
    _put(cfg.inbox / "manual_drop.mp4", b"DROPPED")        # already in the inbox before the pull
    # yt-dlp "downloads" a new file into the inbox during the subprocess call.
    def fake_ytdlp(cmd, **kw):
        _put(cfg.inbox / "pulled_video.mp4", b"PULLED")
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    mocker.patch("fanops.ingest.subprocess.run", side_effect=fake_ytdlp)
    led = download_source(Ledger.load(cfg), cfg, "https://example.com/v")
    origins = {s.source_origin for s in led.sources.values()}
    assert origins == {"drop", "url"}, f"per-file origin lost — got {origins}"
    # the file that existed before the pull is "drop"; the one yt-dlp produced is "url" (bytes-len distinguishes)
    drop_src = next(s for s in led.sources.values() if s.meta["bytes"] == len(b"DROPPED"))
    url_src = next(s for s in led.sources.values() if s.meta["bytes"] == len(b"PULLED"))
    assert drop_src.source_origin == "drop"
    assert url_src.source_origin == "url"


def test_download_url_returns_the_files_it_produced(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "preexisting.mp4", b"OLD")
    def fake_ytdlp(cmd, **kw):
        _put(cfg.inbox / "new.mp4", b"NEW")
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    mocker.patch("fanops.ingest.subprocess.run", side_effect=fake_ytdlp)
    produced = download_url(cfg, "https://example.com/v")
    assert produced == {(cfg.inbox / "new.mp4").resolve()}     # the delta, not the whole inbox
