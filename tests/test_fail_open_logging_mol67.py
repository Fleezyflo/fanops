# tests/test_fail_open_logging_mol67.py
"""MOL-67 — fail-open logging discipline across read-helper layers.

Each read-helper below swallows an exception and falls back to a safe default. The ticket's
contract: every such swallow must LOG before falling back (so a persistently-recurring silent
failure is findable), with NO behavior change — the fallback value/control-flow stays byte-identical.

Per site we assert BOTH halves:
  (a) the log fired — for cfg-in-scope sites via the structured run.log (cfg.log_path text carries the
      component + tag, matching the get_logger(cfg) convention); for cfg-less sites via caplog on the
      module logger (logging.getLogger(__name__), the house module-level convention).
  (b) the fallback value is unchanged (return value / assigned default).
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
    from fanops import vocals
    real_import = builtins.__import__

    def _fake_import(name, *a, **k):
        if name == "certifi":
            raise ImportError("no certifi")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    with caplog.at_level(logging.WARNING, logger="fanops.vocals"):
        env = vocals._demucs_env()
    assert "SSL_CERT_FILE" not in env or env.get("SSL_CERT_FILE")   # fallback: no crash, env returned
    assert isinstance(env, dict)                                    # returns the env dict unchanged in shape
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


# ── 3. persona_directives.persona_facts — except -> store = None (cfg in scope) ──
def test_persona_facts_logs_on_store_load_error(tmp_path, monkeypatch):
    from fanops import persona_directives
    cfg = _cfg(tmp_path)

    def _boom(_cfg):
        raise OSError("store unreadable")

    monkeypatch.setattr("fanops.hashtags.load_store", _boom)

    class _P:
        hashtag_corpus = []
        clip_profile = None
        framing = None
        content_focus = None
        energy = None

    facts = persona_directives.persona_facts(cfg, _P())
    assert set(facts) == {"length_band", "framing", "lead_tags"}   # fallback shape unchanged (store=None path)
    log_text = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "personas" in log_text or "persona" in log_text          # logged before falling to store=None


# ── 4. meta_graph._read_queries — except -> return None (cfg in scope) ──
def test_read_queries_logs_on_corrupt_budget(tmp_path, monkeypatch):
    from fanops import meta_graph
    cfg = _cfg(tmp_path)
    cfg.hashtag_budget_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtag_budget_path.write_text("NOT JSON {{{")
    result = meta_graph._read_queries(cfg)
    assert result is None                                           # fallback unchanged (fail-closed None)
    log_text = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "queries" in log_text or "budget" in log_text            # logged before swallow


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


# ── 7. studio/app._account_arg — except -> pass (cfg NOT reliably in scope: module logger) ──
def test_account_arg_logs_on_resolve_error(tmp_path, monkeypatch, caplog):
    from fanops.studio import app as studio_app
    cfg = _cfg(tmp_path)
    flask_app = studio_app.create_app(cfg)
    monkeypatch.setattr("fanops.studio.views.resolve_account_handle",
                        lambda v, c: (_ for _ in ()).throw(RuntimeError("resolve boom")))
    with flask_app.test_request_context("/?account=someone"):
        with caplog.at_level(logging.WARNING, logger="fanops.studio.app"):
            out = studio_app._account_arg()
    assert out == "someone"                                        # fallback: returns the raw handle unchanged
    assert any(r.name == "fanops.studio.app" for r in caplog.records)


# ── 8. doctor.doctor_report half_live — except -> half_live = False (cfg in scope) ──
def test_doctor_half_live_logs_on_route_error(tmp_path, monkeypatch):
    from fanops import doctor
    monkeypatch.setenv("FANOPS_LIVE", "1")
    cfg = _cfg(tmp_path)

    # force is_live True, live_route_exists raising
    monkeypatch.setattr(type(cfg), "live_route_exists",
                        property(lambda self: (_ for _ in ()).throw(RuntimeError("route boom"))))
    rep = doctor.doctor_report(cfg)
    # fallback: half_live=False -> the "live route exists" check passes (ok=True) despite the raise
    labels = {c["label"]: c for c in rep["checks"]}
    route_check = next((c for k, c in labels.items() if "live route exists" in k), None)
    assert route_check is not None and route_check["ok"] is True    # fallback unchanged (half_live=False)
    log_text = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "half_live" in log_text or "doctor" in log_text


# ── 9. studio/views.build_system_strip postiz_down — except -> {"show": False} (cfg in scope) ──
def test_system_strip_postiz_down_logs_on_health_error(tmp_path, monkeypatch):
    from fanops.studio import views, views_common
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(views_common, "postiz_health_for_banner",
                        lambda c, **k: (_ for _ in ()).throw(RuntimeError("health boom")))
    strip = views.build_system_strip(cfg)
    assert strip["postiz_down"] == {"show": False}                 # fallback unchanged
    log_text = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "postiz_down" in log_text


# ── 10. studio/views_results.lineage_stats — except -> pass (cfg-less: module logger) ──
def test_lineage_stats_logs_on_error(tmp_path, caplog):
    from fanops.studio import views_results

    class _BadRow:
        clip_id = "c1"
        @property
        def lift_score(self):
            raise RuntimeError("lift boom")

    rows = [_BadRow(), _BadRow()]
    with caplog.at_level(logging.WARNING, logger="fanops.studio.views_results"):
        result = views_results.lineage_stats(rows)
    assert result is rows                                          # fail-open returns the input rows unchanged (MOL-70: returns list, not None)
    assert any(r.name == "fanops.studio.views_results" for r in caplog.records)
