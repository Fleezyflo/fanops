# tests/test_post_routing.py — Zernio slice 2: per-account backend routing. Backend selection moves from
# ONE global (FANOPS_POSTER) to a per-(handle x platform) override in accounts.json (`backends`), so IG
# can publish via Postiz while TikTok publishes via Zernio in the SAME run. Default-safe: no override ->
# the post uses the global backend (byte-identical to today). All offline.
import json
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, Clip, ClipState, Moment, MomentState
from fanops.accounts import Account, Accounts, set_backend
from fanops.post import get_poster, get_media_uploader


def _lineage(led, *cids):
    """Materialize the moment -> clip ancestry these posts name. It was never built, so every post here was
    an ORPHAN — a shape production cannot produce (_delete_moment_cascade refuses to drop a clip while a
    protected post hangs off it). Harmless while the publish guard's predicate failed OPEN on a missing
    ancestor; publish_due now asks Ledger.can_promote, which fails CLOSED. These tests are about BACKEND
    ROUTING, so the lineage must be live and the backend the only variable."""
    led.add_moment(Moment(id="m", parent_id="src_1", start=0.0, end=5.0, reason="worth posting",
                          state=MomentState.clipped))
    for cid in cids:
        led.add_clip(Clip(id=cid, parent_id="m", path=f"/{cid}.mp4", state=ClipState.queued))


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
class _R:
    def __init__(self, code, body=None, text=""):
        self.status_code = code
        self._b = {} if body is None else body
        self.text = text
    def json(self):
        return self._b


def test_publish_due_routes_per_account(tmp_path, monkeypatch, mocker):
    # global = postiz; @tk/tiktok overridden to zernio. One run must POST IG to Postiz and TikTok to
    # Zernio. Leftover dryrun:// is not a permalink.
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://p.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "k"); monkeypatch.setenv("ZERNIO_API_KEY", "k")
    cfg = Config(root=tmp_path)
    _accounts_json(tmp_path, [
        {"handle": "@ig", "account_id": "ig_1", "platforms": ["instagram"], "status": "active"},
        {"handle": "@tk", "account_id": "acc_abc", "platforms": ["tiktok"], "status": "active",
         "backends": {"tiktok": "zernio"}}])
    with Ledger.transaction(cfg) as led:
        _lineage(led, "c1", "c2")
        led.add_post(Post(id="pig", parent_id="c1", account="ig", account_id="ig_1", platform=Platform.instagram,
                          caption="ig-cap", state=PostState.queued, post_type="post",
                          media_urls=["https://x/ig.mp4"], scheduled_time="2000-01-01T00:00:00Z", public_url="dryrun://pig"))
        led.add_post(Post(id="ptk", parent_id="c2", account="tk", account_id="acc_abc", platform=Platform.tiktok,
                          caption="tk-cap", state=PostState.queued, created_at="2026-07-16T13:31:00Z",
                          media_urls=["https://x/tk.mp4"], scheduled_time="2000-01-01T00:00:00Z", public_url="dryrun://ptk"))
    backends = []
    def _get(url, **kw):
        if "integrations" in str(url):
            return _R(200, [{"id": "ig_1", "name": "ig", "identifier": "instagram-standalone"}])
        return _R(200, {"posts": []})
    def _post(url, **kw):
        u = str(url)
        if "zernio.com" in u:
            backends.append("zernio"); return _R(201, {"_id": "z_1"})
        if "/posts" in u:
            backends.append("postiz"); return _R(201, {"id": "postiz_1"})
        return _R(201, {"id": "img1", "path": "https://uploads.postiz.com/v.mp4"})
    mocker.patch("requests.get", side_effect=_get)
    mocker.patch("requests.post", side_effect=_post)
    from fanops.post.run import publish_due
    publish_due(cfg)
    assert "postiz" in backends and "zernio" in backends
    led = Ledger.load(cfg)
    assert led.posts["pig"].state is not PostState.published
    assert led.posts["ptk"].state is not PostState.published

def test_publish_due_no_overrides_uses_global(tmp_path, monkeypatch, mocker):
    # with no backends override, every post uses the global backend. Leftover dryrun:// is not a permalink.
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://p.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    cfg = Config(root=tmp_path)
    _accounts_json(tmp_path, [{"handle": "@ig", "account_id": "ig_1", "platforms": ["instagram"], "status": "active"}])
    with Ledger.transaction(cfg) as led:
        _lineage(led, "c1")
        led.add_post(Post(id="pig", parent_id="c1", account="ig", account_id="ig_1", platform=Platform.instagram,
                          caption="c", state=PostState.queued, post_type="post",
                          media_urls=["https://x/ig.mp4"], scheduled_time="2000-01-01T00:00:00Z", public_url="dryrun://pig"))
    backends = []
    def _get(url, **kw):
        if "integrations" in str(url):
            return _R(200, [{"id": "ig_1", "name": "ig", "identifier": "instagram-standalone"}])
        return _R(200, {"posts": []})
    def _post(url, **kw):
        u = str(url)
        if "/posts" in u:
            backends.append("zernio" if "zernio.com" in u else "postiz")
            return _R(201, {"id": "postiz_1"})
        return _R(201, {"id": "img1", "path": "https://uploads.postiz.com/v.mp4"})
    mocker.patch("requests.get", side_effect=_get)
    mocker.patch("requests.post", side_effect=_post)
    from fanops.post.run import publish_due
    publish_due(cfg)
    assert backends == ["postiz"]
    assert Ledger.load(cfg).posts["pig"].state is not PostState.published
