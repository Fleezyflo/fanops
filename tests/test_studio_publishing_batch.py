# tests/test_studio_publishing_batch.py — Face 5: Schedule/Posted pagination (base) + batch legibility
# through to publish (fu): a ?batch= filter (reusing Face 4's _batch_arg), a per-row batch label, and a
# read-only per-batch rollup on Posted. Built on a loop fixture that makes a >page bucket (the single-post
# cockpit/posted fixtures can't). The unbatched / no-?batch path is byte-identical to base.
import json
from datetime import datetime, timezone
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt,
                           Batch, LIFT_SCORE)
from fanops.studio import views
from fanops.studio.app import create_app

FAR = "2099-06-01T00:00:00Z"
NOW = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
GPS = views.GRID_PAGE_SIZE

def _accounts(cfg, handles=("a0",)):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": "0", "platforms": ["instagram"], "status": "active"} for h in handles]}))

def _client(cfg):
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def _seed(cfg, n, *, state=PostState.queued, batch_id=None, batch_name=None, lifts=None, account="a0"):
    # n posts p0..p{n-1} (each its own clip) on `account`; optionally stamp batch_id + register the Batch;
    # optional per-index lift_score in metrics. state: queued -> Schedule, published -> Posted.
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/show.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    if batch_id and batch_name:
        led.add_batch(Batch(id=batch_id, name=batch_name, target_accounts=[account], created_at="2026-06-22T00:00:00Z"))
    for i in range(n):
        cid = f"clip_{i}"; (cdir / f"{cid}.mp4").write_bytes(b"V")
        led.add_clip(Clip(id=cid, parent_id="m1", path=str(cdir / f"{cid}.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))
        metrics = {LIFT_SCORE: lifts[i]} if (lifts is not None and i < len(lifts) and lifts[i] is not None) else {}
        led.add_post(Post(id=f"p{i}", parent_id=cid, account=account, account_id="0", platform=Platform.instagram,
                          caption="c", state=state, scheduled_time=FAR, batch_id=batch_id, metrics=metrics, public_url="dryrun://0"))
    led.save()


# ---- view-level: batch filter + label + rollup ----
def test_schedule_rows_batch_filter_and_label(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    _seed(cfg, 2, batch_id="bx", batch_name="Drop")                      # p0,p1 in bx
    led = Ledger.load(cfg)
    led.add_post(Post(id="p_u", parent_id="clip_0", account="a0", account_id="0",
                      platform=Platform.instagram, caption="c", state=PostState.queued, scheduled_time=FAR, public_url="dryrun://p_u")); led.save()
    led = Ledger.load(cfg)
    assert len(views.schedule_rows(led, cfg, now=NOW)) == 3              # all rows unfiltered
    bx = views.schedule_rows(led, cfg, now=NOW, batch="bx")
    assert len(bx) == 2 and all(r.batch_id == "bx" and r.batch_title == "Drop" for r in bx)

def test_schedule_row_dangling_batch_title_none(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    _seed(cfg, 1, batch_id="ghost")                                      # batch_id stamped, NO add_batch
    r = views.schedule_rows(Ledger.load(cfg), cfg, now=NOW)[0]
    assert r.batch_id == "ghost" and r.batch_title is None               # dangling -> no title, no crash

def test_posted_library_batch_filter_and_label(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    _seed(cfg, 2, state=PostState.published, batch_id="bx", batch_name="Drop")
    rows = views.posted_library(Ledger.load(cfg), cfg, batch="bx")
    assert len(rows) == 2 and all(r.batch_title == "Drop" for r in rows)

def test_posted_batch_rollup_mean_over_lifted_only(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    _seed(cfg, 3, state=PostState.published, batch_id="bx", batch_name="Drop", lifts=[0.4, 0.6, None])
    roll = views.posted_batch_rollup(views.posted_library(Ledger.load(cfg), cfg, batch="bx"))
    assert roll["posted"] == 3 and roll["with_lift"] == 2 and abs(roll["mean_lift"] - 0.5) < 1e-9

def test_posted_batch_rollup_no_lift_is_none(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    _seed(cfg, 2, state=PostState.published, batch_id="bx", batch_name="Drop", lifts=[None, None])
    roll = views.posted_batch_rollup(views.posted_library(Ledger.load(cfg), cfg, batch="bx"))
    assert roll["posted"] == 2 and roll["mean_lift"] is None              # never fabricate a lift

def test_posted_batch_rollup_empty_is_none(tmp_path):
    assert views.posted_batch_rollup([]) is None


# ---- route-level: base pagination ----
def test_schedule_paginates(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, GPS + 5)
    html = _client(cfg).get("/schedule").data.decode()
    assert "Show more" in html and f"of {GPS + 5}" in html

def test_schedule_offset_remainder_is_last_page(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, GPS + 5)
    html = _client(cfg).get(f"/schedule?offset={GPS}").data.decode()
    assert f"of {GPS + 5}" in html and "Show more" not in html           # remainder, no further page

def test_schedule_small_bucket_no_pagination(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, 3)
    assert "Show more" not in _client(cfg).get("/schedule").data.decode()  # <=24 -> byte-identical

def test_schedule_offset_clamps_never_500(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, 3)
    for q in ("9999", "-5", "abc"):
        assert _client(cfg).get(f"/schedule?offset={q}").status_code == 200

def test_posted_paginates_and_day_head_re_emits_on_page2(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, GPS + 5, state=PostState.published)
    html2 = _client(cfg).get(f"/posted?offset={GPS}").data.decode()
    assert f"of {GPS + 5}" in html2 and "day-head" in html2              # group re-emits across the boundary


# ---- route-level: batch legibility (fu) ----
def test_schedule_batch_label_and_showmore_carries_batch(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    _seed(cfg, GPS + 2, batch_id="bx", batch_name="Drop7")
    html = _client(cfg).get("/schedule?batch=bx").data.decode()
    assert "Drop7" in html and "batch=bx" in html                       # per-row label + show-more scope

def test_posted_rollup_renders_only_under_batch(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    _seed(cfg, 2, state=PostState.published, batch_id="bx", batch_name="Drop", lifts=[0.4, 0.6])
    assert "This batch:" in _client(cfg).get("/posted?batch=bx").data.decode()
    assert "This batch:" not in _client(cfg).get("/posted").data.decode()  # no rollup without ?batch=

def test_publishing_unbatched_is_byte_identical(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, 2)
    html = _client(cfg).get("/schedule").data.decode()
    assert "batch=" not in html and "batch-tag" not in html             # no batch artifacts on the default path


# ---- D2: action URLs must PRESERVE the ?batch= scope, not only pagination (scope-bleed fix) ----
def test_schedule_action_urls_carry_batch(tmp_path, monkeypatch):
    # D2: filter Schedule to a batch, then act on a row (move/clear/publish/send-back/respread) — the htmx
    # re-render must stay WITHIN the batch. Before the fix only the show-more link carried ?batch=; the
    # action forms dropped it, bouncing the operator back to all-accounts on every edit. Same scope-bleed
    # class RF6/Batch-1 closed for Review's ?account=.
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    cfg = Config(root=tmp_path); _accounts(cfg)
    _seed(cfg, 1, batch_id="bx", batch_name="Drop")                      # p0 queued + editable in bx
    html = _client(cfg).get("/schedule?batch=bx").data.decode()
    assert "/schedule/move/p0?batch=bx" in html
    assert "/schedule/clear/p0?batch=bx" in html
    assert "/schedule/publish/p0?batch=bx" in html
    assert "/schedule/unapprove/p0?batch=bx" in html
    assert "/schedule/respread?batch=bx" in html

def test_posted_action_urls_carry_batch(tmp_path):
    # D2: same contract on Posted — repost / crosspost-one / backfill keep the ?batch= scope.
    cfg = Config(root=tmp_path); _accounts(cfg, handles=("a0", "a1"))  # >1 account -> backfill form renders
    _seed(cfg, 1, state=PostState.published, batch_id="bx", batch_name="Drop")
    html = _client(cfg).get("/posted?batch=bx").data.decode()
    assert "/posts/repost/p0?batch=bx" in html
    assert "/posts/crosspost/clip_0?batch=bx" in html
    assert "/posts/crosspost-all?batch=bx" in html
