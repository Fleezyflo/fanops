# tests/test_studio_home_onboarding.py — Slice 4 (balanced feel): a brand-new operator landing on Home gets a
# guided "Get started" path (connect → make → review), each step marked done/next from state, instead of a barren
# status page. The panel disappears once the operator has both accounts and footage (an established Home stays lean).
import json
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source


def _accounts(cfg, rows):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    return app.test_client()


def test_brand_new_home_shows_get_started_with_connect_next(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [])           # no accounts, no footage → truly new
    html = _client(cfg).get("/").data.decode()
    assert "Get started" in html
    assert 'data-step="connect" data-state="next"' in html    # step 1 is the next action
    assert 'data-step="make" data-state="todo"' in html        # later steps wait their turn
    assert 'data-step="review" data-state="todo"' in html


def test_home_with_accounts_marks_connect_done_make_next(tmp_path):
    cfg = Config(root=tmp_path)
    _accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    html = _client(cfg).get("/").data.decode()
    assert "Get started" in html                               # still guiding — no footage yet
    assert 'data-step="connect" data-state="done"' in html     # connect satisfied
    assert 'data-step="make" data-state="next"' in html        # make is now the next action


def test_home_fully_setup_hides_get_started(tmp_path):
    cfg = Config(root=tmp_path)
    _accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/v.mp4", origin_kind="native"))
    html = _client(cfg).get("/").data.decode()
    assert "Get started" not in html                           # accounts + footage → established Home stays lean
