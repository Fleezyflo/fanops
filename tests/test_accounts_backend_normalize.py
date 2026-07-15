"""RC-3 (S02): a per-channel backend VALUE in accounts.json is normalized at the ONE load boundary and
skip-and-flagged when unknown, so a hand-edit typo can never reach the six downstream resolvers that
each mishandle an unknown differently (get_poster silently DryRuns on a LIVE system, get_media_uploader
silently file://s it, others pass the raw string through). set_backend was the sole normalizer and it
guards only the Studio/CLI write path; the hand-edited file (the documented operator channel) bypassed
it entirely. Fix mirrors the sibling skipped_rows leniency (test_accounts_malformed_row)."""
import json
from fanops.config import Config
from fanops.accounts import Accounts


def _seed(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))


def test_unknown_backend_value_dropped_and_flagged(tmp_path):
    # A typo'd per-channel backend ('postis') is DROPPED from the loaded account (so it never reaches the
    # divergent resolvers) and FLAGGED via skipped_rows; the account's other valid channel survives.
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram", "tiktok"], "status": "active",
         "integrations": {"instagram": "ig1", "tiktok": "tk1"},
         "backends": {"instagram": "postiz", "tiktok": "postis"}},   # tiktok backend is a typo
    ])
    accts = Accounts.load(cfg)
    assert accts.accounts[0].backends == {"instagram": "postiz"}      # good channel kept, typo dropped
    assert any("postis" in s for s in accts.skipped_rows)             # dropped LOUDLY, not silently


def test_backend_value_case_and_whitespace_normalized(tmp_path):
    # A case/whitespace hand-edit is REPAIRED to canonical (the same strip+lower set_backend applies),
    # not dropped — both channels survive with normalized values, nothing flagged.
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram", "tiktok"], "status": "active",
         "integrations": {"instagram": "ig1", "tiktok": "tk1"},
         "backends": {"instagram": "Postiz", "tiktok": " zernio "}},
    ])
    accts = Accounts.load(cfg)
    assert accts.accounts[0].backends == {"instagram": "postiz", "tiktok": "zernio"}
    assert accts.skipped_rows == []


def test_valid_backend_values_unchanged(tmp_path):
    # Byte-identical for canonical data: values pass through untouched, nothing flagged.
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig1"}, "backends": {"instagram": "postiz"}},
    ])
    accts = Accounts.load(cfg)
    assert accts.accounts[0].backends == {"instagram": "postiz"}
    assert accts.skipped_rows == []
