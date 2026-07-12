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
    led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="ship it", state=state,
                      scheduled_time=when, media_urls=media or [], public_url="dryrun://p1"))
    led.save(); return led


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

def test_publish_now_surfaces_fatal_auth(tmp_path, monkeypatch):
    from fanops.errors import PostizAuthError
    import fanops.post.run as run
    import fanops.post.postiz as postiz
    from fanops.post.postiz import PostizHealth
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path); _seed(cfg, media=["file://x.mp4"])         # pre-stamped -> skips ensure_clip_media
    monkeypatch.setattr(postiz, "postiz_health_probe", lambda c: PostizHealth(True, 200, ""))   # T10: probe healthy -> reach the poster-auth path this test exercises
    monkeypatch.setattr(run, "get_media_uploader", lambda cfg, backend=None: (lambda c, p, **kw: "https://x/u.mp4"))
    class Boom:
        def publish(self, led, post_id): raise PostizAuthError("401 unauthorized")
    monkeypatch.setattr(run, "get_poster", lambda cfg, backend=None: Boom())
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

def test_publish_now_blocks_when_postiz_probe_unhealthy(tmp_path, monkeypatch):
    # The nginx health-check LIES; Postiz is crash-looping (502 on the real /integrations probe). A publish
    # must FAIL FAST with a POSTIZ_OPS pointer BEFORE submitting — never submit-then-park in needs_reconcile.
    import fanops.post.postiz as postiz
    from fanops.post.postiz import PostizHealth
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path); _seed(cfg, media=["file://x.mp4"])
    down = PostizHealth(False, 502, "Postiz backend unreachable (502) — see docs/POSTIZ_OPS.md.")
    monkeypatch.setattr(postiz, "postiz_health_probe", lambda c: down)
    res = actions.publish_now(cfg, "p1", confirmed=True)
    assert res.ok is False and "POSTIZ_OPS" in res.error
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued           # NOT submitted-then-parked


# ---- MOL-179: platform cap reads realized clip duration (not moment envelope alone) ----

def _seed_cap_reuse(cfg, *, window, cut_seconds):
    import json
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


def test_three_cap_sites_agree(tmp_path, mocker, monkeypatch):
    # All three cap gates (crosspost, reuse, approve) route through realized_clip_seconds and agree.
    import json, subprocess
    from pathlib import Path as P
    from fanops import clip as clip_mod
    from fanops.crosspost import crosspost_clips, render_spec
    from fanops.variant_learning import _hook_for_post
    from fanops.studio.actions import crosspost_to_account
    from fanops.studio.actions_approve import approve_posts
    from fanops.models import Render, RenderState
    from fanops.accounts import Accounts
    cross_spy = mocker.patch("fanops.crosspost.realized_clip_seconds", wraps=clip_mod.realized_clip_seconds)
    clip_spy = mocker.spy(clip_mod, "realized_clip_seconds")
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        {"handle": "@b", "account_id": "ig_b", "platforms": ["instagram"], "status": "active"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    real_run = subprocess.run
    def fake_run(cmd, **kw):
        if not (isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "ffmpeg"): return real_run(cmd, **kw)
        out = P(cmd[-1])
        if not str(cmd[-1]).startswith("-"):
            out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    def burn(base, out, hook, **kw):
        P(out).parent.mkdir(parents=True, exist_ok=True); P(out).write_bytes(b"V"); return True
    mocker.patch("fanops.overlay.burn_hook_only", side_effect=burn)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-120", start=0, end=120,
                          reason="r", state=MomentState.clipped, hook="hook A"))
    base = cfg.clips / "clip_1_9x16.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16,
                state=ClipState.captioned, cut_seconds=60.0)
    clip.meta_captions = {"a/instagram": {"caption": "cap", "hashtags": []}, "b/instagram": {"caption": "cap", "hashtags": []}}
    led.add_clip(clip); led.save()
    led = crosspost_clips(Ledger.load(cfg), cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    led.save()
    assert any(p.account == "a" for p in led.posts.values())
    assert cross_spy.call_count >= 1
    n_after_cross = clip_spy.call_count
    r = crosspost_to_account(cfg, "clip_1", "b", "instagram")
    assert r.ok and clip_spy.call_count > n_after_cross
    n_after_reuse = clip_spy.call_count
    p = next(pp for pp in Ledger.load(cfg).posts.values() if pp.account == "a")
    led3 = Ledger.load(cfg)
    hook = _hook_for_post(led3, p); mom = led3.moments.get("mom_1")
    rid, *_ = render_spec(cfg, clip=clip, hook=hook, moment=mom)
    vf = cfg.clips / "ok.mp4"; vf.write_bytes(b"V")
    with Ledger.transaction(cfg) as led2:
        led2.add_render(Render(id=rid, clip_id="clip_1", account="a", surface_key="a|instagram",
                               hook_text=hook, path=str(vf), state=RenderState.rendered,
                               is_account_cut=True, cut_seconds=60.0))
    approve_posts(cfg, [p.id])
    p2 = Ledger.load(cfg).posts[p.id]
    assert p2.state is PostState.queued and clip_spy.call_count > n_after_reuse


def test_publish_guard_self_heals_postiz_via_ensure_up(tmp_path, monkeypatch, mocker):
    # An idle-stopped local Postiz stack should get one ensure_up wake before the guard blocks.
    import fanops.post.postiz as postiz
    from fanops.post.postiz import PostizHealth
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path); _seed(cfg, media=["file://x.mp4"])
    calls = {"n": 0}
    def probe(c):
        calls["n"] += 1
        return PostizHealth(calls["n"] > 1, 200, "") if calls["n"] > 1 else PostizHealth(False, 502, "down")
    monkeypatch.setattr(postiz, "postiz_health_probe", probe)
    ensure = mocker.patch("fanops.postiz_lifecycle.ensure_up")
    post = Ledger.load(cfg).posts["p1"]
    assert actions._studio_publish_guard(cfg, post) is None
    ensure.assert_called_once_with(cfg)


def test_publish_guard_passes_when_postiz_probe_healthy(tmp_path, monkeypatch):
    # A HEALTHY real probe must NOT block — the guard is fail-fast on down, transparent when up. Assert at the
    # guard seam directly (network-free): a healthy probe -> _studio_publish_guard returns None (no block).
    import fanops.post.postiz as postiz
    from fanops.post.postiz import PostizHealth
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path); _seed(cfg, media=["file://x.mp4"])
    monkeypatch.setattr(postiz, "postiz_health_probe", lambda c: PostizHealth(True, 200, ""))
    post = Ledger.load(cfg).posts["p1"]
    assert actions._studio_publish_guard(cfg, post) is None                   # healthy probe -> not blocked
