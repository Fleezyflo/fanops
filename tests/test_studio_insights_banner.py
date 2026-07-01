# tests/test_studio_insights_banner.py
# Leg 2 (Insight): the scope-blocked signal surfaces on the Studio Home system strip (the operator's
# global health line), mirroring the failed-post / blocked-gate alerts. Read-model level (no live server).
import json
from fanops.config import Config
from fanops.studio import views


def _cfg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return Config(root=tmp_path)


def test_system_strip_reports_insights_blocked(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.insights_blocked_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.insights_blocked_path.write_text(json.dumps({"blocked": True}))
    strip = views.build_system_strip(cfg)
    assert strip["insights_blocked"] is True


def test_system_strip_clean_when_not_blocked(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    strip = views.build_system_strip(cfg)
    assert strip["insights_blocked"] is False        # no breadcrumb -> no false alarm
