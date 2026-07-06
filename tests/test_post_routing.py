# tests/test_post_routing.py — Zernio slice 2: per-account backend routing. Backend selection moves from
# ONE global (FANOPS_POSTER) to a per-(handle x platform) override in accounts.json (`backends`), so IG
# can publish via Postiz while TikTok publishes via Zernio in the SAME run. Default-safe: no override ->
# the post uses the global backend (byte-identical to today). All offline.
import json
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState
from fanops.accounts import Account, Accounts, set_backend
from fanops.post import get_poster, get_media_uploader


def _accounts_json(tmp_path, rows):
    p = Config(root=tmp_path).accounts_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"accounts": rows}))


# ---- model: additive `backends` field, legacy files load ----
def test_account_backends_defaults_empty():
    assert Account(handle="a").backends == {}

def test_legacy_accounts_json_loads_without_backends(tmp_path):
    # a file written before this slice (no `backends` key) must load unchanged (additive field)
    _accounts_json(tmp_path, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    accts = Accounts.load(Config(root=tmp_path))
    assert accts.accounts[0].backends == {} and accts.resolve_backend("a", Platform.instagram) is None


# ---- resolve_backend: override else None (publish falls back to global) ----
def test_resolve_backend_returns_override(tmp_path):
    _accounts_json(tmp_path, [{"handle": "@tk", "account_id": "acc", "platforms": ["tiktok"],
                               "status": "active", "backends": {"tiktok": "zernio"}}])
    accts = Accounts.load(Config(root=tmp_path))
    assert accts.resolve_backend("tk", Platform.tiktok) == "zernio"

def test_resolve_backend_none_when_unset(tmp_path):
    _accounts_json(tmp_path, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    accts = Accounts.load(Config(root=tmp_path))
    assert accts.resolve_backend("a", Platform.instagram) is None
    assert accts.resolve_backend("nope", Platform.instagram) is None


# ---- set_backend: atomic write, clears on blank/default, validates ----
def test_set_backend_writes_and_clears(tmp_path):
    cfg = Config(root=tmp_path)
    _accounts_json(tmp_path, [{"handle": "@tk", "account_id": "acc", "platforms": ["tiktok"], "status": "active"}])
    set_backend(cfg, "@tk", "tiktok", "zernio")
    assert Accounts.load(cfg).resolve_backend("tk", Platform.tiktok) == "zernio"
    set_backend(cfg, "@tk", "tiktok", "")                         # blank clears -> back to global
    assert Accounts.load(cfg).resolve_backend("tk", Platform.tiktok) is None

def test_set_backend_default_keyword_clears(tmp_path):
    cfg = Config(root=tmp_path)
    _accounts_json(tmp_path, [{"handle": "@tk", "account_id": "acc", "platforms": ["tiktok"],
                               "status": "active", "backends": {"tiktok": "zernio"}}])
    set_backend(cfg, "@tk", "tiktok", "default")
    assert Accounts.load(cfg).resolve_backend("tk", Platform.tiktok) is None

def test_set_backend_validates(tmp_path):
    cfg = Config(root=tmp_path)
    _accounts_json(tmp_path, [{"handle": "@tk", "account_id": "acc", "platforms": ["tiktok"], "status": "active"}])
    with pytest.raises(ValueError):
        set_backend(cfg, "@tk", "tiktok", "bogus")                # unknown backend
    with pytest.raises(ValueError):
        set_backend(cfg, "@tk", "nosuch", "zernio")               # unknown platform
    with pytest.raises(KeyError):
        set_backend(cfg, "@ghost", "tiktok", "zernio")            # unknown handle

def test_set_backend_preserves_siblings_and_integrations(tmp_path):
    cfg = Config(root=tmp_path)
    _accounts_json(tmp_path, [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "integrations": {"instagram": "ig_1"}},
        {"handle": "@tk", "account_id": "acc", "platforms": ["tiktok"], "status": "active", "integrations": {"tiktok": "acc_abc"}}])
    set_backend(cfg, "@tk", "tiktok", "zernio")
    accts = Accounts.load(cfg)
    assert accts.resolve_account_id("@tk", Platform.tiktok) == "acc_abc"      # id untouched
    assert accts.resolve_account_id("@a", Platform.instagram) == "ig_1"       # sibling untouched
    assert accts.resolve_backend("a", Platform.instagram) is None            # sibling has no override


# ---- factory: explicit backend overrides the global ----
def test_get_poster_explicit_backend_overrides_global(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "dryrun"); monkeypatch.setenv("ZERNIO_API_KEY", "sk_x")
    from fanops.post.zernio import ZernioPoster
    assert isinstance(get_poster(Config(root=tmp_path), "zernio"), ZernioPoster)

def test_get_poster_none_uses_global(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    from fanops.post.dryrun import DryRunPoster
    assert isinstance(get_poster(Config(root=tmp_path)), DryRunPoster)        # back-compat: None -> global

def test_get_media_uploader_explicit_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    from fanops.post.media import dryrun_media_url  # noqa: F401  (the dryrun branch returns a lambda)
    up = get_media_uploader(Config(root=tmp_path), "dryrun")
    assert callable(up)


# ---- the payoff: publish_due routes EACH post to its own backend in one run ----
def test_publish_due_routes_per_account(tmp_path, monkeypatch, mocker):
    # global = postiz; @tk/tiktok overridden to zernio. One run must send the IG post through 'postiz'
    # and the TikTok post through 'zernio' — proving simultaneous mixed-backend publishing.
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    cfg = Config(root=tmp_path)
    _accounts_json(tmp_path, [
        {"handle": "@ig", "account_id": "ig_1", "platforms": ["instagram"], "status": "active"},
        {"handle": "@tk", "account_id": "acc_abc", "platforms": ["tiktok"], "status": "active",
         "backends": {"tiktok": "zernio"}}])
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="pig", parent_id="c1", account="ig", account_id="ig_1", platform=Platform.instagram,
                          caption="c", state=PostState.queued, media_urls=["https://x/ig.mp4"], scheduled_time="2000-01-01T00:00:00Z", public_url="dryrun://pig"))
        led.add_post(Post(id="ptk", parent_id="c2", account="tk", account_id="acc_abc", platform=Platform.tiktok,
                          caption="c", state=PostState.queued, media_urls=["https://x/tk.mp4"], scheduled_time="2000-01-01T00:00:00Z", public_url="dryrun://ptk"))

    seen = {}
    class _FakePoster:
        def __init__(self, backend): self.backend = backend
        def publish(self, led, pid):
            seen[pid] = self.backend
            led.posts[pid].state = PostState.submitted
            return led
    def _fake_get_poster(c, backend=None):
        backend = backend or c.poster_backend
        return _FakePoster(backend)
    mocker.patch("fanops.post.run.get_poster", side_effect=_fake_get_poster)

    from fanops.post.run import publish_due
    publish_due(cfg)
    assert seen["pig"] == "postiz"        # IG (no override) -> global
    assert seen["ptk"] == "zernio"        # TikTok -> per-account override, SAME run

def test_publish_due_no_overrides_uses_global(tmp_path, monkeypatch, mocker):
    # byte-identical: with no backends override, every post uses the global backend
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    cfg = Config(root=tmp_path)
    _accounts_json(tmp_path, [{"handle": "@ig", "account_id": "ig_1", "platforms": ["instagram"], "status": "active"}])
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="pig", parent_id="c1", account="ig", account_id="ig_1", platform=Platform.instagram,
                          caption="c", state=PostState.queued, media_urls=["https://x/ig.mp4"], scheduled_time="2000-01-01T00:00:00Z", public_url="dryrun://pig"))
    seen = {}
    class _FakePoster:
        def __init__(self, backend): self.backend = backend
        def publish(self, led, pid):
            seen[pid] = self.backend; led.posts[pid].state = PostState.submitted; return led
    mocker.patch("fanops.post.run.get_poster", side_effect=lambda c, backend=None: _FakePoster(backend or c.poster_backend))
    from fanops.post.run import publish_due
    publish_due(cfg)
    assert seen["pig"] == "postiz"
