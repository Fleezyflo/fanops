# tests/test_studio_live.py — CREATE
# The Studio live auto-refresh: the Review tab self-polls and surfaces newly-ready clips without a
# manual reload, and WITHOUT clobbering the worklist (the poll swaps only the tiny counts strip — the
# 'load them' button is the ONLY thing that refreshes #review-body, on an explicit click). The Make tab
# status counts self-poll too. These assert the htmx wiring (poll trigger, the new-content banner logic,
# the data-awaiting marker) at the HTTP layer.
import json
from datetime import datetime, timezone, timedelta
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt

NOW = datetime.now(timezone.utc).replace(microsecond=0)
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _seed(cfg, *, awaiting=1):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "persona": "hype"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    for i in range(awaiting):
        cid = f"clip_{i}"
        led.add_clip(Clip(id=cid, parent_id="mom_1", path=f"/clips/{cid}.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id=f"p_{i}", parent_id=cid, account="a", account_id="1",
                          platform=Platform.instagram, caption=f"C{i}", state=PostState.awaiting_approval,
                          scheduled_time=_z(NOW + timedelta(hours=3))))
    led.save()

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg)
    app.config.update(TESTING=True)
    return app.test_client()

def test_review_page_has_live_poller_and_data_awaiting(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, awaiting=2)
    r = _client(cfg).get("/review")
    assert r.status_code == 200
    assert b'hx-trigger="every 5s"' in r.data           # the strip self-polls
    assert b'data-awaiting="2"' in r.data               # the worklist marks its rendered awaiting count

def test_review_live_shows_counts(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, awaiting=2)
    r = _client(cfg).get("/review/live")
    assert r.status_code == 200
    assert b"Awaiting" in r.data and b"<strong>2" in r.data

def test_review_live_banner_when_new_content(tmp_path):
    # live awaiting (2) exceeds what the body shows (shown=0) -> the 'load them' affordance appears.
    cfg = Config(root=tmp_path); _seed(cfg, awaiting=2)
    r = _client(cfg).get("/review/live?shown=0")
    assert r.status_code == 200
    assert b"new" in r.data and b"load" in r.data.lower()
    assert b"/review/refresh" in r.data                 # the button refreshes the worklist body

def test_review_live_no_banner_when_caught_up(tmp_path):
    # shown == awaiting -> nothing new -> no banner (and never on shown > awaiting).
    cfg = Config(root=tmp_path); _seed(cfg, awaiting=2)
    r = _client(cfg).get("/review/live?shown=2")
    assert r.status_code == 200
    assert b"load them" not in r.data

def test_review_live_garbage_shown_is_safe(tmp_path):
    # a hand-typed / malformed ?shown must never 500; it falls back to 0 (so the banner shows if any awaiting).
    cfg = Config(root=tmp_path); _seed(cfg, awaiting=1)
    r = _client(cfg).get("/review/live?shown=abc")
    assert r.status_code == 200 and b"load them" in r.data

def test_review_refresh_returns_worklist_body(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, awaiting=1)
    r = _client(cfg).get("/review/refresh")
    assert r.status_code == 200 and b'id="review-body"' in r.data

def test_run_status_self_polls(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, awaiting=1)
    r = _client(cfg).get("/run/status")
    assert r.status_code == 200
    assert b'hx-trigger="every 5s"' in r.data and b"Clips ready" in r.data
