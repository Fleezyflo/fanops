# Regenerate hook: re-run the frame-seeing hook author for ONE editable post.
# Ingest parity (weak / brand / language) rejects without writing. Acceptance burns
# through reburn_hook. The model call is injectable; these tests never hit a CLI.
from datetime import datetime, timedelta, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, ClipState, Fmt, Moment, MomentState, Platform, Post, PostState, Source)
from fanops.studio.actions import regenerate_hook

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
FUTURE = "2099-01-01T00:00:00Z"
_TRUSTED = {"avg_logprob": -0.3, "compression_ratio": 1.1}


def _seed(cfg, *, hook="OLD HOOK", lang="en", state=PostState.awaiting_approval,
          excerpt="the beat drops here", hook_removed="REMOVED LINE"):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language=lang))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="the pocket opens", transcript_excerpt=excerpt, hook=hook,
                          hook_removed=hook_removed, state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16,
                      state=ClipState.captioned))
    led.add_post(Post(id="p_edit", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="KEEP CAPTION", state=state,
                      scheduled_time=FUTURE, public_url="dryrun://p_edit"))
    led.save()
    return led


def _moment(led, mid, parent, hook, start, token):
    led.add_moment(Moment(id=mid, parent_id=parent, content_token=token, start=start, end=start + 5,
                          reason="r", hook=hook, state=MomentState.clipped))


def _model(hook="FRESH HOOK"):
    def m(prompt, schema):
        return {"hook": hook}
    return m


def _mock_render(monkeypatch, cfg):
    rendered = Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16,
                    state=ClipState.rendered)
    monkeypatch.setattr("fanops.clip.render_moment", lambda *a, **k: (Ledger.load(cfg), rendered))


def _fresh_speech(led):
    led.sources["src_1"].transcript = [{
        "start": 1.0, "end": 4.0, "text": "fresh words here", **_TRUSTED,
    }]
    led.sources["src_1"].signal_peaks = [{"t": 3.0, "e": 1}, {"t": 99.0, "e": 1}]
    led.moments["mom_1"].transcript_excerpt = "stale line"
    led.save()


def test_regenerate_hook_updates_moment_and_keeps_awaiting(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); _seed(cfg); _mock_render(monkeypatch, cfg)
    res = regenerate_hook(cfg, "p_edit", "shorter", model=_model("the pocket stays"), now=NOW)
    assert res.ok is True and res.detail["hook"] == "the pocket stays"
    led = Ledger.load(cfg)
    moment = led.moments["mom_1"]
    assert moment.hook == "the pocket stays"
    assert moment.hook_removed is None
    post = led.posts["p_edit"]
    assert post.state is PostState.awaiting_approval
    assert post.caption == "KEEP CAPTION"


