"""reconcile_due must not touch Blotato when FANOPS_POSTER=dryrun."""
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.reconcile import reconcile_due
from fanops.accounts import add_account, set_backend


class _R:
    def __init__(self, code, body):
        self.status_code = code
        self._b = body
        self.text = str(body)
    def json(self):
        return self._b


def test_reconcile_due_empty_skips_without_blotato_key(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path)
    out = reconcile_due(cfg)
    assert out["healed_submitting"] == 0


def test_reconcile_due_routes_zernio_when_global_dryrun(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path)
    add_account(cfg, "@tt", [Platform.tiktok], status="active")
    set_backend(cfg, "@tt", "tiktok", "zernio")
    led = Ledger.load(cfg)
    led.add_post(Post(id="tt", parent_id="c", account="@tt", account_id="z1", platform=Platform.tiktok,
                      caption="x", state=PostState.needs_reconcile, submission_id="zsid",
                      public_url="dryrun://tt"))
    led.save()
    url = "https://www.tiktok.com/@x/video/1"
    mocker.patch("fanops.postiz_lifecycle.ensure_up")
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_R(200, {"status": "published", "permalink": url}))
    reconcile_due(cfg)
    led2 = Ledger.load(cfg)
    assert led2.posts["tt"].state is PostState.published
    assert led2.posts["tt"].public_url == url


def test_reconcile_post_without_provider_parks_not_blotato(tmp_path, monkeypatch, mocker):
    """Reconcilable post with no channel provider must park (poll error), never BlotatoAuthError."""
    import json
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    led = Ledger.load(cfg)
    led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="orphan_sid",
                      public_url="dryrun://p"))
    led.save()
    mocker.patch("fanops.postiz_lifecycle.ensure_up")
    reconcile_due(cfg)
    p = Ledger.load(cfg).posts["p"]
    assert p.state is PostState.needs_reconcile
    assert not (p.error_reason or "").startswith("reconcile poll error")  # skipped, not polled


def test_reconcile_inflight_live_dryrun_global_zernio(tmp_path, monkeypatch, mocker):
    """Studio Check-for-links uses reconcile_due — must work live + global dryrun + per-channel zernio."""
    from fanops.studio import actions
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path)
    add_account(cfg, "@tt", [Platform.tiktok], status="active")
    set_backend(cfg, "@tt", "tiktok", "zernio")
    led = Ledger.load(cfg)
    led.add_post(Post(id="tt", parent_id="c", account="@tt", account_id="z1", platform=Platform.tiktok,
                      caption="x", state=PostState.needs_reconcile, submission_id="zsid",
                      public_url="dryrun://tt"))
    led.save()
    url = "https://www.tiktok.com/@x/video/9"
    mocker.patch("fanops.postiz_lifecycle.ensure_up")
    mocker.patch("fanops.post.metrics.requests.get",
                 return_value=_R(200, {"status": "published", "permalink": url}))
    res = actions.reconcile_inflight(cfg)
    assert res.ok
    assert Ledger.load(cfg).posts["tt"].public_url == url
