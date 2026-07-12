# tests/test_upload_chunked.py — S02: sequential resumable chunked browser upload for Studio Run-tab ingestion.
import hashlib, io, json
from fanops.config import Config
from fanops.studio import actions


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


# ---- hostile-name parity with test_studio_upload.py ----
import pytest
@pytest.mark.parametrize("raw,reason", [
    ("../../evil.mp4", "unsafe name"),
    ("notes.txt", "unsupported type"),
    ("..\\evil.mp4", "unsafe name"),
    ("/etc/passwd.mp4", "unsafe name"),
])
def test_upload_init_rejects_hostile_names(tmp_path, raw, reason):
    cfg = Config(root=tmp_path)
    res = actions.upload_init(cfg, raw, 100, _sha(b"x"))
    assert not res.ok and reason in (res.error or "")


def test_upload_chunk_offset_mismatch_409(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_UPLOAD_MAX_MB", "1")
    cfg = Config(root=tmp_path)
    data = b"A" * 512
    init = actions.upload_init(cfg, "clip.mp4", len(data), _sha(data))
    assert init.ok
    uid = init.detail["upload_id"]
    bad = actions.upload_chunk(cfg, uid, 999, b"X")
    assert not bad.ok and bad.detail["received"] == 0 and "offset" in bad.detail["error"].lower()
    c = _client(cfg)
    r = c.put(f"/run/upload/chunk?upload_id={uid}&offset=999", data=b"X")
    assert r.status_code == 409 and r.is_json
    body = r.get_json()
    assert body["received"] == 0 and "offset" in body["error"].lower()


def test_upload_chunk_append_and_finalize(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_UPLOAD_MAX_MB", "1")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 5.0))
    cfg = Config(root=tmp_path)
    data = b"V" * (2 * 1024 * 1024)   # 2 MB — exceeds 1 MB per-chunk cap
    init = actions.upload_init(cfg, "big.mp4", len(data), _sha(data))
    assert init.ok and init.detail["offset"] == 0
    uid = init.detail["upload_id"]
    chunk = 1024 * 1024
    off = 0
    while off < len(data):
        part = data[off:off + chunk]
        res = actions.upload_chunk(cfg, uid, off, part)
        assert res.ok and res.detail["received"] == off + len(part)
        off += len(part)
    fin = actions.upload_finalize(cfg, uid, trigger_ingest=False)
    assert fin.ok and (cfg.inbox / "big.mp4").read_bytes() == data
    assert not list(cfg.inbox.glob("*.uploadpart")) and not list(cfg.inbox.glob("*.uploadmeta.json"))


def test_upload_finalize_rejects_sha_mismatch(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_UPLOAD_MAX_MB", "1")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    cfg = Config(root=tmp_path)
    data = b"B" * 2048
    init = actions.upload_init(cfg, "clip.mp4", len(data), _sha(data))
    uid = init.detail["upload_id"]
    actions.upload_chunk(cfg, uid, 0, data)
    # corrupt meta sha to force mismatch at finalize
    meta = next(cfg.inbox.glob("*.uploadmeta.json"))
    m = json.loads(meta.read_text()); m["sha256"] = "0" * 64; meta.write_text(json.dumps(m))
    res = actions.upload_finalize(cfg, uid, trigger_ingest=False)
    assert not res.ok and "sha" in (res.error or "").lower()
    assert not (cfg.inbox / "clip.mp4").exists()


def test_upload_finalize_probes_before_promote(tmp_path, mocker):
    mocker.patch("fanops.ingest.has_video_stream", return_value=False)
    cfg = Config(root=tmp_path)
    data = b"C" * 128
    init = actions.upload_init(cfg, "audio.mp4", len(data), _sha(data))
    uid = init.detail["upload_id"]
    actions.upload_chunk(cfg, uid, 0, data)
    res = actions.upload_finalize(cfg, uid, trigger_ingest=False)
    assert not res.ok and "video" in (res.error or "").lower()
    assert not (cfg.inbox / "audio.mp4").exists()
    assert not list(cfg.inbox.glob("*.uploadpart"))


def test_upload_resume_after_partial(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_UPLOAD_MAX_MB", "1")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    cfg = Config(root=tmp_path)
    data = b"D" * (2 * 1024 * 1024)
    digest = _sha(data)
    init1 = actions.upload_init(cfg, "resume.mp4", len(data), digest)
    uid1 = init1.detail["upload_id"]
    half = 1024 * 1024
    actions.upload_chunk(cfg, uid1, 0, data[:half])
    init2 = actions.upload_init(cfg, "resume.mp4", len(data), digest)
    assert init2.ok and init2.detail["upload_id"] == uid1 and init2.detail["offset"] == half
    actions.upload_chunk(cfg, uid1, half, data[half:])
    fin = actions.upload_finalize(cfg, uid1, trigger_ingest=False)
    assert fin.ok and (cfg.inbox / "resume.mp4").read_bytes() == data


def test_sweep_clears_uploadmeta(tmp_path):
    from fanops.ingest import _sweep_partials
    cfg = Config(root=tmp_path); cfg.inbox.mkdir(parents=True, exist_ok=True)
    meta = cfg.inbox / "clip.mp4.uploadmeta.json"
    meta.write_text('{"upload_id":"x"}')
    part = cfg.inbox / "clip.mp4.uploadpart"
    part.write_bytes(b"partial")
    _sweep_partials(cfg.inbox, cfg)
    assert not meta.exists() and not part.exists()


def test_upload_route_unchanged_without_js(tmp_path, mocker):
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 5.0))
    cfg = Config(root=tmp_path)
    r = _client(cfg).post("/run/upload", data={"files": (io.BytesIO(b"VID"), "up.mp4")},
                          content_type="multipart/form-data")
    assert r.status_code == 200 and (cfg.inbox / ".ingested" / "up.mp4").exists()
