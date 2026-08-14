"""Permalink persistence: same-tick publish→reconcile, keep captured https URLs when liveness fails, no invented URLs."""
import json

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, ClipState, Moment, MomentState, Platform, Post, PostState
from fanops.pipeline import advance
from fanops.reconcile import reconcile_posts


def _live_zernio(monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.setenv("FANOPS_LIVE", "1")


def _accounts(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "98432", "platforms": ["instagram"], "status": "active",
         "backends": {"instagram": "zernio"}}]}))


def _seed_queued(cfg, pid="p1", cid="c1"):
    f = cfg.clips / f"{cid}.mp4"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"V")
    with Ledger.transaction(cfg) as led:
        led.add_moment(Moment(id="mom_1", parent_id="src_1", start=0.0, end=7.0, reason="worth posting",
                              state=MomentState.clipped))
        led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id=cid, account="a", account_id="98432", platform=Platform.instagram,
                          caption="c", scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued,
                          post_type="post", created_at="2026-07-16T13:31:00Z",
                          media_urls=["https://cdn/v.mp4"]))


def test_advance_runs_publish_before_reconcile(tmp_path, monkeypatch, mocker):
    """Same-tick contract: publish must run before reconcile so this tick's ships are reconcilable."""
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    order: list[str] = []
    mocker.patch("fanops.pipeline.publish_due", side_effect=lambda *a, **k: order.append("publish"))
    mocker.patch("fanops.pipeline.reconcile_due", side_effect=lambda *a, **k: order.append("reconcile"))
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert order == ["publish", "reconcile"]


def test_advance_same_tick_publish_then_reconcile_url(tmp_path, monkeypatch, mocker):
    """A post parked needs_reconcile by publish_due in this pass is reconciled in the SAME advance()."""
    _live_zernio(monkeypatch)
    cfg = Config(root=tmp_path)
    _accounts(cfg)
    _seed_queued(cfg)
    url = "https://www.instagram.com/reel/ABC123/"

    class _R:
        status_code = 201
        def json(self):
            return {"id": "zernio_sid_1"}

    mocker.patch("fanops.post.zernio.requests.post", return_value=_R())
    mocker.patch("fanops.post.run._ensure_media", return_value=None)
    mocker.patch("fanops.reconcile._default_get_status", return_value=lambda sid: {
        "status": "published", "publicUrl": url, "releaseId": "17841456789012345",
    })
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    led = Ledger.load(cfg)
    p = led.posts["p1"]
    assert p.state is PostState.published
    assert p.public_url == url


def test_reconcile_keeps_https_url_when_ig_liveness_parked(tmp_path, monkeypatch):
    """Credentialed IG identity gate may park the post, but the captured releaseURL must persist."""
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@markmakmouly", "account_id": "1", "platforms": ["instagram"],
         "status": "active", "ig_user_id": "ig-mark-99"}]}))
    url = "https://www.instagram.com/reel/DaY8y2DCiuf/"
    led.add_post(Post(id="p1", parent_id="c", account="@markmakmouly", account_id="1",
                      platform=Platform.instagram, caption="x",
                      state=PostState.needs_reconcile, submission_id="postiz_1"))
    led = reconcile_posts(led, cfg, get_status=lambda sid: {
        "status": "published", "publicUrl": url, "releaseId": "17841456789012345",
    }, confirm=lambda *a, **k: {"confirmed": False, "owner": None})
    p = led.posts["p1"]
    assert p.state is PostState.needs_reconcile
    assert p.public_url == url
    assert "unverified" in (p.error_reason or "").lower()


def test_reconcile_keeps_https_url_on_ig_transport_failopen(tmp_path, monkeypatch):
    """Graph transport hiccup is fail-open — permalink captured from Postiz must still land on the row."""
    import requests
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@markmakmouly", "account_id": "1", "platforms": ["instagram"],
         "status": "active", "ig_user_id": "ig-mark-99"}]}))
    url = "https://www.instagram.com/reel/DaY8y2DCiuf/"
    led.add_post(Post(id="p1", parent_id="c", account="@markmakmouly", account_id="1",
                      platform=Platform.instagram, caption="x",
                      state=PostState.needs_reconcile, submission_id="postiz_1"))

    def graph_get(url_, params=None, timeout=None):
        raise requests.exceptions.RequestException("boom")

    led = reconcile_posts(led, cfg, get_status=lambda sid: {
        "status": "published", "publicUrl": url, "releaseId": "17841456789012345",
    }, graph_get=graph_get)
    p = led.posts["p1"]
    assert p.state is PostState.needs_reconcile
    assert p.public_url == url
    assert "unverified" not in (p.error_reason or "").lower()


def test_postiz_publish_persists_releaseurl_from_body_not_invented(tmp_path, monkeypatch, mocker):
    """When Postiz 2xx carries releaseURL, persist it; never fabricate when absent."""
    from fanops.post.postiz import PostizPoster, _postiz_permalink_from_body
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.submitting, post_type="post",
                      media_urls=["https://cdn/v.mp4"], scheduled_time="2026-01-01T00:00:00Z"))
    real_url = "https://www.instagram.com/reel/from_postiz/"
    assert _postiz_permalink_from_body({"id": "postiz_1"}) is None
    assert _postiz_permalink_from_body({"id": "postiz_1", "releaseURL": real_url}) == real_url
    class _R:
        status_code = 201
        def json(self):
            return {"id": "postiz_1", "releaseURL": real_url}
    mocker.patch("fanops.post.postiz.requests.post", return_value=_R())
    led = PostizPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].public_url == real_url
    assert "postiz.example" not in (led.posts["p1"].public_url or "")
