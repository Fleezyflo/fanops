"""#3 fail-open: a corrupt control file (accounts.json / ledger.json) must degrade to a clear page at
HTTP 200 on EVERY tab, not a 500. A global @app.errorhandler(ControlFileError) catches the unguarded
Accounts.load/Ledger.load that any route runs (mirrors the _too_large 200 precedent — htmx 2.x drops
non-2xx swaps). PROVEN live failure: the stale-Studio 500 this session was exactly this class."""
import json
from fanops.config import Config
from fanops.studio.app import create_app


def _client(cfg):
    app = create_app(cfg); app.config["PROPAGATE_EXCEPTIONS"] = False   # let the errorhandler answer, not re-raise
    return app.test_client()


def _seed_control(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": []}))


def test_corrupt_accounts_degrades_not_500(tmp_path):
    cfg = Config(root=tmp_path); _seed_control(cfg)
    cfg.accounts_path.write_text("{ this is not valid json")          # corrupt AFTER the dir exists
    c = _client(cfg)
    for path in ("/review", "/posted"):
        r = c.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code} (want 200, never a 500)"
        assert b"Control file unreadable" in r.data
        assert b"accounts.json" in r.data                             # names the offending file


def test_corrupt_ledger_degrades_not_500(tmp_path):
    cfg = Config(root=tmp_path); _seed_control(cfg)
    cfg.ledger_path.write_text("{ broken")
    c = _client(cfg)
    r = c.get("/review")
    assert r.status_code == 200, f"/review -> {r.status_code} (want 200, never a 500)"
    assert b"Control file unreadable" in r.data
    assert b"ledger.json" in r.data


def test_healthy_control_files_still_render(tmp_path):
    # the errorhandler must not change the happy path: valid files -> normal 200 render, no error page.
    cfg = Config(root=tmp_path); _seed_control(cfg)
    c = _client(cfg)
    r = c.get("/review")
    assert r.status_code == 200
    assert b"Control file unreadable" not in r.data
