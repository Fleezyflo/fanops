"""Corrupt personas.json must not leave Accounts.load green.

A valid linked persona still hydrates (negative control below). Torn personas.json is
ControlFileError — not a logged swallow that returns the account registry."""
import json
import pytest
from fanops.config import Config
from fanops.accounts import Accounts
from fanops.errors import ControlFileError


def _write_accounts(cfg, rows):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))


def test_hydrate_failopen_logs_breadcrumb_on_corrupt_personas(tmp_path):
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active",
                           "persona": "inline voice", "persona_id": "ghost"}])
    cfg.personas_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.personas_path.write_text("{not valid json")
    with pytest.raises(ControlFileError, match="personas.json"):
        Accounts.load(cfg)


def test_hydrate_still_applies_when_personas_valid(tmp_path):
    # Negative control: a valid linked persona still overrides the inline voice.
    from fanops import personas as P
    cfg = Config(root=tmp_path)
    pid = P.add_persona(cfg, name="P1", voice="curator voice", niche=["hiphop"])
    _write_accounts(cfg, [{"handle": "@a", "platforms": ["instagram"], "status": "active",
                           "persona": "stale inline", "persona_id": pid}])
    a = Accounts.load(cfg).accounts[0]
    assert a.persona == "curator voice"
