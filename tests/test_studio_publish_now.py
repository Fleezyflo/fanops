# tests/test_studio_publish_now.py — the Studio "Publish now" action/route: ship ONE reviewed post
# immediately via the same poster path the pipeline uses (publish_post), ignoring its schedule.
# Milestone 5 (publish in the UI). The engine is covered by test_publish_post.py; here we prove the
# Studio guards (queued-only, live-confirm, fatal-auth) + wiring.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops.studio import actions

FUTURE = "2099-01-01T00:00:00Z"

def _seed(cfg, *, state=PostState.queued, when=FUTURE, media=None, post_type="post"):
    led = Ledger.load(cfg)
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "clip_1.mp4").write_bytes(b"V")
    led.add_source(Source(id="s1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="m1", path=str(cdir / "clip_1.mp4"), aspect=Fmt.r9x16,
                      state=ClipState.queued))
    led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="ship it", state=state,
                      scheduled_time=when, media_urls=media or [], public_url="dryrun://p1",
                      post_type=post_type))
    led.save(); return led


def _postiz_ready(cfg, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "account_id": "1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "1"}, "backends": {"instagram": "postiz"}}]}))


class _R:
    def __init__(self, code, body=None, text=""):
        self.status_code = code
        self._b = body if body is not None else {}
        self.text = text

    def json(self):
        return self._b


def test_publish_now_dryrun_blocked_in_studio(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); _seed(cfg)
    res = actions.publish_now(cfg, "p1")
    assert not res.ok and "not live" in res.error.lower()
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued

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
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path); _seed(cfg)
    res = actions.publish_now(cfg, "p1", confirmed=False)
    assert res.ok is False and "confirm" in res.error.lower()
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued           # not shipped without confirm

def test_publish_now_surfaces_fatal_auth(tmp_path, monkeypatch, mocker):
    cfg = Config(root=tmp_path)
    _postiz_ready(cfg, monkeypatch)
    _seed(cfg, media=["https://uploads.postiz.com/x.mp4"])
    mocker.patch("fanops.post.postiz.requests.get",
                 return_value=_R(200, [{"id": "1", "identifier": "instagram-standalone", "name": "ig"}]))
    mocker.patch("fanops.post.postiz.requests.post", return_value=_R(401, {}, text="unauthorized"))
    res = actions.publish_now(cfg, "p1", confirmed=True)
    assert res.ok is False and "FATAL" in res.error and "POSTIZ_API_KEY" in res.error


# ---- Flask wiring ----
def test_publish_now_route_blocks_dryrun(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/publish/now/p1")
    assert r.status_code == 200 and b"publishing is off" in r.data.lower()
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued

def test_schedule_publish_blocks_when_not_live(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/schedule/publish/p1")
    assert r.status_code == 200 and "publishing is off" in r.data.decode().lower()
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued

def test_crosspost_all_rejects_source_equals_target(tmp_path, monkeypatch):
    # Phase 1 footgun fix: bulk backfill is CROSS-account; picking the same account for source + target
    # is a no-op (every clip already lives there). Reject up front with a clear message, before any work.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    res = actions.crosspost_all_to_account(cfg, "a", "a", "instagram")
    assert res.ok is False and "same" in res.error.lower()

def test_review_shows_approval_not_publish_now(tmp_path, monkeypatch):
    # post-approval-lifecycle: Review is the APPROVE worklist. Publish-now moved to the Schedule (it is
    # queued-only, and Review shows awaiting_approval posts). Review must offer Approve, never Publish now.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed(cfg, state=PostState.awaiting_approval)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().get("/review?account=all")
    assert r.status_code == 200 and b"Approve selected" in r.data and b"Publish now" not in r.data


# ---- T10: publish preflight fail-fast on an unhealthy real backend probe ----

def test_publish_now_blocks_when_postiz_probe_unhealthy(tmp_path, monkeypatch, mocker):
    # The nginx health-check LIES; Postiz is crash-looping (502 on the real /integrations probe). A publish
    # must FAIL FAST with a POSTIZ_OPS pointer BEFORE submitting — never submit-then-park in needs_reconcile.
    cfg = Config(root=tmp_path)
    _postiz_ready(cfg, monkeypatch)
    _seed(cfg, media=["https://uploads.postiz.com/x.mp4"])
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(502, {}, text="Bad Gateway"))
    res = actions.publish_now(cfg, "p1", confirmed=True)
    assert res.ok is False and "POSTIZ_OPS" in res.error
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued           # NOT submitted-then-parked


# ---- MOL-179: platform cap reads realized clip duration (not moment envelope alone) ----

def _seed_cap_reuse(cfg, *, window, cut_seconds):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@b", "account_id": "ig_b", "platforms": ["instagram"], "status": "active"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-120",
                              start=window[0], end=window[1], reason="r", state=MomentState.clipped))
        cpath = cfg.clips / "c.mp4"; cpath.write_bytes(b"\x00")
        clip = Clip(id="clip_0", parent_id="mom_1", path=str(cpath), aspect=Fmt.r9x16,
                    state=ClipState.queued, cut_seconds=cut_seconds)
        clip.meta_captions = {"b/instagram": {"caption": "reuse me", "hashtags": ["#x"]}}
        led.add_clip(clip)


