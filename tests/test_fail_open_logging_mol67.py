# tests/test_fail_open_logging_mol67.py
"""MOL-67 — fail-open logging discipline across read-helper layers.

Fail-open is a defect to call out, not a passing contract: account_arg / lineage_stats fail closed
on real broken files/rows. certifi pins the exact fallback dict. persona_facts is a real call.
preview_media None-on-miss is OK (no log claimed). doctor half-live is not-confirmed-LIVE without patches.
"""
import logging

import pytest

from fanops.config import Config


def _cfg(tmp_path):
    return Config(root=tmp_path)


# ── 1. ledger_wipe.snapshot_is_restorable — except -> return False (cfg-less: module logger) ──
def test_snapshot_is_restorable_logs_and_returns_false(tmp_path, caplog):
    from fanops import ledger_wipe
    # A directory path: exists() True, read_text() raises IsADirectoryError -> caught.
    d = tmp_path / "snap_dir"
    d.mkdir()
    with caplog.at_level(logging.WARNING, logger="fanops.ledger_wipe"):
        result = ledger_wipe.snapshot_is_restorable(str(d))
    assert result is False                                        # fallback unchanged
    assert any(r.name == "fanops.ledger_wipe" for r in caplog.records)   # logged before swallow


# ── 2. vocals._demucs_env — except -> pass (cfg-less: module logger) + narrowed to ImportError ──
def test_demucs_env_logs_on_missing_certifi(tmp_path, monkeypatch, caplog):
    import builtins
    import os
    from fanops import vocals
    real_import = builtins.__import__

    def _fake_import(name, *a, **k):
        if name == "certifi":
            raise ImportError("no certifi")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    baseline = dict(os.environ)
    with caplog.at_level(logging.WARNING, logger="fanops.vocals"):
        env = vocals._demucs_env()
    assert env == baseline                                          # exact fallback: no SSL overlay when certifi is absent
    assert any(r.name == "fanops.vocals" for r in caplog.records)   # logged before swallow


def test_demucs_env_narrowed_does_not_swallow_unrelated(tmp_path, monkeypatch):
    """Narrowing bare Exception -> ImportError: a real error from certifi.where() must PROPAGATE,
    not be silently absorbed as 'certifi absent'."""
    import builtins
    from fanops import vocals
    real_import = builtins.__import__

    class _BadCertifi:
        @staticmethod
        def where():
            raise RuntimeError("certifi.where blew up")

    def _fake_import(name, *a, **k):
        if name == "certifi":
            return _BadCertifi
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    with pytest.raises(RuntimeError):
        vocals._demucs_env()


# ── 3. persona_directives.persona_facts — real call, no dead measurement-cache patch ──
def test_persona_facts_returns_transparency_shape(tmp_path):
    from fanops import persona_directives
    cfg = _cfg(tmp_path)

    class _P:
        hashtag_corpus = []
        clip_profile = None
        framing = None
        content_focus = None
        energy = None
        niche = []
        cut_policy = []

    facts = persona_directives.persona_facts(cfg, _P())
    assert set(facts) == {"length_band", "framing", "lead_tags", "terms"}
    assert facts["lead_tags"] == []
    assert facts["length_band"] == ""
    assert facts["terms"] == []


# (site 4 — meta_graph._read_queries, the local hashtag budget reader — is GONE with the budget fiction.)
# ── 5. studio/preview_media — returns None when no artifact exists (P9: no render ladder) ──
def test_preview_media_returns_none_when_no_artifact(tmp_path):
    from fanops.studio import preview_media
    cfg = Config(root=tmp_path)

    class _Clip:
        id = "clip-1"; parent_id = "mom-1"; path = None
    class _Post:
        render_id = None; parent_id = "clip-1"; account = "handle"; media_urls = []
    class _Led:
        posts = {"post-1": _Post()}
        renders = {}
        clips = {"clip-1": _Clip()}

    result = preview_media.preview_media_path(cfg, _Led(), "post-1")
    assert result is None


# ── 7. studio/app_request._account_arg — torn accounts.json must fail closed, not return raw ──
def test_account_arg_fails_closed_on_unreadable_accounts(tmp_path):
    from fanops.studio import app as studio_app
    from fanops.studio import app_request
    cfg = _cfg(tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.mkdir()
    flask_app = studio_app.create_app(cfg)
    with flask_app.test_request_context("/?account=someone"):
        with pytest.raises(OSError):
            app_request._account_arg()


# ── 8. doctor.doctor_report half_live — LIVE with no live route is not solid LIVE (no patches) ──
def test_doctor_half_live_logs_on_route_error(tmp_path, monkeypatch):
    from fanops import doctor
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    monkeypatch.delenv("ZERNIO_API_KEY", raising=False)
    cfg = _cfg(tmp_path)
    rep = doctor.doctor_report(cfg)
    labels = {c["label"]: c for c in rep["checks"]}
    route_check = next((c for k, c in labels.items() if "live route exists" in k), None)
    assert route_check is not None and route_check["ok"] is False
    hint = (route_check.get("hint") or "").lower()
    assert "nothing routes" in hint or "not confirmed" in hint


# ── 9. studio/views.build_system_strip postiz_down — except -> {"show": False} (cfg in scope) ──
def test_system_strip_postiz_down_logs_on_health_error(tmp_path, monkeypatch):
    from fanops.studio import views, views_common
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(views_common, "postiz_health_for_banner",
                        lambda c, **k: (_ for _ in ()).throw(RuntimeError("health boom")))
    strip = views.build_system_strip(cfg)
    assert strip["postiz_down"]["show"] is False                   # no postiz routes → still hide
    log_text = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "postiz_down" in log_text


# ── 10. studio/views_posted.lineage_stats — a row that raises must fail closed, not return input ──
def test_lineage_stats_fails_closed_on_row_error():
    from fanops.studio import views_results

    class _BadRow:
        clip_id = "c1"
        @property
        def lift_score(self):
            raise RuntimeError("lift boom")

    with pytest.raises(RuntimeError, match="lift boom"):
        views_results.lineage_stats([_BadRow(), _BadRow()])


def test_system_strip_postiz_down_shows_unknown_when_routed_and_helper_raises(tmp_path, monkeypatch):
    # MOL-963 R2d: helper raise + channel routes to postiz → show unknown, never silent hide.
    import json
    from fanops.studio import views, views_common
    cfg = _cfg(tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "ig", "account_id": "1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig_1"}, "backends": {"instagram": "postiz"}}]}))
    monkeypatch.setattr(views_common, "postiz_health_for_banner",
                        lambda c, **k: (_ for _ in ()).throw(RuntimeError("health boom")))
    strip = views.build_system_strip(cfg)
    assert strip["postiz_down"]["show"] is True
    assert "unknown" in (strip["postiz_down"].get("hint") or "").lower()
