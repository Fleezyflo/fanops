# tests/test_studio_publish_now.py — the Studio "Publish now" action/route: ship ONE reviewed post
# immediately via the same poster path the pipeline uses (publish_post), ignoring its schedule.
# Milestone 5 (publish in the UI). The engine is covered by test_publish_post.py; here we prove the
# Studio guards (queued-only, live-confirm, fatal-auth) + wiring.
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops.studio import actions

FUTURE = "2099-01-01T00:00:00Z"

def _seed(cfg, *, state=PostState.queued, when=FUTURE, media=None):
    led = Ledger.load(cfg)
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "clip_1.mp4").write_bytes(b"V")
    led.add_source(Source(id="s1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="m1", path=str(cdir / "clip_1.mp4"), aspect=Fmt.r9x16,
                      state=ClipState.queued))
    led.add_post(Post(id="p1", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="ship it", state=state,
                      scheduled_time=when, media_urls=media or []))
    led.save(); return led


def test_publish_now_dryrun_publishes_despite_future_schedule(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)                      # dryrun
    cfg = Config(root=tmp_path); _seed(cfg)                                 # scheduled for 2099
    res = actions.publish_now(cfg, "p1")
    assert res.ok is True and res.detail["state"] == "published"
    assert Ledger.load(cfg).posts["p1"].state is PostState.published

def test_publish_now_unknown_post(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); _seed(cfg)
    res = actions.publish_now(cfg, "nope")
    assert res.ok is False and "no such post" in res.error.lower()

def test_publish_now_non_queued_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); _seed(cfg, state=PostState.published)
    res = actions.publish_now(cfg, "p1")
    assert res.ok is False and "only a queued" in res.error.lower()

def test_publish_now_live_requires_confirm(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); _seed(cfg)
    res = actions.publish_now(cfg, "p1", confirmed=False)
    assert res.ok is False and "confirm" in res.error.lower()
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued           # not shipped without confirm

def test_publish_now_surfaces_fatal_auth(tmp_path, monkeypatch):
    from fanops.errors import BlotatoAuthError
    import fanops.post.run as run
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); _seed(cfg, media=["file://x.mp4"])         # pre-stamped -> skips ensure_clip_media
    monkeypatch.setattr(run, "get_media_uploader", lambda cfg: (lambda c, p: "https://x/u.mp4"))
    class Boom:
        def publish(self, led, post_id): raise BlotatoAuthError("401 unauthorized")
    monkeypatch.setattr(run, "get_poster", lambda cfg: Boom())
    res = actions.publish_now(cfg, "p1", confirmed=True)
    assert res.ok is False and "FATAL" in res.error and "BLOTATO_API_KEY" in res.error


# ---- Flask wiring ----
def test_publish_now_route(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/publish/now/p1")
    assert r.status_code == 200
    assert Ledger.load(cfg).posts["p1"].state is PostState.published

def test_review_renders_publish_now_button(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().get("/review")
    assert r.status_code == 200 and b"Publish now" in r.data
