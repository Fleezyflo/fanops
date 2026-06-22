"""M4a — discover → adopt onboarding (backend). `discover_channels` lists every channel the CONNECTED
providers (Postiz + Zernio) already hold, each proposed with a deterministic handle + match; fail-soft per
provider. `adopt_channels` creates the account + maps the id (always) and routes it to its provider (confirm +
creds gated). `accounts.ensure_channel` is the idempotent adopt-side writer (add account / append platform).
Deterministic matching only (exact normalized handle or an existing id) — FanOps never merges on a guess."""
import json
import os
import pytest
from fanops.config import Config
from fanops.accounts import Accounts, ensure_channel
from fanops.models import Platform
from fanops.post.postiz import PostizIntegration
from fanops.post.zernio import ZernioAccount
from fanops.studio import golive


_KEYS = ("FANOPS_LIVE", "FANOPS_POSTER", "POSTIZ_URL", "POSTIZ_API_KEY", "ZERNIO_API_KEY", "BLOTATO_API_KEY")
_BASELINE = {k: os.environ.get(k) for k in _KEYS}


@pytest.fixture(autouse=True)
def _restore_env():
    yield
    for k, v in _BASELINE.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _clean(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in _KEYS:
        monkeypatch.delenv(k, raising=False)
    return Config(root=tmp_path)


def _seed(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))


def _rows(cfg):
    return json.loads(cfg.accounts_path.read_text())["accounts"]


# ------------------------------------------------------------------ ensure_channel ----
def test_ensure_channel_creates_new_active_account(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, [])
    assert ensure_channel(cfg, "@new", "instagram") is True
    a = Accounts.load(cfg).accounts[0]
    assert a.handle == "@new" and a.status.value == "active" and Platform.instagram in a.platforms


def test_ensure_channel_appends_platform_to_existing(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
                 "integrations": {"instagram": "ig_1"}}])
    assert ensure_channel(cfg, "@a", "tiktok") is True
    a = next(a for a in Accounts.load(cfg).accounts if a.handle == "@a")
    assert {p.value for p in a.platforms} == {"instagram", "tiktok"}
    assert a.integrations.get("instagram") == "ig_1"             # existing field preserved


def test_ensure_channel_idempotent_when_platform_present(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    assert ensure_channel(cfg, "@a", "instagram") is False       # no-op -> no write/change


def test_ensure_channel_rejects_unknown_platform(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, [])
    with pytest.raises(ValueError):
        ensure_channel(cfg, "@a", "myspace")


def test_ensure_channel_appends_to_every_duplicate_handle_copy(tmp_path, monkeypatch):
    # hand-edited duplicate handle: the platform must land on EVERY copy (consistent with write_integration
    # which adopt calls next), never just the first — else accounts.json diverges.
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
                {"handle": "@a", "account_id": "2", "platforms": ["instagram"], "status": "active"}])
    assert ensure_channel(cfg, "@a", "tiktok") is True
    rows = [r for r in _rows(cfg) if r["handle"] == "@a"]
    assert all("tiktok" in r["platforms"] for r in rows)        # both copies gained the platform


def test_ensure_channel_does_not_clobber_existing_persona(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
                 "persona": "the archivist"}])
    ensure_channel(cfg, "@a", "tiktok", persona="SOMETHING ELSE")   # persona is creation-only -> ignored here
    a = next(a for a in _rows(cfg) if a["handle"] == "@a")
    assert a["persona"] == "the archivist"


# ------------------------------------------------------------------ discover_channels ----
def _both_connected(monkeypatch):
    monkeypatch.setenv("POSTIZ_URL", "https://p.example.com"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.setenv("ZERNIO_API_KEY", "zk")
    monkeypatch.setattr(golive.postiz, "postiz_list_integrations",
                        lambda c: [PostizIntegration(id="ig_1", name="Mark Makmouly", platform="instagram")])
    monkeypatch.setattr(golive.zernio, "zernio_list_accounts",
                        lambda c: [ZernioAccount(id="tk_9", name="perca.late", platform="tiktok")])


def test_discover_merges_both_providers(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, []); _both_connected(monkeypatch)
    res = golive.discover_channels(cfg)
    assert res.ok is True
    provs = {c.provider for c in res.detail["channels"]}
    assert provs == {"postiz", "zernio"}
    ig = next(c for c in res.detail["channels"] if c.provider == "postiz")
    assert ig.suggested_handle == "@markmakmouly" and ig.match is None and ig.already_mapped is False


def test_discover_matches_existing_handle_deterministically(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "@markmakmouly", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    _both_connected(monkeypatch)
    ig = next(c for c in golive.discover_channels(cfg).detail["channels"] if c.provider == "postiz")
    assert ig.match == "@markmakmouly" and ig.already_mapped is False   # handle matches; this id not yet mapped


def test_discover_flags_already_mapped_id(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "@mark", "account_id": "", "platforms": ["instagram"], "status": "active",
                 "integrations": {"instagram": "ig_1"}}])
    _both_connected(monkeypatch)
    ig = next(c for c in golive.discover_channels(cfg).detail["channels"] if c.provider == "postiz")
    assert ig.match == "@mark" and ig.already_mapped is True            # id already on an account


