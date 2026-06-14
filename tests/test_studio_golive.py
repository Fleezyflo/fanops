# tests/test_studio_golive.py — the Studio "Go Live" actions: connect Postiz, map accounts to Postiz
# integrations, and flip dryrun<->live ENTIRELY in the UI (no env vars / CLI / JSON edit). The load-
# bearing properties under test: the DUAL-WRITE (.env durable + os.environ in-process) so the switch
# takes effect without a restart; the API key is NEVER echoed in a result; go_live is the ONLY
# FANOPS_POSTER=postiz setter, gated on readiness + an explicit confirm; go_dryrun (safe) needs none.
# Env isolation: every test delenv's the three keys golive mutates so a live switch never leaks (the
# direct os.environ writes are undone because monkeypatch tracks the KEY, not the value-at-mutation).
import json
import os
import pytest
from fanops.config import Config
from fanops.errors import PostizAuthError
from fanops.studio import golive

# os.environ baseline captured at import (before any test mutates it) so the autouse fixture below can
# undo golive's DIRECT os.environ writes. monkeypatch.delenv of an ALREADY-ABSENT key registers NO
# restoration (pytest only tracks a delitem when the key was present), so the production dual-write
# (os.environ[...]=...) would otherwise leak FANOPS_POSTER/POSTIZ_* into later tests — e.g. flipping
# test_studio_run's dryrun assertions to postiz. Restore-to-baseline after every test fixes it at the source.
_ENV_KEYS = ("FANOPS_POSTER", "POSTIZ_URL", "POSTIZ_API_KEY")
_ENV_BASELINE = {k: os.environ.get(k) for k in _ENV_KEYS}

@pytest.fixture(autouse=True)
def _restore_golive_env():
    yield
    for k, v in _ENV_BASELINE.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _clean(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in ("FANOPS_POSTER", "POSTIZ_URL", "POSTIZ_API_KEY"):
        monkeypatch.delenv(k, raising=False)             # clean start + registers the key for teardown-restore
    return Config(root=tmp_path)

def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))


