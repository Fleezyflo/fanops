# tests/test_studio_live_library.py — MOL-27 (ledger-rebuild): the Studio "Live library" view for
# imported/live-only media ("viewed there, not authored here"). DISTINCT from Posted (which is the
# authored, shipped library). Read-only over led.imported_media — the projection (M2) fills it, insights
# (M3) fill metrics. Surfaces: permalink, product_type, metrics-when-present, the credentialed-handle
# scope label; empty-state handled. No mutations, no Graph call (a pure ledger read).
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import ImportedMedia, Post, Platform, PostState, Clip, ClipState, LIFT_SCORE
from fanops.studio import views


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


def _seed_imported(cfg, rows):
    with Ledger.transaction(cfg) as led:
        for im in rows:
            led.add_imported_media(im)


# ---- live_library read-model ----
def test_live_library_lists_imported_media(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_imported(cfg, [
        ImportedMedia(media_id="M1", permalink="https://ig/reel/A/", product_type="REELS",
                      account="ig-777", metrics={"reach": 1200, LIFT_SCORE: 0.4}),
        ImportedMedia(media_id="M2", permalink="https://ig/p/B/", product_type="FEED", account="ig-777")])
    rows = views.live_library(Ledger.load(cfg), cfg)
    ids = {r.media_id for r in rows}
    assert ids == {"M1", "M2"}
    m1 = [r for r in rows if r.media_id == "M1"][0]
    assert m1.permalink == "https://ig/reel/A/" and m1.product_type == "REELS"
    assert m1.reach == 1200 and m1.lift_score == 0.4


def test_live_library_metrics_absent_render_none(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_imported(cfg, [ImportedMedia(media_id="M2", permalink="https://ig/p/B/", product_type="FEED")])
    rows = views.live_library(Ledger.load(cfg), cfg)
    m2 = rows[0]
    assert m2.reach is None and m2.lift_score is None       # a not-yet-insighted row: metrics absent -> None -> "—"


def test_live_library_excludes_authored_posts(tmp_path):
    # An authored Post is NEVER in the Live library — that's the Posted library's job. The two surfaces are
    # DISJOINT by construction: live_library reads imported_media only, never posts.
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="c1", parent_id="m1", path="/c/c1.mp4", state=ClipState.published))
        led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="ig_1",
                          platform=Platform.instagram, caption="mine", state=PostState.published,
                          public_url="https://ig/reel/mine/"))
        led.add_imported_media(ImportedMedia(media_id="M1", permalink="https://ig/reel/A/", product_type="REELS"))
    rows = views.live_library(Ledger.load(cfg), cfg)
    assert {r.media_id for r in rows} == {"M1"}             # the authored post p1 is not here


def test_live_library_scope_label_is_credentialed_handle(tmp_path, monkeypatch):
    # The single-credential scope (META_IG_USER_ID) is stated on the surface — the view exposes the handle.
    monkeypatch.setenv("META_IG_USER_ID", "ig-credentialed-99")
    cfg = Config(root=tmp_path)
    _seed_imported(cfg, [ImportedMedia(media_id="M1", permalink="https://ig/reel/A/", product_type="REELS")])
    label = views.live_library_scope(cfg)
    assert "ig-credentialed-99" in label


def test_live_library_scope_label_no_creds(tmp_path, monkeypatch):
    monkeypatch.delenv("META_IG_USER_ID", raising=False)
    cfg = Config(root=tmp_path)
    label = views.live_library_scope(cfg)
    assert label                                            # never blank; a no-creds label still renders


# ---- route ----
def test_live_library_route_renders(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_imported(cfg, [ImportedMedia(media_id="M1", permalink="https://ig/reel/A/", product_type="REELS",
                                       account="ig-777", metrics={"reach": 1200})])
    r = _client(cfg).get("/live-library")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "https://ig/reel/A/" in body                     # the permalink is shown
    assert "REELS" in body                                  # product_type shown
    # the surface is clearly labeled "viewed there, not authored here" (distinct from Posted)
    assert "not authored here" in body.lower() or "viewed there" in body.lower()


def test_live_library_route_empty_state(tmp_path):
    cfg = Config(root=tmp_path)                             # no imported media at all
    r = _client(cfg).get("/live-library")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "not authored here" in body.lower() or "viewed there" in body.lower()   # still labeled
    # an explicit empty state, not a blank page or a crash
    assert "no live" in body.lower() or "nothing" in body.lower() or "empty" in body.lower() or "no imported" in body.lower()


def test_live_library_in_nav(tmp_path):
    cfg = Config(root=tmp_path)
    r = _client(cfg).get("/")
    assert r.status_code == 200
    assert "/live-library" in r.get_data(as_text=True)     # reachable from the rail nav
