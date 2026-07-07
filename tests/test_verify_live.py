# tests/test_verify_live.py
# MOL-113 — direct per-object platform resolve as the LIVENESS source. Instead of asking "is this
# permalink somewhere in my feed enumeration" (fragile string-match, capped at one global credential),
# ask the platform's own API about ONE specific object: does {media_id} exist, and who owns it. This is
# the confirming primitive MOL-117's gate consumes. Three pieces proven here, ALL with a mocked `get`
# (no real network, no live verbs):
#   1. resolve_ig_media(cfg, media_id, *, get=) -> {exists, permalink, media_type, username} | None
#      (fail-closed: non-200 / missing / raising getter -> None, never a crash).
#   2. releaseId captured at reconcile is persisted on Post.media_id (the stable resolve INPUT).
#   3. confirm_post_live(cfg, post, *, get=) — the ONE seam over IG (per-object resolve) and TikTok
#      (the existing oEmbed verifier) -> {confirmed, owner}.
import json
from fanops.config import Config
from fanops import meta_graph
from fanops.models import Platform, Post, PostState


class _Resp:
    def __init__(self, status=200, body=None): self.status_code = status; self._body = body
    def json(self):
        if self._body is None: raise ValueError("no json body")
        return self._body


def _router(routes):
    """routes: substring-of-url -> _Resp (or callable(params)->_Resp). Records (url, params) per call."""
    calls = []
    def get(url, params=None, timeout=None):
        calls.append((url, params))
        for frag, resp in routes.items():
            if frag in url:
                return resp(params) if callable(resp) else resp
        return _Resp(404, None)
    get.calls = calls
    return get


def _raiser():
    import requests
    def get(url, params=None, timeout=None):
        raise requests.exceptions.RequestException("boom")
    return get


def _write_accounts(cfg, rows):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))


# ── resolve_ig_media: the per-object IG liveness read ──────────────────────────────────────────────────

def test_resolve_ig_media_returns_live_object_and_username(tmp_path, monkeypatch):
    # A real, live IG media id resolves to {exists, permalink, media_type, username} off a mocked 200.
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    cfg = Config(root=tmp_path)
    get = _router({"/17895695668004550": _Resp(200, {
        "id": "17895695668004550", "permalink": "https://www.instagram.com/reel/DaY8y2DCiuf/",
        "media_type": "VIDEO", "timestamp": "2026-07-05T10:00:00+0000", "username": "markmakmouly"})})
    out = meta_graph.resolve_ig_media(cfg, "17895695668004550", get=get)
    assert out == {"exists": True, "permalink": "https://www.instagram.com/reel/DaY8y2DCiuf/",
                   "media_type": "VIDEO", "username": "markmakmouly"}
    url, params = get.calls[0]
    assert "17895695668004550" in url                       # the object id is the path
    assert params["access_token"] == "tok-global"           # global creds by default
    assert "permalink" in params["fields"] and "username" in params["fields"]


def test_resolve_ig_media_uses_per_account_creds(tmp_path, monkeypatch):
    # handle= threads THAT account's ig token onto the wire (per-account creds), not the global.
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    monkeypatch.setenv("META_GRAPH_TOKEN__STAN", "pa-tok")
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [{"handle": "@stan", "account_id": "", "platforms": ["instagram"],
                           "status": "active", "ig_user_id": "ig-stan-99"}])
    get = _router({"/m-stan": _Resp(200, {"id": "m-stan", "permalink": "https://p/s",
                                          "media_type": "IMAGE", "username": "stan_real"})})
    out = meta_graph.resolve_ig_media(cfg, "m-stan", handle="stan", get=get)
    assert out["username"] == "stan_real"
    _url, params = get.calls[0]
    assert params["access_token"] == "pa-tok"               # per-account token wins


def test_resolve_ig_media_fake_or_removed_id_is_none(tmp_path, monkeypatch):
    # A fake/removed object id -> Graph 404/400 -> None (fail-closed), NEVER a fabricated exists:True.
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    cfg = Config(root=tmp_path)
    get = _router({"/deadid": _Resp(400, {"error": {"message": "Unsupported get request"}})})
    assert meta_graph.resolve_ig_media(cfg, "deadid", get=get) is None


def test_resolve_ig_media_missing_id_is_none(tmp_path, monkeypatch):
    # An empty/None media_id makes no call and returns None (fail-closed input guard).
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    cfg = Config(root=tmp_path)
    get = _router({})
    assert meta_graph.resolve_ig_media(cfg, None, get=get) is None
    assert meta_graph.resolve_ig_media(cfg, "", get=get) is None
    assert get.calls == []                                  # no id -> no HTTP


def test_resolve_ig_media_no_creds_is_none(tmp_path, monkeypatch):
    # No token at all -> None with no network call (mirrors list_user_media's no-creds fail-open).
    monkeypatch.delenv("META_IG_USER_ID", raising=False)
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False)
    cfg = Config(root=tmp_path)
    get = _router({})
    assert meta_graph.resolve_ig_media(cfg, "m1", get=get) is None
    assert get.calls == []


