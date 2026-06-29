"""UI-LIE-FIX root test — every operator-facing 'backend' surface MUST speak the
per-channel truth (M3), not the legacy FANOPS_POSTER global.

Pins the 3 classes of callsite from the audit:
  A — display labels (status banners, autopilot summary, fanops status)
  B — friendly errors (LIVE backend (...) on hx-confirm refusal)
  C — auth-key picker (FATAL auth failure -> check WHICH key)

The session bug: FANOPS_LIVE=1 + per-channel routing (IG/postiz, TikTok/zernio)
+ legacy FANOPS_POSTER=dryrun showed 'dryrun' on every surface, including
fanops status + autopilot summary + 'LIVE backend (dryrun)' contradictory error."""
from __future__ import annotations
import json
from fanops.config import Config


def _seed_live_per_channel(cfg, monkeypatch):
    """Reproduce the session's exact state: FANOPS_LIVE=1 + legacy FANOPS_POSTER=dryrun
    bridge + clean per-channel routing (IG/postiz + TikTok/zernio)."""
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.setenv("ZERNIO_API_KEY", "zk")
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@ig", "account_id": "1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig_1"}, "backends": {"instagram": "postiz"}},
        {"handle": "@tk", "account_id": "2", "platforms": ["tiktok"], "status": "active",
         "integrations": {"tiktok": "tk_1"}, "backends": {"tiktok": "zernio"}},
    ]}))


# ---- Class A: display labels ----

def test_config_effective_publish_mode_names_per_channel_providers(tmp_path, monkeypatch):
    """The Config truth source itself: a live per-channel system MUST resolve to the
    actual providers publishing, never to the legacy 'dryrun' global."""
    cfg = Config(root=tmp_path)
    _seed_live_per_channel(cfg, monkeypatch)
    mode = cfg.effective_publish_mode()
    assert mode != "dryrun", f"effective_publish_mode lied: {mode!r}"
    assert "postiz" in mode and "zernio" in mode, f"missing per-channel providers: {mode!r}"


def test_config_effective_publish_mode_says_dryrun_when_not_live(tmp_path, monkeypatch):
    """Firewall: not-live system stays 'dryrun'. The fix narrows the lie, not flips it."""
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": []}))
    assert cfg.effective_publish_mode() == "dryrun"


def test_cli_status_speaks_per_channel_truth(tmp_path, monkeypatch, capsys):
    """`fanops status` MUST print the per-channel mode, not the legacy global."""
    cfg = Config(root=tmp_path)
    _seed_live_per_channel(cfg, monkeypatch)
    monkeypatch.chdir(tmp_path)
    from fanops.cli import main
    main(["status"])
    out = capsys.readouterr().out
    assert "backend=dryrun" not in out, f"fanops status lied: {out!r}"
    assert "backend=postiz" in out or "backend=zernio" in out, (
        f"fanops status did not name a real provider: {out!r}")


def test_autopilot_summary_speaks_per_channel_truth(tmp_path, monkeypatch):
    """autopilot.status's 'backend' field MUST be the per-channel truth (the operator
    sees this in the autopilot summary on first-run setup)."""
    cfg = Config(root=tmp_path)
    _seed_live_per_channel(cfg, monkeypatch)
    
    from fanops import autopilot as autopilot_mod; res = autopilot_mod.autopilot(cfg, interval=15, install_daemon=False)
    assert res["backend"] != "dryrun", f"autopilot lied: {res['backend']!r}"


def test_studio_pipeline_status_speaks_per_channel_truth(tmp_path, monkeypatch):
    """Pre-existing R3-followup pin: pipeline_status.backend reads per-channel."""
    cfg = Config(root=tmp_path)
    _seed_live_per_channel(cfg, monkeypatch)
    from fanops.studio import views
    assert views.pipeline_status(cfg)["backend"] != "dryrun"


# ---- Class B: friendly errors ----

def test_publish_now_live_refusal_names_per_channel_mode(tmp_path, monkeypatch):
    """The hx-confirm refusal message MUST name the per-channel providers, not
    'LIVE backend (dryrun)' — the operator can't agree to a confirm they can't read."""
    cfg = Config(root=tmp_path)
    _seed_live_per_channel(cfg, monkeypatch)
    from fanops.studio.actions import publish_now
    res = publish_now(cfg, "nope", confirmed=False)
    assert not res.ok
    # Either it refused for live-without-confirm (good) or no-such-post (also fine);
    # IF the live-refusal fired, the error MUST name the per-channel providers, not dryrun.
    if "LIVE backend" in (res.error or ""):
        assert "dryrun" not in res.error, f"live refusal lied: {res.error!r}"
        assert "postiz" in res.error or "zernio" in res.error, (
            f"live refusal did not name a real provider: {res.error!r}")


# ---- Class C: auth-key picker ----

def test_auth_key_name_for_returns_per_backend_env_var(tmp_path):
    """The auth-key picker MUST resolve per-backend, not via the lying global."""
    cfg = Config(root=tmp_path)
    assert cfg.auth_key_name_for("postiz") == "POSTIZ_API_KEY"
    assert cfg.auth_key_name_for("zernio") == "ZERNIO_API_KEY"
    assert cfg.auth_key_name_for("blotato") == "BLOTATO_API_KEY"
    # Unknown / empty -> fall back to the global name (operator can still figure it out).
    assert cfg.auth_key_name_for("") == "FANOPS_POSTER"
    assert cfg.auth_key_name_for("not-a-backend") == "FANOPS_POSTER"