def test_discover_fail_soft_one_provider_unconnected(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, [])
    monkeypatch.setenv("ZERNIO_API_KEY", "zk")                  # only zernio connected
    monkeypatch.setattr(golive.zernio, "zernio_list_accounts",
                        lambda c: [ZernioAccount(id="tk_9", name="perca.late", platform="tiktok")])
    res = golive.discover_channels(cfg)
    assert res.ok is True
    assert {c.provider for c in res.detail["channels"]} == {"zernio"}
    assert any("postiz" in n and "not connected" in n for n in res.detail["notes"])


def test_discover_fail_soft_provider_list_error(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, [])
    monkeypatch.setenv("POSTIZ_URL", "https://p"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.setenv("ZERNIO_API_KEY", "zk")
    monkeypatch.setattr(golive.postiz, "postiz_list_integrations",
                        lambda c: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(golive.zernio, "zernio_list_accounts",
                        lambda c: [ZernioAccount(id="tk_9", name="x", platform="tiktok")])
    res = golive.discover_channels(cfg)
    assert res.ok is True and {c.provider for c in res.detail["channels"]} == {"zernio"}   # postiz error didn't abort
    assert any("postiz" in n.lower() for n in res.detail["notes"])


def test_discover_refused_when_no_provider_connected(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, [])
    res = golive.discover_channels(cfg)
    assert res.ok is False and "connect" in res.error.lower()


# ------------------------------------------------------------------ adopt_channels ----
def test_adopt_creates_and_maps_but_no_route_without_confirm(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, []); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    res = golive.adopt_channels(cfg, [{"provider": "postiz", "id": "ig_1", "platform": "instagram", "handle": "@new"}],
                                confirmed=False)
    assert res.ok is True and res.detail["adopted"] == 1 and res.detail["routed"] == 0
    accts = Accounts.load(cfg)
    a = next(a for a in accts.accounts if a.handle == "@new")
    assert a.integrations.get("instagram") == "ig_1"            # id mapped
    assert accts.effective_provider("@new", Platform.instagram) is None   # NOT routed (no provider set)


def test_adopt_routes_when_confirmed_with_creds(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, []); monkeypatch.setenv("ZERNIO_API_KEY", "zk")
    res = golive.adopt_channels(cfg, [{"provider": "zernio", "id": "tk_9", "platform": "tiktok", "handle": "@tk"}],
                                confirmed=True)
    assert res.ok is True and res.detail["adopted"] == 1 and res.detail["routed"] == 1
    accts = Accounts.load(cfg)
    assert accts.effective_provider("@tk", Platform.tiktok) == "zernio"   # routed to its provider
    assert accts.live_ready_channels() == [("@tk", "tiktok", "zernio")]   # now publishable once live


def test_adopt_confirmed_without_creds_maps_but_does_not_route(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, [])         # NO ZERNIO_API_KEY
    res = golive.adopt_channels(cfg, [{"provider": "zernio", "id": "tk_9", "platform": "tiktok", "handle": "@tk"}],
                                confirmed=True)
    assert res.detail["adopted"] == 1 and res.detail["routed"] == 0
    assert Accounts.load(cfg).effective_provider("@tk", Platform.tiktok) is None


def test_adopt_per_row_isolated_bad_row_does_not_abort(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, []); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    res = golive.adopt_channels(cfg, [
        {"provider": "postiz", "id": "ig_1", "platform": "myspace", "handle": "@bad"},     # unknown platform
        {"provider": "postiz", "id": "ig_2", "platform": "instagram", "handle": "@good"},
    ], confirmed=True)
    assert res.detail["adopted"] == 1                          # the good row still adopted
    handles = {a.handle for a in Accounts.load(cfg).accounts}
    assert "@good" in handles and "@bad" not in handles
    assert any(r["ok"] is False for r in res.detail["rows"])


def test_adopt_existing_handle_new_platform(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("ZERNIO_API_KEY", "zk")
    _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
                 "integrations": {"instagram": "ig_1"}}])
    res = golive.adopt_channels(cfg, [{"provider": "zernio", "id": "tk_9", "platform": "tiktok", "handle": "@a"}],
                                confirmed=True)
    assert res.detail["adopted"] == 1 and res.detail["routed"] == 1
    a = next(a for a in Accounts.load(cfg).accounts if a.handle == "@a")
    assert {p.value for p in a.platforms} == {"instagram", "tiktok"}
    assert a.integrations.get("tiktok") == "tk_9" and a.integrations.get("instagram") == "ig_1"
    assert len(_rows(cfg)) == 1                                 # appended to the SAME account, not a duplicate


def test_adopt_incomplete_selection_recorded_not_crashed(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, [])
    res = golive.adopt_channels(cfg, [{"provider": "postiz", "platform": "instagram", "handle": "@x"}],  # no id
                                confirmed=True)
    assert res.ok is True and res.detail["adopted"] == 0
    assert res.detail["rows"][0]["ok"] is False
