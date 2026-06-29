"""R3-followup RED — the UI status label must speak the per-channel truth, not the
legacy FANOPS_POSTER global.

PRD evidence (this session): with FANOPS_LIVE=1, per-channel routing for IG/postiz +
TikTok/zernio (validated clean by R2), and the LEGACY FANOPS_POSTER=dryrun in .env
as the bridge fallback, the Studio status field `backend` read 'dryrun' — printing
DRYRUN on every panel that gates on `backend != 'dryrun'`. The system was actually
publishing live; the UI was lying.

The fix is structural: `pipeline_status['backend']` (and every render that mirrors
it) MUST read the per-channel truth via `_publish_mode_label`, not the raw
`cfg.poster_backend` which is the legacy bridge global."""
from __future__ import annotations
import json
from fanops.config import Config
from fanops.studio import views


def _seed_live_per_channel(cfg, monkeypatch):
    """Reproduce the session's exact state: FANOPS_LIVE=1 + legacy FANOPS_POSTER=dryrun
    bridge + a clean per-channel routing (IG/postiz + TikTok/zernio)."""
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")      # the legacy bridge that lies
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.setenv("ZERNIO_API_KEY", "zk")
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@ig", "account_id": "1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig_1"}, "backends": {"instagram": "postiz"}},
        {"handle": "@tk", "account_id": "2", "platforms": ["tiktok"], "status": "active",
         "integrations": {"tiktok": "tk_1"}, "backends": {"tiktok": "zernio"}},
    ]}))


def test_pipeline_status_backend_speaks_per_channel_truth_not_dryrun_global(tmp_path, monkeypatch):
    """R3-followup: pipeline_status['backend'] on a LIVE system with per-channel routing
    MUST resolve to the providers actually publishing — never 'dryrun' just because the
    legacy FANOPS_POSTER global is still 'dryrun'."""
    cfg = Config(root=tmp_path)
    _seed_live_per_channel(cfg, monkeypatch)
    status = views.pipeline_status(cfg)
    assert status["backend"] != "dryrun", (
        f"pipeline_status['backend'] is LYING: shows {status['backend']!r} on a live "
        f"per-channel deployment. The legacy FANOPS_POSTER fallback bridged through.")
    # The label must name the per-channel providers actually publishing.
    assert "postiz" in status["backend"] or "zernio" in status["backend"], (
        f"backend label does not name the real providers: {status['backend']!r}")


def test_pipeline_status_backend_says_dryrun_when_not_live(tmp_path, monkeypatch):
    """R3-followup firewall: when FANOPS_LIVE is 0 (not live), the label MUST stay
    'dryrun' — the fix narrows the lie, it doesn't flip the truth in the other direction."""
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": []}))
    status = views.pipeline_status(cfg)
    assert status["backend"] == "dryrun", (
        f"not-live system MUST say 'dryrun', got {status['backend']!r}")
