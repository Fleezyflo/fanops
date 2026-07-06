# tests/test_studio_p5_density.py — UI Phase 5: Review density + Schedule context.
# The view model already computed source_name / moment_window / transcript_excerpt (ReviewCard) but the
# template never rendered them; the Schedule table never showed the caption; reject was batch-only. This
# adds: a caption on ScheduleRow + a Caption column, the card lineage line + transcript peek, and a
# per-surface reject control (one post, not the whole checkbox set).
import json
from datetime import datetime, timezone, timedelta
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt)
from fanops.studio import views

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def _seed_accounts(cfg, handles=("a", "b")):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": "1", "platforms": ["instagram"], "status": "active"} for h in handles]}))

def _lineage(led, *, excerpt="the moment everyone is talking about"):
    led.add_source(Source(id="src_1", source_path="/v/showtime.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, transcript_excerpt=excerpt))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c/clip.mp4", aspect=Fmt.r9x16, state=ClipState.queued))

def _await(led, pid, account, *, caption="c"):
    led.add_post(Post(id=pid, parent_id="clip_1", account=account, account_id="1", platform=Platform.instagram,
                      caption=caption, state=PostState.awaiting_approval,
                      scheduled_time=_z(NOW + timedelta(hours=3))))


# ── Schedule: ScheduleRow.caption + Caption column ──────────────────────────
def test_schedule_row_carries_caption(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _lineage(led)
        led.add_post(Post(id="q1", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                          caption="ship this one 🔥", state=PostState.queued, scheduled_time=_z(NOW + timedelta(hours=9))))
    rows = views.schedule_rows(Ledger.load(cfg), cfg, now=NOW)
    r = [x for x in rows if x.post_id == "q1"][0]
    assert r.caption == "ship this one 🔥"

def test_schedule_panel_renders_caption_column(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _lineage(led)
        led.add_post(Post(id="q1", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                          caption="UNIQUECAP shippable", state=PostState.queued,
                          scheduled_time=_z(NOW + timedelta(hours=9))))
    html = _client(cfg).get("/schedule").data
    assert b"Caption" in html               # the new column header
    assert b"UNIQUECAP shippable" in html   # the caption text rendered in-row


# ── Review: source / window / transcript on the card ────────────────────────
def test_review_card_renders_source_window_transcript(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _lineage(led, excerpt="UNIQUE transcript peek line")
        _await(led, "p_a", "a")
    html = _client(cfg).get("/review?view=list").data
    assert b"showtime.mp4" in html                  # source_name surfaced
    assert "0–7".encode() in html                   # moment_window (en dash)
    assert b"UNIQUE transcript peek line" in html   # transcript_excerpt surfaced


# ── Review: per-surface reject (one post, not the whole set) ────────────────
def test_review_card_has_per_surface_reject(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _lineage(led)
        _await(led, "p_a", "a"); _await(led, "p_b", "b")
    html = _client(cfg).get("/review?view=list").data.decode()
    # each surface offers its OWN reject (a single-id control via hx-vals), distinct from the batch
    # checkbox set — so there are two single-id reject controls, one per post.
    assert "/posts/reject" in html
    assert html.count('hx-vals=\'{"ids": "p_a"}\'') == 1
    assert html.count('hx-vals=\'{"ids": "p_b"}\'') == 1

def test_per_surface_reject_rejects_single_post(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _lineage(led)
        _await(led, "p_a", "a"); _await(led, "p_b", "b")
    _client(cfg).post("/posts/reject", data={"ids": "p_a"})
    led = Ledger.load(cfg)
    assert led.posts["p_a"].state is PostState.rejected      # the one rejected
    assert led.posts["p_b"].state is PostState.awaiting_approval  # the sibling untouched
