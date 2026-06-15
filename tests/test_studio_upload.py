# tests/test_studio_upload.py — browser-complete ingestion (M1): the operator uploads raw video in the
# Run tab → it streams into 01_inbox → the existing "Ingest inbox" catalogues it. Untrusted multipart
# input crossing a system boundary, so the path-safety + size-cap gates are tested hard.
import io
from pathlib import Path
from fanops.config import Config
from fanops.studio import actions


class _Up:                                          # a minimal FileStorage stand-in for action-level tests
    def __init__(self, name, data=b"VIDEOBYTES"): self.filename = name; self.stream = io.BytesIO(data)
    def save(self, dst): Path(dst).write_bytes(self.stream.getvalue())


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


# ---- Task 1: validation (video ext + traversal) ----
def test_save_uploads_rejects_traversal_name(tmp_path):
    cfg = Config(root=tmp_path)
    res = actions.save_uploads(cfg, [_Up("../../evil.mp4")], probe=False)
    assert res.detail["skipped"] and not res.detail["saved"]
    assert not (cfg.base.parent / "evil.mp4").exists()      # nothing escaped 01_inbox/

def test_save_uploads_rejects_non_video_ext(tmp_path):
    cfg = Config(root=tmp_path)
    res = actions.save_uploads(cfg, [_Up("notes.txt")], probe=False)
    assert res.detail["skipped"] and not res.detail["saved"]
    assert not (cfg.inbox / "notes.txt").exists()

def test_save_uploads_empty_selection(tmp_path):
    assert actions.save_uploads(Config(root=tmp_path), []).ok is False

def test_save_uploads_drops_empty_file_parts(tmp_path):
    # a multipart form submitted with no file chosen sends a part with filename="" — not an error, just empty
    cfg = Config(root=tmp_path)
    assert actions.save_uploads(cfg, [_Up("")], probe=False).ok is False

def test_save_uploads_all_skipped_is_not_ok(tmp_path):
    # every file rejected → ok is False (a green "0 saved" would be a false positive), detail still carries counts
    cfg = Config(root=tmp_path)
    res = actions.save_uploads(cfg, [_Up("notes.txt"), _Up("readme.md")], probe=False)
    assert res.ok is False and res.detail["saved"] == [] and len(res.detail["skipped"]) == 2

def test_save_uploads_caps_overlong_filename(tmp_path):
    # a 300-char name must NOT raise an OSError (which would embed the fs path in a skip reason) — cap it
    cfg = Config(root=tmp_path)
    res = actions.save_uploads(cfg, [_Up("a" * 300 + ".mp4")], probe=False)
    assert res.ok and res.detail["saved"]
    landed = list(cfg.inbox.glob("*.mp4"))
    assert len(landed) == 1 and len(landed[0].name.encode()) <= 255    # within NAME_MAX, extension preserved
    assert not list(cfg.inbox.glob("*.uploadpart"))


# ---- Task 2: stream to temp + atomic os.replace into the inbox ----
def test_save_uploads_lands_video_in_inbox(tmp_path):
    cfg = Config(root=tmp_path)
    res = actions.save_uploads(cfg, [_Up("clip.mp4")], probe=False)
    assert res.ok and res.detail["saved"] == ["clip.mp4"]
    assert (cfg.inbox / "clip.mp4").read_bytes() == b"VIDEOBYTES"

def test_save_uploads_multiple_files(tmp_path):
    cfg = Config(root=tmp_path)
    res = actions.save_uploads(cfg, [_Up("a.mp4"), _Up("b.mov")], probe=False)
    assert sorted(res.detail["saved"]) == ["a.mp4", "b.mov"]
    assert (cfg.inbox / "a.mp4").exists() and (cfg.inbox / "b.mov").exists()

def test_no_uploadpart_left_after_success(tmp_path):
    cfg = Config(root=tmp_path); actions.save_uploads(cfg, [_Up("a.mp4")], probe=False)
    assert not list(cfg.inbox.glob("*.uploadpart"))         # temp swapped in, none orphaned

def test_save_uploads_same_file_twice_is_idempotent_at_ingest(tmp_path, mocker):
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 5.0))
    cfg = Config(root=tmp_path)
    actions.save_uploads(cfg, [_Up("dup.mp4", b"SAMEBYTES")], probe=False)   # 1st upload
    actions.save_uploads(cfg, [_Up("dup.mp4", b"SAMEBYTES")], probe=False)   # 2nd identical → os.replace overwrites
    assert len(list(cfg.inbox.glob("*.mp4"))) == 1                            # one file on disk, not two
    assert actions.run_ingest(cfg).detail["sources"] == 1                     # SHA256 dedup → catalogued once


# ---- Task 3: optional probe pre-check, ToolchainMissing-safe ----
def test_save_uploads_skips_audio_only_when_probing(tmp_path, mocker):
    mocker.patch("fanops.ingest.has_video_stream", return_value=False)
    cfg = Config(root=tmp_path)
    res = actions.save_uploads(cfg, [_Up("audio.mp4")], probe=True)
    assert res.detail["skipped"] and not res.detail["saved"]
    assert not (cfg.inbox / "audio.mp4").exists()           # removed after the probe rejected it

def test_save_uploads_probe_toolchain_absent_keeps_file(tmp_path, mocker):
    from fanops.errors import ToolchainMissingError
    mocker.patch("fanops.ingest.has_video_stream", side_effect=ToolchainMissingError("no ffprobe"))
    cfg = Config(root=tmp_path)
    res = actions.save_uploads(cfg, [_Up("clip.mp4")], probe=True)
    assert res.ok and res.detail["saved"] == ["clip.mp4"]   # ffprobe missing must NOT drop a real upload


# ---- Task 4: POST /run/upload route + MAX_CONTENT_LENGTH cap + oversize handler ----
def test_upload_route_lands_file_then_ingest_catalogues(tmp_path, mocker):
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 5.0))
    cfg = Config(root=tmp_path)
    r = _client(cfg).post("/run/upload", data={"files": (io.BytesIO(b"VID"), "up.mp4")},
                          content_type="multipart/form-data")
    assert r.status_code == 200 and (cfg.inbox / "up.mp4").exists()
    assert actions.run_ingest(cfg).detail["sources"] == 1   # the uploaded file is catalogued, one click later

def test_upload_route_rejects_oversize_with_clean_panel(tmp_path):
    from fanops.studio.app import create_app
    app = create_app(Config(root=tmp_path)); app.config.update(TESTING=True)
    app.config["MAX_CONTENT_LENGTH"] = 8                     # tiny cap on the CONSTRUCTED app
    r = app.test_client().post("/run/upload",
            data={"files": (io.BytesIO(b"X" * 64), "big.mp4")}, content_type="multipart/form-data")
    assert r.status_code == 200 and b"too large" in r.data   # 200 so htmx swaps; clean panel, not Flask's HTML


# ---- Task 5: the upload form renders in the Run tab ----
def test_run_route_shows_upload_form(tmp_path):
    from fanops.studio.app import create_app
    app = create_app(Config(root=tmp_path)); app.config.update(TESTING=True)
    r = app.test_client().get("/run")
    assert r.status_code == 200 and b'type="file"' in r.data and b"Add video" in r.data
