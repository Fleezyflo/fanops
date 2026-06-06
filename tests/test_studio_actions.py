# tests/test_studio_actions.py — CREATE
from datetime import datetime, timezone, timedelta
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops.studio.actions import reschedule_post, edit_caption, snooze_clip, ActionResult

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _seed(cfg):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16,
                      state=ClipState.queued))
    led.add_post(Post(id="p_edit", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="OLD", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=3))))
    led.save()
    return led

def test_reschedule_persists_tz_aware_z(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = reschedule_post(cfg, "p_edit", _z(NOW + timedelta(hours=8)), now=NOW)
    assert res.ok is True
    val = Ledger.load(cfg).posts["p_edit"].scheduled_time
    assert val.endswith("Z") and val == _z(NOW + timedelta(hours=8))

def test_reschedule_naive_input_never_persists_naive(tmp_path):
    # spec §9 fix #5: a naive time would later mark the post failed in publish_due. Must be coerced
    # to tz-aware UTC Z before it touches the ledger.
    cfg = Config(root=tmp_path); _seed(cfg)
    res = reschedule_post(cfg, "p_edit", "2026-06-06T20:00:00", now=NOW)   # NAIVE (no Z/offset)
    assert res.ok is True
    val = Ledger.load(cfg).posts["p_edit"].scheduled_time
    assert val.endswith("Z") and val == "2026-06-06T20:00:00Z"   # coerced to UTC Z

def test_reschedule_garbage_time_rejected(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = reschedule_post(cfg, "p_edit", "not-a-time", now=NOW)
    assert res.ok is False and res.error
    assert Ledger.load(cfg).posts["p_edit"].scheduled_time == _z(NOW + timedelta(hours=3))  # unchanged

def test_reschedule_unknown_post_rejected(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = reschedule_post(cfg, "nope", _z(NOW + timedelta(hours=8)), now=NOW)
    assert res.ok is False and "no such post" in res.error.lower()

def test_reschedule_non_queued_rejected(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.posts["p_edit"].state = PostState.published; led.save()
    res = reschedule_post(cfg, "p_edit", _z(NOW + timedelta(hours=8)), now=NOW)
    assert res.ok is False and "queued" in res.error.lower()

def test_reschedule_imminent_rejected(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.posts["p_edit"].scheduled_time = _z(NOW + timedelta(minutes=1)); led.save()
    res = reschedule_post(cfg, "p_edit", _z(NOW + timedelta(hours=8)), now=NOW)
    assert res.ok is False and "imminent" in res.error.lower()

def test_edit_caption_persists(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = edit_caption(cfg, "p_edit", "BRAND NEW CAPTION", now=NOW)
    assert res.ok is True
    assert Ledger.load(cfg).posts["p_edit"].caption == "BRAND NEW CAPTION"

def test_edit_caption_imminent_rejected(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.posts["p_edit"].scheduled_time = _z(NOW - timedelta(minutes=1)); led.save()  # already due
    res = edit_caption(cfg, "p_edit", "TOO LATE", now=NOW)
    assert res.ok is False
    assert Ledger.load(cfg).posts["p_edit"].caption == "OLD"

def test_snooze_pushes_all_clip_posts_far_out(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.add_post(Post(id="p2", parent_id="clip_1", account="@b", account_id="2",
                      platform=Platform.youtube, caption="y", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(hours=4))))
    # one imminent post on the same clip should be left alone
    led.add_post(Post(id="p_imm", parent_id="clip_1", account="@c", account_id="3",
                      platform=Platform.tiktok, caption="t", state=PostState.queued,
                      scheduled_time=_z(NOW + timedelta(minutes=2))))
    led.save()
    res = snooze_clip(cfg, "clip_1", now=NOW)
    assert res.ok is True and res.detail["count"] == 2   # p_edit + p2 (not p_imm)
    out = Ledger.load(cfg)
    from fanops.timeutil import parse_iso
    assert parse_iso(out.posts["p_edit"].scheduled_time) >= NOW + timedelta(days=364)
    assert parse_iso(out.posts["p2"].scheduled_time) >= NOW + timedelta(days=364)
    assert out.posts["p_imm"].scheduled_time == _z(NOW + timedelta(minutes=2))   # untouched

def test_actions_use_single_transaction(tmp_path, mocker):
    cfg = Config(root=tmp_path); _seed(cfg)
    spy = mocker.spy(Ledger, "transaction")
    reschedule_post(cfg, "p_edit", _z(NOW + timedelta(hours=8)), now=NOW)
    assert spy.call_count == 1   # exactly one lock acquisition per mutation (no lock-free load+save)
