"""S04 — Go-Live channel readiness matrix: channel_readiness truth table, go_live agreement,
template smoke (matrix ✗/✓, YouTube checklist, persona link). Pattern from test_studio_golive.py."""
import json
import os
import pytest
from fanops.config import Config
from fanops.accounts import Accounts
from fanops.studio import golive, views


_ENV_KEYS = ("FANOPS_LIVE", "FANOPS_POSTER", "POSTIZ_URL", "POSTIZ_API_KEY", "ZERNIO_API_KEY",
             "FANOPS_ACCOUNT_CASTING")
_ENV_BASELINE = {k: os.environ.get(k) for k in _ENV_KEYS}


@pytest.fixture(autouse=True)
def _restore_env():
    yield
    for k, v in _ENV_BASELINE.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _clean(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    return Config(root=tmp_path)


def _seed(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    return app.test_client()


def _ch(st, handle, platform):
    return next(c for c in st.channels if c.handle == handle and c.platform == platform)


# ---- channel_readiness truth table ----
def test_readiness_fresh_workspace_no_accounts(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    st = views.golive_status(cfg)
    assert st.channels == []
    assert st.next_blocker == "connect Postiz or Zernio first"


def test_readiness_no_scheduler_keys_blocks_first(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    st = views.golive_status(cfg)
    ch = _ch(st, "ig", "instagram")
    assert ch.mapped is False and ch.ready is False
    assert ch.first_blocker == "connect Postiz or Zernio first"
    assert st.next_blocker == "connect Postiz or Zernio first"


def test_readiness_unmapped_after_scheduler_connected(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    st = views.golive_status(cfg)
    ch = _ch(st, "ig", "instagram")
    assert ch.mapped is False and ch.first_blocker == "map an integration id"


def test_readiness_r2_integration_without_backend(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active",
                 "integrations": {"instagram": "ig_1"}}])
    st = views.golive_status(cfg)
    ch = _ch(st, "ig", "instagram")
    assert ch.mapped is True and ch.backend == "" and ch.ready is False
    assert ch.first_blocker == "route to a scheduler backend"


def test_readiness_missing_creds_for_backend(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)                       # no POSTIZ key
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active",
                 "integrations": {"instagram": "ig_1"}, "backends": {"instagram": "postiz"}}])
    st = views.golive_status(cfg)
    ch = _ch(st, "ig", "instagram")
    assert ch.first_blocker == "connect Postiz or Zernio first"   # global keys absent -> #1 first


def test_readiness_missing_postiz_creds_when_zernio_connected(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("ZERNIO_API_KEY", "zk")
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active",
                 "integrations": {"instagram": "ig_1"}, "backends": {"instagram": "postiz"}}])
    st = views.golive_status(cfg)
    ch = _ch(st, "ig", "instagram")
    assert ch.first_blocker == "connect postiz first (set POSTIZ_API_KEY)"


def test_readiness_persona_required_when_casting_on(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active",
                 "integrations": {"instagram": "ig_1"}, "backends": {"instagram": "postiz"}}])
    st = views.golive_status(cfg)
    ch = _ch(st, "ig", "instagram")
    assert ch.creds is True and ch.persona is False and ch.ready is False
    assert ch.first_blocker == "link a persona"


def test_readiness_live_ready_row_all_green(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    _seed(cfg, [{"handle": "tk", "account_id": "", "platforms": ["tiktok"], "status": "active",
                 "integrations": {"tiktok": "tk_1"}, "backends": {"tiktok": "zernio"}, "persona": "voice"}])
    st = views.golive_status(cfg)
    ch = _ch(st, "tk", "tiktok")
    assert ch.mapped and ch.creds and ch.persona and ch.window
    assert ch.backend == "zernio" and ch.ready is True and ch.first_blocker == ""
    assert st.next_blocker == ""


def test_readiness_window_always_true(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    assert _ch(views.golive_status(cfg), "ig", "instagram").window is True


def test_readiness_mapped_falls_back_to_account_id(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "ig", "account_id": "shared", "platforms": ["instagram"], "status": "active",
                 "backends": {"instagram": "postiz"}}])
    ch = _ch(views.golive_status(cfg), "ig", "instagram")
    assert ch.mapped is True


