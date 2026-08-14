# tests/test_studio_candidates.py — Track C: approve discover candidates in the browser instead of
# the Finder shuffle. `fanops discover` writes thumbnails to 00_review/; approving admits the
# original via discover.intake + inbox ingest (creates a Source).
import json

from fanops.config import Config
from fanops.ids import make_id
from fanops.ingest import sha256_of
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.studio import views, actions


def _thumb(cfg, eid="abc"):
    cfg.review.mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{eid}.jpg").write_bytes(b"JPG")


def _candidate(cfg, content=b"VIDEO", eid=None):
    """Original media + manifest + review thumbnail — the minimum discover candidate fixture."""
    bank = cfg.root / "bank"
    bank.mkdir(parents=True, exist_ok=True)
    vid = bank / "clip.mp4"
    vid.write_bytes(content)
    digest = sha256_of(vid)
    eid = eid or digest[:16]
    cfg.review.mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{eid}.jpg").write_bytes(b"JPG")
    manifest = {eid: {"source_path": str(vid), "sha256": digest, "bytes": len(content)}}
    (cfg.review / "manifest.json").write_text(json.dumps(manifest))
    return eid, vid, digest


def _ingest_mocks(mocker):
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1920, 1080, 10.0))
    mocker.patch("fanops.studio.actions_run.kick_prepare")
    mocker.patch("fanops.digest.write_digest")


# ---- views.review_candidates ----
def test_lists_unapproved_candidates(tmp_path):
    cfg = Config(root=tmp_path); _thumb(cfg, "abc"); _thumb(cfg, "def")
    assert {c["eid"] for c in views.review_candidates(cfg)} == {"abc", "def"}

def test_excludes_already_approved(tmp_path):
    cfg = Config(root=tmp_path); _thumb(cfg, "abc")
    (cfg.review / "approved").mkdir(parents=True, exist_ok=True)
    (cfg.review / "approved" / "def.jpg").write_bytes(b"J")
    assert {c["eid"] for c in views.review_candidates(cfg)} == {"abc"}

def test_empty_when_no_review_dir(tmp_path):
    assert views.review_candidates(Config(root=tmp_path)) == []


# ---- actions.approve_candidate ----
def test_approve_moves_to_approved(tmp_path, mocker):
    cfg = Config(root=tmp_path); eid, _, _ = _candidate(cfg)
    _ingest_mocks(mocker)
    assert actions.approve_candidate(cfg, eid).ok
    assert (cfg.review / "approved" / f"{eid}.jpg").exists() and not (cfg.review / f"{eid}.jpg").exists()

def test_approve_creates_source(tmp_path, mocker):
    cfg = Config(root=tmp_path); eid, _, digest = _candidate(cfg, content=b"ADMIT_ME")
    _ingest_mocks(mocker)
    res = actions.approve_candidate(cfg, eid)
    assert res.ok
    sid = make_id("src", digest)
    assert res.detail["source_id"] == sid
    led = Ledger.load(cfg)
    assert sid in led.sources
    assert (cfg.sources / f"{sid}.mp4").exists()

def test_approve_missing_original_fails(tmp_path, mocker):
    cfg = Config(root=tmp_path); eid, vid, _ = _candidate(cfg)
    vid.unlink()
    _ingest_mocks(mocker)
    res = actions.approve_candidate(cfg, eid)
    assert not res.ok and "original missing" in res.error
    assert (cfg.review / f"{eid}.jpg").exists()          # thumb not moved on honest failure

def test_approve_retired_sha_is_dead_end(tmp_path, mocker):
    cfg = Config(root=tmp_path); eid, _, digest = _candidate(cfg, content=b"RETIRED")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_old", source_path="/old.mp4", state=SourceState.retired, sha256=digest))
    _ingest_mocks(mocker)
    res = actions.approve_candidate(cfg, eid)
    assert not res.ok and res.detail.get("retired_dedup") == ["src_old"]
    assert make_id("src", digest) not in Ledger.load(cfg).sources
    assert (cfg.review / f"{eid}.jpg").exists()          # restored for retry
    assert not (cfg.review / "approved" / f"{eid}.jpg").exists()

def test_approve_ingest_failure_restores_thumbnail(tmp_path, mocker):
    cfg = Config(root=tmp_path); eid, _, _ = _candidate(cfg)
    _ingest_mocks(mocker)
    mocker.patch("fanops.ingest.ingest_staged", side_effect=RuntimeError("ingest boom"))
    res = actions.approve_candidate(cfg, eid)
    assert not res.ok and "ingest failed" in res.error
    assert (cfg.review / f"{eid}.jpg").exists()
    assert not (cfg.review / "approved" / f"{eid}.jpg").exists()

def test_approve_unknown_errors(tmp_path):
    assert not actions.approve_candidate(Config(root=tmp_path), "nope").ok

def test_approve_rejects_path_traversal(tmp_path):
    cfg = Config(root=tmp_path)
    res = actions.approve_candidate(cfg, "../../etc/passwd")
    assert not res.ok

def test_approve_wraps_os_error(tmp_path, mocker):
    # ecc:python-review: a read-only mount / disk-full / rename race must be a clean ActionResult,
    # not a 500. Force the move to raise OSError.
    cfg = Config(root=tmp_path); eid, _, _ = _candidate(cfg)
    _ingest_mocks(mocker)
    mocker.patch("pathlib.Path.rename", side_effect=OSError("read-only fs"))
    res = actions.approve_candidate(cfg, eid)
    assert not res.ok and "approve failed" in res.error


# ---- Studio routes ----
def test_candidates_route_renders(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _thumb(cfg, "abc")
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().get("/candidates")
    assert r.status_code == 200 and b"abc" in r.data

def test_candidates_approve_route(tmp_path, mocker):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); eid, _, _ = _candidate(cfg)
    _ingest_mocks(mocker)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post(f"/candidates/approve/{eid}")
    assert r.status_code == 200 and (cfg.review / "approved" / f"{eid}.jpg").exists()

def test_review_thumb_serves_jpg(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _thumb(cfg, "abc")
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().get("/review-thumb/abc")
    assert r.status_code == 200 and r.data == b"JPG"
