"""Foundation-honesty wave6: accounts._hydrate_from_personas bare except -> fail_open.

Hydrate semantics unchanged: corrupt/unreadable personas.json leaves inline account values
standing. The only change is a logged fail_open breadcrumb (never a silent swallow)."""
import json
import logging
from fanops.config import Config
from fanops.accounts import Accounts


def _write_accounts(cfg, rows):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))


def test_hydrate_failopen_logs_breadcrumb_on_corrupt_personas(tmp_path, caplog):
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active",
                           "persona": "inline voice", "persona_id": "ghost"}])
    cfg.personas_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.personas_path.write_text("{not valid json")
    with caplog.at_level(logging.WARNING):
        a = Accounts.load(cfg).accounts[0]
    assert a.persona == "inline voice"    # inline stands — hydrate semantics unchanged
    assert any("accounts._hydrate_from_personas" in r.getMessage() and "fail-open" in r.getMessage()
               for r in caplog.records)


def test_hydrate_still_applies_when_personas_valid(tmp_path):
    # Negative control: a valid linked persona still overrides the inline voice.
    from fanops import personas as P
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="P1", voice="curator voice", niche=["hiphop"])
    _write_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active",
                           "persona": "stale inline", "persona_id": pid}])
    a = Accounts.load(cfg).accounts[0]
    assert a.persona == "curator voice"
