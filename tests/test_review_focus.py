# tests/test_review_focus.py — U6: bare /review switcher + feed; account=all mixed worklist.
import json
import pytest
pytest.importorskip("flask")
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _seed_accounts(cfg, handles=("a", "b")):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": "1", "platforms": ["instagram"], "status": "active"} for h in handles]}))

def _lineage(led, *, cid="clip_1", mid="mom_1", sid="src_1", path="/c/clip.mp4"):
    led.add_source(Source(id=sid, source_path="/v/show.mp4", language="en"))
    led.add_moment(Moment(id=mid, parent_id=sid, content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped))
    led.add_clip(Clip(id=cid, parent_id=mid, path=path, aspect=Fmt.r9x16, state=ClipState.queued))

def _await_post(led, pid, clip_id, account):
    led.add_post(Post(id=pid, parent_id=clip_id, account=account, account_id="1", platform=Platform.instagram,
                      caption=f"await {account}", state=PostState.awaiting_approval,
                      scheduled_time=_z(NOW + timedelta(hours=3))))

def _seed_two_accounts_all_surfaces(cfg):
    _seed_accounts(cfg)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "c.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    with Ledger.transaction(cfg) as led:
        for acct in ("a", "b"):
            tag = acct.strip("@")
            _lineage(led, cid=f"clip_{tag}", mid=f"mom_{tag}", sid="src_1", path=str(base))
            _await_post(led, f"aw_{tag}", f"clip_{tag}", acct)

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def test_bare_review_switcher_two_accounts(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts_all_surfaces(cfg)
    html = _client(cfg).get("/review").data.decode()
    assert "review-switcher" in html
    assert "review-feed" not in html
    assert "review-pick-prompt" in html
    assert "<video" not in html

def test_bare_review_single_account_switcher_and_feed(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg, handles=("a",))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "c.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    with Ledger.transaction(cfg) as led:
        _lineage(led, path=str(base)); _await_post(led, "aw_a", "clip_1", "a")
    r = _client(cfg).get("/review", follow_redirects=False)
    assert r.status_code == 200
    html = r.data.decode()
    assert "review-switcher" in html and "review-feed" in html
    assert "<video" in html

def test_bare_review_empty_zero_pending(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    html = _client(cfg).get("/review").data.decode()
    assert "review-switcher" not in html
    assert "No footage yet" in html

def test_account_all_mixed_worklist(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts_all_surfaces(cfg)
    html = _client(cfg).get("/review?account=all").data.decode()
    assert "await a" in html and "await b" in html
    assert 'class="card clip-card"' in html or "clip-card" in html

def test_account_all_chip_on_cards(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts_all_surfaces(cfg)
    html = _client(cfg).get("/review?account=all&view=list").data.decode()
    assert "account-chip" in html
    assert "@a" in html and "@b" in html

def test_feed_strip_scope_agrees(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts_all_surfaces(cfg)
    c = _client(cfg)
    scoped = c.get("/review/live?account=@a").data.decode()
    body = c.get("/review?account=@a").data.decode()
    assert "Awaiting <strong>1</strong>" in scoped
    assert 'data-awaiting="1"' in body

def test_batch_link_skips_switcher_only(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts_all_surfaces(cfg)
    with Ledger.transaction(cfg) as led:
        for p in led.posts.values():
            p.batch_id = "batch_x"
    html = _client(cfg).get("/review?batch=batch_x").data.decode()
    assert "review-pick-prompt" not in html
    assert "clip-card" in html or "await" in html

def test_switcher_all_accounts_link(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts_all_surfaces(cfg)
    html = _client(cfg).get("/review").data.decode()
    assert "account=all" in html
