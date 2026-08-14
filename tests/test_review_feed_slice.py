# Review feed-slice: paginate without a full-ledger card census; filters ride the sentinel URL.
import json
from unittest.mock import patch

import pytest

pytest.importorskip("flask")
from datetime import datetime, timezone, timedelta

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops.studio.views_common import REVIEW_FEED_SLICE

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)


def _z(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _accounts(cfg, handles=("a",)):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": "1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig1"}} for h in handles]}))


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg)
    app.config.update(TESTING=True)
    return app.test_client()


def _lineage(led, *, sid, mid, cid, path, batch_id=None):
    led.add_source(Source(id=sid, source_path=f"/v/{sid}.mp4", language="en",
                          created_at=_z(NOW - timedelta(days=1))))
    led.add_moment(Moment(id=mid, parent_id=sid, content_token="0-7", start=0, end=7,
                          reason="pick", state=MomentState.clipped, hook="HOOK"))
    led.add_clip(Clip(id=cid, parent_id=mid, path=path, aspect=Fmt.r9x16, state=ClipState.captioned))


def _await_post(led, pid, cid, account, *, batch_id=None, created_at=None):
    led.add_post(Post(id=pid, parent_id=cid, account=account, account_id="1", platform=Platform.instagram,
                      caption="cap", state=PostState.awaiting_approval,
                      scheduled_time=_z(NOW + timedelta(hours=3)),
                      created_at=created_at or _z(NOW), batch_id=batch_id))


def _seed_many_awaiting(cfg, n=30, *, handle="a", batch_id=None, sid="src_1"):
    _accounts(cfg, handles=(handle,))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "base.mp4"
    base.write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id=sid, source_path=f"/v/{sid}.mp4", language="en",
                              created_at=_z(NOW - timedelta(days=1))))
        for i in range(n):
            mid = f"mom_{i}"
            cid = f"clip_{i}"
            led.add_moment(Moment(id=mid, parent_id=sid, content_token=f"{i}-{i+5}", start=i, end=i + 5,
                                  reason="r", state=MomentState.clipped, hook=f"H{i}"))
            led.add_clip(Clip(id=cid, parent_id=mid, path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned))
            _await_post(led, f"p_{i}", cid, handle, batch_id=batch_id,
                        created_at=_z(NOW - timedelta(minutes=n - i)))


def test_feed_slice_builds_only_page_surfaces(tmp_path):
    """feed-slice pays _card only for the returned page, not every editable clip."""
    cfg = Config(root=tmp_path)
    _seed_many_awaiting(cfg, n=30)
    calls = {"n": 0}
    real_card = __import__("fanops.studio.views_review", fromlist=["_card"])._card

    def _counting_card(*args, **kwargs):
        calls["n"] += 1
        return real_card(*args, **kwargs)

    c = _client(cfg)
    with patch("fanops.studio.views_review._card", side_effect=_counting_card):
        r = c.get(f"/review/feed-slice?account=a&offset={REVIEW_FEED_SLICE}")
    assert r.status_code == 200
    assert calls["n"] <= REVIEW_FEED_SLICE
    assert r.data.decode().count("<video") <= REVIEW_FEED_SLICE


def test_feed_slice_skips_review_buckets(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_many_awaiting(cfg, n=25)
    buckets = {"n": 0}
    real_rb = __import__("fanops.studio.views", fromlist=["review_buckets"]).review_buckets

    def _guard(*args, **kwargs):
        buckets["n"] += 1
        return real_rb(*args, **kwargs)

    c = _client(cfg)
    with patch("fanops.studio.views.review_buckets", side_effect=_guard):
        r = c.get("/review/feed-slice?account=a&offset=0")
    assert r.status_code == 200
    assert buckets["n"] == 0


def test_feed_sentinel_preserves_batch_source_state_filters(tmp_path):
    cfg = Config(root=tmp_path)
    _accounts(cfg)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "base.mp4"
    base.write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    with Ledger.transaction(cfg) as led:
        _lineage(led, sid="src_a", mid="ma", cid="ca", path=str(base))
        _lineage(led, sid="src_b", mid="mb", cid="cb", path=str(base))
        for i in range(15):
            cid = f"bx_{i}"
            mid = f"mx_{i}"
            led.add_moment(Moment(id=mid, parent_id="src_a", content_token=f"{i}-{i+5}", start=i, end=i + 5,
                                  reason="r", state=MomentState.clipped, hook="H"))
            led.add_clip(Clip(id=cid, parent_id=mid, path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned))
            _await_post(led, f"p_bx_{i}", cid, "a", batch_id="bx")
        _await_post(led, "p_other", "cb", "a", batch_id="by")
    html = _client(cfg).get("/review?account=a&batch=bx&source=src_a&state=awaiting").data.decode()
    assert "feed-sentinel" in html
    assert "batch=bx" in html
    assert "source=src_a" in html
    assert "state=awaiting" in html


def test_feed_slice_pagination_respects_batch_filter(tmp_path):
    cfg = Config(root=tmp_path)
    _accounts(cfg)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "base.mp4"
    base.write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    with Ledger.transaction(cfg) as led:
        for i in range(20):
            mid = f"m_{i}"
            cid = f"c_{i}"
            _lineage(led, sid="src_1", mid=mid, cid=cid, path=str(base))
            _await_post(led, f"p_{i}", cid, "a", batch_id="bx",
                        created_at=_z(NOW - timedelta(minutes=20 - i)))
        _lineage(led, sid="src_2", mid="m_out", cid="c_out", path=str(base))
        _await_post(led, "p_out", "c_out", "a", batch_id="by")
    c = _client(cfg)
    first = c.get("/review/feed-slice?account=a&batch=bx&offset=0").data.decode()
    assert first.count("<video") == REVIEW_FEED_SLICE
    assert "c_out" not in first
    assert "batch=bx" in first
    second = c.get(f"/review/feed-slice?account=a&batch=bx&offset={REVIEW_FEED_SLICE}").data.decode()
    assert "c_out" not in second
    assert second.count("<video") == 8