# ---- set_postiz_config: dual-write (.env + os.environ), auth tested, key NEVER returned ----
def test_set_postiz_config_dual_writes_and_tests_auth(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setattr(golive.postiz, "postiz_check_auth", lambda c: True)
    res = golive.set_postiz_config(cfg, "https://postiz.example.com", "SECRETKEY")
    assert res.ok is True
    env = (tmp_path / ".env").read_text()                # durable
    assert "POSTIZ_URL=https://postiz.example.com" in env and "POSTIZ_API_KEY=SECRETKEY" in env
    assert os.environ["POSTIZ_URL"] == "https://postiz.example.com"     # in-process (no restart needed)
    assert os.environ["POSTIZ_API_KEY"] == "SECRETKEY"
    assert "SECRETKEY" not in repr(res)                  # the key must NEVER appear in a result

def test_set_postiz_config_rejects_bad_url_no_write(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    res = golive.set_postiz_config(cfg, "not-a-url", "K")
    assert res.ok is False and "http" in res.error.lower()
    assert not (tmp_path / ".env").exists()              # no partial write on bad input

def test_set_postiz_config_reports_auth_failure_redacted(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    def boom(c): raise PostizAuthError("401 bad key SENTINEL")
    monkeypatch.setattr(golive.postiz, "postiz_check_auth", boom)
    res = golive.set_postiz_config(cfg, "https://x.example.com", "WRONGKEY")
    assert res.ok is False and "POSTIZ_API_KEY" in res.error
    assert "WRONGKEY" not in repr(res)                   # key never echoed even on failure

def test_set_postiz_config_url_only_keeps_existing_key(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "existing")
    monkeypatch.setattr(golive.postiz, "postiz_check_auth", lambda c: True)
    res = golive.set_postiz_config(cfg, "https://x.example.com", "")    # blank key -> not rewritten
    assert res.ok is True
    env = (tmp_path / ".env").read_text()
    assert "POSTIZ_URL=" in env and "POSTIZ_API_KEY" not in env

def test_set_postiz_config_unreachable_reports_clean(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setattr(golive.postiz, "postiz_check_auth", lambda c: False)   # bad URL / down
    res = golive.set_postiz_config(cfg, "https://nope.example.com", "K")
    assert res.ok is False and "reach" in res.error.lower()


# ---- refresh_integrations ----
def test_refresh_integrations_returns_list(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setattr(golive.postiz, "postiz_list_integrations",
                        lambda c: [{"id": "i1", "name": "IG", "platform": "instagram"}])
    res = golive.refresh_integrations(cfg)
    assert res.ok is True and res.detail["integrations"][0]["id"] == "i1"

def test_refresh_integrations_auth_failure_is_fatal(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    def boom(c): raise PostizAuthError("401")
    monkeypatch.setattr(golive.postiz, "postiz_list_integrations", boom)
    res = golive.refresh_integrations(cfg)
    assert res.ok is False and "POSTIZ_API_KEY" in res.error

def test_refresh_integrations_other_error_clean(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    def boom(c): raise RuntimeError("postiz down")
    monkeypatch.setattr(golive.postiz, "postiz_list_integrations", boom)
    res = golive.refresh_integrations(cfg)
    assert res.ok is False and "postiz down" in res.error


# ---- add_account: onboard a brand-new account in the UI (no JSON edit) ----
def test_add_account_appends_active_postiz(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    res = golive.add_account(cfg, "@new", ["instagram", "tiktok"], "hype edits")
    assert res.ok is True and res.detail["added"] == "@new"
    raw = json.loads(cfg.accounts_path.read_text())
    new = next(x for x in raw["accounts"] if x["handle"] == "@new")
    assert new["status"] == "active" and new["access"] == "postiz"
    assert new["platforms"] == ["instagram", "tiktok"] and new["persona"] == "hype edits"

def test_add_account_requires_handle(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    res = golive.add_account(cfg, "  ", ["instagram"])
    assert res.ok is False and "handle" in res.error.lower()

def test_add_account_requires_a_platform(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    res = golive.add_account(cfg, "@x", [])
    assert res.ok is False and "platform" in res.error.lower()

def test_add_account_duplicate_handle_clean_error(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    res = golive.add_account(cfg, "@a", ["tiktok"])
    assert res.ok is False and "duplicate" in res.error.lower()


# ---- map_account: per (handle, platform) -> its own Postiz integration id ----
def test_map_account_writes_per_platform_id(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "", "platforms": ["instagram", "tiktok"], "status": "active"}])
    assert golive.map_account(cfg, "@a", "instagram", "ig_5").ok is True
    assert golive.map_account(cfg, "@a", "tiktok", "tk_9").ok is True
    raw = json.loads(cfg.accounts_path.read_text())
    assert raw["accounts"][0]["integrations"] == {"instagram": "ig_5", "tiktok": "tk_9"}
    assert raw["accounts"][0]["account_id"] == ""              # per-platform write does NOT touch the shared id

def test_map_account_unknown_handle_clean_error(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    res = golive.map_account(cfg, "@nope", "instagram", "x")
    assert res.ok is False and "no such account" in res.error.lower()

def test_map_account_blank_id_rejected(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    res = golive.map_account(cfg, "@a", "instagram", "")
    assert res.ok is False

def test_map_account_blank_platform_rejected(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    res = golive.map_account(cfg, "@a", "", "ig_1")
    assert res.ok is False and "platform" in res.error.lower()


# ---- go_live: the ONLY FANOPS_POSTER=postiz setter; gated on readiness + explicit confirm ----
def test_go_live_blocked_unconfigured(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    res = golive.go_live(cfg, confirmed=True)
    assert res.ok is False and "POSTIZ_URL" in res.error
    assert cfg.poster_backend == "dryrun"                # NOT switched

def test_go_live_blocked_active_account_missing_id(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("POSTIZ_URL", "https://x"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    res = golive.go_live(cfg, confirmed=True)
    assert res.ok is False and "@a" in res.error
    assert cfg.poster_backend == "dryrun"

def test_go_live_needs_confirm(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("POSTIZ_URL", "https://x"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    res = golive.go_live(cfg, confirmed=False)
    assert res.ok is False and "confirm" in res.error.lower()
    assert cfg.poster_backend == "dryrun"                # ready, but not shipped without confirm

def test_go_live_success_flips_backend_dual_write(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("POSTIZ_URL", "https://x"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    res = golive.go_live(cfg, confirmed=True)
    assert res.ok is True and res.detail["mode"] == "postiz"
    assert os.environ["FANOPS_POSTER"] == "postiz"                          # in-process
    assert "FANOPS_POSTER=postiz" in (tmp_path / ".env").read_text()        # durable
    assert cfg.poster_backend == "postiz"


# ---- go_dryrun: always allowed (safe direction), no confirm ----
def test_go_dryrun_flips_back(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    res = golive.go_dryrun(cfg)
    assert res.ok is True and res.detail["mode"] == "dryrun"
    assert cfg.poster_backend == "dryrun"
    assert "FANOPS_POSTER=dryrun" in (tmp_path / ".env").read_text()


# ---- golive_status read-model (views.golive_status): mode, config-set bools, active accounts to map,
# doctor readiness. Lock-free; key exposed as a BOOL only; malformed accounts.json must not 500. ----
def test_golive_status_default_dryrun(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    from fanops.studio import views
    st = views.golive_status(cfg)
    assert st.mode == "dryrun" and st.is_live is False
    assert st.key_set is False and st.postiz_url is None
    assert st.checks == [] or st.checks is not None         # dataclass attrs present
    assert st.notes is not None

def test_golive_status_reflects_config_and_per_platform_channels(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://p.example.com"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    _seed_accounts(cfg, [
        {"handle": "@a", "account_id": "", "platforms": ["instagram", "tiktok"], "status": "active",
         "integrations": {"instagram": "ig_1", "tiktok": "tk_9"}},
        {"handle": "@soon", "account_id": "", "platforms": ["instagram"], "status": "planned"},
    ])
    from fanops.studio import views
    st = views.golive_status(cfg)
    assert st.mode == "postiz" and st.is_live is True
    assert st.postiz_url == "https://p.example.com" and st.key_set is True
    assert [a.handle for a in st.accounts] == ["@a"]          # active only; @soon (planned) excluded
    chans = {c.platform: c.integration_id for c in st.accounts[0].channels}
    assert chans == {"instagram": "ig_1", "tiktok": "tk_9"}   # each channel shows its OWN integration id

def test_golive_status_channel_falls_back_to_shared_account_id(tmp_path, monkeypatch):
    # a legacy account (shared account_id, no integrations) shows that id as each channel's effective id.
    cfg = _clean(monkeypatch, tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "shared", "platforms": ["instagram", "tiktok"], "status": "active"}])
    from fanops.studio import views
    st = views.golive_status(cfg)
    assert all(c.integration_id == "shared" for c in st.accounts[0].channels)

def test_golive_status_tolerates_malformed_accounts(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text("{ not json")
    from fanops.studio import views
    st = views.golive_status(cfg)                              # must not raise
    assert st.accounts == [] and st.mode == "dryrun"

def test_golive_status_tolerates_doctor_failure(tmp_path, monkeypatch):
    # invariant: the Go-Live tab must never 500 — a raising doctor_report falls back to an empty report
    cfg = _clean(monkeypatch, tmp_path)
    import fanops.doctor as doctor
    monkeypatch.setattr(doctor, "doctor_report", lambda c: (_ for _ in ()).throw(RuntimeError("doctor broke")))
    from fanops.studio import views
    st = views.golive_status(cfg)                              # must not raise
    assert st.checks == [] and st.mode == "dryrun"

def test_golive_status_never_exposes_key(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("POSTIZ_API_KEY", "TOPSECRET")
    from fanops.studio import views
    st = views.golive_status(cfg)
    assert "TOPSECRET" not in repr(st) and st.key_set is True


# ---- .env write failure must surface as a clean ActionResult, never a 500 (the tab's invariant) ----
def test_set_postiz_config_disk_error_is_clean_not_raise(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setattr(golive, "set_env_var", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only fs")))
    res = golive.set_postiz_config(cfg, "https://x.example.com", "K")
    assert res.ok is False and ".env" in res.error
    assert "POSTIZ_URL" not in os.environ                # os.environ NOT mutated when the durable write failed

def test_go_dryrun_disk_error_is_clean(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setattr(golive, "set_env_var", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    res = golive.go_dryrun(cfg)
    assert res.ok is False and ".env" in res.error

def test_set_postiz_config_newline_in_key_blocked_cleanly(tmp_path, monkeypatch):
    # end-to-end: a key with an embedded newline (injection attempt) is rejected by set_env_var and
    # surfaced as a clean ActionResult, never written, never a 500.
    cfg = _clean(monkeypatch, tmp_path)
    res = golive.set_postiz_config(cfg, "https://x.example.com", "good\nINJECTED=1")
    assert res.ok is False
    assert os.environ.get("INJECTED") is None            # the injected key never lands


# ---- Flask wiring (create_app + test_client), mirroring test_studio_publish_now's route tests ----
def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    return app.test_client()

def test_get_golive_renders_banner_without_key(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "TOPSECRET")
    r = _client(cfg).get("/golive")
    assert r.status_code == 200
    assert b"Go Live" in r.data and b"DRYRUN" in r.data
    assert b"TOPSECRET" not in r.data                 # the key VALUE never appears in the HTML

def test_get_golive_has_nav_tab(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    r = _client(cfg).get("/review")                   # the nav tab is on every page
    assert r.status_code == 200 and b"/golive" in r.data

def test_post_golive_config_route_no_key_echo(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setattr(golive.postiz, "postiz_check_auth", lambda c: True)
    r = _client(cfg).post("/golive/config", data={"url": "https://p.example.com", "key": "SECRETKEY"})
    assert r.status_code == 200
    assert b"SECRETKEY" not in r.data                 # key never rendered back
    assert cfg.postiz_url == "https://p.example.com"

def test_post_golive_refresh_route_lists_integrations(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    monkeypatch.setattr(golive.postiz, "postiz_list_integrations",
                        lambda c: [{"id": "i1", "name": "IG Reels", "platform": "instagram"}])
    r = _client(cfg).post("/golive/refresh")
    assert r.status_code == 200 and b"IG Reels" in r.data

def test_post_golive_map_route_maps_only_picked_channel(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed_accounts(cfg, [
        {"handle": "@a", "account_id": "", "platforms": ["instagram", "tiktok"], "status": "active"},
        {"handle": "@b", "account_id": "", "platforms": ["tiktok"], "status": "active"},
    ])
    # map only @a/instagram; leave @a/tiktok and @b/tiktok blank
    r = _client(cfg).post("/golive/map", data={"map__@a__instagram": "ig_9",
                                               "map__@a__tiktok": "", "map__@b__tiktok": ""})
    assert r.status_code == 200
    by = {a["handle"]: a.get("integrations", {}) for a in json.loads(cfg.accounts_path.read_text())["accounts"]}
    assert by["@a"] == {"instagram": "ig_9"}          # only the picked channel mapped
    assert by["@b"] == {}                              # @b untouched

def test_post_golive_account_add_route_appends(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    r = _client(cfg).post("/golive/account/add",
                          data={"handle": "@fresh", "platform": ["instagram", "tiktok"], "persona": "raw"})
    assert r.status_code == 200
    accts = json.loads(cfg.accounts_path.read_text())["accounts"]
    fresh = next(a for a in accts if a["handle"] == "@fresh")
    assert fresh["status"] == "active" and fresh["platforms"] == ["instagram", "tiktok"]
    assert b"@fresh" in r.data                          # the new account shows in the refreshed panel

def test_post_golive_account_add_route_rejects_no_platform(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    r = _client(cfg).post("/golive/account/add", data={"handle": "@x"})   # no platform checkboxes
    assert r.status_code == 200 and b"platform" in r.data
    assert not cfg.accounts_path.exists() or json.loads(cfg.accounts_path.read_text())["accounts"] == []

def test_post_golive_dryrun_route_sets_dryrun(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://x"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    r = _client(cfg).post("/golive/dryrun")
    assert r.status_code == 200 and cfg.poster_backend == "dryrun"

def test_post_golive_live_route_blocked_unconfigured(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    r = _client(cfg).post("/golive/live", data={"confirm": "1"})
    assert r.status_code == 200
    assert cfg.poster_backend == "dryrun"             # still dryrun — blocked by readiness
    assert b"POSTIZ_URL" in r.data                     # the panel shows the failing reason

def test_post_golive_live_route_success_flips_and_shows_live(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("POSTIZ_URL", "https://x"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    r = _client(cfg).post("/golive/live", data={"confirm": "1"})
    assert r.status_code == 200 and cfg.poster_backend == "postiz"
    assert b"LIVE" in r.data
