# tests/test_persona_cut_derivation.py — P2: a persona's cut_policy DERIVE a default CUT spec
import json
from types import SimpleNamespace
from fanops.config import Config, FRAMING_NAMES
from fanops.personas import (Persona, derive_cut_spec, resolved_cut_spec, compose_breakdown,
                             _FOCUS_PROFILE, _FRAMING_MAP, add_persona)
from fanops.accounts import Accounts, link_persona
from fanops.bands import PROFILE_NAMES

def _p(**kw): return Persona(id="p", **kw)

def _accounts(cfg, accts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accts}))

def _acct(handle="a"):
    return {"handle": handle, "account_id": "1", "platforms": ["instagram"], "status": "active"}

def test_derivation_values_are_valid_names():
    assert set(_FOCUS_PROFILE.values()) <= PROFILE_NAMES
    assert set(_FRAMING_MAP.values()) <= FRAMING_NAMES

def test_policy_does_not_map_to_length():
    assert derive_cut_spec(_p(cut_policy=["storytelling"]))[0] is None
    assert derive_cut_spec(_p(cut_policy=["punchlines"]))[0] is None
    assert derive_cut_spec(_p(cut_policy=["emotional"]))[0] is None

def test_cut_policy_maps_to_framing():
    assert derive_cut_spec(_p(cut_policy=["punchlines"]))[1] == "center"
    assert derive_cut_spec(_p(cut_policy=["storytelling"]))[1] == "top"
    assert derive_cut_spec(_p(cut_policy=[]))[1] is None

def test_multi_policy_length_stays_none():
    assert derive_cut_spec(_p(cut_policy=["punchlines", "storytelling"]))[0] is None
    assert derive_cut_spec(_p(cut_policy=["storytelling", "punchlines"]))[0] is None

def test_empty_persona_derives_nothing():
    assert derive_cut_spec(_p()) == (None, None)

def test_account_carrier_pin_beats_derived():
    # Account carrier duck-typing: cut_policy is tokens.
    acc = SimpleNamespace(cut_policy=["punchlines", "hype"], clip_profile="medium", framing="top")
    assert resolved_cut_spec(acc) == (None, "top")

def test_persona_can_no_longer_pin_only_derives():
    p = _p(cut_policy=["punchlines", "hype"], clip_profile="medium", framing="top")
    assert resolved_cut_spec(p) == (None, "center")

def test_derived_when_unpinned():
    assert resolved_cut_spec(_p(cut_policy=["punchlines", "hype"])) == (None, "center")

def test_global_when_bare():
    assert resolved_cut_spec(_p(voice="v")) == (None, None)

def test_hydration_stamps_cut_spec(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct()])
    pid = add_persona(cfg, name="Storyteller", voice="v", cut_policy=["storytelling", "emotional"], niche=["hiphop"])
    link_persona(cfg, "@a", pid)
    acc = next(a for a in Accounts.load(cfg).accounts if a.handle == "a")
    assert acc.cut_policy == ["storytelling", "emotional"]
    assert acc.clip_profile is None and acc.framing == "top"

def test_unlinked_account_unchanged(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct()])
    acc = next(a for a in Accounts.load(cfg).accounts if a.handle == "a")
    assert acc.clip_profile is None and acc.framing is None

def test_compose_breakdown_cut_source_three_way(tmp_path):
    cfg = Config(root=tmp_path)
    pinned = SimpleNamespace(clip_profile="short", cut_policy=[], selection_scope=None, hook_angle=None,
                             hashtag_corpus=[], voice="v")
    assert compose_breakdown(cfg, pinned)["cut"]["source"] == "global"
    assert compose_breakdown(cfg, _p(cut_policy=["storytelling"]))["cut"]["source"] == "derived"
    assert compose_breakdown(cfg, _p(voice="v"))["cut"]["source"] == "global"
    assert compose_breakdown(cfg, _p(cut_policy=["storytelling"]))["cut"]["band"] == ""

def test_voice_match_hydrates_levers_and_cut_spec(tmp_path):
    cfg = Config(root=tmp_path)
    voice = "music-blogger curator who champions craft."
    _accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "persona": voice}])
    add_persona(cfg, name="Craft", voice=voice, cut_policy=["storytelling", "emotional"], niche=["hiphop"])
    acc = next(a for a in Accounts.load(cfg).accounts if a.handle == "a")
    assert acc.persona_id is None and acc.cut_policy == ["storytelling", "emotional"]
    assert acc.clip_profile is None and acc.framing == "top"
