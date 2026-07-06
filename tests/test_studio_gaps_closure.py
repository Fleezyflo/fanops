# Gap-closure: preview media, review_nav, casting gates, retry rate-limit.
import json
from pathlib import Path
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops.studio import views, actions
from fanops.studio.preview_media import preview_media_path

def _accounts(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "ig1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig1"}}]}))

def _seed_awaiting(cfg, hook="WAIT"):
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    (cdir / "c0.mp4").write_bytes(b"V" * 100)
    led.add_clip(Clip(id="c0", parent_id="m1", path=str(cdir / "c0.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id="p0", parent_id="c0", account="a", account_id="ig1", platform=Platform.instagram,
                      caption="c", state=PostState.awaiting_approval, variant_hook=hook))
    led.save()

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def test_review_nav_params_includes_focus(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed_awaiting(cfg)
    p = views.review_nav_params(cfg, "a")
    assert p["view"] == "account" and p["focus"] == 1 and p["account"] == "a"

def test_focus_uses_media_preview_url(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _accounts(cfg); _seed_awaiting(cfg, hook="HOOK")
    html = _client(cfg).get("/review?account=@a&view=account&focus=1&fi=0").data.decode()
    assert "/media-preview/p0" in html

def test_preview_media_returns_playable_path(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _accounts(cfg); _seed_awaiting(cfg, hook="HOOK")
    led = Ledger.load(cfg)
    path = preview_media_path(cfg, led, "p0")
    assert path and Path(path).exists()

def test_pipeline_status_counts_casting_gate(tmp_path, monkeypatch):
    from fanops.agentstep import write_request
    cfg = Config(root=tmp_path)
    write_request(cfg, kind="moment_casting", key="src1", payload={"source_id": "src1", "moments": [], "personas": []})
    ps = views.pipeline_status(cfg)
    assert ps.get("pending_moment_casting", 0) >= 1

def test_retry_rate_limited_failures(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed_awaiting(cfg, hook=None)
    led = Ledger.load(cfg)
    p = led.posts["p0"]; p.state = PostState.failed; p.error_reason = "postiz 429"; led.save()
    res = actions.retry_rate_limited_failures(cfg)
    assert res.ok and res.detail["retried"] == 1
    assert Ledger.load(cfg).posts["p0"].state is PostState.queued

def test_spine_next_links_focus_review(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed_awaiting(cfg)
    html = _client(cfg).get("/run").data.decode()
    assert "focus=1" in html and "view=account" in html


def test_restore_persona_hook_reburns(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _accounts(cfg); _seed_awaiting(cfg, hook=None)
    led = Ledger.load(cfg)
    led.moments["m1"] = led.moments["m1"].model_copy(update={"hook_removed": "STRIPPED"})
    led.save()
    res = actions.restore_persona_hook(cfg, "p0")
    assert res.ok
    led2 = Ledger.load(cfg)
    assert led2.posts["p0"].variant_hook == "STRIPPED"
    assert led2.moments["m1"].hook == "STRIPPED" and led2.moments["m1"].hook_removed is None

def test_retry_rate_limit_staggers_schedule(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed_awaiting(cfg, hook=None)
    led = Ledger.load(cfg)
    for i, pid in enumerate(["p0", "p1"]):
        if pid not in led.posts:
            led.add_post(Post(id=pid, parent_id="c0", account="a", account_id="ig1", platform=Platform.instagram,
                              caption="c", state=PostState.failed, error_reason="postiz 429"))
        else:
            led.posts[pid].state = PostState.failed; led.posts[pid].error_reason = "postiz 429"
    led.save()
    res = actions.retry_rate_limited_failures(cfg)
    assert res.ok and res.detail["retried"] == 2
    times = [Ledger.load(cfg).posts[pid].scheduled_time for pid in ("p0", "p1")]
    assert times[0] and times[1] and times[0] != times[1]

def test_zero_post_clips_surfaces_orphans(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    (cdir / "orph.mp4").write_bytes(b"V")
    led.add_clip(Clip(id="orph", parent_id="m1", path=str(cdir / "orph.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))
    led.save()
    assert len(views.zero_post_clips(cfg)) == 1

def test_home_renders_zero_post_clip_warning(tmp_path):
    # home.html's {% if zero_post_clips %} block must actually receive the projection —
    # the view existed but the route never passed it, so the warning silently never rendered.
    cfg = Config(root=tmp_path); _accounts(cfg)
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    (cdir / "orph.mp4").write_bytes(b"V")
    led.add_clip(Clip(id="orph", parent_id="m1", path=str(cdir / "orph.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))
    led.save()
    html = _client(cfg).get("/").data.decode()
    assert "birthed zero posts" in html and "orph" in html


def test_answer_casting_gate_from_studio(tmp_path):
    from fanops.agentstep import write_request, latest_request_id
    cfg = Config(root=tmp_path)
    write_request(cfg, kind="moment_casting", key="src1", payload={
        "source_id": "src1", "moments": [{"moment_id": "m1", "start": 0, "end": 7, "reason": "r"}],
        "personas": [{"handle": "@a", "persona": "p"}]})
    rid = latest_request_id(cfg, "moment_casting", "src1")
    class F:
        def get(self, k): return None
        def __iter__(self):
            return iter(["cast__@a__m1"])
        def getlist(self, k): return []
    class Form(dict):
        def get(self, k, default=None):
            return super().get(k, default)
    f = Form({"cast__@a__m1": "1"})
    from fanops.studio.app import _parse_gate_form
    data = _parse_gate_form("moment_casting", f)
    assert data["selections"]["a"] == ["m1"]
    res = actions.answer_gate(cfg, "moment_casting", "src1", {**data, "request_id": rid})
    assert res.ok

def test_account_work_counts_includes_review_batch(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed_awaiting(cfg)
    led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c0", account="a", account_id="ig1", platform=Platform.instagram, caption="c", state=PostState.awaiting_approval))
    led.save()
    with Ledger.transaction(cfg) as led:
        for p in led.posts.values(): p.batch_id = "b1"
    wc = views.account_work_counts(cfg)
    assert wc["a"].get("review_batch") == "b1"
