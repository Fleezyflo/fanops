# MOL-148 (P7): remove hooks_by_persona fields; crosspost reads m.hook; old ledgers load.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Moment, MomentHookDecision, Platform
from fanops.accounts import Accounts, Account, AccountStatus
from fanops.crosspost import crosspost_clips


def test_moment_model_has_no_hooks_by_persona():
    assert "hooks_by_persona" not in Moment.model_fields
    assert "hooks_by_persona_removed" not in Moment.model_fields
    assert "hooks_by_persona" not in MomentHookDecision.model_fields


def test_old_ledger_with_hooks_by_persona_loads(tmp_path):
    cfg = Config(root=tmp_path)
    raw = {
        "schema_version": 8,
        "sources": {"s1": {"id": "s1", "source_path": "/s.mp4", "state": "catalogued"}},
        "moments": {"m1": {"id": "m1", "parent_id": "s1", "content_token": "0-5", "start": 0, "end": 5,
                           "reason": "r", "state": "decided", "hook": "the kept hook",
                           "hooks_by_persona": {"@a": "legacy"}, "hooks_by_persona_removed": {"@a": "x"}}},
        "clips": {}, "posts": {}, "renders": {}, "selection_facts": {}, "batches": {}, "stitch_plans": {},
    }
    cfg.control.mkdir(parents=True, exist_ok=True)
    cfg.ledger_path.write_text(json.dumps(raw))
    assert Ledger.load(cfg).moments["m1"].hook == "the kept hook"


def test_crosspost_uses_m_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active"},
    ]}))
    cfg.control.mkdir(parents=True, exist_ok=True)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "c1.mp4"; base.write_bytes(b"X")
    cfg.ledger_path.write_text(json.dumps({
        "schema_version": 8,
        "sources": {"s1": {"id": "s1", "source_path": "/s.mp4", "state": "catalogued", "width": 1080, "height": 1920}},
        "moments": {"m1": {"id": "m1", "parent_id": "s1", "content_token": "0-5", "start": 0, "end": 5,
                           "reason": "r", "state": "clipped", "hook": "ONE MOMENT HOOK",
                           "hooks_by_persona": {"@a": "LEGACY"}}},
        "clips": {"c1": {"id": "c1", "parent_id": "m1", "path": str(base), "aspect": "9:16", "state": "captioned",
                         "meta_captions": {"@a/instagram": {"caption": "A", "hashtags": []},
                                           "@b/instagram": {"caption": "B", "hashtags": []}}}},
        "posts": {}, "renders": {}, "selection_facts": {}, "batches": {}, "stitch_plans": {},
    }))
    accts = Accounts(cfg)
    accts.accounts = [Account(handle="@a", account_id="1", platforms=[Platform.instagram], status=AccountStatus.active),
                      Account(handle="@b", account_id="2", platforms=[Platform.instagram], status=AccountStatus.active)]
    led = crosspost_clips(Ledger.load(cfg), cfg, accts, base_time="2026-06-02T18:00:00Z")
    hooks = {p.account: p.variant_hook for p in led.posts.values()}
    assert hooks == {"@a": "ONE MOMENT HOOK", "@b": "ONE MOMENT HOOK"}