def test_regenerate_hook_keeps_latin_hook_on_french_source(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    _seed(cfg, lang="fr")
    _mock_render(monkeypatch, cfg)
    res = regenerate_hook(cfg, "p_edit", "", model=_model("the pocket stays"), now=NOW)
    assert res.ok is True
    assert Ledger.load(cfg).moments["mom_1"].hook == "the pocket stays"


def test_regenerate_hook_rejects_arabic_hook_on_french_source(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    _seed(cfg, lang="fr")
    _mock_render(monkeypatch, cfg)
    res = regenerate_hook(cfg, "p_edit", "", model=_model("مرحبا بالعالم"), now=NOW)
    assert res.ok is False
    assert "language does not match" in (res.error or "")
    assert Ledger.load(cfg).moments["mom_1"].hook == "OLD HOOK"


def test_regenerate_hook_prompt_sees_guidance_excerpt_window_and_frames(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); led = _seed(cfg); _fresh_speech(led); _mock_render(monkeypatch, cfg)
    cfg.context_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.context_path.write_text("BRAND BRIEF XYZ")
    monkeypatch.setattr("fanops.moments._window_frames", lambda *a, **k: ["/tmp/frame.jpg"])
    seen = {}
    def m(prompt, schema):
        seen["prompt"] = prompt
        seen["schema"] = schema
        return {"hook": "the fresh pocket"}
    res = regenerate_hook(cfg, "p_edit", "mention the pocket", model=m, now=NOW)
    assert res.ok is True
    prompt = seen["prompt"]
    assert "mention the pocket" in prompt
    assert "OPERATOR INSTRUCTION FOR THIS REGENERATION" in prompt
    assert "BRAND BRIEF XYZ" in prompt
    assert "fresh words here" in prompt
    assert "stale line" not in prompt
    assert "the pocket opens" in prompt
    assert "3.0" in prompt and "99.0" not in prompt
    assert "stills attached" in prompt
    assert "hook" in seen["schema"].get("properties", {})
    assert Ledger.load(cfg).moments["mom_1"].transcript_excerpt == "fresh words here"


def test_regenerate_hook_default_model_attaches_window_frames(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); _seed(cfg); _mock_render(monkeypatch, cfg)
    monkeypatch.setattr("fanops.moments._window_frames", lambda *a, **k: ["/tmp/frame.jpg"])
    seen = {}
    def fake(prompt, schema, **kw):
        seen["images"] = kw.get("images")
        seen["model"] = kw.get("model")
        seen["read_root"] = kw.get("read_root")
        return {"hook": "the pocket frame"}, "moment-hooks", False
    monkeypatch.setattr("fanops.llm.claude_json_meta", fake)
    res = regenerate_hook(cfg, "p_edit", "", now=NOW)
    assert res.ok is True
    assert seen["images"] == ["/tmp/frame.jpg"]
    assert seen["model"] == cfg.llm_model_for("moment_hooks")
    assert seen["read_root"] == str(cfg.agent_io / "keyframes")
    assert Ledger.load(cfg).moments["mom_1"].hook == "the pocket frame"


def test_regenerate_hook_frames_unread_does_not_persist(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); _seed(cfg)
    monkeypatch.setattr("fanops.moments._window_frames", lambda *a, **k: ["/tmp/frame.jpg"])
    monkeypatch.setattr("fanops.llm.claude_json_meta",
                        lambda prompt, schema, **kw: ({"hook": "the pocket opens"}, "moment-hooks", True))
    res = regenerate_hook(cfg, "p_edit", "", now=NOW)
    assert res.ok is False and "frames were not read" in (res.error or "")
    assert "try again" in (res.error or "").lower()
    moment = Ledger.load(cfg).moments["mom_1"]
    assert moment.hook == "OLD HOOK" and moment.hook_removed == "REMOVED LINE"


def test_regenerate_hook_frames_unread_error_does_not_persist(tmp_path, monkeypatch):
    from fanops.llm import LlmFramesUnreadError
    cfg = Config(root=tmp_path); _seed(cfg)
    monkeypatch.setattr("fanops.moments._window_frames", lambda *a, **k: ["/tmp/frame.jpg"])

    def fake(prompt, schema, **kw):
        raise LlmFramesUnreadError("attached frames unread")
    monkeypatch.setattr("fanops.llm.claude_json_meta", fake)
    res = regenerate_hook(cfg, "p_edit", "", now=NOW)
    assert res.ok is False and "frames were not read" in (res.error or "")
    assert Ledger.load(cfg).moments["mom_1"].hook == "OLD HOOK"


def test_regenerate_hook_same_line_is_not_a_self_duplicate(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); _seed(cfg, hook="the pocket stays"); _mock_render(monkeypatch, cfg)
    res = regenerate_hook(cfg, "p_edit", "", model=_model("the pocket stays"), now=NOW)
    assert res.ok is True
    assert Ledger.load(cfg).moments["mom_1"].hook == "the pocket stays"
    assert Ledger.load(cfg).moments["mom_1"].hook_removed is None


def test_regenerate_hook_rejects_weak_duplicate_without_write(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.add_source(Source(id="src_2", source_path="/o.mp4", language="en"))
    _moment(led, "mom_2", "src_2", "TAKEN LINE", 20, "20-25")
    led.save()
    res = regenerate_hook(cfg, "p_edit", "", model=_model("TAKEN LINE"), now=NOW)
    assert res.ok is False and "weak hook" in (res.error or "")
    assert "try again" in (res.error or "").lower()
    moment = Ledger.load(cfg).moments["mom_1"]
    assert moment.hook == "OLD HOOK" and moment.hook_removed == "REMOVED LINE"


def test_regenerate_hook_rejects_same_source_cluster_without_write(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    for i, word in enumerate(("drop", "beat", "snare")):
        _moment(led, f"mom_c{i}", "src_1", f"wait for the {word}", 10 + i * 8, f"c{i}")
    led.save()
    res = regenerate_hook(cfg, "p_edit", "", model=_model("wait for the chorus"), now=NOW)
    assert res.ok is False and "weak hook" in (res.error or "")
    assert Ledger.load(cfg).moments["mom_1"].hook == "OLD HOOK"


def test_regenerate_hook_other_source_cluster_does_not_reject(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); led = _seed(cfg); _mock_render(monkeypatch, cfg)
    led.add_source(Source(id="src_2", source_path="/o.mp4", language="en"))
    for i, word in enumerate(("drop", "beat", "snare")):
        _moment(led, f"mom_o{i}", "src_2", f"wait for the {word}", 10 + i * 8, f"o{i}")
    led.save()
    res = regenerate_hook(cfg, "p_edit", "", model=_model("wait for the pocket chorus"), now=NOW)
    assert res.ok is True
    assert Ledger.load(cfg).moments["mom_1"].hook == "wait for the pocket chorus"


def test_regenerate_hook_rejects_brand_without_write(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg); _fresh_speech(led)
    res = regenerate_hook(cfg, "p_edit", "", model=_model("check the link in bio"), now=NOW)
    assert res.ok is False and "brand" in (res.error or "").lower()
    assert "try again" in (res.error or "").lower()
    moment = Ledger.load(cfg).moments["mom_1"]
    assert moment.hook == "OLD HOOK"
    assert moment.hook_removed == "REMOVED LINE"
    assert moment.transcript_excerpt == "stale line"
    assert Ledger.load(cfg).posts["p_edit"].state is PostState.awaiting_approval


def test_regenerate_hook_rejects_lang_without_write(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, lang="en")
    res = regenerate_hook(cfg, "p_edit", "", model=_model("هذا الخط يمسك المشاهد"), now=NOW)
    assert res.ok is False and "language" in (res.error or "").lower()
    assert "try again" in (res.error or "").lower()
    moment = Ledger.load(cfg).moments["mom_1"]
    assert moment.hook == "OLD HOOK" and moment.hook_removed == "REMOVED LINE"


def test_regenerate_hook_refuses_before_render_when_queued_post_becomes_imminent(tmp_path, monkeypatch):
    # Queued, 6 minutes out: editable at the entry clock. The model call moves the clock
    # past the 5-minute window. render_moment must not run, and the moment hook stays.
    cfg = Config(root=tmp_path)
    led = _seed(cfg, state=PostState.queued)
    led.posts["p_edit"].scheduled_time = "2026-06-06T12:06:00Z"
    led.save()
    clock = {"t": NOW}

    def fake_now(n=None):
        return n if n is not None else clock["t"]

    monkeypatch.setattr("fanops.studio.actions._now", fake_now)

    def boom(*_a, **_k):
        raise AssertionError("render_moment ran after the post became imminent")

    monkeypatch.setattr("fanops.clip.render_moment", boom)

    def model(_prompt, _schema):
        clock["t"] = NOW + timedelta(minutes=10)
        return {"hook": "the pocket stays"}

    res = regenerate_hook(cfg, "p_edit", "", model=model, now=NOW)
    assert res.ok is False and "imminent" in (res.error or "").lower()
    assert Ledger.load(cfg).moments["mom_1"].hook == "OLD HOOK"
    assert Ledger.load(cfg).posts["p_edit"].state is PostState.queued
    assert Ledger.load(cfg).posts["p_edit"].scheduled_time == "2026-06-06T12:06:00Z"


def test_regenerate_hook_unknown_post(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = regenerate_hook(cfg, "nope", "", model=_model("X"), now=NOW)
    assert res.ok is False and "no such post" in (res.error or "").lower()
    assert Ledger.load(cfg).moments["mom_1"].hook == "OLD HOOK"


def test_review_renders_regenerate_hook_beside_reburn(tmp_path):
    import json
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
    ]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    (cfg.clips / "clip_1.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    led = _seed(cfg)
    led.clips["clip_1"].path = str(cfg.clips / "clip_1.mp4")
    led.save()
    app = create_app(cfg); app.config.update(TESTING=True)
    client = app.test_client()
    for url in ("/review?account=all", "/review?account=a"):
        html = client.get(url).data.decode()
        assert html.count('id="hook-p_edit"') == 1
        assert html.count('name="hook"') == 1
        assert 'class="hook-edit"' in html and 'class="hook-regen"' in html
        assert 'hx-post="/reburn-hook/p_edit' in html
        assert 'hx-post="/regenerate-hook/p_edit' in html
        assert "Re-burn text" in html and "Regenerate hook" in html
        assert html.find('id="hook-p_edit"') < html.find("/regenerate-hook/p_edit")


def test_regenerate_hook_route_swaps_hook_field(tmp_path, monkeypatch):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    (cfg.clips / "clip_1.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    led = _seed(cfg)
    led.clips["clip_1"].path = str(cfg.clips / "clip_1.mp4")
    led.save()
    rendered = Clip(id="clip_1", parent_id="mom_1", path=str(cfg.clips / "clip_1.mp4"),
                    aspect=Fmt.r9x16, state=ClipState.rendered)
    monkeypatch.setattr("fanops.clip.render_moment", lambda *a, **k: (Ledger.load(cfg), rendered))
    monkeypatch.setattr("fanops.llm.claude_json_meta",
                        lambda prompt, schema, **kw: ({"hook": "the pocket routed"}, "moment-hooks", False))
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/regenerate-hook/p_edit", data={"guidance": "shorter", "swap": "card"})
    assert r.status_code == 200
    body = r.data.decode()
    assert "the pocket routed" in body and "Regenerated" in body
    assert 'hx-post="/regenerate-hook/p_edit' in body and 'hx-post="/reburn-hook/p_edit' in body
    assert "couldn't burn" not in body
    saved = Ledger.load(cfg)
    assert saved.moments["mom_1"].hook == "the pocket routed"
    assert saved.posts["p_edit"].state is PostState.awaiting_approval
    assert saved.posts["p_edit"].caption == "KEEP CAPTION"
