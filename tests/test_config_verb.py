"""MOL-294: fanops config introspection verb."""
from fanops.config import Config
from fanops.config_introspect import config_rows, format_config_report
from fanops.settings import Settings
from tests.keyring_fake import install_mem_keyring


def test_config_rows_from_settings_model_fields(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("FANOPS_RESPONDER=llm\n")
    cfg = Config(root=tmp_path)
    rows = config_rows(cfg)
    names = {r["name"] for r in rows}
    assert "FANOPS_RESPONDER" in names
    assert "POSTIZ_API_KEY" in names
    assert len(rows) == len(Settings.model_fields)


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


def test_config_row_shows_keychain_source_for_keyring_secret(tmp_path, monkeypatch):
    install_mem_keyring(monkeypatch)
    monkeypatch.chdir(tmp_path)
    from fanops import secret_provider
    secret_provider.set_secret("POSTIZ_API_KEY", "from-keyring")
    cfg = Config(root=tmp_path)
    row = next(r for r in config_rows(cfg) if r["name"] == "POSTIZ_API_KEY")
    assert row["source"] == "keychain"


def test_config_keychain_source_wins_over_stale_dotenv_plaintext(tmp_path, monkeypatch):
    install_mem_keyring(monkeypatch)
    monkeypatch.chdir(tmp_path)
    from fanops import secret_provider
    (tmp_path / ".env").write_text("POSTIZ_API_KEY=stale-plaintext\n")
    secret_provider.set_secret("POSTIZ_API_KEY", "from-keyring")
    cfg = Config(root=tmp_path)
    row = next(r for r in config_rows(cfg) if r["name"] == "POSTIZ_API_KEY")
    assert row["source"] == "keychain"


def test_config_keychain_source_wins_over_stale_os_environ(tmp_path, monkeypatch):
    install_mem_keyring(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POSTIZ_API_KEY", "stale-shell")
    from fanops import secret_provider
    secret_provider.set_secret("POSTIZ_API_KEY", "from-keyring")
    cfg = Config(root=tmp_path)
    row = next(r for r in config_rows(cfg) if r["name"] == "POSTIZ_API_KEY")
    assert row["source"] == "keychain"
