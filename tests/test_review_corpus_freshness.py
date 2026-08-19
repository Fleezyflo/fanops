# Review off-lock badge — True only when a completed source lock exists and the post carries a tag not on it.
import json

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, ClipState, Moment, MomentState, Platform, Post, PostState, Source
from fanops.source_tags import source_tag_locks_path
from fanops.studio.views_review import _caption_corpus_stale


def _led(cfg):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.decided))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))
    led.save()
    return led


def _post(**kw):
    base = dict(id="p1", parent_id="clip_1", account="a", account_id="a", platform=Platform.instagram,
                caption="x", state=PostState.awaiting_approval, hashtags=["#lock"])
    base.update(kw)
    return Post(**base)


def _lock(cfg, lock, *, researched=True):
    p = source_tag_locks_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"pile": list(lock), "lock": list(lock)}
    if researched:
        rec["researched_at"] = "2026-08-19T00:00:00Z"
    p.write_text(json.dumps({"src_1": rec}))


def test_off_lock_tag_is_stale(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg)
    _lock(cfg, ["#lock"])
    assert _caption_corpus_stale(cfg, led, _post(hashtags=["#nope"])) is True
    assert _caption_corpus_stale(cfg, led, _post(hashtags=["#lock"])) is False
    assert _caption_corpus_stale(cfg, led, _post(hashtags=[])) is False


def test_no_completed_lock_is_not_stale(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg)
    assert _caption_corpus_stale(cfg, led, _post(hashtags=["#nope"])) is False
    _lock(cfg, ["#lock"], researched=False)
    assert _caption_corpus_stale(cfg, led, _post(hashtags=["#nope"])) is False


def test_empty_completed_lock_flags_any_tag(tmp_path):
    cfg = Config(root=tmp_path)
    led = _led(cfg)
    _lock(cfg, [])
    assert _caption_corpus_stale(cfg, led, _post(hashtags=["#x"])) is True
    assert _caption_corpus_stale(cfg, led, _post(hashtags=[])) is False
