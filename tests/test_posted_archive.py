# tests/test_posted_archive.py — R2 Leg 2: 06_published/ is a read-only Posted supplement (ledger wins on dedupe).
import json
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, Clip, ClipState, Source, Moment
from fanops.studio import views


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


def _archive_fixture(cfg, *, post_id="p1", day="2026-07-05", **fields):
    rec = {"post_id": post_id, "clip_id": "c1", "account": "a", "platform": "instagram",
           "caption": "archived caption", "public_url": "https://instagram.com/p/abc",
           "scheduled_time": "2026-07-05T12:00:00Z", "published_at": "2026-07-05T12:05:00Z",
           **fields}
    d = cfg.published / day; d.mkdir(parents=True, exist_ok=True)
    (d / f"{post_id}.json").write_text(json.dumps(rec))
    return rec


def test_posted_archive_rows_reads_fixture(tmp_path):
    cfg = Config(root=tmp_path)
    _archive_fixture(cfg)
    rows = views.posted_archive_rows(cfg)
    assert len(rows) == 1
    r = rows[0]
    assert r.post_id == "p1" and r.clip_id == "c1" and r.account == "a" and r.platform == "instagram"
    assert r.caption == "archived caption" and r.public_url == "https://instagram.com/p/abc"
    assert r.scheduled_time == "2026-07-05T12:00:00Z" and r.published_at == "2026-07-05T12:05:00Z"
    assert r.posted_via == "live" and r.is_archived is True


def test_posted_archive_rows_dryrun_url(tmp_path):
    cfg = Config(root=tmp_path)
    _archive_fixture(cfg, post_id="p2", public_url="dryrun://p2")
    r = views.posted_archive_rows(cfg)[0]
    assert r.posted_via == "dryrun"


def test_posted_archive_rows_dedupes_ledger(tmp_path):
    cfg = Config(root=tmp_path)
    _archive_fixture(cfg, post_id="p1")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_p", source_path="/s.mp4"))
        led.add_moment(Moment(id="m1", parent_id="src_p", content_token="0-7", start=0, end=7, reason="r"))
        led.add_clip(Clip(id="c1", parent_id="m1", path="/c.mp4", state=ClipState.published))
        led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                          caption="ledger", state=PostState.published, public_url="https://instagram.com/p/ledger",
                          published_at="2026-07-05T12:05:00Z"))
    ledger_ids = {r.post_id for r in views.posted_library(Ledger.load(cfg), cfg)}
    rows = views.posted_archive_rows(cfg, ledger_ids=ledger_ids)
    assert all(r.post_id != "p1" for r in rows)


def test_posted_archive_rows_missing_dir_fail_open(tmp_path):
    cfg = Config(root=tmp_path)
    assert views.posted_archive_rows(cfg) == []


def test_posted_renders_archived_row_without_post_again(tmp_path):
    cfg = Config(root=tmp_path)
    _archive_fixture(cfg, post_id="arch_only")
    html = _client(cfg).get("/posted").data.decode()
    assert "archived caption" in html and "Archived" in html
    assert "/posts/repost/arch_only" not in html