def test_cap_reads_realized_not_envelope(tmp_path):
    # envelope 120s > IG 90s cap, but cut_seconds 60s -> reuse ADMITS (MOL-179).
    from fanops.studio.actions import crosspost_to_account
    cfg = Config(root=tmp_path); _seed_cap_reuse(cfg, window=(0.0, 120.0), cut_seconds=60.0)
    r = crosspost_to_account(cfg, "clip_0", "b", "instagram")
    assert r.ok and r.detail.get("already_exists") is False


def test_cap_old_clip_falls_back_to_envelope(tmp_path):
    # cut_seconds=None -> envelope 120s > IG 90s -> reuse REJECTED (MOL-179).
    from fanops.studio.actions import crosspost_to_account
    cfg = Config(root=tmp_path); _seed_cap_reuse(cfg, window=(0.0, 120.0), cut_seconds=None)
    r = crosspost_to_account(cfg, "clip_0", "b", "instagram")
    assert not r.ok and "exceeds" in (r.error or "")
    assert not Ledger.load(cfg).posts


def test_approve_rejects_over_cap_realized(tmp_path):
    # Approve-side cap uses realized_clip_seconds the same way reuse does (MOL-179 / MOL-832).
    from fanops.studio.actions_approve import approve_posts
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-120",
                              start=0.0, end=120.0, reason="r", state=MomentState.clipped))
        cpath = cfg.clips / "c.mp4"; cpath.write_bytes(b"\x00")
        led.add_clip(Clip(id="clip_1", parent_id="mom_1", path=str(cpath), aspect=Fmt.r9x16,
                          state=ClipState.captioned, cut_seconds=120.0))
        led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1",
                          platform=Platform.instagram, caption="cap",
                          state=PostState.awaiting_approval))
    r = approve_posts(cfg, ["p1"])
    assert r.ok and r.detail.get("approved") == 0 and r.detail.get("cut_over_cap") == 1
    assert Ledger.load(cfg).posts["p1"].state is PostState.awaiting_approval


def test_publish_guard_passes_when_postiz_probe_healthy(tmp_path, monkeypatch, mocker):
    # A HEALTHY real probe must NOT block — the guard is fail-fast on down, transparent when up.
    cfg = Config(root=tmp_path)
    _postiz_ready(cfg, monkeypatch)
    _seed(cfg, media=["https://uploads.postiz.com/x.mp4"])
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(200, []))
    post = Ledger.load(cfg).posts["p1"]
    assert actions._studio_publish_guard(cfg, post) is None                   # healthy probe -> not blocked
