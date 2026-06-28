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
    # ING-6: the pull now catalogues ONLY its isolated .pull stage, so a manual drop sitting in the inbox is
    # never scanned by the pull — it CANNOT be mislabeled "url" (it waits for a later native ingest pass). This
    # is the same c0-f1 guarantee, made structural: the drop and the pull no longer share a scan domain.
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(0, 0, 1.0))
    _put(cfg.inbox / "manual_drop.mp4", b"DROPPED")        # already in the inbox before the pull
    def fake_ytdlp(cmd, **kw):
        from fanops.ingest import _pull_stage
        _put(_pull_stage(cfg) / "pulled_video.mp4", b"PULLED")
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    mocker.patch("fanops.ingest.subprocess.run", side_effect=fake_ytdlp)
    led = download_source(Ledger.load(cfg), cfg, "https://example.com/v")
    assert len(led.sources) == 1                               # ONLY the pulled file is catalogued
    url_src = next(iter(led.sources.values()))
    assert url_src.source_origin == "url" and url_src.meta["bytes"] == len(b"PULLED")
    assert (cfg.inbox / "manual_drop.mp4").exists()            # the manual drop is left for a native pass


def test_download_url_returns_the_files_it_produced(tmp_path, mocker):
    from fanops.ingest import _pull_stage
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "preexisting.mp4", b"OLD")               # a manual drop in the inbox — NOT in the pull stage
    def fake_ytdlp(cmd, **kw):
        _put(_pull_stage(cfg) / "new.mp4", b"NEW")
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    mocker.patch("fanops.ingest.subprocess.run", side_effect=fake_ytdlp)
    produced = download_url(cfg, "https://example.com/v")
    assert produced == {(_pull_stage(cfg) / "new.mp4").resolve()}   # the stage delta, not the inbox