# ---- go_live agreement ----
def test_ready_agrees_with_live_ready_channels_and_validate(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    _seed(cfg, [
        {"handle": "tk", "account_id": "", "platforms": ["tiktok"], "status": "active",
         "integrations": {"tiktok": "tk_1"}, "backends": {"tiktok": "zernio"}, "persona": "v"},
        {"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig_1"}},   # R2 drift — not ready
    ])
    accts = Accounts.load(cfg)
    live_ready = set(accts.live_ready_channels())
    st = views.golive_status(cfg)
    for ch in st.channels:
        h, p, prov = ch.handle, ch.platform, ch.backend
        in_live = (h, p, prov) in live_ready if prov else False
        a = next(x for x in accts.active() if x.handle == h)
        has_backend = bool(a.backends.get(p))
        r2_bad = (bool(a.integrations.get(p)) and not has_backend) or (has_backend and not bool(a.integrations.get(p)))
        persona_ok = bool((a.persona_id or "").strip() or (a.persona or "").strip())
        casting_block = cfg.account_casting and not persona_ok
        expect = in_live and not r2_bad and not casting_block
        assert ch.ready == expect, (h, p, ch.ready, expect)
    assert _ch(st, "tk", "tiktok").ready is True
    assert _ch(st, "ig", "instagram").ready is False
    assert golive.go_live(cfg, confirmed=True).ok is False   # validate catches R2 on ig


def test_go_live_succeeds_when_all_channels_ready(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")   # casting-off avoids validate cut-spec gate
    _seed(cfg, [{"handle": "tk", "account_id": "", "platforms": ["tiktok"], "status": "active",
                 "integrations": {"tiktok": "tk_1"}, "backends": {"tiktok": "zernio"}, "persona": "v"}])
    st = views.golive_status(cfg)
    assert all(c.ready for c in st.channels) and st.next_blocker == ""
    assert golive.go_live(cfg, confirmed=True).ok is True


# ---- template smoke ----
def test_panel_shows_next_blocker_banner(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    html = _client(cfg).get("/golive").get_data(as_text=True)
    assert "Next:" in html and "connect Postiz or Zernio first" in html


def test_panel_readiness_matrix_fresh_all_x(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    html = _client(cfg).get("/golive").get_data(as_text=True)
    assert "readiness-matrix" in html
    assert html.count("✗") >= 5                              # mapped/creds/persona etc all fail


def test_panel_readiness_matrix_live_ready_all_check(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    _seed(cfg, [{"handle": "tk", "account_id": "", "platforms": ["tiktok"], "status": "active",
                 "integrations": {"tiktok": "tk_1"}, "backends": {"tiktok": "zernio"}, "persona": "v"}])
    html = _client(cfg).get("/golive").get_data(as_text=True)
    assert "readiness-matrix" in html and "channel-ready" in html
    assert "✓" in html


def test_panel_youtube_checklist_when_no_youtube_discovered(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "yt", "account_id": "", "platforms": ["youtube"], "status": "active"}])
    monkeypatch.setattr(golive.postiz, "postiz_list_integrations",
                        lambda c: [type("R", (), {"id": "ig_1", "name": "Mark", "platform": "instagram"})()])
    monkeypatch.setattr(golive.zernio, "zernio_list_accounts", lambda c: [])
    r = _client(cfg).post("/golive/discover")
    assert b"youtube-checklist" in r.data


def test_panel_persona_tab_link_when_unlinked(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "bare", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    html = _client(cfg).get("/golive").get_data(as_text=True)
    assert "Link a first-class persona" in html and "/personas" in html
