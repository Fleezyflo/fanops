"""MOL-302: SetupState derived on read — boundaries, no cache, next matches doctor hints."""
from fanops.config import Config
from fanops.doctor import SetupState, setup_state, setup_next_action, _brief_ok


def _cfg(tmp_path, monkeypatch, **env):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "MohFlow-FanOps" / "00_control").mkdir(parents=True)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Config(root=tmp_path)


def test_not_configured_without_brief(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert setup_state(cfg) == SetupState.NOT_CONFIGURED
    assert "context.md" in setup_next_action(cfg)


def test_configured_with_brief_and_accounts(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.context_path.write_text("brand brief")
    (cfg.accounts_path).write_text('{"accounts":[{"handle":"a","platforms":["instagram"],"status":"active","account_id":"1"}]}')
    assert setup_state(cfg) == SetupState.CONFIGURED
    assert "Connect Postiz" in setup_next_action(cfg)


def test_connected_with_postiz_key(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, POSTIZ_URL="http://localhost:5000", POSTIZ_API_KEY="k")
    cfg.context_path.write_text("brand brief")
    (cfg.accounts_path).write_text('{"accounts":[{"handle":"a","platforms":["instagram"],"status":"active","account_id":"1"}]}')
    assert setup_state(cfg) == SetupState.CONNECTED


def test_setup_state_recomputed_not_cached(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert setup_state(cfg) == SetupState.NOT_CONFIGURED
    cfg.context_path.write_text("now authored")
    assert setup_state(cfg) == SetupState.CONFIGURED  # brief ok, empty accounts.json passes validate
    (cfg.accounts_path).write_text('{"accounts":[{"handle":"a","platforms":["instagram"],"status":"active","account_id":"1"}]}')
    assert setup_state(cfg) == SetupState.CONFIGURED  # still no postiz key


def test_brief_ok_reads_live(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert _brief_ok(cfg) is False
    cfg.context_path.write_text("x")
    assert _brief_ok(cfg) is True
