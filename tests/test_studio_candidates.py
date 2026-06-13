# tests/test_studio_candidates.py — Track C: approve discover candidates in the browser instead of
# the Finder shuffle. `fanops discover` writes thumbnails to 00_review/; approving moves one to
# 00_review/approved/ (then `fanops intake` / the Run tab copies the original into the inbox).
from fanops.config import Config
from fanops.studio import views, actions


def _thumb(cfg, eid="abc"):
    cfg.review.mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{eid}.jpg").write_bytes(b"JPG")


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
def test_approve_moves_to_approved(tmp_path):
    cfg = Config(root=tmp_path); _thumb(cfg, "abc")
    assert actions.approve_candidate(cfg, "abc").ok
    assert (cfg.review / "approved" / "abc.jpg").exists() and not (cfg.review / "abc.jpg").exists()

def test_approve_unknown_errors(tmp_path):
    assert not actions.approve_candidate(Config(root=tmp_path), "nope").ok

def test_approve_rejects_path_traversal(tmp_path):
    cfg = Config(root=tmp_path)
    res = actions.approve_candidate(cfg, "../../etc/passwd")
    assert not res.ok

def test_approve_wraps_os_error(tmp_path, mocker):
    # ecc:python-review: a read-only mount / disk-full / rename race must be a clean ActionResult,
    # not a 500. Force the move to raise OSError.
    cfg = Config(root=tmp_path); _thumb(cfg, "abc")
    mocker.patch("pathlib.Path.rename", side_effect=OSError("read-only fs"))
    res = actions.approve_candidate(cfg, "abc")
    assert not res.ok and "approve failed" in res.error


# ---- Studio routes ----
def test_candidates_route_renders(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _thumb(cfg, "abc")
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().get("/candidates")
    assert r.status_code == 200 and b"abc" in r.data

def test_candidates_approve_route(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _thumb(cfg, "abc")
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/candidates/approve/abc")
    assert r.status_code == 200 and (cfg.review / "approved" / "abc.jpg").exists()

def test_review_thumb_serves_jpg(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _thumb(cfg, "abc")
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().get("/review-thumb/abc")
    assert r.status_code == 200 and r.data == b"JPG"
