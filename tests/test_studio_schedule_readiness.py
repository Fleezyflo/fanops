# tests/test_studio_schedule_readiness.py — S5: make the Schedule honest. "Publish now" gave no signal the
# artifact actually exists/coheres, and the suggested time was printed with no rationale. publish_readiness()
# is a pure (ready, reason) over already-loaded objects (a real per-account render that's shippable + its burned
# hook matches the shown one, ELSE a reusable shared clip; torn lineage -> not ready); explain_suggested_time()
# is one plain sentence naming the account/platform/lead. Both ADVISORY — neither ever gates publish.
from datetime import datetime, timezone, timedelta
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, Render, Platform, PostState, ClipState,
                           MomentState, RenderState, Fmt)
from fanops.studio import views

NOW = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
FUTURE = (NOW + timedelta(hours=5)).isoformat()        # safely beyond the imminent threshold -> editable


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


def _led_with(cfg, *, render=None, clip_state=ClipState.queued, post_over=None):
    cfg.clips.mkdir(parents=True, exist_ok=True); base = cfg.clips / "b.mp4"; base.write_bytes(b"\x00ftypmp42")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s", source_path="/v.mp4"))
        led.add_moment(Moment(id="m", parent_id="s", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
        led.add_clip(Clip(id="c", parent_id="m", path=str(base), aspect=Fmt.r9x16, state=clip_state))
        if render is not None: led.add_render(render)
        po = dict(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                  caption="x", state=PostState.queued)
        po.update(post_over or {})
        led.add_post(Post(**po))
    return Ledger.load(cfg)


# ── publish_readiness: (ready, reason) ─────────────────────────────────────────────────────────────
def test_ready_when_render_shippable_and_hook_matches(tmp_path):
    cfg = Config(root=tmp_path)
    r = Render(id="r1", clip_id="c", account="@a", surface_key="@a/instagram", hook_text="H",
               path=str(cfg.clips / "b.mp4"), state=RenderState.rendered, is_account_cut=True)
    led = _led_with(cfg, render=r, post_over={"render_id": "r1", "variant_hook": "H"})
    ready, reason = views.publish_readiness(led, led.posts["p"])
    assert ready is True and "cut" in reason


def test_ready_when_no_render_but_clip_reusable(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led_with(cfg, clip_state=ClipState.queued)              # no render_id -> shared clip path
    ready, reason = views.publish_readiness(led, led.posts["p"])
    assert ready is True and "shared" in reason


def test_not_ready_when_render_record_missing(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led_with(cfg, post_over={"render_id": "ghost"})         # render_id points at nothing
    ready, reason = views.publish_readiness(led, led.posts["p"])
    assert ready is False and "render" in reason.lower()


def test_not_ready_when_render_not_finished(tmp_path):
    cfg = Config(root=tmp_path)
    r = Render(id="r1", clip_id="c", account="@a", surface_key="@a/instagram", hook_text="H",
               path=str(cfg.clips / "b.mp4"), state=RenderState.retired)
    led = _led_with(cfg, render=r, post_over={"render_id": "r1", "variant_hook": "H"})
    ready, reason = views.publish_readiness(led, led.posts["p"])
    assert ready is False


def test_not_ready_on_hook_drift(tmp_path):
    cfg = Config(root=tmp_path)
    r = Render(id="r1", clip_id="c", account="@a", surface_key="@a/instagram", hook_text="BURNED",
               path=str(cfg.clips / "b.mp4"), state=RenderState.rendered, is_account_cut=True)
    led = _led_with(cfg, render=r, post_over={"render_id": "r1", "variant_hook": "SHOWN"})
    ready, reason = views.publish_readiness(led, led.posts["p"])
    assert ready is False and "drift" in reason.lower()


def test_not_ready_when_clip_not_shippable(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led_with(cfg, clip_state=ClipState.held)               # held clip is not reusable
    ready, reason = views.publish_readiness(led, led.posts["p"])
    assert ready is False


def test_ready_when_render_is_queued_state(tmp_path):
    # audit LOW: RenderState.queued is a legit pre-ship state (mirrors ClipState.queued) -> shippable, not a warn
    cfg = Config(root=tmp_path)
    r = Render(id="r1", clip_id="c", account="@a", surface_key="@a/instagram", hook_text="H",
               path=str(cfg.clips / "b.mp4"), state=RenderState.queued, is_account_cut=True)
    led = _led_with(cfg, render=r, post_over={"render_id": "r1", "variant_hook": "H"})
    ready, _ = views.publish_readiness(led, led.posts["p"])
    assert ready is True


def test_not_ready_when_render_file_absent(tmp_path):
    # audit LOW: the chip must not say "ready" when the artifact file is gone — the publish would fail downstream
    cfg = Config(root=tmp_path)
    r = Render(id="r1", clip_id="c", account="@a", surface_key="@a/instagram", hook_text="H",
               path=str(cfg.clips / "GONE.mp4"), state=RenderState.rendered, is_account_cut=True)  # no such file
    led = _led_with(cfg, render=r, post_over={"render_id": "r1", "variant_hook": "H"})
    ready, reason = views.publish_readiness(led, led.posts["p"])
    assert ready is False and "disk" in reason.lower()


def test_not_ready_when_shared_clip_file_absent(tmp_path):
    # the shared-clip path gets the same honesty: a missing clip file is not "ready"
    cfg = Config(root=tmp_path)
    led = _led_with(cfg, clip_state=ClipState.queued)
    # blow away the clip file on disk after the ledger was built
    (cfg.clips / "b.mp4").unlink()
    ready, reason = views.publish_readiness(led, led.posts["p"])
    assert ready is False and "disk" in reason.lower()


def test_publish_readiness_fail_open(tmp_path):
    class Weird: pass
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    ready, reason = views.publish_readiness(led, Weird())          # missing every attr -> never raises
    assert ready is False and isinstance(reason, str)


# ── explain_suggested_time: one plain why-sentence ─────────────────────────────────────────────────
def test_explain_suggested_time_names_account_platform_lead(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_PUBLISH_LEAD_MINUTES", "45")
    cfg = Config(root=tmp_path)
    row = views.ScheduleRow(post_id="p", scheduled_time=None, account="@a", platform="instagram",
                            clip_id="c", state="queued", imminent=False, editable=True)
    why = views.explain_suggested_time(cfg, row)
    assert "@a" in why and "instagram" in why and "45" in why


# ── rows carry readiness/why only on editable rows ─────────────────────────────────────────────────
def test_editable_row_carries_readiness_and_why(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led_with(cfg, clip_state=ClipState.queued, post_over={"scheduled_time": FUTURE})   # future -> editable
    rows = views.schedule_rows(led, cfg, now=NOW)
    row = next(r for r in rows if r.post_id == "p")
    assert row.editable and row.ready is True and row.why_suggested and "@a" in row.why_suggested


def test_readonly_past_row_has_no_readiness(tmp_path):
    cfg = Config(root=tmp_path)
    # a published (recent, read-only) row -> not editable -> readiness/why stay None
    led = _led_with(cfg, clip_state=ClipState.published,
                    post_over={"state": PostState.published, "scheduled_time": NOW.isoformat()})
    rows = views.schedule_rows(led, cfg, now=NOW)
    row = next(r for r in rows if r.post_id == "p")
    assert row.editable is False and row.ready is None and row.why_suggested is None


# ── route renders both the ready chip and the why-string ───────────────────────────────────────────
def test_schedule_route_shows_ready_and_why(tmp_path):
    cfg = Config(root=tmp_path)
    # the route reads REAL wall-clock now (no now injection), so seed a future RELATIVE to real now — the
    # fixed module-level FUTURE time-bombs once wall-clock passes it (row stops being editable -> no chip).
    route_future = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    _led_with(cfg, clip_state=ClipState.queued, post_over={"scheduled_time": route_future})
    html = _client(cfg).get("/schedule").data.decode()
    assert "schedule-ready" in html                              # the readiness chip rendered
    assert "lead" in html.lower()                                # the suggested-time rationale rendered


def test_cv_off_row_is_ready_not_warn(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0"); monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    cfg = Config(root=tmp_path)
    led = _led_with(cfg, clip_state=ClipState.queued, post_over={"scheduled_time": FUTURE})   # CV OFF -> shared clip -> READY
    rows = views.schedule_rows(led, cfg, now=NOW)
    row = next(r for r in rows if r.post_id == "p")
    assert row.ready is True                                     # a shared clip is the expected OFF artifact
