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

def test_schedule_publish_re_renders_bucket_without_shipped_post(tmp_path, monkeypatch):
    # Phase 1 bug fix: publishing from the SCHEDULE bucket re-renders the panel so the shipped post drops
    # out of the actionable bucket (distinct from /publish/now which returns a one-off result fragment).
    monkeypatch.delenv("FANOPS_POSTER", raising=False)                       # dryrun
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed(cfg)                                  # p1 queued, scheduled 2099
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/schedule/publish/p1")
    assert r.status_code == 200
    body = r.data.decode()
    assert Ledger.load(cfg).posts["p1"].state is PostState.published        # shipped
    assert "/schedule/publish/p1" not in body                               # no publish form for the now-shipped post
    assert "Published" in body                                              # the panel banner reports the ship

def test_crosspost_all_rejects_source_equals_target(tmp_path, monkeypatch):
    # Phase 1 footgun fix: bulk backfill is CROSS-account; picking the same account for source + target
    # is a no-op (every clip already lives there). Reject up front with a clear message, before any work.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    res = actions.crosspost_all_to_account(cfg, "@a", "@a", "instagram")
    assert res.ok is False and "same" in res.error.lower()

def test_review_shows_approval_not_publish_now(tmp_path, monkeypatch):
    # post-approval-lifecycle: Review is the APPROVE worklist. Publish-now moved to the Schedule (it is
    # queued-only, and Review shows awaiting_approval posts). Review must offer Approve, never Publish now.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed(cfg, state=PostState.awaiting_approval)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().get("/review")
    assert r.status_code == 200 and b"Approve selected" in r.data and b"Publish now" not in r.data
