# tests/test_persona_cut_derivation.py — P2: a persona's content_focus + energy DERIVE a default CUT spec
# (length band + framing) so a distinct persona produces a distinct CLIP with NO hand-set clip_profile. The
# wire (hydrate -> resolve -> wants_cut -> render_account_cut) is already built; this only supplies its inputs.
# Pin always wins; derivation is the floor; identical signals derive identically (dedup-safe); empty -> global.
import json
from fanops.config import Config, FRAMING_NAMES
from fanops.personas import (Persona, derive_cut_spec, resolved_cut_spec, compose_breakdown,
                             _FOCUS_PROFILE, _ENERGY_FRAMING, add_persona)
from fanops.accounts import Accounts, link_persona
from fanops.bands import PROFILE_NAMES


def _p(**kw): return Persona(id="p", **kw)

def _accounts(cfg, accts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accts}))

def _acct(handle="@a"):
    return {"handle": handle, "account_id": "1", "platforms": ["instagram"], "status": "active"}


# ---- derivation table: every emitted value is a valid engine name (a typo would silently kill the cut) ----
def test_derivation_values_are_valid_names():
    assert set(_FOCUS_PROFILE.values()) <= PROFILE_NAMES
    assert set(_ENERGY_FRAMING.values()) <= FRAMING_NAMES

def test_focus_maps_to_length():
    assert derive_cut_spec(_p(content_focus=["storytelling"]))[0] == "long"
    assert derive_cut_spec(_p(content_focus=["punchlines"]))[0] == "short"
    assert derive_cut_spec(_p(content_focus=["emotional"]))[0] == "medium"

def test_energy_maps_to_framing():
    assert derive_cut_spec(_p(energy="high"))[1] == "center"
    assert derive_cut_spec(_p(energy="low"))[1] == "top"
    assert derive_cut_spec(_p(energy="medium"))[1] is None        # medium = no opinion -> global

def test_multi_focus_is_deterministic_longer_bias_first():
    assert derive_cut_spec(_p(content_focus=["punchlines", "storytelling"]))[0] == "long"
    assert derive_cut_spec(_p(content_focus=["storytelling", "punchlines"]))[0] == "long"   # order-independent

def test_empty_persona_derives_nothing():
    assert derive_cut_spec(_p()) == (None, None)


# ---- resolved_cut_spec: pin OVER derived OVER None ----
def test_pin_beats_derived():
    p = _p(content_focus=["storytelling"], energy="high", clip_profile="medium", framing="top")
    assert resolved_cut_spec(p) == ("medium", "top")              # explicit pin wins over derived long/center

def test_derived_when_unpinned():
    assert resolved_cut_spec(_p(content_focus=["punchlines"], energy="high")) == ("short", "center")

def test_global_when_bare():
    assert resolved_cut_spec(_p(voice="v")) == (None, None)


# ---- hydration: a linked signal-bearing persona drives the account's cut; unlinked is unchanged ----
def test_hydration_applies_derived_spec(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct()])
    pid = add_persona(cfg, name="Storyteller", voice="v", content_focus=["storytelling"], energy="low")
    link_persona(cfg, "@a", pid)
    acc = next(a for a in Accounts.load(cfg).accounts if a.handle == "@a")
    assert acc.clip_profile == "long" and acc.framing == "top"    # derived from content_focus/energy

def test_hydration_pin_wins(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct()])
    pid = add_persona(cfg, name="Pinned", voice="v", content_focus=["storytelling"], clip_profile="short")
    link_persona(cfg, "@a", pid)
    acc = next(a for a in Accounts.load(cfg).accounts if a.handle == "@a")
    assert acc.clip_profile == "short"                            # pin wins over derived long

def test_unlinked_account_unchanged(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct()])
    acc = next(a for a in Accounts.load(cfg).accounts if a.handle == "@a")
    assert acc.clip_profile is None and acc.framing is None       # no link -> byte-identical (global stands)


# ---- compose_breakdown surfaces the derived source truthfully (persona / derived / global) ----
def test_compose_breakdown_cut_source_three_way(tmp_path):
    cfg = Config(root=tmp_path)
    assert compose_breakdown(cfg, _p(clip_profile="short"))["cut"]["source"] == "persona"
    assert compose_breakdown(cfg, _p(content_focus=["storytelling"]))["cut"]["source"] == "derived"
    assert compose_breakdown(cfg, _p(voice="v"))["cut"]["source"] == "global"
    assert "28-45s" in compose_breakdown(cfg, _p(content_focus=["storytelling"]))["cut"]["band"]   # the derived long band shows
