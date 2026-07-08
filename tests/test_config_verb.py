"""MOL-294: fanops config introspection verb."""
from fanops.config import Config
from fanops.config_introspect import config_rows, format_config_report


def test_config_rows_from_settings_model_fields(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("FANOPS_RESPONDER=llm\n")
    cfg = Config(root=tmp_path)
    rows = config_rows(cfg)
    names = {r["name"] for r in rows}
    assert "FANOPS_RESPONDER" in names
    assert "POSTIZ_API_KEY" in names
    assert len(rows) == len(Config(root=tmp_path)._settings.__class__.model_fields)


def test_config_row_shows_env_source_and_masks_secret(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POSTIZ_API_KEY", "sekret")
    cfg = Config(root=tmp_path)
    row = next(r for r in config_rows(cfg) if r["name"] == "POSTIZ_API_KEY")
    assert row["source"] == "os.environ"
    assert row["effective"] == "(set)"
    assert row["studio"] is True


def test_config_row_studio_settable_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    resp = next(r for r in config_rows(cfg) if r["name"] == "FANOPS_RESPONDER")
    whisper = next(r for r in config_rows(cfg) if r["name"] == "FANOPS_WHISPER_MODEL")
    assert resp["studio"] is True
    assert whisper["studio"] is False


def test_format_config_report_header(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = format_config_report(Config(root=tmp_path))
    lines = out.splitlines()
    assert lines[0] == "fanops config"
    assert "STUDIO" in lines[1]
