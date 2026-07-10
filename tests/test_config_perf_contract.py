# tests/test_config_perf_contract.py — MOL-292 PR-1: single-parse env load contract
from pathlib import Path

from fanops.config import Config
from fanops.config_introspect import config_rows


def test_config_parses_settings_once(monkeypatch, tmp_path):
    calls = {"n": 0}
    from fanops import settings as settings_mod
    orig = settings_mod.Settings.runtime_load

    def _counting(root):
        calls["n"] += 1
        return orig(root)

    monkeypatch.setattr(settings_mod.Settings, "runtime_load", classmethod(lambda cls, root: _counting(root)))
    cfg = Config(root=tmp_path)
    _ = cfg.poster_backend
    _ = cfg.is_live
    _ = cfg.postiz_url
    assert calls["n"] == 1


def test_config_has_no_os_getenv_in_properties():
    text = Path("src/fanops/config.py").read_text()
    init_end = text.index("    def render_path")
    body = text[init_end:]
    assert "os.getenv" not in body, "config.py must not call os.getenv outside __init__"


def test_introspect_matches_config_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    cfg = Config(root=tmp_path)
    row = next(r for r in config_rows(cfg) if r["name"] == "FANOPS_POSTER")
    assert cfg.poster_backend == "postiz"
    assert "postiz" in row["effective"] or row["effective"] == "postiz"