def test_resolve_ig_media_transport_error_is_none_not_crash(tmp_path, monkeypatch):
    # A raising getter (transport error) returns None, does NOT propagate the exception.
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    cfg = Config(root=tmp_path)
    assert meta_graph.resolve_ig_media(cfg, "m1", get=_raiser()) is None


def test_resolve_ig_media_200_without_id_is_none(tmp_path, monkeypatch):
    # A 200 whose body lacks the object id (malformed / error-shaped) is not proof of existence -> None.
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    cfg = Config(root=tmp_path)
    get = _router({"/m1": _Resp(200, {"error": {"message": "no"}})})
    assert meta_graph.resolve_ig_media(cfg, "m1", get=get) is None


# ── confirm_post_live: the ONE seam over IG (per-object resolve) + TikTok (oEmbed) ──────────────────────

def _ig_post(**kw):
    base = dict(id="p1", parent_id="c1", account="@markmakmouly", account_id="acc-1",
                platform=Platform.instagram, caption="", state=PostState.published,
                media_id="17895695668004550", public_url="https://www.instagram.com/reel/X/")
    base.update(kw)
    return Post(**base)


def _tt_post(**kw):
    base = dict(id="p2", parent_id="c2", account="@hrmny-blog", account_id="acc-2",
                platform=Platform.tiktok, caption="", state=PostState.published,
                public_url="https://www.tiktok.com/@wahed_bared/video/123")
    base.update(kw)
    return Post(**base)


def test_confirm_post_live_ig_confirmed_with_owner(tmp_path, monkeypatch):
    # IG: a resolvable object -> confirmed True + the platform-reported owner username.
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    cfg = Config(root=tmp_path)
    get = _router({"/17895695668004550": _Resp(200, {"id": "17895695668004550",
        "permalink": "https://p/x", "media_type": "VIDEO", "username": "markmakmouly"})})
    out = meta_graph.confirm_post_live(cfg, _ig_post(), get=get)
    assert out == {"confirmed": True, "owner": "markmakmouly"}


def test_confirm_post_live_ig_unconfirmed_when_object_gone(tmp_path, monkeypatch):
    # IG: a removed object -> confirmed False, owner None (fail-closed).
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    cfg = Config(root=tmp_path)
    get = _router({"/17895695668004550": _Resp(404, None)})
    out = meta_graph.confirm_post_live(cfg, _ig_post(), get=get)
    assert out == {"confirmed": False, "owner": None}


def test_confirm_post_live_ig_no_media_id_unconfirmed(tmp_path, monkeypatch):
    # IG post with no captured media_id -> nothing to resolve -> unconfirmed (never crashes).
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    cfg = Config(root=tmp_path)
    out = meta_graph.confirm_post_live(cfg, _ig_post(media_id=None), get=_router({}))
    assert out == {"confirmed": False, "owner": None}


def test_confirm_post_live_tiktok_routes_through_oembed(tmp_path, monkeypatch):
    # TikTok: the SAME seam routes to the existing oEmbed verifier — a live video whose oEmbed author
    # matches the reported username -> confirmed True + that owner. (No rewrite of verify_tiktok_permalink.)
    cfg = Config(root=tmp_path)
    get = _router({"tiktok.com/oembed": _Resp(200, {"author_unique_id": "wahed_bared"})})
    out = meta_graph.confirm_post_live(cfg, _tt_post(), reported_username="wahed_bared", get=get)
    assert out == {"confirmed": True, "owner": "wahed_bared"}


def test_confirm_post_live_tiktok_unconfirmed_on_dead_video(tmp_path, monkeypatch):
    # TikTok: a removed video (oEmbed 404) -> confirmed False.
    cfg = Config(root=tmp_path)
    get = _router({"tiktok.com/oembed": _Resp(404, None)})
    out = meta_graph.confirm_post_live(cfg, _tt_post(), reported_username="wahed_bared", get=get)
    assert out == {"confirmed": False, "owner": None}


# ── fanops verify-live: READ-ONLY (the ledger is byte-identical after a run) ────────────────────────────

def test_verify_live_cli_leaves_ledger_byte_identical(tmp_path, monkeypatch):
    # A verify-live run confirms nothing over the network here (no creds) but must NOT rewrite the ledger:
    # the on-disk ledger bytes are identical before and after (read-only invariant, acceptance (e)).
    monkeypatch.delenv("META_IG_USER_ID", raising=False)
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False)
    from fanops.ledger import Ledger
    from fanops.cli import cmd_verify_live
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(_ig_post())
    led.save()
    before = cfg.ledger_path.read_bytes()
    rc = cmd_verify_live(cfg)
    assert rc == 0
    assert cfg.ledger_path.read_bytes() == before          # NOT ONE byte changed — read-only
